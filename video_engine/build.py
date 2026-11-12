"""Smart router and one-command build.

Stages (wall time of each is recorded):
  captions   timeline captions, else Grok STT words of the voiceover (BLAKE3 cache) + caption_fixes
  preflight  all gates (gates.run_preflight); any "fail" stops the build before rendering
  sounds     sound.request.ensure_sounds: missing music/SFX of a profile [sound] -> request bundle
             <project>/out/sound_request/ and AwaitingAgent (exit 3); --resume imports the answer; then mixer.plan
             and the Epidemic Sound credit lines appended to credits.txt
             A sound lock in out/build_manifest.json (video_engine/manifest.py) replaces both unless --relock.
  manifest   out/build_manifest.json: build_id, input/font/sound sha256, profile hash, toolchain, seeds, budget
  segments   NATIVE_RUST_FFMPEG segments in a thread pool (budget.native_workers x budget.x264_threads) and
             HYPERFRAMES_GSAP segments in a thread pool of budget.hf_parallel(native count) (budget.hf_workers
             Chrome workers each); both run concurrently with loudnorm pass 1 (video_engine/resources.py: one shared worker budget)
  ass        Rust .ass compile of the caption words
  composite  transition clips (profile [transitions], video_engine/transitions.py) from the segment files, then
             ONE video encode: concat + transition clips + caption burn + watermark, exact frame count, no audio
             (cache kind "composite")
  audio      ONE audio encode: BGM ducking (profile [sound]: mixer.Mix with SFX cues and keyframed ducking) +
             loudnorm, then the A01 corrective gain passes on the audio file alone (cache kind "audio")
  mux        stream copy of composite video + master audio into the staged file
  qa         frame count check, pixel gates on sampled frames, A01 (from the audio stage), silence (S01), audio
             source (A02, profile [audio] require_source); severity.apply splits fails from style warns
  publish    only when no stage raised and no gate reported "fail": os.replace of the staged file onto
             <project>/out/final.mp4, then of build_report.json
Output: <project>/out/final.mp4 plus <project>/out/build_report.json. The mux writes into a unique staging dir
<out dir>/.<stem>.staging-*/ (same filesystem as the output), removed on every exit path. A failed build leaves both
published files untouched and writes <out dir>/build_report.failed.json; a published build removes that file.
Work files: /dev/shm/video_engine/<project_id>/.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    audio,
    cache,
    captions,
    config,
    grade,
    hyperframes,
    manifest,
    mixer,
    native,
    profiles,
    qa,
    resources,
    streams,
    timeline,
    transitions,
)
from .ai import AwaitingAgent
from .gates import audio_source, layer1, run_preflight, severity, style
from .sfx import kinds_used as sfx_kinds
from .sound import cache as sound_cache
from .sound import request as sound_request
from .timeline import HYPERFRAMES, NATIVE, Timeline
from .util import EngineError, Timer, count_frames, video_info

REPORT = "build_report.json"
FAILED_REPORT = "build_report.failed.json"


@dataclass
class SegResult:
    seg: int
    engine: str
    path: str
    cache_hit: bool
    mode: str
    seconds: float
    frames: int = 0


@dataclass
class BuildResult:
    final: Path | None                 # published output; None when the build failed
    frames: int
    expected_frames: int
    segments: list[SegResult] = field(default_factory=list)
    preflight: str = ""
    qa: list[str] = field(default_factory=list)
    loudness: dict = field(default_factory=dict)
    timer: Timer | None = None
    ok: bool = True                    # False when any gate reported "fail"; nothing was published
    mode: str = "release"              # "draft": technical gates lowered, output in out/draft/ (never deliverable)
    failed: list[str] = field(default_factory=list)
    report: Path | None = None         # build_report.json, or build_report.failed.json when not ok
    cache: dict = field(default_factory=dict)   # cache lease session: leased count, evicted, skipped_leased, reclaimed
    quality: str = "delivery"


class BuildFailed(EngineError):
    """A build with a "fail" finding: nothing was published; `result` holds the findings, `report` the failure
    report path."""

    def __init__(self, message: str, result: BuildResult, report: Path):
        super().__init__(message)
        self.result, self.report = result, report


def stage_json(path: Path, data) -> Path:
    """Write JSON to a unique temp file next to `path` (same filesystem, so os.replace onto `path` is atomic) and
    return the temp file; the caller replaces it onto `path` or unlinks it."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp)


def write_json_atomic(path: Path, data) -> Path:
    """Write JSON through a unique temp file in the same dir and os.replace it onto `path`."""
    tmp = stage_json(path, data)
    try:
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def project_workdir(tl: Timeline) -> Path:
    """Scratch dir of this build, created here (config.workdir) under SHM_ROOT or under settings.work_dir_shm,
    which must lie strictly inside SHM_ROOT."""
    base = Path(tl.settings.work_dir_shm) if tl.settings.work_dir_shm else None
    try:
        return config.workdir(tl.project_id, base)
    except ValueError as e:
        raise EngineError(f"scratch dir rejected: {e}") from None


def render_segments(tl: Timeline, project_dir: Path, workdir: Path, use_cache: bool = True,
                    budget: resources.Budget | None = None) -> list[SegResult]:
    budget = budget or resources.budget(tl)
    natives = [s for s in tl.segments if s.engine_type == NATIVE]
    hfs = [s for s in tl.segments if s.engine_type == HYPERFRAMES]
    total = len(tl.segments)
    done = 0
    lock = threading.Lock()

    def _report(r: SegResult):
        nonlocal done
        with lock:
            done += 1
            hit = " (hit)" if r.cache_hit else ""
            print(f"[build] seg {done}/{total} s{r.seg:02d} {r.mode}{hit} {r.seconds:.1f}s",
                  file=sys.stderr, flush=True)
        return r

    def do_native(s):
        t = time.perf_counter()
        job = native.plan(s, tl, project_dir)
        p, hit, mode = native.render(job, tl, workdir, threads=budget.x264_threads, use_cache=use_cache)
        return _report(SegResult(s.seg_index, NATIVE, str(p), hit, mode, round(time.perf_counter() - t, 3)))

    def do_hf(s):
        t = time.perf_counter()
        p, hit = hyperframes.render(s, tl, project_dir, workdir, workers=budget.hf_workers, use_cache=use_cache)
        return _report(SegResult(s.seg_index, HYPERFRAMES, str(p), hit, "hyperframes", round(time.perf_counter() - t, 3)))

    with ThreadPoolExecutor(max_workers=budget.hf_parallel(len(natives))) as hf_ex, \
            ThreadPoolExecutor(max_workers=budget.native_workers) as nat_ex:
        hf_f = [hf_ex.submit(do_hf, s) for s in hfs]
        nat = list(nat_ex.map(do_native, natives))
        res = nat + [f.result() for f in hf_f]
    res.sort(key=lambda r: r.seg)
    for r in res:
        seg = tl.segments[r.seg]
        streams.verify(r.path, seg.n_frames, tl.settings.fps, f"segment {r.seg}")
        if grade.ranges(seg, tl, project_dir):
            t = time.perf_counter()
            p, hit = grade.apply(seg, tl, project_dir, Path(r.path), workdir, use_cache=use_cache)
            streams.verify(p, seg.n_frames, tl.settings.fps, f"graded segment {r.seg}")
            r.path, r.mode = str(p), r.mode + "+grade" + ("(hit)" if hit else "")
            r.seconds = round(r.seconds + time.perf_counter() - t, 3)
        r.frames = seg.n_frames
    return res


MODES = ("release", "draft")
DRAFT_DIR = "draft"
STAGES = ("captions", "preflight", "manifest", "segments", "ass", "composite", "audio", "mux", "qa")   # timer stages
POST_GATES = "frame count, pixel QA (R38, R38space, R56px), A01 loudness, S01 silence, A02 audio source"


def not_checked(findings: list, tl: Timeline, ai_qa, *, audio_cached: bool | None = None,
                sampled: bool = False) -> list[dict]:
    """build_report.json `not_checked` (docs/BUILD.md): severity.not_checked plus what only the build knows: AI QA
    not requested, A01 judging the measurement stored with a cached audio pass, and pixel QA on sampled frames."""
    out = severity.not_checked(findings, tl)
    if ai_qa is None:
        out.append(severity.unchecked("AIQA", "off", "AI visual QA not requested (--ai-qa)"))
    if audio_cached:
        out.append(severity.unchecked("A01", "cached", "audio cache hit: loudness judged on the measurement stored "
                                                       "with the cached audio pass, not measured again"))
    if sampled:
        out.append(severity.unchecked(None, "sampled", f"pixel QA (R38, R38space, R56px) looked at every "
                                                       f"{qa.SAMPLE_STEP}th frame of the final file, at 1/"
                                                       f"{qa.SNAP_DIV} size"))
    return out


def stages_not_run(rows: list, error: BaseException) -> list[dict]:
    """`not_checked` entries of a stage error: every build stage that did not start (timer rows name the ones that
    did), with the post-render gates the qa stage would have judged."""
    ran = {r[1] for r in rows}
    return [severity.unchecked(None, "not_run", f"stage {s} did not run: the build stopped with {type(error).__name__}"
                               + (f" ({POST_GATES} not judged)" if s == "qa" else ""))
            for s in STAGES if s not in ran]


def build(project_dir: Path, *, use_cache: bool = True, run_label: str = "build", ai_qa=None,
          out: Path | None = None, keep_workdir: bool = False, pin_words: bool = True, resume: bool = False,
          resume_cmd: str | None = None, raise_on_fail: bool = True, relock: bool = False,
          mode: str = "release", repin: bool = False, quality: str = "delivery") -> BuildResult:
    """Build and publish `out` (default <project>/out/final.mp4). Any "fail" finding means a failed build: with
    raise_on_fail it raises BuildFailed, else it returns a BuildResult with ok=False and final=None. A stage error
    raises its exception. Every failed build keeps the published final and build_report.json byte-identical and
    writes build_report.failed.json next to them. The whole build runs in one cache lease session: every cache file
    it resolves stays leased (safe from eviction by this or any other process) until the build returns or raises.

    mode "draft": the fail of every technical gate that a profile may make advisory becomes a warning (gates/
    severity.py), and the default output is <project>/out/draft/final.draft.mp4 with its own build_report.json (marked
    "mode": "draft") and build_manifest.json, so out/final.mp4 and its report stay untouched; `engine deliver`
    refuses a draft. Segment, composite and audio cache keys do not depend on the mode.
    repin: when the pinned caption words are stale (gate W01), pin them again before the pre-flight."""
    if mode not in MODES:
        raise EngineError(f"unknown build mode {mode!r} (known: {', '.join(MODES)})")
    profiles.validate_quality(quality, mode)
    if quality == "proxy" and repin:
        raise EngineError("proxy quality cannot --repin the source timeline; pin words separately")
    draft = mode == "draft"
    project_dir = Path(project_dir).resolve()
    if quality == "proxy":
        proxy_out = Path(out) if out is not None else project_dir / "out" / DRAFT_DIR / "final.draft.mp4"
        if not proxy_out.resolve().is_relative_to(project_dir / "out" / DRAFT_DIR):
            raise EngineError("proxy output must be inside the project out/draft directory")
    timer = Timer(run_label)
    t_all = time.perf_counter()
    tl = timeline.load(project_dir)
    source_profile = profiles.of(tl).report()
    tl = profiles.for_quality(tl, quality)
    render_quality = profiles.quality_report(tl)
    budget = resources.budget(tl)
    timer.monitor = resources.Monitor()
    workdir = project_workdir(tl)
    out = out or (project_dir / "out" / DRAFT_DIR / "final.draft.mp4" if draft else project_dir / "out" / "final.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    failed_report = out.parent / FAILED_REPORT
    stage = Path(tempfile.mkdtemp(prefix=f".{out.stem}.staging-", dir=out.parent))
    staged = stage / out.name
    lease = cache.session()
    held = lease.__enter__()
    gate_rows: dict | None = None
    pre_findings: list | None = None
    credit_token = None
    report_tmp: Path | None = None
    try:
        from . import assets
        from .gates.layer1 import EXTRA_CREDIT_URLS
        tl, asset_credits = assets.ensure_credits(tl, project_dir)
        tl = assets.resolve_timeline_paths(tl, project_dir)
        credit_token = EXTRA_CREDIT_URLS.set(tuple(c["source_url"] for c in asset_credits if c["source_url"]))
        with timer.stage("captions") as st:
            repinned = False
            if repin and tl.captions and layer1.pins_stale(tl, project_dir):
                fresh, meta = audio.pin_captions(project_dir, use_cache=use_cache)
                tl.captions, tl.captions_meta = fresh.captions, meta
                repinned = True
            words, src = audio.captions_for(tl, project_dir, use_cache=use_cache, pin=pin_words and quality != "proxy")
            st["note"] = f"{len(words)} words from {src}" + (" (stale pin replaced: --repin)" if repinned else "")
        with timer.stage("preflight") as st:
            pf_kwargs: dict[str, Any] = {"draft": True} if draft else {}   # release calls stay exactly as they were
            rep = run_preflight(tl, project_dir, words, workdir=workdir, ai_qa=ai_qa, **pf_kwargs)
            st["note"] = " ".join(f"{k}={v}" for k, v in rep.timings.items())
        gate_rows, pre_findings = severity.summary(rep.findings, tl), rep.findings
        if not rep.ok:
            raise EngineError("preflight failed:\n" + rep.summary())
        lock = None
        if profiles.of(tl).sound is not None and not relock:
            lock = manifest.locked_sounds(manifest.load(out.parent), tl, sfx_kinds(tl), project_dir)
        if lock is None:
            if quality == "proxy":
                if sound_cache.missing(tl, profiles.of(tl)):
                    raise EngineError("proxy quality requires prepared sounds; run python -m video_engine.sound "
                                      "PROJECT --credits first (follow its --resume instructions), then retry proxy")
            else:
                sound_request.ensure_sounds(tl, project_dir, resume=resume, resume_cmd=resume_cmd)
        mix = mixer.plan(tl, project_dir, words, lock=lock)   # None unless the profile has [sound]
        if mix:
            provenances = [r.provenance for r in mix.sounds.values()] + ([mix.music.provenance] if mix.music else [])
            if quality == "proxy":
                credits_file = project_dir / "credits.txt"
                text = credits_file.read_text() if credits_file.is_file() else ""
                have = {ln.strip()[2:] for ln in text.splitlines() if ln.strip().startswith("- ")}
                if any(c not in have for c in sound_cache.credit_lines(provenances)):
                    raise EngineError("proxy quality cannot append missing sound credits to credits.txt; "
                                      "run python -m video_engine.sound PROJECT --credits first, then retry proxy")
            else:
                sound_cache.append_credits(project_dir, provenances)
        with timer.stage("manifest") as st:
            man = manifest.write(out.parent, tl, project_dir, mix=mix, kinds_of=mix.kinds if mix else {},
                                 budget=budget, relock=relock, quality=quality, source_profile=source_profile)
            st["note"] = f"build_id={man['build_id']} sounds={'locked' if lock else 'resolved'}"
        with timer.stage("segments") as st, ThreadPoolExecutor(max_workers=1) as aud:
            audio_hit = audio.cached_master(tl, project_dir, workdir / "master.m4a", mix=mix,
                                            use_cache=use_cache, correct=True, threads=budget.mux_threads)
            measured_f = (None if audio_hit is not None
                          else aud.submit(audio.measure_mix, tl, project_dir, mix, budget.mux_threads))
            segs = render_segments(tl, project_dir, workdir, use_cache=use_cache, budget=budget)
            measured = None if audio_hit is not None else measured_f.result()
            st["note"] = " ".join(f"s{r.seg}:{r.mode}{'(hit)' if r.cache_hit else ''}={r.seconds}s" for r in segs)
        with timer.stage("ass") as st:
            ass, events = None, []
            if words:
                ass, events = captions.write_ass(words, tl, workdir / "captions.ass", rep.caption_y_overrides)
                st["note"] = f"{len(events)} caption events, {len(rep.caption_y_overrides)} placement overrides"
        with timer.stage("composite") as st:
            joined, join_diffs = streams.joinable([Path(r.path) for r in segs])
            join = {"method": "concat demuxer" if joined else "concat filter", "differences": join_diffs}
            files = [Path(r.path) for r in segs]
            tkey = transitions.plan_key(tl, files)
            clips = (None if (tkey and use_cache)
                     else (transitions.render(tl, files, workdir, budget.mux_threads) if tkey else None))
            factory = ((lambda: transitions.render(tl, files, workdir, budget.mux_threads))
                       if tkey and use_cache else None)
            video, v_hit, v_key = audio.composite(tl, files, ass, workdir / "composite.mp4",
                                                  threads=budget.mux_threads, transitions=clips,
                                                  transition_key=tkey or None, transition_factory=factory,
                                                  use_cache=use_cache)
            st["note"] = f"{join['method']}{' (hit)' if v_hit else ''}"
        with timer.stage("audio") as st:
            if audio_hit is not None:
                master, a_hit, a_key, a01, loud_hist = audio_hit
            else:
                master, a_hit, a_key, a01, loud_hist = audio.master_audio(
                    tl, project_dir, workdir / "master.m4a", measured=measured, mix=mix, use_cache=use_cache,
                    correct=True, threads=budget.mux_threads)
            st["note"] = f"loudness_passes={len(loud_hist)}{' (hit)' if a_hit else ''}"
        with timer.stage("mux") as st:
            audio.mux(tl, video, master, staged)
            st["note"] = "stream copy"
        man["stage_keys"] = {"composite": v_key, "audio": a_key,
                             "segments": {str(r.seg): Path(r.path).stem for r in segs}}
        with timer.stage("qa") as st:
            frames = count_frames(staged)
            if frames != tl.total_frames:
                raise EngineError(f"final has {frames} frames, expected {tl.total_frames}")
            findings, info = qa.pixel_gates(staged, tl, threads=budget.mux_threads, project_dir=project_dir)
            s01 = style.silence_findings(staged, tl)
            a02 = audio_source.build_findings(tl, mix)
            findings = severity.apply(findings + a01 + s01 + a02, tl, draft=draft)
            loud = loud_hist[-1]
            st["note"] = (f"frames={frames} samples={info['samples']} I={loud['I']} TP={loud['TP']} "
                          f"loudness_passes={len(loud_hist)}")
        timer.add("total", time.perf_counter() - t_all)
        failed = [f for f in findings if f.level == "fail"]
        res = BuildResult(None if failed else out, frames, tl.total_frames, segs, rep.summary(),
                          [f.line() for f in findings], loud, timer, ok=not failed, mode=mode,
                          failed=[f.line() for f in failed], cache=held.report(), quality=quality)
        L = profiles.loudness(tl)
        report = {
            "ok": not failed, "build_id": man["build_id"], "final": None if failed else str(out),
            **({"mode": "draft"} if draft else {}),
            "final_sha256": manifest.sha256(staged), "frames": frames, "expected_frames": tl.total_frames,
            "profile": profiles.of(tl).report(), "source_profile": source_profile,
            "quality": quality, "render_quality": render_quality,
            "credits_manifest": [c.model_dump() for c in tl.credits_manifest],
            "additional_credits": bool(asset_credits),
            "video": video_info(staged),
            "loudness": {"target_lufs": L.target_lufs, "tolerance_lu": L.tolerance_lu,
                         "max_true_peak": L.true_peak, "measured_lufs": loud["I"], "measured_tp": loud["TP"],
                         "measured_lra": loud["LRA"], "passes": loud_hist, "mix_pass1": measured},
            "captions": captions.metrics(events, tl), "segments": [r.__dict__ for r in segs], "join": join,
            "preflight": rep.summary().splitlines(), "preflight_fixes": rep.fixes, "ai_qa": rep.ai, "qa": res.qa, "qa_motion": info["motion"],
            "gates": severity.summary(rep.findings + findings, tl),
            "not_checked": not_checked(rep.findings + findings, tl, ai_qa, audio_cached=a_hit, sampled=True),
            "timings": [list(r) for r in timer.rows], "preflight_subtimings": rep.subtimings,
            "resources": {"budget": budget.report(), "stages": timer.resources},
            "stage_keys": man["stage_keys"],
            **({"workdir": str(workdir)} if keep_workdir else {}),
            **({"sfx_events": sfx} if (sfx := hyperframes.sfx_events(tl)) else {}),
            **({"sound": mix.report()} if mix else {}),
            "cache": res.cache,
        }
        if failed:
            gates = "/".join(sorted({f.gate for f in failed}))
            msg = (f"gate {gates} failed: " + "; ".join(f.message for f in failed)
                   + f" ({out.name} not published; report: {failed_report})")
            res.report = write_json_atomic(failed_report, {**report, "error": msg})
            if raise_on_fail:
                raise BuildFailed(msg, res, failed_report)
            return res
        # Every write that can fail on content or disk space happens before final.mp4 is replaced: the report is
        # staged next to its target first, then published with a rename right after the video.
        report_tmp = stage_json(out.parent / REPORT, report)
        if asset_credits:
            source = project_dir / "credits.txt"
            text = source.read_text(encoding="utf-8") if source.is_file() else ""
            urls = list(dict.fromkeys(c["source_url"] for c in asset_credits if c["source_url"] and c["source_url"] not in text))
            extra = "\n\nAset tambahan\n" + "\n".join(urls) + "\n" if urls else ""
            assets._atomic_text(out.parent / "credits.txt", text.rstrip() + extra)
        manifest.save(out.parent, man)
        os.replace(staged, out)
        os.replace(report_tmp, out.parent / REPORT)
        res.report = out.parent / REPORT
        failed_report.unlink(missing_ok=True)
        return res
    except (AwaitingAgent, BuildFailed):
        raise
    except Exception as e:
        write_json_atomic(failed_report, {"ok": False, "final": None, "error": f"{type(e).__name__}: {e}"[:4000],
                                          "mode": mode, "quality": quality, "render_quality": render_quality,
                                          "source_profile": source_profile, "profile": profiles.of(tl).report(),
                                          **({"gates": gate_rows} if gate_rows else {}),
                                          "not_checked": ((not_checked(pre_findings, tl, ai_qa)
                                                           if pre_findings is not None else [])
                                                          + stages_not_run(timer.rows, e)),
                                          "timings": [list(r) for r in timer.rows]})
        raise
    finally:
        if credit_token is not None:
            EXTRA_CREDIT_URLS.reset(credit_token)
        if report_tmp is not None:
            report_tmp.unlink(missing_ok=True)            # gone after a publish; left over after a failure
        timer.monitor.close()
        lease.__exit__(None, None, None)
        shutil.rmtree(stage, ignore_errors=True)
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
        # Clean up a stale .batch.lock if its recorded PID is dead; leave live locks or locks
        # held by this process (via ProjectLock outside build) untouched.
        try:
            from .batch import _clean_stale_lock
            _clean_stale_lock(project_dir / "out" / ".batch.lock")
        except OSError:
            pass
