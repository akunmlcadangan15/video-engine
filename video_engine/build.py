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

import os

import json
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .ai import AwaitingAgent
from . import (audio, cache, captions, config, grade, hyperframes, manifest, mixer, native, profiles, qa, resources, streams,
               timeline, transitions)
from .sfx import kinds_used as sfx_kinds
from .gates import audio_source, run_preflight, severity, style
from .sound import cache as sound_cache, request as sound_request
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
    failed: list[str] = field(default_factory=list)
    report: Path | None = None         # build_report.json, or build_report.failed.json when not ok
    cache: dict = field(default_factory=dict)   # cache lease session: leased count, evicted, skipped_leased, reclaimed


class BuildFailed(EngineError):
    """A build with a "fail" finding: nothing was published; `result` holds the findings, `report` the failure
    report path."""

    def __init__(self, message: str, result: BuildResult, report: Path):
        super().__init__(message)
        self.result, self.report = result, report


def write_json_atomic(path: Path, data) -> Path:
    """Write JSON through a unique temp file in the same dir and os.replace it onto `path`."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
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

    def do_native(s):
        t = time.perf_counter()
        job = native.plan(s, tl, project_dir)
        p, hit, mode = native.render(job, tl, workdir, threads=budget.x264_threads, use_cache=use_cache)
        return SegResult(s.seg_index, NATIVE, str(p), hit, mode, round(time.perf_counter() - t, 3))

    def do_hf(s):
        t = time.perf_counter()
        p, hit = hyperframes.render(s, tl, project_dir, workdir, workers=budget.hf_workers, use_cache=use_cache)
        return SegResult(s.seg_index, HYPERFRAMES, str(p), hit, "hyperframes", round(time.perf_counter() - t, 3))

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


def build(project_dir: Path, *, use_cache: bool = True, run_label: str = "build", ai_qa=None,
          out: Path | None = None, keep_workdir: bool = False, pin_words: bool = True, resume: bool = False,
          resume_cmd: str | None = None, raise_on_fail: bool = True, relock: bool = False) -> BuildResult:
    """Build and publish `out` (default <project>/out/final.mp4). Any "fail" finding means a failed build: with
    raise_on_fail it raises BuildFailed, else it returns a BuildResult with ok=False and final=None. A stage error
    raises its exception. Every failed build keeps the published final and build_report.json byte-identical and
    writes build_report.failed.json next to them. The whole build runs in one cache lease session: every cache file
    it resolves stays leased (safe from eviction by this or any other process) until the build returns or raises."""
    project_dir = Path(project_dir).resolve()
    timer = Timer(run_label)
    t_all = time.perf_counter()
    tl = timeline.load(project_dir)
    budget = resources.budget(tl)
    timer.monitor = resources.Monitor()
    workdir = project_workdir(tl)
    out = out or project_dir / "out" / "final.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    failed_report = out.parent / FAILED_REPORT
    stage = Path(tempfile.mkdtemp(prefix=f".{out.stem}.staging-", dir=out.parent))
    staged = stage / out.name
    lease = cache.session()
    held = lease.__enter__()
    gate_rows: dict | None = None
    try:
        with timer.stage("captions") as st:
            words, src = audio.captions_for(tl, project_dir, use_cache=use_cache, pin=pin_words)
            st["note"] = f"{len(words)} words from {src}"
        with timer.stage("preflight") as st:
            rep = run_preflight(tl, project_dir, words, workdir=workdir, ai_qa=ai_qa)
            st["note"] = " ".join(f"{k}={v}" for k, v in rep.timings.items())
        gate_rows = severity.summary(rep.findings, tl)
        if not rep.ok:
            raise EngineError("preflight failed:\n" + rep.summary())
        lock = None
        if profiles.of(tl).sound is not None and not relock:
            lock = manifest.locked_sounds(manifest.load(out.parent), tl, sfx_kinds(tl), project_dir)
        if lock is None:
            sound_request.ensure_sounds(tl, project_dir, resume=resume, resume_cmd=resume_cmd)
        mix = mixer.plan(tl, project_dir, words, lock=lock)   # None unless the profile has [sound]
        if mix:
            sound_cache.append_credits(project_dir, [r.provenance for r in mix.sounds.values()]
                                       + ([mix.music.provenance] if mix.music else []))
        with timer.stage("manifest") as st:
            man = manifest.write(out.parent, tl, project_dir, mix=mix, kinds_of=mix.kinds if mix else {},
                                 budget=budget, relock=relock)
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
            findings, info = qa.pixel_gates(staged, tl, threads=budget.mux_threads)
            s01 = style.silence_findings(staged, tl)
            a02 = audio_source.build_findings(tl, mix)
            findings = severity.apply(findings + a01 + s01 + a02, tl)
            loud = loud_hist[-1]
            st["note"] = (f"frames={frames} samples={info['samples']} I={loud['I']} TP={loud['TP']} "
                          f"loudness_passes={len(loud_hist)}")
        timer.add("total", time.perf_counter() - t_all)
        failed = [f for f in findings if f.level == "fail"]
        res = BuildResult(None if failed else out, frames, tl.total_frames, segs, rep.summary(),
                          [f.line() for f in findings], loud, timer, ok=not failed, failed=[f.line() for f in failed],
                          cache=held.report())
        L = profiles.loudness(tl)
        report = {
            "ok": not failed, "build_id": man["build_id"], "final": None if failed else str(out),
            "final_sha256": manifest.sha256(staged), "frames": frames, "expected_frames": tl.total_frames,
            "profile": profiles.of(tl).report(),
            "video": video_info(staged),
            "loudness": {"target_lufs": L.target_lufs, "tolerance_lu": L.tolerance_lu,
                         "max_true_peak": L.true_peak, "measured_lufs": loud["I"], "measured_tp": loud["TP"],
                         "measured_lra": loud["LRA"], "passes": loud_hist, "mix_pass1": measured},
            "captions": captions.metrics(events, tl), "segments": [r.__dict__ for r in segs], "join": join,
            "preflight": rep.summary().splitlines(), "preflight_fixes": rep.fixes, "ai_qa": rep.ai, "qa": res.qa, "qa_motion": info["motion"],
            "gates": severity.summary(rep.findings + findings, tl),
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
        manifest.save(out.parent, man)
        os.replace(staged, out)
        res.report = write_json_atomic(out.parent / REPORT, report)
        failed_report.unlink(missing_ok=True)
        return res
    except (AwaitingAgent, BuildFailed):
        raise
    except Exception as e:
        write_json_atomic(failed_report, {"ok": False, "final": None, "error": f"{type(e).__name__}: {e}"[:4000],
                                          **({"gates": gate_rows} if gate_rows else {}),
                                          "timings": [list(r) for r in timer.rows]})
        raise
    finally:
        timer.monitor.close()
        lease.__exit__(None, None, None)
        shutil.rmtree(stage, ignore_errors=True)
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
