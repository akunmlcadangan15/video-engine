"""Audio chain: xAI Grok Voice Transcribe word timestamps (BLAKE3-cached) and the final single-pass mux
(concat video + burned .ass captions + sidechain-ducked BGM + loudnorm)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import ai, cache, config, profiles, streams, toolchain
from .timeline import CaptionsMeta, CaptionWord, Timeline, sec_to_frame
from .util import EngineError, blake3_files, blake3_json, code_hash, count_frames, probe, run, wav_16k_mono

STT_MODEL = ai.STT_DEFAULTS["model"]     # defaults live in ai.STT_DEFAULTS; [stt] in the AI config overrides them
STT_URL = ai.STT_DEFAULTS["url"]
STT_TIMEOUT_S = ai.STT_DEFAULTS["timeout_s"]
STT_VERSION = "stt-1-grok-2"


def _stt_cfg() -> dict:
    """Effective STT settings. A broken AI config is an EngineError, not a silent return to the defaults."""
    return ai.stt_config()


def _key(env_name: str = "XAI_API_KEY") -> str:
    key = os.environ.get(env_name)
    if not key:
        raise EngineError(f"{env_name} is not set")
    return key


def words_key(audio: Path, language: str, model: str, keyterms=()) -> str:
    return blake3_json({"v": STT_VERSION, "audio": blake3_files([audio])[0], "lang": language, "model": model,
                        "keyterms": list(keyterms)})


def _post(url: str, headers: dict, fields: list, timeout: float):
    import httpx
    return httpx.post(url, headers=headers, files=fields, timeout=timeout)


def _wav_16k(path: Path) -> bytes:
    """16 kHz mono WAV bytes via ffmpeg (the STT upload)."""
    return wav_16k_mono(path, "STT audio decode")


def _fields(model: str, language: str, keyterms: list[str], wav: bytes) -> list:
    """Multipart fields in wire order; `file` MUST stay last."""
    fields = [("model", (None, model)), ("language", (None, language))]
    fields += [("keyterm", (None, term)) for term in keyterms]
    fields += [("filler_words", (None, "false")), ("file", ("audio.wav", wav, "audio/wav"))]
    return fields


def _stt(audio: Path, language: str, model: str, keyterms: list[str]) -> list[dict]:
    from .ai import base
    scfg = _stt_cfg()
    key = _key(scfg.get("api_key_env", "XAI_API_KEY"))
    stt_url = scfg.get("url", STT_URL)
    stt_timeout = scfg.get("timeout_s", STT_TIMEOUT_S)
    wav = _wav_16k(audio)
    fields = _fields(model, language, keyterms, wav)
    headers = {"Authorization": f"Bearer {key}"}
    for attempt in range(base.SAME_MODEL_RETRIES + 1):
        try:
            r = _post(stt_url, headers, fields, stt_timeout)
        except Exception as e:
            if base.is_transport(e) and attempt < base.SAME_MODEL_RETRIES:
                base._sleep(base.backoff_delay(attempt + 1))
                continue
            raise EngineError(f"xAI STT request failed: transport {type(e).__name__}: {e}") from None
        if r.status_code == 200:
            try:
                body = r.json()
            except ValueError:
                raise EngineError("xAI STT returned invalid JSON") from None
            return _stt_words(body)
        if r.status_code in base.SAME_MODEL_RETRY_CODES and attempt < base.SAME_MODEL_RETRIES:
            base._sleep(base.backoff_delay(attempt + 1))
            continue
        raise EngineError(f"xAI STT request failed: HTTP {r.status_code}: {r.text[:300]}")
    raise EngineError("xAI STT request failed: retries exhausted")


def _stt_words(body) -> list[dict]:
    """[{word, start, end}] from an STT response body; EngineError when it is not the documented shape (a word
    without text or with times that are not numbers would otherwise surface as a KeyError deep in the build)."""
    words = body.get("words") if isinstance(body, dict) else None
    if words is None:
        words = []
    if not isinstance(words, list):
        raise EngineError("xAI STT response: `words` must be a list")
    out = []
    for i, w in enumerate(words):
        try:
            out.append({"word": str(w["text"]), "start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3)})
        except (KeyError, TypeError, ValueError):
            raise EngineError(f"xAI STT response: words[{i}] needs text, start and end (got {str(w)[:120]})") from None
    return out


def transcribe_words(audio: Path, language: str = "id", model: str = STT_MODEL, keyterms=(),
                     use_cache: bool = True) -> tuple[list[dict], bool]:
    """[{word, start, end}] in seconds. Second value: cache hit. An empty transcript is returned but not cached:
    a silent upload or a provider hiccup must not pin "no words" to this voiceover."""
    keyterms = list(keyterms or [])
    key = words_key(audio, language, model, keyterms)
    if use_cache and (hit := cache.get_json("words", key)):
        return hit, True
    words = _stt(audio, language, model, keyterms)
    if words:
        cache.put_json("words", key, words)
    return words, False


def _norm(t: str) -> str:
    return re.sub(r"[^\w]+", "", t.lower())


def apply_fixes(words: list[dict], fixes) -> list[dict]:
    """Replace recognised word sequences (STT corrections); timing of the run is split evenly over the new words.
    A blank `to` removes the matched words. A blank `from` matches nothing (an empty pattern would match at every
    position and never advance)."""
    out = list(words)
    for fx in fixes:
        pat = [_norm(x) for x in fx.match.split()]
        rep = fx.to.split()
        if not pat:
            continue
        i = 0
        while i + len(pat) <= len(out):
            if [_norm(w["word"]) for w in out[i:i + len(pat)]] == pat:
                if not rep:
                    del out[i:i + len(pat)]
                    continue
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
    # strictly monotone, no overlaps (STT words occasionally overlap by a frame)
    for a, b in zip(caps, caps[1:]):
        if a.end_frame > b.start_frame:
            a.end_frame = max(a.start_frame + 1, b.start_frame)
    return [c for c in caps if c.end_frame > c.start_frame]


def fixes_digest(fixes) -> str:
    return blake3_json([[f.match, f.to] for f in fixes])


def keyterms_digest(keyterms) -> str:
    return blake3_json(list(keyterms))


def expected_meta(tl: Timeline, project_dir: Path) -> dict:
    """Fields of CaptionsMeta that must match the current timeline for pinned words to be valid."""
    ac = tl.audio_chain
    return {"voiceover_blake3": blake3_files([project_dir / ac.voiceover_path])[0], "stt_model": ac.stt.model,
            "language": ac.language, "keyterms_blake3": keyterms_digest(ac.stt.keyterms),
            "fixes_blake3": fixes_digest(ac.caption_fixes), "fps": tl.settings.fps}


def transcribe_captions(tl: Timeline, project_dir: Path, use_cache: bool = True
                        ) -> tuple[list[CaptionWord], CaptionsMeta]:
    """Grok STT words (BLAKE3 cache) + caption_fixes -> frame captions and their provenance."""
    ac = tl.audio_chain
    if not ac.voiceover_path:
        raise EngineError("audio_chain.voiceover_path is empty: nothing to transcribe")
    vo = project_dir / ac.voiceover_path
    if not vo.is_file():
        raise EngineError(f"voiceover not found: {ac.voiceover_path}")
    words, hit = transcribe_words(vo, ac.language, ac.stt.model, ac.stt.keyterms, use_cache=use_cache)
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
        return tl.captions, ("stt-cache" if meta.words_cache_hit else "stt") + "+pinned"
    caps, meta = transcribe_captions(tl, project_dir, use_cache=use_cache)
    return caps, "stt-cache" if meta.words_cache_hit else "stt"


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
        if ac.auto_ducking:
            fa.append(f"[{vi}:a]{fmt},apad,asplit=2[vo][sc]")
            fa.append(f"[{bi}:a]{fmt},volume={ac.bgm_gain_db}dB[bg]")
            fa.append("[bg][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=450:makeup=1[duck]")
        else:   # no sidechain branch: an unbounded VO split into anullsink never reaches EOF on FFmpeg 6.x
            fa.append(f"[{vi}:a]{fmt},apad[vo]")
            fa.append(f"[{bi}:a]{fmt},volume={ac.bgm_gain_db}dB[duck]")
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


def measure_mix(tl: Timeline, project_dir: Path, mix=None, threads: int | None = None) -> dict | None:
    """Loudnorm pass 1 on the audio mix only (no video). Returns loudnorm's measured_* JSON, or None without audio."""
    import json as _json
    import subprocess
    L = profiles.loudness(tl)
    meas = f"loudnorm=I={L.target_lufs}:TP={L.true_peak}:LRA={L.lra}:print_format=json"
    args, fa = _mix_inputs(tl, project_dir, mix, 0, meas)
    if fa is None:
        return None
    cmd = [config.FFMPEG, "-hide_banner", "-nostats", *args, *(["-threads", str(threads)] if threads else []),
           "-filter_complex", ";".join(fa), "-map", "[a]", "-f", "null", "-"]
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise EngineError(f"loudness measure failed: {r.stderr[-600:]}")
    txt = r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1]
    try:
        return _json.loads(txt)
    except ValueError as e:
        raise EngineError(f"loudness measure produced no usable JSON: {e}") from None


TP_RATE = 4 * MIX_RATE   # Hz: true-peak limiting runs 4x oversampled (the ebur128 true-peak meter oversamples 4x)
TP_MARGIN_DB = 0.5       # first limiter ceiling under the profile true-peak limit (resampling + AAC overshoot)
TP_STEP_DB = 0.2         # extra headroom per corrective pass on top of the measured true-peak excess


def tp_limiter(ceiling_db: float) -> str:
    """True-peak-aware limiter: oversample to TP_RATE, limit at `ceiling_db` (latency-compensated, so the audio keeps
    its timing and length), back to MIX_RATE. A limiter at MIX_RATE only bounds sample peaks: the peaks between
    samples (what A01 measures) stay above its ceiling."""
    return (f"aresample={TP_RATE},alimiter=limit={10 ** (ceiling_db / 20):.5f}:level=false:latency=1,"
            f"aresample={MIX_RATE}")


def norm_filter(tl: Timeline, m: dict | None) -> str:
    """Loudnorm pass 2 (linear gain from the pass-1 measurement) followed by the true-peak limiter TP_MARGIN_DB
    under TP."""
    L = profiles.loudness(tl)
    base = f"loudnorm=I={L.target_lufs}:TP={L.true_peak}:LRA={L.lra}"
    if m:
        base += (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                 f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    return f"{base},{tp_limiter(L.true_peak - TP_MARGIN_DB)}"


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
    vf, cwd, sub_env = "null", None, None
    extra = {"segments": segments, "threads": threads, "ass": ass}
    if ass is not None:
        vf, cwd, sub_env = subtitle_filter(ass)
    wm_png = out.with_name(out.stem + ".watermark.png")
    pl = watermark.render(tl, wm_png)
    R = profiles.of(tl).render
    pf = R.final_pix_fmt()
    if pf != "yuv420p" and pf not in toolchain.x264_pix_fmts(str(config.FFMPEG)):
        raise EngineError(f"[render] final_bit_depth = {R.final_bit_depth}: {config.FFMPEG} libx264 has no {pf} "
                          f"(needs an ffmpeg built with a multi-bit-depth x264)")
    if pl is None:
        fv = f"{vsrc}{vf},format={pf}[v]"
    else:
        cmd += ["-i", wm_png]
        fv = (f"{vsrc}{vf}[base];[{nv}:v]format=rgba,{config.OVERLAY_YUV_VF}[wm];"
              f"[base][wm]overlay=x={pl.x}:y={pl.y}:format=yuv420,format={pf}[v]")
    if transitions:
        chain, lab = trans.overlay_graph(transitions, nv + (pl is not None), S.fps, vsrc)
        for clip, _ in transitions:
            cmd += ["-i", clip.resolve()]
        fv = chain + ";" + lab + fv[len(vsrc):]
    cmd += ["-filter_complex", vpre + fv, "-map", "[v]", *R.x264("final"), "-threads", str(threads),
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
    sub_env = None
    if ass is not None:
        _, _, sub_env = subtitle_filter(ass)
    try:
        if use_cache and canonical is None:
            key = composite_key(cmd, parts, ass, out, scratch[1], transition_key=transition_key, clips=clips)
        if use_cache and (hit := cache.get_file("composite", key)):
            return hit, True, key
        run(cmd, cwd=cwd, env=sub_env)
    finally:
        for f in scratch:
            f.unlink(missing_ok=True)
    streams.verify(out, tl.total_frames, tl.settings.fps, "composite")
    return (cache.put_file("composite", key, out) if use_cache else out), False, key


def audio_cmd(tl: Timeline, project_dir: Path, out: Path, measured: dict | None, mix=None,
              threads: int | None = None) -> list:
    """ffmpeg argv of the audio pass: ducked BGM under VO (or the [sound] mix), two-pass loudnorm (pass 1 =
    measure_mix), exact sample count, AAC 192k 48 kHz, cut at the video duration."""
    n, fps = tl.total_frames, tl.settings.fps
    project_dir = project_dir.resolve()
    args, fa_audio = _mix_inputs(tl, project_dir, mix, 0, norm_filter(tl, measured))
    if fa_audio is None:
        fa_audio = _audio_graph(tl, None, None, "")
    return [config.FFMPEG, "-v", "error", "-y", *args, "-filter_complex", ";".join(fa_audio), "-map", "[a]",
            *(["-threads", str(threads)] if threads else []),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{n / fps:.6f}", out.resolve()]


def audio_key(tl: Timeline, cmd: list, out: Path, correct: bool) -> str:
    """Key of the audio pass: the command with every input replaced by its sha256 (manifest.cmd_key), the loudness
    correction values when `correct`, and this module's source (enforce_loudness, the cached loudness history)."""
    from .manifest import cmd_key
    L = profiles.loudness(tl)
    return cmd_key("audio", [c for c in cmd if Path(str(c)) != out],
                   {"correct": [L.target_lufs, L.true_peak, L.tolerance_lu, MAX_CORRECTIONS] if correct else None,
                    "code": code_hash([__file__])})


def _audio_plan_key(tl: Timeline, project_dir: Path, out: Path, mix, correct: bool, threads: int | None) -> str:
    """Key of the audio pass before loudnorm pass 1: the command is built with `measured=None`, because the
    measurement is a function of the same inputs (and is stored with the cached loudness history)."""
    return audio_key(tl, audio_cmd(tl, project_dir, out, None, mix, threads), out, correct)


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
                  correct: bool = False, threads: int | None = None):
    """The cached audio pass for these inputs (file copied to `out`), or None. Lets a build skip loudnorm pass 1
    when the audio cache can answer."""
    if not use_cache:
        return None
    out = out.resolve()
    return _cached_master(tl, out, _audio_plan_key(tl, project_dir, out, mix, correct, threads), correct)


def master_audio(tl: Timeline, project_dir: Path, out: Path, measured: dict | None = None, mix=None,
                 use_cache: bool = True, correct: bool = False,
                 threads: int | None = None) -> tuple[Path, bool, str, list, list[dict]]:
    """Audio pass (+ with `correct`, the A01 corrective gain passes on the audio file alone) ->
    (file, cache hit, key, A01 findings, loudness history). Cache kind "audio": a visual change never re-runs it and
    a hit does not run loudnorm pass 1."""
    out = out.resolve()
    key = _audio_plan_key(tl, project_dir, out, mix, correct, threads) if use_cache else ""
    if use_cache and (hit := _cached_master(tl, out, key, correct)) is not None:
        return hit
    if measured is None:
        measured = measure_mix(tl, project_dir, mix, threads)
    cmd = audio_cmd(tl, project_dir, out, measured, mix, threads)
    run(cmd)
    fs, hist = (enforce_loudness(out, tl, threads) if correct
                else ([], [{"pass": "mux", **loudness(out, threads), "gain_db": 0.0}]))
    if use_cache:
        cache.put_file("audio", key, out, ".m4a")
        cache.put_json("audio", key, hist)
    return out, False, key, fs, hist


def mux(tl: Timeline, video: Path, audio_file: Path, out: Path) -> Path:
    """Stream-copy mux of the composite video and the master audio (no re-encode)."""
    run([config.FFMPEG, "-v", "error", "-y", "-i", video, "-i", audio_file, "-map", "0:v:0", "-map", "1:a:0",
         "-c", "copy", "-movflags", "+faststart", out])
    streams.verify(out, tl.total_frames, tl.settings.fps, "final mux")
    streams.verify_level(out, "final mux")
    return out


SUB_NAME = "captions.ass"
SUB_FONTS = "fonts"
SUB_FONTCONFIG = "fonts.conf"


def _vendor_fontconfig(d: Path) -> Path:
    """Fontconfig file that exposes only vendor/fonts/ to libass, so caption burn-in can never resolve a
    system font (e.g. a system Inter on macOS) when a family name exists on both sides."""
    from xml.sax.saxutils import escape
    fonts = config.FONTS_DIR.resolve()
    cache = d / ".fontconfig-cache"
    conf = d / SUB_FONTCONFIG
    conf.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
        f"  <dir>{escape(str(fonts))}</dir>\n"
        f"  <cachedir>{escape(str(cache))}</cachedir>\n</fontconfig>\n")
    return conf


def subtitle_filter(ass: Path) -> tuple[str, Path, dict]:
    """(filter, cwd, env) for burning `ass`: ffmpeg runs in the .ass folder and the filter names only fixed bare
    filenames (no filter-graph escaping of arbitrary paths). The .ass is copied to SUB_NAME if named otherwise;
    SUB_FONTS is a symlink to config.FONTS_DIR, and the returned env points FONTCONFIG_FILE at a fontconfig that
    lists only vendor/fonts/ (pass it to the ffmpeg subprocess so libass never falls back to a system font)."""
    import os
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
    env = {**os.environ, "FONTCONFIG_FILE": str(_vendor_fontconfig(d))}
    return f"subtitles=filename={SUB_NAME}:fontsdir={SUB_FONTS}", d, env


def loudness(path: Path, threads: int | None = None) -> dict:
    """Integrated loudness (LUFS), true peak (dBTP) and loudness range (LU) of a file (ebur128)."""
    import subprocess
    r = subprocess.run([config.FFMPEG, "-hide_banner", "-nostats", *(["-threads", str(threads)] if threads else []),
                        "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = r.stderr[r.stderr.rfind("Summary:"):]
    I = re.search(r"I:\s+(-?[\d.]+) LUFS", txt)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", txt)
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU\b", txt)
    return {"I": float(I.group(1)) if I else None, "TP": float(tp.group(1)) if tp else None,
            "LRA": float(lra.group(1)) if lra else None}


MAX_CORRECTIONS = 3     # corrective gain passes before A01 fails the build


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


def apply_gain(src: Path, dst: Path, gain_db: float, true_peak: float, threads: int | None = None,
               ceiling: float | None = None) -> Path:
    """Corrective pass: audio gain + true-peak limiter at `ceiling` dBTP (default TP_MARGIN_DB under the true-peak
    limit); video stream copied. The copied stream must keep every streams.VIDEO_KEYS parameter, its frame count
    and its duration."""
    af = f"volume={gain_db:.2f}dB,{tp_limiter(true_peak - TP_MARGIN_DB if ceiling is None else ceiling)}"
    if not any(st.get("codec_type") == "video" for st in probe(src)["streams"]):   # audio-only master
        run([config.FFMPEG, "-v", "error", "-y", "-i", src, "-map", "0:a:0", *(["-threads", str(threads)] if threads else []),
             "-af", af, "-c:a", "aac", "-b:a", "192k",
             "-ar", "48000", dst])
        return dst
    run([config.FFMPEG, "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
         *(["-threads", str(threads)] if threads else []),
         "-af", af, "-c:a", "aac", "-b:a", "192k",
         "-ar", "48000", "-movflags", "+faststart", dst])
    if diff := streams.mismatch(streams.params(dst, "v:0"), streams.params(src, "v:0")):
        raise EngineError(f"gain pass changed the copied video stream: {diff}")
    fps = streams.params(src, "v:0")[0]["r_frame_rate"].split("/")
    n = count_frames(src)
    streams.verify(dst, n, round(int(fps[0]) / int(fps[1])), "gain pass")
    return dst


def next_ceiling(ceiling: float, m: dict, max_tp: float) -> float:
    """Limiter ceiling of the next corrective pass after one at `ceiling` measured `m` on the encoded file: lowered
    by the true-peak excess over `max_tp` (the encoder overshoot that survived the limiter) plus TP_STEP_DB."""
    tp = m.get("TP")
    return ceiling if tp is None or tp <= max_tp else ceiling - (tp - max_tp) - TP_STEP_DB


def enforce_loudness(path: Path, tl: Timeline, threads: int | None = None) -> tuple[list, list[dict]]:
    """Measure -> corrective gain pass(es) while outside the profile tolerance -> re-measure. Replaces `path` in place.
    Each pass re-limits the decoded audio with the true-peak limiter. The first runs at the mux pass ceiling: an
    excess on the mux file can come from a codec burst that the re-limit itself removes. After that, every true-peak
    excess measured on the encoded result lowers the ceiling of the next pass (next_ceiling) instead of repeating it.
    Returns (A01 findings of the last measurement, list of measurements {"pass", "I", "TP", "gain_db", "ceiling"})."""
    L = profiles.loudness(tl)
    m = loudness(path, threads)
    hist = [{"pass": "mux", **m, "gain_db": 0.0}]
    ceiling = L.true_peak - TP_MARGIN_DB
    for k in range(MAX_CORRECTIONS):
        fs = loudness_findings(m, L.target_lufs, L.true_peak, L.tolerance_lu)
        if all(f.level == "pass" for f in fs) or m.get("I") is None:
            return fs, hist
        if k:
            ceiling = next_ceiling(ceiling, m, L.true_peak)
        gain = L.target_lufs - m["I"]
        tmp = path.with_suffix(f".gain{k}{path.suffix}")
        apply_gain(path, tmp, gain, L.true_peak, threads, ceiling=ceiling)
        tmp.replace(path)
        m = loudness(path, threads)
        hist.append({"pass": f"correct{k + 1}", **m, "gain_db": round(gain, 2), "ceiling": round(ceiling, 2)})
    return loudness_findings(m, L.target_lufs, L.true_peak, L.tolerance_lu), hist
