"""`engine draft` with a mocked Gemini client: good and bad cases (inputs, assembly, repair loop, retries,
fallback, upload deletion verified, URLs only from credits.txt)."""
from __future__ import annotations

import json

import pytest

from video_engine.ai import base
from video_engine.ai import gemini as ai
from video_engine import audio, draft, timeline
from video_engine.cli import main
from video_engine.util import EngineError

from test_ai_director import APIErr, FakeFiles
from test_words_pin import RAW

CREDITS = """Kredit uji
- still — Fotografer A — https://example.org/asset/still — CC-BY-4.0
- clip | Videografer B | Pexels License | https://example.org/asset/clip
- map — Dinas Peta — https://example.org/asset/map — Public Domain
- wide clip — Studio C — https://example.org/asset/wide — Pexels License
"""


def storyboards(**over):
    """3 concepts over the 4 fixture words (Surabaya | bergadir malabi tewas.); `over` patches concept 0."""
    def concept(name, direction, fit, clar, pattern="establish", text="Surabaya"):
        return {"name": name, "direction": direction, "opener": f"{name} opener", "hero_moment": "Mallaby",
                "spans": [{"from_word": 0, "to_word": 0, "goal": "where: Surabaya", "pattern": pattern,
                           "assets": ["still"], "on_screen_text": text, "transition": "continuous zoom"},
                          {"from_word": 1, "to_word": 3, "goal": "what happened to Mallaby", "pattern": "footage_beat",
                           "assets": ["clip_1080x1920_30"], "transition": "whip"}],
                "self_scores": {"narration_fit": fit, "clarity": clar}}
    cs = [concept("lurus", "grounded", 8, 9), concept("banding", "contrast", 7, 7, "comparison"),
          concept("berani", "bold", 9, 6, "tracing")]
    cs[0].update(over)
    return {"concepts": cs}


PASS_REVIEW = {"verdict": "pass", "findings": []}


class RecModels:
    """Records every request's contents; script[model] is a list of responses consumed per call. Storyboard and
    review requests are answered from `sb` / `review` (lists consumed per call, last repeats), recorded apart."""
    def __init__(self, script, sb=None, review=None):
        self.script, self.calls = {k: list(v) for k, v in script.items()}, []
        self.side = {"STORYBOARD REQUEST": list(sb or [storyboards()]), "DRAFT REVIEW": list(review or [PASS_REVIEW])}
        self.side_calls = {k: [] for k in self.side}

    def generate_content(self, model, contents, config):
        kind = next((k for k in self.side if any(isinstance(x, str) and x.startswith(k) for x in contents)), None)
        if kind:
            self.side_calls[kind].append((model, contents))
            q = self.side[kind]
            r = q.pop(0) if len(q) > 1 else q[0]
            return type("R", (), {"text": json.dumps(r), "usage_metadata": None})()
        self.calls.append((model, contents))
        r = self.script[model].pop(0) if len(self.script[model]) > 1 else self.script[model][0]
        if isinstance(r, Exception):
            raise r
        return type("R", (), {"text": r if isinstance(r, str) else json.dumps(r), "usage_metadata": None})()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


@pytest.fixture
def proj(project, monkeypatch):
    monkeypatch.setattr(audio, "transcribe_words",
                        lambda path, lang="id", model=audio.STT_MODEL, keyterms=(), use_cache=True:
                        ([dict(w) for w in RAW[:4]], True))
    p, _ = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav"}))
    (p / "credits.txt").write_text(CREDITS)
    (p / "script.txt").write_text("Surabaya. Brigadir Mallaby tewas.")
    return p


def good_plan(total=105):
    return {"title": "uji", "segments": [
        {"start_frame": 0, "end_frame": 48, "kind": "image", "asset": "still", "narration": "Surabaya",
         "why": "kota disebut", "camera_preset": "ken_burns_zoom_in", "intensity": 1.2,
         "overlays": [{"kind": "label", "position": "top", "text": "Jawa Timur 1945"}]},
        {"start_frame": 48, "end_frame": total, "kind": "video", "asset": "clip_1080x1920_30",
         "narration": "Brigadir Mallaby tewas", "why": "aksi", "source_in_s": 0.2}],
        "credits": [{"asset": "still", "credit_line": 1, "watermark_free": True},
                    {"asset": "clip_1080x1920_30", "credit_line": 2, "watermark_free": True}]}


def client(script, keep=False, sb=None, review=None):
    return type("C", (), {"models": RecModels(script, sb, review), "files": FakeFiles(keep)})()


def run(p, c, **kw):
    kw.setdefault("video_proxies", 0)
    prov = ai.GeminiProvider(client=c, models=kw.pop("models", ["m38", "fb"]))
    return draft.draft(p, provider=prov, dom=False, **kw)


def test_good_parse_credits_both_formats():
    ls = draft.parse_credits(CREDITS)
    assert [c.n for c in ls] == [1, 2, 3, 4] and ls[0].url == "https://example.org/asset/still"
    assert (ls[0].license, ls[0].author) == ("CC-BY-4.0", "Fotografer A")
    assert (ls[1].license, ls[1].author) == ("Pexels License", "Videografer B")


def test_good_collect_inputs(proj):
    inp = draft.collect(proj)
    assert inp.total_frames == 90 + 15 and inp.script.startswith("Surabaya")
    ids = {a.id: a for a in inp.assets}
    assert set(ids) == {"still", "clip_1080x1920_30", "clip_1280x720_25", "map"}       # tone.wav = voiceover
    assert ids["still"].credit_line == 1 and ids["map"].credit_line == 3                 # matched via old URLs
    assert ids["clip_1280x720_25"].credit_line is None
    assert [w.word for w in inp.words][:2] == ["Surabaya", "bergadir"] and inp.words_meta.total_frames == 105
    sch = draft.schema(inp)
    assert sch["properties"]["segments"]["items"]["properties"]["asset"]["enum"] == [a.id for a in inp.assets]
    pr = draft.prompt(inp)
    assert "[3]Surabaya" in pr and "4. wide clip" in pr and "https://" in pr                # lines shown, numbered


def test_good_draft_first_round(proj):
    c = client({"m38": [good_plan()]})
    r = run(proj, c)
    assert r.ok and r.timeline_path == proj / "master_timeline.draft.json"
    rep = json.loads(r.report_path.read_text())
    assert rep["model"] == "m38" and rep["retries_total"] == 0 and not rep["fallback"] and rep["repair_rounds"] == 0
    assert rep["file_deletions"] and all(d["verified_gone"] for d in rep["file_deletions"]) and c.files.live == {}
    tl = timeline.load(r.timeline_path)
    assert [s.engine_type for s in tl.segments] == ["NATIVE_RUST_FFMPEG"] * 2 and tl.total_frames == 105
    assert tl.segments[0].overlays[0].kind == "label" and tl.segments[0].overlays[0].box == [190, 260, 700, 110]
    assert tl.segments[1].source_in_frame == 6 and tl.captions_meta is not None
    assert all(cr.source_url in CREDITS for cr in tl.credits_manifest)
    assert rep["segments"][1]["why"] == "aksi"
    assert not (proj / "master_timeline.json").read_text().count("draft")                # original untouched


def test_good_repair_round_feeds_gate_errors_back(proj):
    bad = good_plan()
    bad["segments"][1].update(asset="clip_1280x720_25")                              # no fixed credit, no pick
    c = client({"m38": [bad, good_plan()]})
    r = run(proj, c)
    rep = r.report
    assert r.ok and rep["repair_rounds"] == 1 and "no credit line" in rep["rounds"][0]["errors"][0]
    second = c.models.calls[1][1]
    assert any(isinstance(x, str) and "GATE ERRORS" in x and "no credit line" in x for x in second)


def test_good_model_picked_credit_line_copies_url(proj):
    plan = good_plan()
    plan["segments"][1].update(asset="clip_1280x720_25")
    plan["credits"].append({"asset": "clip_1280x720_25", "credit_line": 4, "watermark_free": True})
    r = run(proj, client({"m38": [plan]}))
    tl = timeline.load(r.timeline_path)
    cr = tl.credit_for(ref="clip_1280x720_25")
    assert cr.source_url == "https://example.org/asset/wide" and cr.license == "Pexels License"
    assert "model picked line 4" in " ".join(r.report["rounds"][0]["notes"])


def test_bad_gate_failures_exhaust_repairs(proj):
    bad = good_plan()
    bad["segments"][1]["source_in_s"] = 2.9                                          # M01: clip too short
    c = client({"m38": [bad]})
    r = run(proj, c)
    assert not r.ok and r.timeline_path is None and len(r.report["rounds"]) == 3
    assert any("M01" in e for e in r.report["rounds"][-1]["errors"])
    assert (proj / "master_timeline.draft.rejected.json").exists() and not (proj / "master_timeline.draft.json").exists()
    assert all(d["verified_gone"] for d in r.report["file_deletions"]) and c.files.live == {}


def test_bad_watermark_flag_fails_R14(proj):
    plan = good_plan()
    plan["credits"][0]["watermark_free"] = False
    r = run(proj, client({"m38": [plan]}), max_repairs=0)
    pre = r.report["rounds"][0]["preflight"]
    # R14 is lint: the false watermark flag is reported, and it does not block the draft unless required.
    assert "[WARN] R14" in pre and "watermark_free is false" in pre and r.ok


def test_good_normalise_records_changes():
    segs, notes = draft.normalise([{"start_frame": 5, "end_frame": 40}, {"start_frame": 38, "end_frame": 40},
                                   {"start_frame": 40, "end_frame": 90}], 100)
    assert [(s["start_frame"], s["end_frame"]) for s in segs] == [(0, 40), (40, 100)]
    assert any("dropped" in n for n in notes) and any("timeline end" in n for n in notes)


def test_good_503_retries_same_model(proj):
    r = run(proj, client({"m38": [APIErr(503), APIErr(503), good_plan()]}))
    assert r.ok and r.report["model"] == "m38" and r.report["retries_total"] == 2 and not r.report["warnings"]


def test_good_fallback_is_reported_with_warning(proj):
    r = run(proj, client({"m38": [APIErr(503)], "fb": [good_plan()]}))
    assert r.ok and r.report["model"] == "fb" and r.report["fallback"] and "FALLBACK" in r.report["warnings"][0]
    assert [t[0] for t in r.report["rounds"][0]["ai"]["tried"]] == ["m38"] * 4 + ["fb"]


def test_bad_400_raises_and_still_deletes_uploads(proj):
    c = client({"m38": [APIErr(400)], "fb": [good_plan()]})
    with pytest.raises(EngineError, match="400"):
        run(proj, c, video_proxies=1)
    assert len(c.files.deleted) == 2 and c.files.live == {}                          # audio + 1 proxy
    rep = json.loads((proj / "draft_report.json").read_text())
    assert "400" in rep["error"] and all(d["verified_gone"] for d in rep["file_deletions"])


def test_bad_delete_not_verified_is_reported(proj):
    r = run(proj, client({"m38": [good_plan()]}, keep=True))
    assert r.report["file_deletions"][0]["verified_gone"] is False


def test_bad_missing_credits_txt(proj):
    (proj / "credits.txt").unlink()
    with pytest.raises(EngineError, match="credits.txt"):
        draft.collect(proj)


def test_good_cli_draft(proj, monkeypatch, capsys):
    monkeypatch.setattr(ai, "_client", lambda: client({"gemini-3.8-flash": [good_plan()]}))
    monkeypatch.setattr(ai, "PLANNING_MODELS", ["gemini-3.8-flash"])
    assert main(["draft", str(proj), "--video-proxies", "0", "--no-dom"]) == 0
    out = capsys.readouterr().out
    assert "model gemini-3.8-flash retries 0" in out and "deleted+verified 1/1" in out
    assert main(["draft", str(proj), "--out", str(proj / "master_timeline.json")]) == 1   # refuses without --force
