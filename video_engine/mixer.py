"""Mixer for profiles with a [sound] table: voiceover + music with keyframed ducking under the voiceover + SFX
cues (sfx.cues) at their own gain, summed before the two-pass loudnorm and limiter of audio.final_mux.

Sound files come through the resolver adapter (resolve_sfx / resolve_music): `video_engine.sound.cache`
(resolve(kind, profile), resolve_music(brief, profile) -> ResolvedSound with .path and .provenance
{source, id, title, artist, credit}) when that package is installed; without it, local test audio with
provenance source "local-test": generated tones per SFX kind and the project's audio_chain.bgm_path as music.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import cache as engine_cache, config, profiles, sfx
from .timeline import CaptionWord, SfxCue, Timeline
from .util import EngineError

LOCAL_SOURCE = "local-test"
LOCAL_VERSION = 1
CUE_FADE_S = 0.04      # fade-out at a cue's max_s cap
PEAK_SR = 48000
# generated local test tones (lavfi, 48 kHz mono, deterministic seeds)
LOCAL_TONES = {
    "pop": "sine=f=880:d=0.09:r=48000,afade=t=out:st=0.01:d=0.08",
    "tick": "sine=f=2400:d=0.03:r=48000,afade=t=out:st=0.005:d=0.025",
    "click": "anoisesrc=d=0.012:c=white:seed=11:a=0.9:r=48000,afade=t=out:d=0.012",
    "beep": "sine=f=1000:d=0.16:r=48000,afade=t=in:d=0.005,afade=t=out:st=0.13:d=0.03",
    "counter_tick": "sine=f=3200:d=0.014:r=48000,afade=t=out:d=0.014",
    "whoosh": "anoisesrc=d=0.6:c=pink:seed=7:a=0.8:r=48000,bandpass=f=900:w=1200,afade=t=in:d=0.32,"
              "afade=t=out:st=0.32:d=0.28",
    "impact": "sine=f=55:d=0.8:r=48000,afade=t=out:st=0.03:d=0.77",
    "riser": "anoisesrc=d=1.2:c=white:seed=5:a=0.6:r=48000,highpass=f=1500,afade=t=in:d=1.15",
}


@dataclass
class Resolved:
    path: Path
    provenance: dict     # source, id, title, artist, credit


# ---------------------------------------------------------------------------------------------------- adapter

def _sound_cache():
    """The sound package's resolver module, or None when the package is not installed."""
    try:
        from .sound import cache
    except ModuleNotFoundError as e:
        if e.name and e.name.startswith(__package__ + ".sound"):
            return None
        raise
    return cache


def _wrap(r) -> Resolved:
    return Resolved(Path(r.path), dict(r.provenance))


def local_sfx(kind: str) -> Resolved:
    d = config.CACHE_DIR / "local_test_sfx" / f"v{LOCAL_VERSION}"
    f = engine_cache.acquire(d / f"{kind}.wav")
    if not f.is_file():
        d.mkdir(parents=True, exist_ok=True)
        tmp = engine_cache.acquire(d / f"{kind}.tmp.wav")
        r = subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", LOCAL_TONES[kind],
                            "-c:a", "pcm_s16le", "-bitexact", str(tmp)], capture_output=True, text=True)
        if r.returncode != 0:
            raise EngineError(f"local test tone '{kind}': {r.stderr[-300:]}")
        tmp.replace(f)
    return Resolved(f, {"source": LOCAL_SOURCE, "id": f"{LOCAL_SOURCE}:{kind}", "title": f"{kind} (generated tone)",
                        "artist": "video-engine", "credit": "generated test tone, not for publication"})


def resolve_sfx(kind: str, profile) -> Resolved:
    cache = _sound_cache()
    if cache is None:
        return local_sfx(kind)
    r = cache.resolve(kind, profile)
    if r is None:
        raise EngineError(f"sound resolver has no file for SFX kind '{kind}'")
    return _wrap(r)


def resolve_music(tl: Timeline, project_dir: Path, profile) -> Resolved | None:
    """Music: the resolver's pick for audio_chain.music_brief, else audio_chain.bgm_path (local), else None."""
    ac = tl.audio_chain
    cache = _sound_cache()
    if ac.music_brief is not None and cache is not None:
        r = cache.resolve_music(ac.music_brief.model_dump(), profile)
        if r is None:
            raise EngineError(f"sound resolver has no music for brief {ac.music_brief.model_dump()}")
        return _wrap(r)
    if ac.bgm_path:
        return Resolved(project_dir / ac.bgm_path, {"source": LOCAL_SOURCE, "id": ac.bgm_asset_id or ac.bgm_path,
                                                    "title": Path(ac.bgm_path).stem, "artist": "",
                                                    "credit": "project audio_chain.bgm_path"})
    return None


@lru_cache(maxsize=256)
def _peak(path: str, mtime: float) -> float:
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", path, "-ac", "1", "-ar", str(PEAK_SR), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise EngineError(f"{path}: not a decodable audio file")
    x = np.frombuffer(r.stdout, dtype=np.float32)
    return round(int(np.argmax(np.abs(x))) / PEAK_SR, 4)


def peak_s(p: Path) -> float:
    """Time of the loudest sample of an audio file."""
    return _peak(str(p), p.stat().st_mtime)


# ---------------------------------------------------------------------------------------------------- plan

@dataclass
class Mix:
    music: Resolved | None
    music_gain_db: float
    duck: str | None                 # ffmpeg volume expression of t (keyframed duck); None = no ducking
    fade_in_s: float
    fade_out_s: float
    cues: list[SfxCue] = field(default_factory=list)
    sounds: dict[str, Resolved] = field(default_factory=dict)   # asset_id -> file
    kinds: dict[str, str] = field(default_factory=dict)         # asset_id -> SFX kind
    locked: bool = False                                        # files taken from out/build_manifest.json

    def report(self) -> dict:
        return {"music": self.music.provenance if self.music else None,
                "music_file": str(self.music.path) if self.music else None,
                "ducking": "keyframed" if self.duck else "off",
                "sfx": {a: r.provenance for a, r in self.sounds.items()},
                "cues": [c.model_dump(mode="json") for c in self.cues]}


def speech_spans(words: list[CaptionWord], fps: int, merge_s: float) -> list[tuple[float, float]]:
    """Voiceover speech intervals (s) from the word timestamps; gaps up to merge_s are bridged."""
    spans: list[list[float]] = []
    for w in sorted(words, key=lambda w: w.start_frame):
        a, b = w.start_frame / fps, w.end_frame / fps
        if spans and a - spans[-1][1] <= merge_s:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in spans]


def duck_expr(spans: list[tuple[float, float]], duck_db: float, attack_s: float, release_s: float) -> str | None:
    """Music gain over t: 1 outside speech, 10^(duck_db/20) inside, linear ramps of attack_s before each span and
    release_s after it."""
    if not spans:
        return None
    g = 10 ** (duck_db / 20)
    terms = [f"clip(min((t{-(a - attack_s):+.3f})/{attack_s:.3f},({b + release_s:.3f}-t)/{release_s:.3f}),0,1)"
             for a, b in spans]
    m = terms[0]
    for x in terms[1:]:
        m = f"max({m},{x})"
    return f"1-{1 - g:.6f}*{m}"


def plan(tl: Timeline, project_dir: Path, words: list[CaptionWord], lock: dict | None = None) -> Mix | None:
    """Mix plan of a profile with [sound]; None for other profiles (audio.final_mux keeps its BGM sidechain mix).
    lock: manifest.locked_sounds(...) -> those exact files, the sound resolver is not asked."""
    prof = profiles.of(tl)
    sp = prof.sound
    if sp is None:
        return None
    ac = tl.audio_chain
    sounds: dict[str, Resolved] = {}
    kinds: dict[str, str] = {}
    by_kind: dict[str, tuple[str, float]] = {}
    for kind in sfx.kinds_used(tl):
        r = Resolved(*lock["sfx"][kind]) if lock else resolve_sfx(kind, prof)
        aid = str(r.provenance.get("id") or r.path.name)
        sounds[aid] = r
        kinds[aid] = kind
        by_kind[kind] = (aid, peak_s(r.path))
    spans = speech_spans(words, tl.settings.fps, sp.duck_merge_s) if ac.voiceover_path and ac.auto_ducking else []
    if lock:
        music = Resolved(*lock["music"]) if lock["music"] else None
    else:
        music = resolve_music(tl, project_dir, prof)
    return Mix(music, ac.bgm_gain_db,
               duck_expr(spans, sp.duck_db, sp.duck_attack_s, sp.duck_release_s), sp.fade_in_s, sp.fade_out_s,
               sfx.cues(tl, by_kind.__getitem__), sounds, kinds, bool(lock))


# ---------------------------------------------------------------------------------------------------- graph

def inputs(tl: Timeline, project_dir: Path, mix: Mix, first_idx: int) -> tuple[list, int | None, int | None, dict]:
    """ffmpeg input args: voiceover, looped music, one input per SFX file. Returns (args, vo idx, music idx,
    {asset_id: idx})."""
    args, idx, vi, mi, si = [], first_idx, None, None, {}
    if tl.audio_chain.voiceover_path:
        args += ["-i", project_dir / tl.audio_chain.voiceover_path]
        vi, idx = idx, idx + 1
    if mix.music:
        args += ["-stream_loop", "-1", "-i", mix.music.path]
        mi, idx = idx, idx + 1
    for aid in dict.fromkeys(c.asset_id for c in mix.cues):
        args += ["-i", mix.sounds[aid].path]
        si[aid], idx = idx, idx + 1
    return args, vi, mi, si


def graph(tl: Timeline, mix: Mix, vi: int | None, mi: int | None, si: dict, norm: str) -> list[str]:
    """Filter chains producing [a]: VO + ducked, faded music + every cue (trim, cap with fade, gain, delay),
    summed without normalisation, cut to the exact sample count, then `norm` and the exact-count guard. Every
    input is bounded by a sample-exact atrim (no filter that buffers across EOF sits before the sum)."""
    from .audio import MIX_RATE, exact_cut, exact_tail
    dur = tl.total_frames / tl.settings.fps
    fmt = f"aresample={MIX_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    cut, tail = exact_cut(tl), exact_tail(tl)
    fa, streams = [], []
    if vi is not None:
        fa.append(f"[{vi}:a]{fmt},apad,{cut}[vo]")
        streams.append("[vo]")
    if mi is not None:
        m = f"[{mi}:a]{fmt},volume={mix.music_gain_db}dB,{cut}"
        if mix.fade_in_s > 0:
            m += f",afade=t=in:d={mix.fade_in_s}"
        if mix.fade_out_s > 0:
            m += f",afade=t=out:st={max(0.0, dur - mix.fade_out_s):.3f}:d={mix.fade_out_s}"
        if mix.duck:
            m += f",volume='{mix.duck}':eval=frame"
        fa.append(m + "[mu]")
        streams.append("[mu]")
    per: dict[str, list[int]] = {}
    for i, c in enumerate(mix.cues):
        per.setdefault(c.asset_id, []).append(i)
    for aid, idxs in per.items():
        outs = "".join(f"[x{i}]" for i in idxs)
        fa.append(f"[{si[aid]}:a]{fmt},asplit={len(idxs)}{outs}")
        for i in idxs:
            c = mix.cues[i]
            ms = round(c.time_s * 1000)
            fa.append(f"[x{i}]atrim=start={c.trim_s}:duration={c.max_s},asetpts=PTS-STARTPTS,"
                      f"afade=t=out:st={max(0.0, c.max_s - CUE_FADE_S):.3f}:d={CUE_FADE_S},volume={c.gain_db}dB,"
                      f"adelay={ms}|{ms}[c{i}]")
            streams.append(f"[c{i}]")
    norm = f",{norm}" if norm else ""
    if not streams:
        return [f"anullsrc=r={MIX_RATE}:cl=stereo,{cut}[a]"]
    if len(streams) == 1:
        fa.append(f"{streams[0]}aresample={MIX_RATE},apad,{cut}{norm},{tail}[a]")
    else:
        fa.append(f"{''.join(streams)}amix=inputs={len(streams)}:duration=longest:normalize=0,"
                  f"apad,{cut}{norm},{tail}[a]")
    return fa
