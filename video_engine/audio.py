"""Audio chain: faster-whisper word timestamps (int8, BLAKE3-cached) and the final single-pass mux
(concat video + burned .ass captions + sidechain-ducked BGM + loudnorm)."""
from __future__ import annotations

import re
from pathlib import Path

from . import cache, config, profiles, streams
from .timeline import CaptionsMeta, CaptionWord, Timeline, sec_to_frame
from .util import EngineError, blake3_files, blake3_json, code_hash, count_frames, probe, run

WHISPER_VERSION = "fw-1-t0"
_MODELS: dict = {}


def _model(name: str, threads: int):
    from faster_whisper import WhisperModel
    k = (name, threads)
    if k not in _MODELS:
        _MODELS[k] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
    return _MODELS[k]


def words_key(audio: Path, language: str, model: str, initial_prompt: str | None) -> str:
    return blake3_json({"v": WHISPER_VERSION, "audio": blake3_files([audio])[0], "lang": language, "model": model,
                        "prompt": initial_prompt, "faster_whisper": _pkg_version("faster-whisper")})


def _pkg_version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(name)
    except PackageNotFoundError:
        return "missing"


def transcribe_words(audio: Path, language: str = "id", model: str = "small", initial_prompt: str | None = None,
                     threads: int | None = None, use_cache: bool = True) -> tuple[list[dict], bool]:
    """[{word, start, end, prob}] in seconds. Second value: cache hit."""
    threads = min(8, threads or config.THREADS)
    key = words_key(audio, language, model, initial_prompt)
    if use_cache and (hit := cache.get_json("words", key)) is not None:
        return hit, True
    segs, _info = _model(model, threads).transcribe(
        decode_16k(audio), language=language, word_timestamps=True, beam_size=5, temperature=0.0, vad_filter=True,
        initial_prompt=initial_prompt, condition_on_previous_text=False)
    words = [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3), "prob": round(w.probability, 3)}
             for s in segs for w in (s.words or [])]
    cache.put_json("words", key, words)
    return words, False


def decode_16k(path: Path):
    """16 kHz mono float32 PCM via ffmpeg (faster-whisper input)."""
    import subprocess

    import numpy as np
    raw = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _norm(t: str) -> str:
    return re.sub(r"[^\w]+", "", t.lower())


def apply_fixes(words: list[dict], fixes) -> list[dict]:
    """Replace recognised word sequences (STT corrections); timing of the run is split evenly over the new words."""
    out = list(words)
    for fx in fixes:
        pat = [_norm(x) for x in fx.match.split()]
        rep = fx.to.split()
        i = 0
        while i + len(pat) <= len(out):
            if [_norm(w["word"]) for w in out[i:i + len(pat)]] == pat:
                s, e = out[i]["start"], out[i + len(pat) - 1]["end"]
                step = (e - s) / len(rep)
                new = [{"word": r, "start": round(s + k * step, 3), "end": round(s + (k + 1) * step, 3), "prob": 1.0}
                       for k, r in enumerate(rep)]
                out[i:i + len(pat)] = new
                i += len(new)
            else:
                i += 1
    return out


def words_to_captions(words: list[dict], fps: int, total_frames: int) -> list[CaptionWord]:
    caps = []
    for w in words:
        s, e = sec_to_frame(w["start"], fps), sec_to_frame(w["end"], fps)
        if s >= total_frames:
            continue
        e = min(max(e, s + 1), total_frames)
        caps.append(CaptionWord(word=w["word"].strip(), start_frame=s, end_frame=e))
    caps.sort(key=lambda c: c.start_frame)   # Rust compile_ass requires words sorted by start
    # strictly monotone, no overlaps (whisper occasionally overlaps by a frame)
    for a, b in zip(caps, caps[1:]):
        if a.end_frame > b.start_frame:
            a.end_frame = max(a.start_frame + 1, b.start_frame)
    return [c for c in caps if c.end_frame > c.start_frame]


def fixes_digest(fixes) -> str:
    return blake3_json([[f.match, f.to] for f in fixes])


def expected_meta(tl: Timeline, project_dir: Path) -> dict:
    """Fields of CaptionsMeta that must match the current timeline for pinned words to be valid."""
    ac = tl.audio_chain
    return {"voiceover_blake3": blake3_files([project_dir / ac.voiceover_path])[0], "whisper_model": ac.whisper_model,
            "language": ac.language, "prompt": ac.initial_prompt, "fixes_blake3": fixes_digest(ac.caption_fixes),
            "fps": tl.settings.fps}


def transcribe_captions(tl: Timeline, project_dir: Path, use_cache: bool = True) -> tuple[list[CaptionWord], CaptionsMeta]:
    """faster-whisper words (BLAKE3 cache) + caption_fixes -> frame captions and their provenance."""
    ac = tl.audio_chain
    if not ac.voiceover_path:
        raise EngineError("audio_chain.voiceover_path is empty: nothing to transcribe")
    vo = project_dir / ac.voiceover_path
    if not vo.is_file():
        raise EngineError(f"voiceover not found: {ac.voiceover_path}")
    words, hit = transcribe_words(vo, ac.language, ac.whisper_model, ac.initial_prompt, use_cache=use_cache)
    words = apply_fixes(words, ac.caption_fixes)
    total = tl.total_frames
    caps = words_to_captions(words, tl.settings.fps, total)
    clipped = sum(1 for w in words if sec_to_frame(w["end"], tl.settings.fps) > total)
    meta = CaptionsMeta(**expected_meta(tl, project_dir), total_frames=total, clipped_words=clipped,
                        words_cache_hit=hit)
    return caps, meta


def pin_captions(project_dir: Path, use_cache: bool = True) -> tuple[Timeline, CaptionsMeta]:
    """Transcribe + fix once and write `captions` and `captions_meta` into master_timeline.json. Loads a fresh copy
    of the file so in-memory gate fixes are never persisted by this call."""
    from . import timeline
    fresh = timeline.load(project_dir)
    caps, meta = transcribe_captions(fresh, project_dir, use_cache=use_cache)
    fresh.captions, fresh.captions_meta = caps, meta
    timeline.save(fresh, project_dir)
    return fresh, meta


def captions_for(tl: Timeline, project_dir: Path, use_cache: bool = True, pin: bool = False
                 ) -> tuple[list[CaptionWord], str]:
    """Pinned timeline captions if present; else (auto_captions) transcribed narration, written into the
    timeline when `pin` is true. Returns (words, source)."""
    if tl.captions:
        return tl.captions, "timeline"
    ac = tl.audio_chain
    if not (ac.auto_captions and ac.voiceover_path):
        return [], "none"
    if pin:
        fresh, meta = pin_captions(project_dir, use_cache=use_cache)
        tl.captions, tl.captions_meta = fresh.captions, meta
        return tl.captions, ("whisper-cache" if meta.words_cache_hit else "whisper") + "+pinned"
    caps, meta = transcribe_captions(tl, project_dir, use_cache=use_cache)
    return caps, "whisper-cache" if meta.words_cache_hit else "whisper"


MIX_RATE = 48000   # Hz of every mix graph and of the final audio stream


def mix_samples(tl: Timeline) -> int:
    """Exact sample count of the final audio: total_frames / fps x MIX_RATE, rounded."""
    return round(tl.total_frames * MIX_RATE / tl.settings.fps)


def exact_cut(tl: Timeline) -> str:
    """Cut to exactly mix_samples(tl) samples with timestamps restarting at 0 (input must be at MIX_RATE)."""
    return f"atrim=end_sample={mix_samples(tl)},asetpts=N/SR/TB"


def exact_tail(tl: Timeline) -> str:
    """Final guard after the loudness filters: back to MIX_RATE, silence-padded and cut to the exact sample count."""
    return f"aresample={MIX_RATE},apad,{exact_cut(tl)}"


def _audio_graph(tl: Timeline, vi: int | None, bi: int | None, norm: str) -> list[str]:
    """Filter chains producing [a]: VO + BGM (sidechain-ducked under VO), `norm` filter, exact sample count.
    The VO (silence-padded) and the BGM (looped input) stay unbounded through the sidechain compressor and the mix;
    the mixed signal is cut to the exact sample count before `norm`, and exact_tail guards the count after it."""
    ac = tl.audio_chain
    fmt = f"aresample={MIX_RATE},aformat=channel_layouts=stereo"
    cut, tail = exact_cut(tl), exact_tail(tl)
    norm = f",{norm}" if norm else ""
    fa = []
    if vi is not None and bi is not None:
        fa.append(f"[{vi}:a]{fmt},apad,asplit=2[vo][sc]")
        fa.append(f"[{bi}:a]{fmt},volume={ac.bgm_gain_db}dB[bg]")
        if ac.auto_ducking:
            fa.append("[bg][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=450:makeup=1[duck]")
        else:
            fa.append("[sc]anullsink;[bg]anull[duck]")
        fa.append(f"[vo][duck]amix=inputs=2:duration=first:normalize=0,{cut}{norm},{tail}[a]")
    elif vi is not None:
        fa.append(f"[{vi}:a]{fmt},apad,{cut}{norm},{tail}[a]")
    elif bi is not None:
        fa.append(f"[{bi}:a]{fmt},volume={ac.bgm_gain_db}dB,{cut}{norm},{tail}[a]")
    else:
        fa.append(f"anullsrc=r={MIX_RATE}:cl=stereo,{cut}[a]")
    return fa


def _audio_inputs(tl: Timeline, project_dir: Path, first_idx: int) -> tuple[list, int | None, int | None]:
    ac = tl.audio_chain
    args, idx, vi, bi = [], first_idx, None, None
    if ac.voiceover_path:
        args += ["-i", project_dir / ac.voiceover_path]
        vi, idx = idx, idx + 1
    if ac.bgm_path:
        args += ["-stream_loop", "-1", "-i", project_dir / ac.bgm_path]
        bi, idx = idx, idx + 1
    return args, vi, bi


def _mix_inputs(tl: Timeline, project_dir: Path, mix, first_idx: int, norm: str) -> tuple[list, list[str] | None]:
    """(input args, filter chains producing [a]) of the mix; chains None when there is no audio at all.
    mix: mixer.Mix of a profile with [sound], else None (VO + BGM sidechain mix)."""
    if mix is None:
        args, vi, bi = _audio_inputs(tl, project_dir, first_idx)
        return args, (None if vi is None and bi is None else _audio_graph(tl, vi, bi, norm))
    from . import mixer
    args, vi, mi, si = mixer.inputs(tl, project_dir, mix, first_idx)
    return args, (None if not args else mixer.graph(tl, mix, vi, mi, si, norm))


def measure_mix(tl: Timeline, project_dir: Path, mix=None) -> dict | None:
    """Loudnorm pass 1 on the audio mix only (no video). Returns loudnorm's measured_* JSON, or None without audio."""
    import json as _json
    import subprocess
    L = profiles.loudness(tl)
    meas = f"loudnorm=I={L.target_lufs}:TP={L.true_peak}:LRA={L.lra}:print_format=json"
    args, fa = _mix_inputs(tl, project_dir, mix, 0, meas)
    if fa is None:
        return None
    cmd = [config.FFMPEG, "-hide_banner", "-nostats", *args, "-filter_complex", ";".join(fa),
           "-map", "[a]", "-f", "null", "-"]
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise EngineError(f"loudness measure failed: {r.stderr[-600:]}")
    txt = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    return _json.loads(txt)


def norm_filter(tl: Timeline, m: dict | None) -> str:
    """Loudnorm pass 2 (linear gain from the pass-1 measurement) followed by a sample-peak limiter 0.5 dB under TP."""
    L = profiles.loudness(tl)
    lim = 10 ** ((L.true_peak - 0.5) / 20)
    base = f"loudnorm=I={L.target_lufs}:TP={L.true_peak}:LRA={L.lra}"
    if m:
        base += (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                 f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    return f"{base},alimiter=limit={lim:.4f}:level=false"


def video_inputs(segments: list[Path], list_path: Path, fps: int) -> tuple[list, int, str, str]:
    """(input args, input count, filter chain prefix, video label) joining `segments` in order.
    streams.joinable segments: one concat-demuxer input (packets joined, decoded once), list written to list_path.
    Otherwise one input per segment, each decoded on its own and retimed to frame index / fps (settb, setpts=N),
    joined by the concat filter and retimed again, so every frame keeps its place whatever the segment time bases,
    codec parameters or colour tags."""
    if streams.joinable(segments)[0]:
        list_path.write_text("".join(f"file '{p}'\n" for p in segments))
        return ["-f", "concat", "-safe", "0", "-i", list_path], 1, "", "[0:v]"
    args = [a for p in segments for a in ("-threads", "2", "-i", p)]
    pre = "".join(f"[{i}:v]settb=1/{fps},setpts=N[c{i}];" for i in range(len(segments)))
    pre += "".join(f"[c{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0,settb=1/{fps},setpts=N[vcat];"
    return args, len(segments), pre, "[vcat]"


def final_mux(tl: Timeline, project_dir: Path, segments: list[Path], ass: Path | None, out: Path, threads: int,
              measured: dict | None = None, mix=None, transitions: list[tuple[Path, int]] | None = None) -> None:
    """composite (video pass) + master_audio (audio pass, no loudness correction) + mux (stream copy), uncached."""
    out = out.resolve()
    v = out.with_name(out.stem + ".video.mp4")
    a = out.with_name(out.stem + ".audio.m4a")
    try:
        composite(tl, segments, ass, v, threads, transitions=transitions, use_cache=False)
        master_audio(tl, project_dir, a, measured=measured, mix=mix, use_cache=False)
        mux(tl, v, a, out)
    finally:
        v.unlink(missing_ok=True)
        a.unlink(missing_ok=True)


def composite_cmd(tl: Timeline, segments: list[Path], ass: Path | None, out: Path, threads: int,
                  transitions: list[tuple[Path, int]] | None = None) -> tuple[list, Path | None, dict, list[Path]]:
    """(ffmpeg argv, cwd, key parts, scratch files) of the ONE video pass: join the segment files (video_inputs),
    transition clips (video_engine/transitions.py: (clip, first frame)) over them, burn captions, profile watermark
    on top, exact frame count, no audio."""
    from . import transitions as trans
    from . import watermark
    S = tl.settings
    n = tl.total_frames
    dur = n / S.fps
    # ffmpeg may run with cwd = the .ass folder: every path handed to it is absolute.
    out = out.resolve()
    segments = [Path(p).resolve() for p in segments]
    concat_list = out.with_name(out.stem + ".concat.txt")
    vin, nv, vpre, vsrc = video_inputs(segments, concat_list, S.fps)
    cmd = [config.FFMPEG, "-v", "error", "-y", *vin]
    vf, cwd = "null", None
    extra = {"segments": segments, "threads": threads, "ass": ass}
    if ass is not None:
        vf, cwd = subtitle_filter(ass)
    wm_png = out.with_name(out.stem + ".watermark.png")
    pl = watermark.render(tl, wm_png)
    if pl is None:
        fv = f"{vsrc}{vf},format=yuv420p[v]"
    else:
        cmd += ["-i", wm_png]
        fv = f"{vsrc}{vf}[base];[base][{nv}:v]overlay=x={pl.x}:y={pl.y},format=yuv420p[v]"
    if transitions:
        chain, lab = trans.overlay_graph(transitions, nv + (pl is not None), S.fps, vsrc)
        for clip, _ in transitions:
            cmd += ["-i", clip.resolve()]
        fv = chain + ";" + lab + fv[len(vsrc):]
    cmd += ["-filter_complex", vpre + fv, "-map", "[v]", *profiles.of(tl).render.x264("final"), "-threads", str(threads),
            "-r", str(S.fps), "-frames:v", str(n), "-an", "-movflags", "+faststart", "-t", f"{dur:.6f}", out]
    return cmd, cwd, extra, [wm_png, concat_list]


def composite_key(cmd: list, parts: dict, ass: Path | None, out: Path, concat: Path,
                  transition_key: str | None = None, clips=()) -> str:
    """Key of the video pass: the command with every input file (segments, transition clips drawn from both
    neighbouring segments, watermark) replaced by its sha256, plus the segment, caption (.ass, fonts) and thread
    entries (manifest.cmd_key). With `transition_key`, the rendered clips drop out of the command and their content
    key is used instead, so the key exists before a clip is rendered. Call while composite_cmd's scratch files exist."""
    from .manifest import cmd_key, font_files, sha256
    extra = {"segments": [sha256(p) for p in parts["segments"]], "threads": parts["threads"],
             **({"ass": sha256(ass), "fonts": [sha256(f) for f in font_files()]} if ass is not None else {}),
             **({"transitions": transition_key} if transition_key else {})}
    drop = {out, concat, *map(Path, clips)}
    return cmd_key("composite", [c for c in cmd if Path(str(c)) not in drop], extra)


def composite(tl: Timeline, segments: list[Path], ass: Path | None, out: Path, threads: int,
              transitions: list[tuple[Path, int]] | None = None, use_cache: bool = True,
              transition_key: str | None = None, transition_factory=None) -> tuple[Path, bool, str]:
    """Video pass -> (file, cache hit, key). Cache kind "composite"; the key is the command with every input
    replaced by its sha256 plus the segment, caption (.ass, fonts) and thread entries (manifest.cmd_key).
    With `transition_key` and `transition_factory`, the factory (which renders the transition clips) runs only
    after the cache lookup missed: a composite cache hit costs no transition render."""
    out = out.resolve()
    key, canonical = "", None
    if use_cache and transition_factory is not None:
        ccmd, ccwd, cparts, cscratch = composite_cmd(tl, segments, ass, out, threads, None)
        try:
            key = composite_key(ccmd, cparts, ass, out, cscratch[1], transition_key=transition_key)
            if hit := cache.get_file("composite", key):
                return hit, True, key
        finally:
            for f in cscratch:
                f.unlink(missing_ok=True)
        canonical = key
        transitions = transition_factory()
    clips = [] if transitions is None else [p for p, _ in transitions]
    cmd, cwd, parts, scratch = composite_cmd(tl, segments, ass, out, threads, transitions)
    try:
        if use_cache and canonical is None:
            key = composite_key(cmd, parts, ass, out, scratch[1], transition_key=transition_key, clips=clips)
        if use_cache and (hit := cache.get_file("composite", key)):
            return hit, True, key
        run(cmd, cwd=cwd)
    finally:
        for f in scratch:
            f.unlink(missing_ok=True)
    streams.verify(out, tl.total_frames, tl.settings.fps, "composite")
    return (cache.put_file("composite", key, out) if use_cache else out), False, key


def audio_cmd(tl: Timeline, project_dir: Path, out: Path, measured: dict | None, mix=None) -> list:
    """ffmpeg argv of the audio pass: ducked BGM under VO (or the [sound] mix), two-pass loudnorm (pass 1 =
    measure_mix), exact sample count, AAC 192k 48 kHz, cut at the video duration."""
    n, fps = tl.total_frames, tl.settings.fps
    project_dir = project_dir.resolve()
    args, fa_audio = _mix_inputs(tl, project_dir, mix, 0, norm_filter(tl, measured))
    if fa_audio is None:
        fa_audio = _audio_graph(tl, None, None, "")
    return [config.FFMPEG, "-v", "error", "-y", *args, "-filter_complex", ";".join(fa_audio), "-map", "[a]",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{n / fps:.6f}", out.resolve()]


def audio_key(tl: Timeline, cmd: list, out: Path, correct: bool) -> str:
    """Key of the audio pass: the command with every input replaced by its sha256 (manifest.cmd_key), the loudness
    correction values when `correct`, and this module's source (enforce_loudness, the cached loudness history)."""
    from .manifest import cmd_key
    L = profiles.loudness(tl)
    return cmd_key("audio", [c for c in cmd if Path(str(c)) != out],
                   {"correct": [L.target_lufs, L.true_peak, L.tolerance_lu, MAX_CORRECTIONS] if correct else None,
                    "code": code_hash([__file__])})


def _audio_plan_key(tl: Timeline, project_dir: Path, out: Path, mix, correct: bool) -> str:
    """Key of the audio pass before loudnorm pass 1: the command is built with `measured=None`, because the
    measurement is a function of the same inputs (and is stored with the cached loudness history)."""
    return audio_key(tl, audio_cmd(tl, project_dir, out, None, mix), out, correct)


def _cached_master(tl: Timeline, out: Path, key: str, correct: bool):
    """Cached audio pass copied to `out`: (out, True, key, findings, history) or None."""
    import shutil as _sh
    if not (hit := cache.get_file("audio", key, ".m4a")):
        return None
    meta = cache.get_json("audio", key)
    if meta is None:
        return None
    _sh.copyfile(hit, out)
    L = profiles.loudness(tl)
    fs = loudness_findings(meta[-1], L.target_lufs, L.true_peak, L.tolerance_lu) if correct else []
    return out, True, key, fs, meta


def cached_master(tl: Timeline, project_dir: Path, out: Path, mix=None, use_cache: bool = True,
                  correct: bool = False):
    """The cached audio pass for these inputs (file copied to `out`), or None. Lets a build skip loudnorm pass 1
    when the audio cache can answer."""
    if not use_cache:
        return None
    out = out.resolve()
    return _cached_master(tl, out, _audio_plan_key(tl, project_dir, out, mix, correct), correct)


def master_audio(tl: Timeline, project_dir: Path, out: Path, measured: dict | None = None, mix=None,
                 use_cache: bool = True, correct: bool = False) -> tuple[Path, bool, str, list, list[dict]]:
    """Audio pass (+ with `correct`, the A01 corrective gain passes on the audio file alone) ->
    (file, cache hit, key, A01 findings, loudness history). Cache kind "audio": a visual change never re-runs it and
    a hit does not run loudnorm pass 1."""
    out = out.resolve()
    key = _audio_plan_key(tl, project_dir, out, mix, correct) if use_cache else ""
    if use_cache and (hit := _cached_master(tl, out, key, correct)) is not None:
        return hit
    if measured is None:
        measured = measure_mix(tl, project_dir, mix)
    cmd = audio_cmd(tl, project_dir, out, measured, mix)
    run(cmd)
    fs, hist = enforce_loudness(out, tl) if correct else ([], [{"pass": "mux", **loudness(out), "gain_db": 0.0}])
    if use_cache:
        cache.put_file("audio", key, out, ".m4a")
        cache.put_json("audio", key, hist)
    return out, False, key, fs, hist


def mux(tl: Timeline, video: Path, audio_file: Path, out: Path) -> Path:
    """Stream-copy mux of the composite video and the master audio (no re-encode)."""
    run([config.FFMPEG, "-v", "error", "-y", "-i", video, "-i", audio_file, "-map", "0:v:0", "-map", "1:a:0",
         "-c", "copy", "-movflags", "+faststart", out])
    streams.verify(out, tl.total_frames, tl.settings.fps, "final mux")
    return out


SUB_NAME = "captions.ass"
SUB_FONTS = "fonts"


def subtitle_filter(ass: Path) -> tuple[str, Path]:
    """(filter, cwd) for burning `ass`: ffmpeg runs in the .ass folder and the filter names only fixed bare
    filenames (no filter-graph escaping of arbitrary paths). The .ass is copied to SUB_NAME if named otherwise;
    SUB_FONTS is a symlink to config.FONTS_DIR."""
    import shutil
    d = ass.resolve().parent
    if ass.name != SUB_NAME:
        shutil.copyfile(ass, d / SUB_NAME)
    link = d / SUB_FONTS
    if link.is_symlink() or link.exists():
        if not (link.is_symlink() and link.resolve() == config.FONTS_DIR.resolve()):
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                raise EngineError(f"{link} exists and is not the fonts symlink")
    if not link.exists():
        link.symlink_to(config.FONTS_DIR.resolve(), target_is_directory=True)
    return f"subtitles=filename={SUB_NAME}:fontsdir={SUB_FONTS}", d


def loudness(path: Path) -> dict:
    """Integrated loudness (LUFS), true peak (dBTP) and loudness range (LU) of a file (ebur128)."""
    import subprocess
    r = subprocess.run([config.FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
                        "-f", "null", "-"], capture_output=True, text=True)
    txt = r.stderr[r.stderr.rfind("Summary:"):]
    I = re.search(r"I:\s+(-?[\d.]+) LUFS", txt)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", txt)
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU\b", txt)
    return {"I": float(I.group(1)) if I else None, "TP": float(tp.group(1)) if tp else None,
            "LRA": float(lra.group(1)) if lra else None}


MAX_CORRECTIONS = 2     # corrective gain passes before A01 fails the build


def loudness_findings(m: dict, target_lufs: float, max_tp: float, tol: float) -> list:
    """A01 delivery gate on a measurement {"I": LUFS, "TP": dBTP}."""
    from .gates.core import Finding
    I, TP = m.get("I"), m.get("TP")
    if I is None or TP is None:
        return [Finding("A01", "fail", f"loudness not measurable: {m}")]
    out = []
    if abs(I - target_lufs) > tol + 1e-9:
        out.append(Finding("A01", "fail", f"integrated loudness {I:.1f} LUFS outside {target_lufs:.1f} +/- {tol} LU"))
    if TP > max_tp + 1e-9:
        out.append(Finding("A01", "fail", f"true peak {TP:.1f} dBTP > {max_tp:.1f} dBTP"))
    return out or [Finding("A01", "pass", f"{I:.1f} LUFS, true peak {TP:.1f} dBTP")]


def apply_gain(src: Path, dst: Path, gain_db: float, true_peak: float) -> Path:
    """Corrective pass: audio gain + sample-peak limiter 0.5 dB under the true-peak limit; video stream copied.
    The copied stream must keep every streams.VIDEO_KEYS parameter, its frame count and its duration."""
    lim = 10 ** ((true_peak - 0.5) / 20)
    if not any(st.get("codec_type") == "video" for st in probe(src)["streams"]):   # audio-only master
        run([config.FFMPEG, "-v", "error", "-y", "-i", src, "-map", "0:a:0",
             "-af", f"volume={gain_db:.2f}dB,alimiter=limit={lim:.4f}:level=false", "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", dst])
        return dst
    run([config.FFMPEG, "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
         "-af", f"volume={gain_db:.2f}dB,alimiter=limit={lim:.4f}:level=false", "-c:a", "aac", "-b:a", "192k",
         "-ar", "48000", "-movflags", "+faststart", dst])
    if diff := streams.mismatch(streams.params(dst, "v:0"), streams.params(src, "v:0")):
        raise EngineError(f"gain pass changed the copied video stream: {diff}")
    fps = streams.params(src, "v:0")[0]["r_frame_rate"].split("/")
    n = count_frames(src)
    streams.verify(dst, n, round(int(fps[0]) / int(fps[1])), "gain pass")
    return dst


def enforce_loudness(path: Path, tl: Timeline) -> tuple[list, list[dict]]:
    """Measure -> corrective gain pass(es) while outside the profile tolerance -> re-measure. Replaces `path` in place.
    Returns (A01 findings of the last measurement, list of measurements {"pass", "I", "TP", "gain_db"})."""
    L = profiles.loudness(tl)
    m = loudness(path)
    hist = [{"pass": "mux", **m, "gain_db": 0.0}]
    for k in range(MAX_CORRECTIONS):
        fs = loudness_findings(m, L.target_lufs, L.true_peak, L.tolerance_lu)
        if all(f.level == "pass" for f in fs) or m.get("I") is None:
            return fs, hist
        gain = L.target_lufs - m["I"]
        tmp = path.with_suffix(f".gain{k}{path.suffix}")
        apply_gain(path, tmp, gain, L.true_peak)
        tmp.replace(path)
        m = loudness(path)
        hist.append({"pass": f"correct{k + 1}", **m, "gain_db": round(gain, 2)})
    return loudness_findings(m, L.target_lufs, L.true_peak, L.tolerance_lu), hist
