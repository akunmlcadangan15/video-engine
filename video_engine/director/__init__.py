"""Director stage of `engine draft`: storyboard concepts before the timeline, and a frame review after it.

storyboard: one request (`draft/storyboard`, one repair `draft/storyboard1`) -> 3 concepts over the locked narration
(spans = contiguous word spans in VO order, each with a goal and a pattern from patterns.toml) -> engine validation
(order, spans, facts only from narration/script/asset notes/credits) -> score (narration fit, clarity, asset
availability, render cost, taste penalties) -> the best valid concept is appended to the draft prompt.
review: frames sampled at critical times of the drafted timeline + narration timing -> timestamped findings ->
at most `rounds` revision requests; every accepted version is kept under <project>/draft/.
taste store: <taste dir>/<channel>.json (liked, rejected, rules, recent openers/transitions)."""
from __future__ import annotations

import json
import os
import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config
from ..ai.base import Image, Text

HERE = Path(__file__).parent
DIRECTIONS = ("grounded", "contrast", "bold")
WEIGHTS = {"narration_fit": 0.35, "clarity": 0.25, "availability": 0.25, "render": 0.15}
OPENER_PENALTY, TRANSITION_PENALTY, TRANSITION_PENALTY_MAX = 0.15, 0.03, 0.15
RECENT_KEEP = 20
REVIEW_MAX_FRAMES = 16
REVIEW_PX = 720
MAP_HINT = re.compile(r"map|peta|satell?it|globe|atlas", re.I)


# ------------------------------------------------------------------------------------------------- data: patterns
def patterns() -> dict[str, dict]:
    return tomllib.loads((HERE / "patterns.toml").read_text())["patterns"]


def guidance(section: str) -> str:
    """Prompt text of `## STORYBOARD` or `## REVIEW` in guidance.md."""
    text = (HERE / "guidance.md").read_text()
    parts = re.split(r"^## (\w+)\s*$", text, flags=re.M)
    return dict(zip(parts[1::2], (p.strip() for p in parts[2::2])))[section]


# ---------------------------------------------------------------------------------------------------- taste store
def taste_dir() -> Path:
    return Path(os.environ.get("VIDEO_ENGINE_TASTE_DIR") or config.PROFILES_DIR / "taste")


def _empty(channel: str) -> dict:
    return {"channel": channel, "version": 1, "rules": [], "liked": [], "rejected": [],
            "recent": {"openers": [], "transitions": []}, "avoid_recent": 3}


def load_taste(channel: str) -> dict:
    p = taste_dir() / f"{channel}.json"
    d = _empty(channel)
    if p.is_file():
        d.update(json.loads(p.read_text()))
        d.setdefault("recent", {}).setdefault("openers", [])
        d["recent"].setdefault("transitions", [])
    return d


def save_taste(d: dict) -> Path:
    p = taste_dir() / f"{d['channel']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    return p


def record_feedback(channel: str, verdict: str, what: str, why: str, checks: list[dict] | None = None) -> dict:
    """verdict liked|rejected; the same `what` replaces its older entry (in either list). `checks`: conditions the
    channel profile has to satisfy for the entry to be true (taste_lint.py; `engine taste CHANNEL --lint`)."""
    if verdict not in ("liked", "rejected"):
        raise ValueError("verdict must be liked or rejected")
    if not what.strip() or not why.strip():
        raise ValueError("feedback needs what and why")
    d = load_taste(channel)
    for k in ("liked", "rejected"):
        d[k] = [x for x in d[k] if x.get("what") != what]
    entry: dict[str, Any] = {"what": what, "why": why, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if checks:
        from ..taste_lint import validate
        entry["check"] = [validate(c) for c in checks]
    d[verdict].append(entry)
    save_taste(d)
    return d


def remember(channel: str, opener: str | None, transitions: list[str]) -> dict:
    """Prepend what this draft used, so the next drafts avoid repeating it."""
    d = load_taste(channel)
    r = d["recent"]
    if opener:
        r["openers"] = ([opener] + r["openers"])[:RECENT_KEEP]
    ts = [t for t in transitions if t]
    if ts:
        r["transitions"] = (list(dict.fromkeys(ts)) + r["transitions"])[:RECENT_KEEP]
    save_taste(d)
    return d


def _recent(taste: dict, key: str) -> list[str]:
    return taste["recent"][key][:int(taste.get("avoid_recent", 3))]


def taste_text(taste: dict) -> str:
    lines = [f"CHANNEL TASTE ({taste['channel']}; historical preferences, not universal constraints). "
             "Words such as never/always in these notes describe past preferences, not technical blockers. "
             "The current project brief and segment intent take precedence for creative choices; "
             "effective profile settings, source facts and render integrity remain authoritative."]
    lines += [f"- rule: {r}" for r in taste.get("rules", [])]
    lines += [f"- liked: {x['what']} (because {x['why']})" for x in taste.get("liked", [])]
    lines += [f"- rejected: {x['what']} (because {x['why']})" for x in taste.get("rejected", [])]
    if _recent(taste, "openers"):
        lines.append("- recent openers, do not repeat: " + "; ".join(_recent(taste, "openers")))
    if _recent(taste, "transitions"):
        lines.append("- recent transitions, avoid: " + "; ".join(_recent(taste, "transitions")))
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------- storyboard
def asset_kinds(a) -> set[str]:
    k = {a.kind}
    if a.kind == "image" and MAP_HINT.search(" ".join([a.id, a.path, *a.hints])):
        k.add("map")
    return k


def schema(inp) -> dict:
    s, i, n = {"type": "string"}, {"type": "integer"}, {"type": "number"}
    span = {"type": "object", "properties": {
        "from_word": i, "to_word": i, "goal": s, "pattern": {"type": "string", "enum": sorted(patterns())},
        "assets": {"type": "array", "items": {"type": "string", "enum": [a.id for a in inp.assets]}},
        "on_screen_text": s, "camera": s, "transition": s},
        "required": ["from_word", "to_word", "goal", "pattern", "assets"]}
    concept = {"type": "object", "properties": {
        "name": s, "direction": {"type": "string", "enum": list(DIRECTIONS)}, "opener": s, "hero_moment": s,
        "spans": {"type": "array", "items": span, "minItems": 1},
        "self_scores": {"type": "object", "properties": {"narration_fit": n, "clarity": n},
                        "required": ["narration_fit", "clarity"]}},
        "required": ["name", "direction", "opener", "spans", "self_scores"]}
    return {"type": "object", "properties": {"concepts": {"type": "array", "items": concept, "minItems": 3,
                                                          "maxItems": 3}}, "required": ["concepts"]}


def _words(inp) -> str:
    return " ".join(f"[{k}]{w.word.strip()}" for k, w in enumerate(inp.words))


def prompt(inp, taste: dict) -> str:
    pats = "\n".join(f"- {k} (render cost {v['render_cost']}): {v['purpose']} Needs: "
                     f"{v['needs'].get('min_assets', 1)}+ asset(s) of {'/'.join(v['needs'].get('kinds', []))}"
                     for k, v in sorted(patterns().items()))
    assets = "\n".join(f"- {a.id}: {'/'.join(sorted(asset_kinds(a)))}" + (f", {a.duration_s:.1f}s" if a.duration_s else "")
                       + "".join(f"; {h}" for h in a.hints) for a in inp.assets)
    return f"""STORYBOARD REQUEST. You are the director of a vertical documentary short. Before any timeline is drafted,
propose exactly 3 storyboard concepts for the attached narration, one per direction: grounded, contrast, bold.

NARRATION WORDS, indexed [k] (locked: keep this order, cover every word exactly once):
{_words(inp)}

SCRIPT (meaning and names):
{inp.script or '(no script file; use the words above)'}

ASSETS (thumbnails follow, labelled by id):
{assets}

PATTERNS (pattern library):
{pats}

{guidance('STORYBOARD')}

{taste_text(taste)}

FORMAT
- spans: contiguous word spans (storyboard spans; not to be confused with timeline Beat, the per-frame screen unit of a map scene): the first from_word 0, each from_word = previous to_word + 1, the last to_word = {max(0, len(inp.words) - 1)}.
- goal: what the viewer must understand or feel at the end of the beat (one sentence).
- pattern from the library; assets = the asset ids the beat shows; on_screen_text only with facts from the sources.
- opener: one line describing the opening; transition: how the beat is entered (e.g. continuous zoom, hold, cut).
- The three directions must differ in storytelling strategy, pacing and use of evidence, not only colours or transitions.
  Grounded may use a documentary hold; contrast may use comparison; bold may use a minimalist reveal or camera journey.
  A static shot, negative space, a footage-led opener and an ending without a keyword callback are valid when purposeful.
- self_scores: narration_fit and clarity 0-10, honest.
Return JSON only, matching the schema."""


def _norm(t: str) -> str:
    return re.sub(r"[^\w]", "", t.lower())


def sources_text(inp) -> str:
    return " ".join([" ".join(w.word for w in inp.words), inp.script or "",
                     " ".join(" ".join([a.id, a.path, *a.hints]) for a in inp.assets),
                     " ".join(c.text for c in inp.credit_lines)])


def invented(text: str, sources: str) -> list[str]:
    """Numbers and capitalised names in `text` that no source contains."""
    src = _norm(sources)
    src_words = {_norm(w) for w in re.findall(r"[\w.,]+", sources)}
    out = []
    for tok in re.findall(r"[\w.,'-]+", text or ""):
        core_tok = tok.strip(".,'-")
        if not core_tok:
            continue
        if re.search(r"\d", core_tok):
            if re.sub(r"[.,]", "", core_tok) not in re.sub(r"[.,]", "", src):
                out.append(core_tok)
        elif core_tok[0].isupper() and len(core_tok) >= 3 and _norm(core_tok) not in src_words:
            out.append(core_tok)
    return out


def validate(c: dict, inp, pats: dict) -> dict:
    """Engine checks of one concept -> {errors, availability, render, spans_checked}."""
    errs: list[str] = []
    spans = c.get("spans") or c.get("beats") or []  # "beats" is the legacy alias, normalised to spans
    n = len(inp.words)
    by_id = {a.id: a for a in inp.assets}
    want = 0
    for k, b in enumerate(spans):
        f, t = b.get("from_word"), b.get("to_word")
        if not isinstance(f, int) or not isinstance(t, int) or f > t:
            errs.append(f"span {k}: bad word span {f}-{t}")
            continue
        if f != want:
            errs.append(f"span {k}: starts at word {f}, expected {want} (narration order is locked; no gaps or overlaps)")
        want = t + 1
        if b.get("pattern") not in pats:
            errs.append(f"span {k}: unknown pattern {b.get('pattern')!r}")
        bad = invented(b.get("on_screen_text", ""), sources_text(inp))
        if bad:
            errs.append(f"span {k}: on-screen text states facts not in the sources: {', '.join(bad)}")
    if spans and n and want != n:
        errs.append(f"spans end at word {want - 1}, narration has {n} words (cover every word)")
    if not spans:
        errs.append("no spans")
    avail, costs = [], []
    for b in spans:
        p = pats.get(b.get("pattern"), {})
        ids = b.get("assets") or []
        known = [by_id[x] for x in ids if x in by_id]
        need = p.get("needs", {})
        kinds = set().union(*(asset_kinds(a) for a in known)) if known else set()
        ok_needs = (len(set(a.id for a in known)) >= need.get("min_assets", 1)
                    and (not need.get("kinds") or kinds & set(need["kinds"]))
                    and (not need.get("map") or "map" in kinds))
        avail.append(0.0 if len(known) < len(ids) or not known else 1.0 if ok_needs else 0.5)
        costs.append(p.get("render_cost", 2))
    mean_cost = sum(costs) / len(costs) if costs else 3
    return {"errors": errs, "availability": round(sum(avail) / len(avail), 3) if avail else 0.0,
            "render": round(1 - (mean_cost - 1) / 2, 3)}


def score(c: dict, v: dict, taste: dict) -> dict:
    ss = c.get("self_scores") or {}
    fit = min(10.0, max(0.0, float(ss.get("narration_fit", 0)))) / 10
    clar = min(10.0, max(0.0, float(ss.get("clarity", 0)))) / 10
    pen, why = 0.0, []
    if c.get("opener") and _norm(c["opener"]) in {_norm(o) for o in _recent(taste, "openers")}:
        pen += OPENER_PENALTY
        why.append("opener used recently")
    rec_t = {_norm(t) for t in _recent(taste, "transitions")}
    rep = [b.get("transition") for b in (c.get("spans") or c.get("beats") or []) if b.get("transition") and _norm(b["transition"]) in rec_t]
    if rep:
        pen += min(TRANSITION_PENALTY_MAX, TRANSITION_PENALTY * len(rep))
        why.append(f"{len(rep)} recently used transition(s)")
    parts = {"narration_fit": fit, "clarity": clar, "availability": v["availability"], "render": v["render"]}
    total = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS) - pen
    return {**{k: round(x, 3) for k, x in parts.items()}, "taste_penalty": round(pen, 3), "taste_notes": why,
            "total": round(total, 3)}


def rank(data: dict, inp, taste: dict) -> list[dict]:
    """All concepts with validation + score; valid ones first, best first."""
    pats = patterns()
    out = []
    for k, c in enumerate(data.get("concepts") or []):
        v = validate(c, inp, pats)
        out.append({"index": k, "concept": c, "errors": v["errors"], "valid": not v["errors"], "score": score(c, v, taste)})
    return sorted(out, key=lambda r: (not r["valid"], -r["score"]["total"], r["index"]))


def beat_narration(inp, b: dict) -> str:
    return " ".join(w.word.strip() for w in inp.words[b["from_word"]:b["to_word"] + 1])


def chosen_text(inp, c: dict) -> str:
    fps = inp.settings.fps
    lines = [f"CHOSEN STORYBOARD ({c.get('direction')}: {c.get('name')}). Build the segments from these spans in order: "
             "a span may cover several segments, and segments may be split for timing, but keep its goal, pattern and "
             "assets. Copy the beat goal into each segment's \"goal\".",
             f"Opener (first 1.5 s): {c.get('opener')}", f"Hero moment: {c.get('hero_moment') or '-'}"]
    pats = patterns()
    for k, b in enumerate(c.get("spans") or c.get("beats") or []):
        w0, w1 = inp.words[b["from_word"]], inp.words[b["to_word"]]
        rec = pats.get(b["pattern"], {}).get("recipe")
        lines.append(f"{k}. frames {w0.start_frame}-{w1.end_frame} ({w0.start_frame / fps:.1f}-{w1.end_frame / fps:.1f} s) "
                     f"\"{beat_narration(inp, b)}\" | goal: {b['goal']} | pattern: {b['pattern']} | assets: "
                     f"{', '.join(b.get('assets') or [])}" + (f" | text: {b['on_screen_text']}" if b.get("on_screen_text") else "")
                     + (f" | camera: {b['camera']}" if b.get("camera") else "")
                     + (f" | enter: {b['transition']}" if b.get("transition") else "")
                     + (f" | on a map: map_recipe {rec}" if rec else ""))
    return "\n".join(lines)


def plan(provider, inp, media: list, files: dict, taste: dict) -> dict:
    """Storyboard requests -> {"rounds": [...], "ranked": [...], "chosen": rank row | None, "warning"?}."""
    sch = schema(inp)
    base = media + [Text(prompt(inp, taste))]
    contents, rounds, ranked = base, [], []
    for rnd in range(2):
        rid = "draft/storyboard" if rnd == 0 else f"draft/storyboard{rnd}"
        r = provider.generate_json("planning", contents, sch, rid, files)
        ranked = rank(r.data, inp, taste)
        rounds.append({"round": rnd, "ai": r.meta(), "errors": [e for x in ranked for e in x["errors"]]})
        if any(x["valid"] for x in ranked):
            break
        errs = "\n".join(f"- concept {x['index']}: {e}" for x in ranked for e in x["errors"])
        contents = base + [Text("YOUR PREVIOUS STORYBOARDS:\n" + json.dumps(r.data, ensure_ascii=False)),
                           Text("THE ENGINE REJECTED EVERY CONCEPT:\n" + errs + "\nReturn 3 corrected concepts.")]
        files = {**files, "previous_response.json": r.data, "storyboard_errors.txt": errs}
    chosen = next((x for x in ranked if x["valid"]), None)
    out = {"rounds": rounds, "ranked": ranked, "chosen": chosen}
    if chosen is None:
        out["warning"] = "no valid storyboard concept after repair; drafting without a storyboard"
    return out


# ---------------------------------------------------------------------------------------------------------- review
def critical_times(tl) -> list[tuple[float, int]]:
    """(t_s, seg): hook 0 s and 1.5 s, each segment just after its start and just before its end, the last frame."""
    fps = tl.settings.fps
    last = (tl.total_frames - 1) / fps
    ts = [0.0, min(1.5, last)]
    for s in tl.segments:
        ts += [min(s.end_frame - 1, s.start_frame + round(0.25 * fps)) / fps, max(s.start_frame, s.end_frame - 3) / fps]
    ts.append(last)
    ts = sorted({round(t, 2) for t in ts})
    if len(ts) > REVIEW_MAX_FRAMES:
        step = (len(ts) - 1) / (REVIEW_MAX_FRAMES - 1)
        ts = [ts[round(k * step)] for k in range(REVIEW_MAX_FRAMES)]
    seg_of = lambda t: next(s.seg_index for s in tl.segments if s.start_frame <= round(t * fps) < s.end_frame)
    return [(t, seg_of(t)) for t in ts]


def sample_frames(tl, pdir: Path, times: list[tuple[float, int]], workdir: Path) -> list[tuple[float, int, bytes]]:
    """Render one frame per (t, seg) and return small JPEGs. Native segments via the native preview, HyperFrames
    segments via one DOM probe per segment."""
    import subprocess

    from .. import hyperframes, native
    fps = tl.settings.fps
    workdir.mkdir(parents=True, exist_ok=True)
    pngs: dict[float, Path] = {}
    for seg in sorted({s for _, s in times}):
        s = tl.segments[seg]
        mine = [t for t, k in times if k == seg]
        if s.engine_type == "NATIVE_RUST_FFMPEG":
            job = native.plan(s, tl, pdir)
            for t in mine:
                pngs[t] = native.preview_frame(job, tl, round(t * fps) - s.start_frame, workdir / f"f{t:.2f}.png")
        else:
            skey = hyperframes.keys(s, tl, pdir)[0]
            hf_dir = workdir / "hf" / f"seg{seg:03d}"
            kfile = workdir / "hf" / f"seg{seg:03d}.key"
            if (hf_dir / "index.html").is_file() and (hf_dir / "scene.js").is_file() and kfile.is_file() and kfile.read_text() == skey:
                d = hf_dir
            else:
                d, _ = hyperframes.build_dir(s, tl, pdir, hf_dir)
                kfile.parent.mkdir(parents=True, exist_ok=True)
                kfile.write_text(skey)
            rel = [min((round(t * fps) - s.start_frame) / fps, (s.n_frames - 1) / fps) for t in mine]
            res = hyperframes.probe_dom(d, rel, tl.settings.width, tl.settings.height, workdir / f"snaps{seg}", ref=skey)
            for t, r in zip(mine, res["times"]):
                pngs[t] = Path(r["png"])
    out = []
    for t, seg in times:
        r = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(pngs[t]), "-vf", f"scale={REVIEW_PX}:-2",
                            "-f", "image2", "-c:v", "mjpeg", "-q:v", "2", "-"], capture_output=True, check=True)
        out.append((t, seg, r.stdout))
    return out


def review_schema(tl) -> dict:
    s, n = {"type": "string"}, {"type": "number"}
    f = {"type": "object", "properties": {
        "t_s": n, "seg": {"type": "integer"}, "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
        "issue": s, "evidence": s, "fix": s}, "required": ["t_s", "seg", "severity", "issue", "fix"]}
    return {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["pass", "revise"]},
                                             "findings": {"type": "array", "items": f}},
            "required": ["verdict", "findings"]}


def review_parts(tl, inp, frames: list[tuple[float, int, bytes]], goals: dict[int, str]) -> list:
    fps = tl.settings.fps
    words = " ".join(f"[{w.start_frame / fps:.2f}s]{w.word.strip()}" for w in inp.words)
    segs = "\n".join(f"- seg {s.seg_index}: {s.start_frame / fps:.2f}-{s.end_frame / fps:.2f} s, {s.engine_type}, "
                     f"asset {s.asset_id}" + (f", goal: {goals[s.seg_index]}" if goals.get(s.seg_index) else "")
                     + (f", intent: {s.intent.model_dump(exclude_none=True)}" if s.intent else "")
                     + (", hold: read" if s.hold == "read" else "")
                     for s in tl.segments)
    parts: list = [Text(f"""DRAFT REVIEW. Frames of the drafted video follow, each labelled with its time and segment.

NARRATION WORDS with start time:
{words}

SEGMENTS:
{segs}

{guidance('REVIEW')}
Return JSON only, matching the schema.""")]
    for t, seg, jpg in frames:
        label = f"FRAME t={t:.2f}s seg {seg}"
        parts += [Text(label), Image(jpg, "image/jpeg", label)]
    return parts


def needs_revision(data: dict) -> list[dict]:
    """Findings that justify a revision round (blocker/major), only when the verdict asks for it."""
    if data.get("verdict") != "revise":
        return []
    return [f for f in data.get("findings") or [] if f.get("severity") in ("blocker", "major")]


def findings_text(fs: list[dict]) -> str:
    return "\n".join(f"- t={float(f['t_s']):.2f}s seg {f['seg']} [{f['severity']}] {f['issue']}"
                     + (f" (evidence: {f['evidence']})" if f.get("evidence") else "") + f" -> fix: {f['fix']}" for f in fs)
