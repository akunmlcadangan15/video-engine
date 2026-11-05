"""`engine` command line. PROJECT is a project directory holding master_timeline.json, credits.txt and assets/.
Exit codes: 0 ok, 1 gate failure or engine error, 2 usage error, 3 waiting for an agent response (agent provider:
a request bundle was written; answer it and rerun with --resume)."""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config
from .util import EngineError


def _load(project: str):
    from . import timeline
    p = Path(project).resolve()
    return p, timeline.load(p)


def _pairs(items: list[str], flag: str) -> dict[str, str]:
    out = {}
    for item in items:
        old, sep, new = item.partition("=")
        if not sep or not old or not new:
            raise EngineError(f"{flag} expects OLD=NEW, got {item!r}")
        out[old] = new
    return out


EXIT_AWAITING = 3
AI_REQUEST_DIR = "ai_request"


def _provider(a, root: Path):
    """Provider from --provider / env / AI config; agent bundles go under `root`."""
    from . import ai
    argv = [x for x in a.argv if x != "--resume"]
    return ai.get(a.provider, root=root, resume=a.resume, resume_cmd=shlex.join(["engine", *argv, "--resume"]))


def _ai_qa(a, p: Path):
    if not a.ai_qa:
        return None
    prov = _provider(a, p / AI_REQUEST_DIR)
    if hasattr(prov, "reset"):
        prov.reset("aiqa")
    return prov


def cmd_validate(a) -> int:
    from pydantic import ValidationError
    try:
        _p, tl = _load(a.project)
    except EngineError as e:
        if not isinstance(e.__cause__, ValidationError):
            raise
        print(f"invalid master_timeline.json:\n{e}")
        return 1
    print(f"valid: {tl.project_id} {len(tl.segments)} segments, {tl.total_frames} frames "
          f"({tl.total_frames / tl.settings.fps:.2f} s at {tl.settings.fps} fps)")
    return 0


def cmd_preflight(a) -> int:
    from . import audio, gates, timeline
    p, tl = _load(a.project)
    words = None
    if tl.captions:
        words = tl.captions
    elif a.captions:
        words, _ = audio.captions_for(tl, p)
    extra: dict[str, Any] = {"draft": True} if a.mode == "draft" else {}   # release calls stay exactly as they were
    rep = gates.run_preflight(tl, p, words, dom=not a.no_dom, ai_qa=_ai_qa(a, p), **extra)
    print(rep.summary())
    if a.write_fixes and (rep.fixes):
        if rep.caption_y_overrides:
            tl.caption_style["y_overrides"] = [list(o) for o in rep.caption_y_overrides]
        timeline.save(tl, p)
        print(f"wrote {len(rep.fixes)} fixes to {p / 'master_timeline.json'}")
    return 0 if rep.ok else 1


def cmd_build(a) -> int:
    from . import build
    from . import profiles
    profiles.validate_quality(a.quality, a.mode)
    if a.quality == "proxy" and a.repin:
        raise EngineError("proxy quality cannot --repin the source timeline; pin words separately")
    p = Path(a.project).resolve()
    if a.no_cache:
        from . import cache
        c = cache.clear()
        if c["skipped_leased"]:
            print(f"cache: kept {len(c['skipped_leased'])} files leased by a running build")
    from .batch import ProjectLock
    argv = [x for x in a.argv if x != "--resume"]
    with ProjectLock(p):   # one build per project at a time (shared with `engine batch`)
        r = build.build(p, use_cache=True, run_label=a.label, ai_qa=_ai_qa(a, p), keep_workdir=a.keep_workdir,
                        resume=a.resume, resume_cmd=shlex.join(["engine", *argv, "--resume"]), raise_on_fail=False,
                        relock=a.relock, mode=a.mode, repin=a.repin, quality=a.quality)
    for row in r.timer.rows:
        print("\t".join(str(x) for x in row))
    if a.timing_tsv:
        r.timer.write_tsv(Path(a.timing_tsv))
    print(r.preflight)
    print("\n".join(r.qa))
    if not r.ok:
        print(f"error: build failed, nothing published ({len(r.failed)} gate fail(s)); report: {r.report}", file=sys.stderr)
        return 1
    warns = sum(1 for ln in [*r.preflight.splitlines(), *r.qa] if "[WARN]" in ln)
    warn_str = f" ({warns} warning(s); see {r.report})" if warns else ""
    print(f"final: {r.final} frames={r.frames}/{r.expected_frames} I={r.loudness.get('I')} TP={r.loudness.get('TP')}{warn_str}")
    if r.mode == "draft":
        print("draft build: technical gates were lowered to warnings; out/final.mp4 is untouched and this file "
              "cannot be delivered (build again without --mode draft)")
    if r.quality == "proxy":
        print("proxy quality: low-cost full-geometry encode (not delivery quality)")
    _print_cache(r.cache)
    return 0


def _print_cache(c: dict) -> None:
    """One line when this build's cache evictions deleted or skipped anything (skipped = leased by a running build)."""
    if c.get("evicted") or c.get("skipped_leased") or c.get("reclaimed_leases"):
        print(f"cache: evicted {len(c['evicted'])}, skipped {len(c['skipped_leased'])} leased"
              + "".join(f"\n  skipped (leased): {x}" for x in c["skipped_leased"])
              + (f"\n  reclaimed stale leases: {', '.join(c['reclaimed_leases'])}" if c["reclaimed_leases"] else ""))


def _preview_one(s, tl, p: Path, t_sec: float, out: Path, timeout: int | None) -> Path:
    from . import hyperframes, native
    out.parent.mkdir(parents=True, exist_ok=True)
    fi = round(t_sec * tl.settings.fps)
    if s.engine_type == "NATIVE_RUST_FFMPEG":
        print(f"[preview] seg {s.seg_index} native frame {fi} …", file=sys.stderr, flush=True)
        native.preview_frame(native.plan(s, tl, p), tl, fi, out)
    else:
        wd = config.workdir(f"preview_{tl.project_id}_{s.seg_index}")
        try:
            print(f"[preview] seg {s.seg_index} building HyperFrames dir …", file=sys.stderr, flush=True)
            d, _ = hyperframes.build_dir(s, tl, p, wd / "hf")
            seek_t = min(max(0.0, t_sec), (s.n_frames - 1) / tl.settings.fps)
            print(f"[preview] seg {s.seg_index} probing DOM at t={seek_t:.2f}s …", file=sys.stderr, flush=True)
            res = hyperframes.probe_dom(d, [seek_t], tl.settings.width, tl.settings.height, wd / "snaps",
                                        timeout=timeout)
            shutil.move(res["times"][0]["png"], out)
        finally:
            shutil.rmtree(wd, ignore_errors=True)
    return out


def cmd_preview(a) -> int:
    from . import sheet
    p, tl = _load(a.project)
    if getattr(a, "all", False):
        if a.seg is not None or a.time is not None:
            raise EngineError("--all renders the middle frame of every segment; drop --seg/--time")
        if a.out and str(a.out).endswith(".png") and not getattr(a, "sheet", False):
            raise EngineError("with --all, --out names a directory for the frames (a .png --out needs --sheet)")
        out_dir = Path(a.out) if a.out and not str(a.out).endswith(".png") else (config.SHM_ROOT / "preview" / tl.project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        tiles: dict[int, Path] = {}
        for s in tl.segments:
            mid_t = ((s.n_frames - 1) / 2) / tl.settings.fps
            dst = out_dir / f"{tl.project_id}_seg{s.seg_index:03d}_{mid_t:.2f}.png"
            _preview_one(s, tl, p, mid_t, dst, getattr(a, "timeout", None))
            tiles[s.seg_index] = dst
            print(dst)
        if getattr(a, "sheet", False):
            sh_out = Path(a.out) if a.out and str(a.out).endswith(".png") else (p / "out" / "preview_sheet.png")
            sheet.make_from_tiles(tl, tiles, sh_out)
            print(sh_out)
        return 0
    if a.seg is None or a.time is None:
        raise EngineError("preview requires --seg and --time (or --all)")
    if not 0 <= a.seg < len(tl.segments):
        raise EngineError(f"--seg must be 0..{len(tl.segments) - 1}")
    s = tl.segments[a.seg]
    out = Path(a.out or config.SHM_ROOT / "preview" / f"{tl.project_id}_seg{a.seg:03d}_{a.time:.2f}.png")
    _preview_one(s, tl, p, a.time, out, getattr(a, "timeout", None))
    print(out)
    return 0


def cmd_audio(a) -> int:
    from . import audio
    p, tl = _load(a.project)
    words, src = audio.captions_for(tl, p, use_cache=not a.no_cache)
    print(f"{len(words)} caption words from {src}")
    for w in words:
        print(f"{w.start_frame:6d} {w.end_frame:6d} {w.word.strip()}")
    return 0


def cmd_words(a) -> int:
    from . import audio
    p, tl = _load(a.project)
    old = [(w.word, w.start_frame, w.end_frame) for w in tl.captions]
    fresh, meta = audio.pin_captions(p, use_cache=not a.retranscribe)
    new = [(w.word, w.start_frame, w.end_frame) for w in fresh.captions]
    changed = sum(1 for x, y in zip(old, new) if x != y) + abs(len(old) - len(new))
    print(f"pinned {len(new)} words into {p / 'master_timeline.json'} "
          f"({'stt cache' if meta.words_cache_hit else 'fresh transcription'}; {changed} differ from the previous pin; "
          f"{meta.clipped_words} cut at frame {meta.total_frames})")
    if a.print:
        for w in fresh.captions:
            print(f"{w.start_frame:6d} {w.end_frame:6d} {w.word}")
    return 0


def cmd_deliver(a) -> int:
    from . import deliver
    if not a.dry_run and not a.export:
        print("error: pass --dry-run (print the plan) or --export DIR (write the upload package)", file=sys.stderr)
        raise SystemExit(2)
    p, tl = _load(a.project)
    final = Path(a.final) if a.final else p / "out" / "final.mp4"
    deliver.check_build(final)
    if a.dry_run:
        print(deliver.plan(tl, p, final).to_json())
    if a.export:
        print(json.dumps(deliver.export_package(tl, p, final, Path(a.export)), indent=2, ensure_ascii=False))
    return 0


def cmd_director(a) -> int:
    from .ai import tasks
    src = Path(a.src)
    prov = _provider(a, Path(a.bundle_root) if a.bundle_root else config.SHM_ROOT / AI_REQUEST_DIR)
    if hasattr(prov, "reset"):
        prov.reset(tasks.director_request_id(src, a.start))
    wd = config.workdir("director")
    try:
        r = tasks.analyze_footage(src, wd, prov, a.start, a.seconds)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    print(json.dumps({"provider": r.provider, "model": r.model, "tried": r.tried, "seconds": r.seconds,
                      "usage": r.usage, "data": r.data}, indent=2, ensure_ascii=False))
    return 0


def cmd_pauses(a) -> int:
    from . import pauses
    p, tl = _load(a.project)
    if not tl.audio_chain.voiceover_path:
        raise EngineError("audio_chain.voiceover_path is empty")
    r = pauses.analyze(p / tl.audio_chain.voiceover_path, tl.settings.fps, threshold_db=a.threshold_db,
                       min_pause_s=a.min_pause, min_seg_s=a.min_seg, max_seg_s=a.max_seg)
    if a.json:
        print(json.dumps(r.to_dict(), indent=1))
        return 0
    fps = r.fps
    print(f"{len(r.pauses)} pauses (threshold {r.threshold_db} dBFS), {r.total_frames} frames")
    for x in r.pauses:
        print(f"  pause {x.start_frame:6d}-{x.end_frame:6d}  {x.start_frame / fps:7.2f}-{x.end_frame / fps:7.2f} s  {x.mean_db} dB")
    print(f"{len(r.segments)} suggested segments:")
    for s, e in r.segments:
        print(f"  {s:6d}-{e:6d}  {(e - s) / fps:5.2f} s")
    return 0


def cmd_draft(a) -> int:
    from . import draft
    p = Path(a.project).resolve()
    out = Path(a.out) if a.out else (p / "master_timeline.json" if a.force else None)
    if out is not None and out.name == "master_timeline.json" and out.exists() and not a.force:
        raise EngineError(f"{out} exists; pass --force to overwrite it")
    r = draft.draft(p, out=out, provider=_provider(a, p / AI_REQUEST_DIR), max_repairs=a.max_repairs,
                    video_proxies=a.video_proxies, voiceover=a.voiceover, script=a.script, dom=not a.no_dom,
                    profile=a.profile, director=not a.no_director, review=a.review, review_rounds=a.review_rounds)
    rep = r.report
    for w in rep.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr)
    print(f"provider {rep['provider']} model {rep['model']} retries {rep['retries_total']} repairs {rep['repair_rounds']} "
          f"fallback {rep['fallback']} uploads deleted+verified "
          f"{sum(d['verified_gone'] for d in rep['file_deletions'])}/{len(rep['file_deletions'])}")
    sb = rep.get("storyboard")
    if sb:
        print(f"storyboard: concept {sb['chosen']} ({sb['direction']}) -> {sb['path']}"
              + (f"\nwarning: {sb['warning']}" if sb.get("warning") else ""))
    for x in rep.get("review", []):
        if not x.get("revision"):
            print(f"review {x['round']} ({x['version']}): {x['verdict']}, {len(x['findings'])} finding(s)")
            for f in x["findings"]:
                print(f"  t={float(f['t_s']):.2f}s seg {f['seg']} [{f['severity']}] {f['issue']} -> {f['fix']}")
        elif x["errors"]:
            print(f"revision {x['round']} failed the gates; kept {x.get('kept')}")
    if r.ok:
        print(f"draft OK -> {r.timeline_path}")
    else:
        print("draft REJECTED after repairs:\n" + "\n".join(rep["rounds"][-1]["errors"]))
    print(f"report -> {r.report_path}")
    return 0 if r.ok else 1


def cmd_taste(a) -> int:
    from . import director, profiles, taste_lint
    if bool(a.like) + bool(a.reject) > 1:
        raise EngineError("pass one of --like / --reject")
    if a.lint and (a.like or a.reject or a.check):
        raise EngineError("--lint only reads; it cannot be combined with --like / --reject / --check")
    if a.check and not (a.like or a.reject):
        raise EngineError("--check belongs to --like / --reject")
    if a.profile and not a.lint:
        raise EngineError("--profile is for --lint")
    if a.lint:
        profile = a.profile or a.channel
        report = taste_lint.lint(director.load_taste(a.channel),
                                 json.loads(profiles.load(profile).data.model_dump_json()))
        print(taste_lint.render(report, a.channel, profile))
        return 0 if taste_lint.ok(report) else 1
    if a.like or a.reject:
        if not a.why:
            raise EngineError("--why is required with --like / --reject")
        try:
            checks = [taste_lint.parse_check(c) for c in a.check]
        except taste_lint.CheckError as e:
            raise EngineError(str(e)) from None
        d = director.record_feedback(a.channel, "liked" if a.like else "rejected", a.like or a.reject, a.why, checks)
    else:
        d = director.load_taste(a.channel)
    print(director.taste_text(d))
    print(f"store -> {director.taste_dir() / (a.channel + '.json')}")
    return 0


def cmd_gates(a) -> int:
    from . import gate_tools, profiles
    if a.stats:
        if a.profile or a.format == "md" or a.id:
            raise EngineError("--stats takes report paths only; --profile, gate ids and --format md do not apply")
        try:
            files = gate_tools.collect_reports(a.stats)
        except FileNotFoundError as e:
            raise EngineError(str(e)) from None
        s = gate_tools.stats(files)
        print(json.dumps(s, indent=2) if a.format == "json" else gate_tools.render_stats(s))
        return 0 if files else 1
    try:
        gates = gate_tools.select(a.id, a.layer, a.kind)
    except ValueError as e:
        raise EngineError(str(e)) from None
    if a.format == "md":
        if a.profile or a.id or a.layer or a.kind:
            raise EngineError("--format md prints the whole registry; it takes no filters or --profile")
        print(gate_tools.render_markdown())
        return 0
    rows = gate_tools.rows(gates, profiles.load(a.profile) if a.profile else None)
    if a.format == "json":
        print(json.dumps(rows, indent=2))
    else:
        print(gate_tools.render_detail(rows) if a.id else gate_tools.render_table(rows))
    return 0 if rows else 1


def cmd_import_shots(a) -> int:
    from . import importer
    subs = _pairs(a.substitute, "--substitute")
    settings = {k: v for k, v in {"project_number": a.project_number, "topic": a.topic, "date": a.date}.items() if v}
    r = importer.import_shots(Path(a.csv), Path(a.dst), [Path(d) for d in a.search], credits_txt=Path(a.credits),
                              voiceover=Path(a.voiceover), manifest=Path(a.manifest) if a.manifest else None,
                              substitute=subs, exclude=set(a.exclude), watermark_checked=a.watermark_checked,
                              settings=settings, force=a.force)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def cmd_sheet(a) -> int:
    from . import sheet, timeline
    p = Path(a.project)
    out = sheet.make(timeline.load(p), Path(a.video) if a.video else p / "out" / "final.mp4",
                     Path(a.out) if a.out else p / "out" / "sheet.png", cols=a.cols)
    print(out)
    return 0


def cmd_assets(a) -> int:
    from . import library
    if a.action == "add":
        from . import assets
        if not a.path or not a.licence or not a.source:
            raise EngineError("assets add requires PATH, --licence and --source")
        result = assets.register(Path(a.path), licence=a.licence, source=a.source, source_url=a.source_url or "",
                                 author=a.author or "", dest=a.to, kind=a.kind,
                                 project=Path(a.project) if a.project else None, asset_id=a.id, dry_run=a.dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if a.action == "backfill":
        from . import assets
        print(json.dumps(assets.backfill(dry_run=a.dry_run), indent=2, ensure_ascii=False))
        return 0
    if a.copy:
        if not a.project:
            raise EngineError("--copy needs --project")
        asset = library.credit_for_path(Path(a.copy), open_assets_root=Path(a.open_assets) if a.open_assets else None,
                                       stock_roots=tuple(Path(x) for x in a.stock_root) if a.stock_root else None)
        if asset is None:
            raise EngineError(f"{a.copy} is not in the open-assets or stock index")
        print(json.dumps(library.credit_row(asset, Path(a.project)), indent=2, ensure_ascii=False))
        return 0
    if a.query and not a.open_assets and not a.stock_root:
        from dataclasses import asdict

        from . import assets
        aliases = {"broll": "video", "photos": "image", "icons": "icon", "textures": "texture",
                   "maps": "map", "models": "model", "fonts": "font"}
        kind = aliases.get(a.kind, a.kind)
        matches = assets.find(a.query, kind=kind, project=Path(a.project) if a.project else None, limit=a.limit)
        if a.json:
            print(json.dumps([{**asdict(r), "path": str(r.path), "license": r.licence,
                               "name": r.path.stem, "collection": r.tier} for r in matches],
                             indent=2, ensure_ascii=False))
        else:
            for r in matches:
                print(f"{r.kind:10s} {r.licence or '-':16s} {r.path}")
        return 0
    rows = library.search(a.query or "", kind=a.kind, limit=a.limit,
                          open_assets_root=Path(a.open_assets) if a.open_assets else None,
                          stock_roots=tuple(Path(x) for x in a.stock_root) if a.stock_root else None)
    if a.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r.kind:10s} {r.license or '-':16s} {r.path}")
    return 0


def cmd_block(a) -> int:
    from . import blocks
    kw = {"width": a.width, "height": a.height}
    if a.colour:
        kw["colour"] = a.colour
    if a.background:
        kw["background"] = a.background
    out = blocks.render(a.kind, a.data, Path(a.out), **kw)
    print(out)
    return 0


def cmd_batch(a) -> int:
    from . import batch
    projects = [Path(x) for x in a.project]
    if a.queue:
        projects = [*batch.load_queue(Path(a.queue)), *projects]
    if not projects:
        raise EngineError("batch needs at least one project or --queue")
    r = batch.run(projects, fail_fast=a.fail_fast, run_label=a.label, use_cache=not a.no_cache, relock=a.relock,
                  mode=a.mode, quality=a.quality)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0 if r["ok"] else 1


def cmd_cache(a) -> int:
    from . import cache_prune
    projects = [Path(x) for x in a.project] or cache_prune.default_projects()
    if not projects:
        print("error: cache --prune needs at least one project (--project P); nothing was deleted", file=sys.stderr)
        return 1
    r = cache_prune.prune(projects, dry_run=a.dry_run)
    print(json.dumps(r, indent=2))
    return 1 if r["problems"] else 0


def cmd_doctor(a) -> int:
    from . import doctor
    res = doctor.install() if a.install else doctor.check()
    for c in res:
        print(c.line())
    ok = all(c.ok for c in res)
    if not ok and not a.install:
        print("run `engine doctor --install` to install the missing components into vendor/")
    return 0 if ok else 1


def cmd_toolchain(a) -> int:
    """show / list / use SPEC: which HyperFrames the build renders with (hfversion.py)."""
    from . import doctor, hfversion
    if a.action == "use":
        if not a.spec:
            print("usage: engine toolchain use pinned|latest|system|X.Y.Z", file=sys.stderr)
            return 2
        spec = hfversion.parse_spec(a.spec)
        pin = hfversion.pinned_version()
        want = doctor.npm_latest() if spec == "latest" else spec
        if want not in hfversion.KEYWORDS and want != pin and not (hfversion.vendor_root(want) / "bin" / "hyperframes.mjs").is_file():
            print(f"installing hyperframes@{want} into {config.VENDOR / 'hf' / want}", flush=True)
            try:
                doctor.install_hf_version(config.VENDOR, want, config.workdir("doctor"))
            except subprocess.CalledProcessError as e:
                raise EngineError(f"npm install hyperframes@{want} failed (exit {e.returncode})") from e
        (config.VENDOR / hfversion.SELECT_FILE).write_text(spec + "\n")
        print(f"selected {spec!r} in {config.VENDOR / hfversion.SELECT_FILE}")
    act = hfversion.active()
    if a.action == "list":
        pin = hfversion.pinned_version()
        sysroot = hfversion.system_root()
        print(f"pinned  {pin} ({'installed' if hfversion.pinned_root().joinpath('bin/hyperframes.mjs').is_file() else 'missing'}; vendor/npm)")
        for v in hfversion.installed_versions():
            print(f"vendor  {v} (vendor/hf/{v})")
        print(f"system  {hfversion._pkg_version(sysroot) if sysroot else 'missing'}" + (f" ({sysroot})" if sysroot else ""))
    if hfversion.setting()[1] == "env" and a.action == "use":
        print(f"note: {hfversion.ENV} is set and wins over the selection file")
    print(f"active: HyperFrames {act.label()}; cli {act.cli}")
    return 0 if act.installed else 1


def _clean_project_ids(projects: list[str]) -> set[str]:
    from . import timeline
    ids = set()
    for pr in projects:
        d = Path(pr).resolve()
        try:
            ids.add(timeline.load(d).project_id)
        except Exception:  # noqa: BLE001  (an unreadable project still names a scratch prefix)
            ids.add(config.safe_project_id(d.name))
    return ids


def cmd_clean(a) -> int:
    ids = _clean_project_ids(a.project)
    removed = []
    if config.SHM_ROOT.is_dir():
        for entry in sorted(config.SHM_ROOT.iterdir()):
            if not any(entry.name.startswith(f"{pid}-") for pid in ids):
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed.append(str(entry))
    for pr in a.project:
        d = Path(pr).resolve() / AI_REQUEST_DIR
        if d.is_dir():
            shutil.rmtree(d)
            removed.append(str(d))
    kept = []
    if a.cache:
        from . import cache
        kept = cache.clear()["skipped_leased"]
    print(("removed " + ", ".join(removed) if removed else "removed nothing")
          + (f" and the cache entries in {config.CACHE_DIR}" if a.cache else "")
          + "".join(f"\nkept (leased by a running build): {x}" for x in kept))
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="engine", description="Hybrid video engine: master_timeline.json -> final mp4.")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, aliases=()):
        x = sp.add_parser(name, help=help_, description=help_, aliases=list(aliases))
        x.set_defaults(fn=fn)
        return x

    def ai_args(x, what: str, resume_also: str = ""):
        x.add_argument("--provider", help=f"AI provider for {what}: gemini, agent or a name from the AI config file "
                                          "(default: env VIDEO_ENGINE_AI_PROVIDER, config `default`, gemini)")
        x.add_argument("--resume", action="store_true", help="agent provider: read response.json of the pending "
                                                             "request bundle(s) instead of starting over" + resume_also)
    x = add("validate", cmd_validate, "Schema check of master_timeline.json.")
    x.add_argument("project")
    x = add("preflight", cmd_preflight, "Run all pre-flight gates (target < 5 s). Exit 1 on any fail.")
    x.add_argument("project")
    x.add_argument("--captions", action="store_true", help="transcribe (or read cached) narration for caption gates")
    x.add_argument("--no-dom", action="store_true", help="skip the HyperFrames DOM probe (R63 element checks)")
    x.add_argument("--ai-qa", action="store_true", help="send gate snapshots to the AI provider's QA models")
    ai_args(x, "--ai-qa")
    x.add_argument("--write-fixes", action="store_true", help="persist automatic fixes (R56 intensity, caption y) to the timeline")
    x.add_argument("--mode", choices=["release", "draft"], default="release",
                   help="draft: fails of technical gates that a profile may make advisory are reported as warnings")
    x = add("build", cmd_build, "Captions, pre-flight, segment render, caption burn, mux, QA. Output <project>/out/final.mp4, "
            "published only when every gate passes; a failed build writes out/build_report.failed.json and exits 1.",
            aliases=["render"])
    x.add_argument("project")
    x.add_argument("--no-cache", action="store_true", help="delete the cache entries (words/, segments/, maps/) first (cold run); the build repopulates them")
    x.add_argument("--label", default="build", help="run label in timing rows")
    x.add_argument("--timing-tsv", help="append stage timings to this TSV")
    x.add_argument("--ai-qa", action="store_true", help="send gate snapshots to the AI provider's QA models")
    ai_args(x, "--ai-qa", "; also (without --ai-qa) import the answer of the pending sound request out/sound_request/")
    x.add_argument("--keep-workdir", action="store_true", help="keep the scratch dir of this build (path in build_report.json)")
    x.add_argument("--relock", action="store_true", help="resolve music/SFX again instead of the files locked in "
                                                         "out/build_manifest.json")
    x.add_argument("--mode", choices=["release", "draft"], default="release",
                   help="draft: technical gates a profile may make advisory only warn; the output goes to "
                        "out/draft/final.draft.mp4 (out/final.mp4 is untouched) and cannot be delivered")
    x.add_argument("--quality", choices=["delivery", "proxy"], default="delivery",
                   help="proxy: low-cost full-geometry encode; requires --mode draft (default: delivery)")
    x.add_argument("--repin", action="store_true",
                   help="when the pinned caption words are stale (gate W01), pin them again before the pre-flight "
                        "(the same step as `engine words`)")
    x = add("preview", cmd_preview, "Render one frame of one segment (or all segments with --all) to PNG without rendering the video.")
    x.add_argument("project")
    x.add_argument("--seg", type=int, help="segment index (0..N-1)")
    x.add_argument("--time", type=float, help="seconds from segment start")
    x.add_argument("--all", action="store_true", help="render the middle frame of every segment")
    x.add_argument("--sheet", action="store_true", help="with --all, also assemble a contact sheet PNG (--out F.png, "
                                                        "default out/preview_sheet.png)")
    x.add_argument("--timeout", type=int, help="HyperFrames DOM probe timeout in seconds (default: the probe's own, "
                                               "120 s)")
    x.add_argument("--out")
    x = add("audio", cmd_audio, "Print caption words (pinned timeline words, else cached or transcribed; writes nothing).")
    x.add_argument("project")
    x.add_argument("--no-cache", action="store_true")
    x = add("words", cmd_words, "Transcribe (STT cache unless --retranscribe) + caption_fixes and pin the words into "
                                  "master_timeline.json `captions` + `captions_meta`.")
    x.add_argument("project")
    x.add_argument("--retranscribe", action="store_true", help="ignore the words cache and run Grok STT again")
    x.add_argument("--print", action="store_true")
    x = add("deliver", cmd_deliver, "Print the delivery plan or write the upload package (video parts <= 100 MB, "
            "JOIN, credits, SRT, thumbnail, YouTube/Reels metadata). No upload.")
    x.add_argument("project")
    x.add_argument("--dry-run", action="store_true", help="print the delivery plan as JSON")
    x.add_argument("--export", metavar="DIR", help="write the upload-ready package tree under DIR")
    x.add_argument("--final")
    x = add("director", cmd_director, "Footage analysis of a 360p proxy by the AI provider (planning models).")
    x.add_argument("src")
    ai_args(x, "the analysis")
    x.add_argument("--bundle-root", help="agent provider: bundle root (default <SHM_ROOT>/ai_request: /dev/shm/video_engine on Linux, "
                                         "$TMPDIR/video_engine on macOS)")
    x.add_argument("--start", type=float, default=0.0)
    x.add_argument("--seconds", type=float, default=5.0)
    x = add("pauses", cmd_pauses, "Narration pauses (Rust energy detector) and suggested segment boundaries in frames.")
    x.add_argument("project")
    x.add_argument("--threshold-db", type=float, help="silence level in dBFS (default: adaptive)")
    x.add_argument("--min-pause", type=float, default=0.20, help="shortest pause in seconds")
    x.add_argument("--min-seg", type=float, default=2.0)
    x.add_argument("--max-seg", type=float, default=6.0)
    x.add_argument("--json", action="store_true")
    x = add("draft", cmd_draft, "The AI provider drafts a timeline from narration, script, credits.txt and assets/; validated, "
                                  "pre-flight checked, repaired from gate errors. Writes master_timeline.draft.json + "
                                  "draft_report.json.")
    x.add_argument("project")
    x.add_argument("--out", help="timeline output path (default <project>/master_timeline.draft.json)")
    x.add_argument("--force", action="store_true", help="write <project>/master_timeline.json (overwrites)")
    x.add_argument("--max-repairs", type=int, default=2, help="repair rounds fed with gate errors (0-2)", choices=[0, 1, 2])
    x.add_argument("--video-proxies", type=int, help="videos sent as 360p proxies (rest: thumbnails; default 2, agent 0)")
    x.add_argument("--profile", help="channel profile (profiles/<name>.toml); default: the existing timeline's, else default")
    ai_args(x, "the draft")
    x.add_argument("--voiceover", help="project-relative narration audio (default: audio_chain or narasi/vo file)")
    x.add_argument("--script", help="script text file (default script.txt / narasi.txt)")
    x.add_argument("--no-dom", action="store_true", help="skip the HyperFrames DOM probe in the draft pre-flight")
    x.add_argument("--no-director", action="store_true", help="skip the storyboard stage (3 concepts, one chosen)")
    x.add_argument("--review", action="store_true", help="after the draft passes: sample frames, AI review with "
                                                         "timestamped findings, revise (versions in <project>/draft/)")
    x.add_argument("--review-rounds", type=int, default=1, choices=[1, 2], help="review/revision rounds (default 1)")
    x = add("taste", cmd_taste, "Show or edit a channel taste store (liked/rejected with reasons, recent openers and "
                                "transitions) read by the draft director.")
    x.add_argument("channel")
    x.add_argument("--like", help="what was liked")
    x.add_argument("--reject", help="what was rejected")
    x.add_argument("--why", help="the reason (required with --like / --reject)")
    x.add_argument("--check", action="append", default=[], metavar='"KEY OP VALUE"',
                   help='with --like / --reject: a condition the channel profile has to satisfy for the entry to be '
                        'true, e.g. "caption_style.size == 84" (VALUE is JSON; repeatable); checked by --lint')
    x.add_argument("--lint", action="store_true",
                   help="compare the checks of the taste entries with the channel profile; exit 1 on a mismatch")
    x.add_argument("--profile", help="with --lint: profile to check against (default: the channel name)")
    x = add("gates", cmd_gates, "List the gates (id, class, layer, rule); with --profile, what each does in that "
                                "channel profile (off / warns / blocks); with --stats, how often each gate failed, warned "
                                "or was waived in build reports. Read-only.")
    x.add_argument("id", nargs="*", help="gate id(s) to describe (default: all)")
    x.add_argument("--profile", help="show the effect of each gate under this channel profile (profiles/<name>.toml)")
    x.add_argument("--layer", help="only this layer (static, math, dom, place, layout, props, visual, profile, post, "
                                   "delivery, meta)")
    x.add_argument("--kind", choices=["technical", "style"], help="only this class")
    x.add_argument("--format", choices=["table", "json", "md"], default="table",
                   help="md = the generated gate index of docs/GATES.md")
    x.add_argument("--stats", nargs="+", metavar="PATH",
                   help="count fails/warns/waived per gate in build reports: report files, project folders "
                        "(out/build_report*.json) or a folder of projects")
    x = add("import-shots", cmd_import_shots, "Build a project (master_timeline.json, credits.txt, assets/) from a "
                                                "shot-list CSV of an existing edit. Sources are only read.")
    x.add_argument("csv")
    x.add_argument("dst")
    x.add_argument("--search", action="append", required=True, help="directory searched for shot files (repeatable)")
    x.add_argument("--credits", required=True, help="credits.txt copied into the project")
    x.add_argument("--voiceover", required=True)
    x.add_argument("--manifest", help="manifest.csv with credits for substitute files")
    x.add_argument("--substitute", action="append", default=[], metavar="OLD=NEW")
    x.add_argument("--exclude", action="append", default=[], metavar="SHOT")
    x.add_argument("--watermark-checked", action="store_true", help="declare every imported asset watermark-free")
    x.add_argument("--project-number")
    x.add_argument("--topic")
    x.add_argument("--date")
    x.add_argument("--force", action="store_true")
    x = add("sheet", cmd_sheet, "Contact sheet of out/final.mp4: one labelled tile per segment (middle frame).")
    x.add_argument("project")
    x.add_argument("--video")
    x.add_argument("--out", help="default <project>/out/sheet.png")
    x.add_argument("--cols", type=int, default=7)
    x = add("assets", cmd_assets, "Search the local open-assets and stock/B-roll index, or copy one asset and print "
                                  "its credits_manifest row. No network request.")
    x.add_argument("action", nargs="?", choices=["backfill", "add"])
    x.add_argument("path", nargs="?", help="asset to register")
    x.add_argument("--licence")
    x.add_argument("--source")
    x.add_argument("--source-url")
    x.add_argument("--author")
    x.add_argument("--to", choices=["stock", "open", "project"], default="stock")
    x.add_argument("--id")
    x.add_argument("--dry-run", action="store_true", help="report backfill changes without writing")
    x.add_argument("--query", default="", help="space-separated words, all must match")
    x.add_argument("--kind", help="filter: broll, icons, textures, maps, models, ...")
    x.add_argument("--limit", type=int, default=20)
    x.add_argument("--json", action="store_true")
    x.add_argument("--copy", metavar="PATH", help="copy this indexed asset and print its credit row")
    x.add_argument("--project", help="destination project for --copy (default assets/)")
    x.add_argument("--open-assets", help="overlay the open-assets root")
    x.add_argument("--stock-root", action="append", default=[], help="stock/B-roll root (repeatable)")
    x = add("block", cmd_block, "Render a chart, timeline or infographic block to a transparent PNG.")
    x.add_argument("kind", choices=["chart", "timeline", "infographic"])
    x.add_argument("--data", required=True, help="JSON object/array, or @file.json")
    x.add_argument("--out", required=True)
    x.add_argument("--width", type=int, default=1080)
    x.add_argument("--height", type=int, default=720)
    x.add_argument("--colour", default=None)
    x.add_argument("--background", default=None)
    x = add("batch", cmd_batch, "Build several projects in order, with one lock per project and a shared queue. "
                                  "A failed item does not stop the queue unless --fail-fast.")
    x.add_argument("project", nargs="*")
    x.add_argument("--queue", help="JSON array/object with `projects`, or one project path per line")
    x.add_argument("--fail-fast", action="store_true")
    x.add_argument("--label", default="batch")
    x.add_argument("--no-cache", action="store_true")
    x.add_argument("--relock", action="store_true")
    x.add_argument("--mode", choices=["release", "draft"], default="release")
    x.add_argument("--quality", choices=["delivery", "proxy"], default="delivery",
                   help="proxy: low-cost full-geometry encode; requires --mode draft")
    x = add("cache", cmd_cache, "Cache maintenance. --prune deletes words/segments/maps entries not referenced by "
                                  "the current keys of the projects (default: every project passed via --project) and broken "
                                  "entries of the sound cache (sound/). Files leased by a running build are kept and "
                                  "listed under skipped_leased; leases of dead processes are reclaimed.")
    x.add_argument("--prune", action="store_true", required=True)
    x.add_argument("--project", action="append", default=[], help="project dir whose keys are kept (repeatable)")
    x.add_argument("--dry-run", action="store_true", help="list what would be deleted")
    x = add("doctor", cmd_doctor, "Check the pinned HyperFrames toolchain in vendor/ (node, hyperframes, puppeteer-core, "
                                    "chrome-headless-shell; pins in vendor/VERSIONS.json) and the active HyperFrames "
                                    "(`engine toolchain`); a HyperFrames other than the pin is a WARN.")
    x.add_argument("--install", action="store_true", help="download and install missing or mismatched components")
    x = add("toolchain", cmd_toolchain, "Show or switch the HyperFrames version the build renders with. Setting: env "
                                          "VIDEO_ENGINE_HYPERFRAMES_VERSION, else vendor/hyperframes.select (written by "
                                          "`use`), else the vendor/VERSIONS.json pin. SPEC: pinned, latest (newest npm "
                                          "release; installed into vendor/hf/), system (global `hyperframes` on PATH) "
                                          "or an exact X.Y.Z. A switch changes the segment cache keys.")
    x.add_argument("action", nargs="?", choices=("show", "list", "use"), default="show")
    x.add_argument("spec", nargs="?", help="for `use`: pinned | latest | system | X.Y.Z")
    x = add("clean", cmd_clean, "Remove only the given projects' scratch dirs, their <project>/ai_request/ bundles, "
                                  "and with --cache the cache entries words/, segments/, maps/.")
    x.add_argument("project", nargs="*")
    x.add_argument("--cache", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    from pydantic import ValidationError

    from .ai import AwaitingAgent
    argv = sys.argv[1:] if argv is None else list(argv)
    a = parser().parse_args(argv)
    a.argv = argv
    from . import cache
    try:
        with cache.session():     # cache files this command resolves stay leased until it returns or raises
            return a.fn(a)
    except AwaitingAgent as e:
        print(f"waiting for agent response: {e.bundle}\n  answer: {e.bundle / 'response.json'} "
              f"(schema: {e.bundle / 'schema.json'})\n  resume: {e.resume}")
        return EXIT_AWAITING
    except (EngineError, ValidationError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
