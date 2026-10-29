"""Exact, repeatable audio mixes: the VO + BGM sidechain graph keeps both sources unbounded through the compressor and
cuts the mixed signal to the exact sample count before loudness; the [sound] mixer graph cuts its sum the same way."""
import hashlib
import re
import subprocess

import pytest

from video_engine import audio, mixer
from video_engine.timeline import SfxCue

SR = audio.MIX_RATE


def _ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], check=True)


def _sidechain(project, mut=None, vo_s=4.0, bg_s=0.7):
    """Default-profile project with an mp3 VO and a short looped mp3 BGM (44.1 kHz, so it is resampled)."""
    p, tl = project(mut)
    _ff("-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=48000:duration={vo_s}", "-c:a", "libmp3lame",
        p / "assets" / "vo_sc.mp3")
    _ff("-f", "lavfi", "-i", f"sine=frequency=500:sample_rate=44100:duration={bg_s}", "-c:a", "libmp3lame",
        p / "assets" / "bg_sc.mp3")
    ac = tl.audio_chain.model_copy(update={"voiceover_path": "assets/vo_sc.mp3", "bgm_path": "assets/bg_sc.mp3",
                                           "auto_ducking": True, "bgm_gain_db": -8.0})
    return p, tl.model_copy(update={"audio_chain": ac})


def _master_graph(tl, vi, bi, norm):
    """The VO + BGM graph as master 530df9b built it: both compressor inputs trimmed before sidechaincompress."""
    ac = tl.audio_chain
    dur = tl.total_frames / tl.settings.fps
    tail = f"aresample=48000,apad,atrim=0:{dur:.6f},asetpts=N/SR/TB"
    pre = f"apad,atrim=0:{dur:.6f}"
    norm = f",{norm}" if norm else ""
    return [f"[{vi}:a]aresample=48000,aformat=channel_layouts=stereo,{pre},asplit=2[vo][sc]",
            f"[{bi}:a]aresample=48000,aformat=channel_layouts=stereo,volume={ac.bgm_gain_db}dB,atrim=0:{dur:.6f}[bg]",
            "[bg][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=450:makeup=1[duck]",
            f"[vo][duck]amix=inputs=2:duration=first:normalize=0{norm},{tail}[a]"]


def _packets(fa, vo_n=None, bg_n=None):
    """Same graph with the VO and BGM re-packetized (asetnsamples) right after the format stage."""
    out = list(fa)
    for i, n in ((0, vo_n), (1, bg_n)):
        if n:
            out[i] = out[i].replace("aformat=channel_layouts=stereo,", f"aformat=channel_layouts=stereo,asetnsamples=n={n},", 1)
    return out


def _pcm(p, tl, fa, label="a", args=None):
    args = args if args is not None else audio._audio_inputs(tl, p, 0)[0]
    r = subprocess.run(["ffmpeg", "-v", "error", *map(str, args), "-filter_complex", ";".join(fa), "-map", f"[{label}]",
                        "-f", "s16le", "-ac", "2", "-ar", str(SR), "-"], capture_output=True, check=True, timeout=120)
    return r.stdout


def _md5(b):
    return hashlib.md5(b).hexdigest()


def _tail_peak(pcm, seconds=0.04):
    import numpy as np
    x = np.frombuffer(pcm, dtype=np.int16)
    return int(np.abs(x[-int(seconds * SR) * 2:]).max())


def _trims_before_compressor(fa):
    """True when a chain whose output feeds sidechaincompress contains an atrim."""
    comp = next(c for c in fa if "sidechaincompress" in c)
    ins = set(re.findall(r"\[(\w+)\]", comp.split("sidechaincompress")[0]))
    outs = lambda c: set(re.findall(r"\[(\w+)\]", re.search(r"((?:\[\w+\])+)$", c).group(1)))   # noqa: E731
    return any("atrim" in c for c in fa if c is not comp and outs(c) & ins)


# -------------------------------------------------------------------------------------------- graph structure
def test_good_sidechain_graph_cuts_after_the_mix_and_before_loudness(project):
    p, tl = _sidechain(project)
    fa = audio._audio_graph(tl, 0, 1, audio.norm_filter(tl, None))
    assert not _trims_before_compressor(fa)
    last = fa[-1]
    assert last.index("amix") < last.index(f"atrim=end_sample={audio.mix_samples(tl)}") < last.index("loudnorm")


def test_bad_master_graph_trims_before_the_compressor(project):
    p, tl = _sidechain(project)
    fa = _master_graph(tl, 0, 1, audio.norm_filter(tl, None))
    assert _trims_before_compressor(fa)
    assert "atrim" not in fa[-1][:fa[-1].index("loudnorm")]       # its mix reaches loudness uncut


# ------------------------------------------------------------------------------------------ exact sample count
@pytest.mark.parametrize("frames", [180, 181, 199])
def test_good_every_audio_path_has_the_exact_sample_count(project, frames):
    def mut(d):
        d["segments"][-1]["end_frame"] = frames
    p, tl = _sidechain(project, mut, vo_s=2.0)
    n = audio.mix_samples(tl)
    assert n == round(frames / 30 * SR)
    norm = audio.norm_filter(tl, None)
    vo_only = tl.model_copy(update={"audio_chain": tl.audio_chain.model_copy(update={"bgm_path": None})})
    bg_only = tl.model_copy(update={"audio_chain": tl.audio_chain.model_copy(update={"voiceover_path": None})})
    no_duck = tl.model_copy(update={"audio_chain": tl.audio_chain.model_copy(update={"auto_ducking": False})})
    for t in (tl, vo_only, bg_only, no_duck):
        args, vi, bi = audio._audio_inputs(t, p, 0)
        for nm in ("", norm):
            assert len(_pcm(p, t, audio._audio_graph(t, vi, bi, nm), args=args)) == n * 4
    assert len(_pcm(p, tl, audio._audio_graph(tl, None, None, ""), args=[])) == n * 4


def test_bad_master_graph_loses_the_music_tail_behind_correct_padding(project):
    p, tl = _sidechain(project)
    n = audio.mix_samples(tl)
    bad, good = _master_graph(tl, 0, 1, ""), audio._audio_graph(tl, 0, 1, "")
    duck = _pcm(p, tl, bad[:3] + ["[vo]anullsink"], label="duck")
    assert len(duck) // 4 < n                                     # compressor output ends early
    bad_pcm, good_pcm = _pcm(p, tl, bad), _pcm(p, tl, good)
    assert len(bad_pcm) == len(good_pcm) == n * 4                  # final padding hides it in the length
    assert _tail_peak(bad_pcm) == 0 and _tail_peak(good_pcm) > 1000   # music audible to the last sample


# ----------------------------------------------------------------------------------------------- repeatability
def test_good_mix_is_identical_across_repeats_and_packetizations(project):
    p, tl = _sidechain(project)
    fa = audio._audio_graph(tl, 0, 1, audio.norm_filter(tl, None))
    hs = {_md5(_pcm(p, tl, fa)) for _ in range(5)}
    hs |= {_md5(_pcm(p, tl, _packets(fa, v, b))) for v, b in ((1024, 512), (4096, 48000), (30000, 2000))}
    assert len(hs) == 1


def test_bad_master_graph_hash_depends_on_packetization(project):
    p, tl = _sidechain(project)
    fa = _master_graph(tl, 0, 1, "")
    hs = {_md5(_pcm(p, tl, _packets(fa, v, b))) for v, b in ((1024, 512), (4096, 48000))}
    assert len(hs) == 2


# --------------------------------------------------------------------------------------- [sound] mixer graph
def _sound_mix(project, tmp_path, cue_s):
    p, tl = project(lambda d: d.update(profile="geoverum"))
    music = tmp_path / "music.mp3"
    _ff("-f", "lavfi", "-i", "sine=frequency=220:sample_rate=44100:duration=0.9", "-c:a", "libmp3lame", music)
    cue = SfxCue(time_s=cue_s, kind="pop", gain_db=0.0, asset_id="t:pop", event="pin", frame=round(cue_s * 30))
    m = mixer.Mix(mixer.Resolved(music, {}), -8.0, mixer.duck_expr([(1.0, 2.0)], -12, 0.1, 0.1), 0.3, 1.0,
                  [cue], {"t:pop": mixer.local_sfx("pop")})
    return p, tl, m


def _sound_pcm(p, tl, m, norm):
    args, vi, mi, si = mixer.inputs(tl, p, m, 0)
    return _pcm(p, tl, mixer.graph(tl, m, vi, mi, si, norm), args=args)


def test_good_sound_mix_exact_and_repeatable(project, tmp_path):
    p, tl, m = _sound_mix(project, tmp_path, 3.0)
    norm = audio.norm_filter(tl, audio.measure_mix(tl, p, m))
    outs = [_sound_pcm(p, tl, m, norm) for _ in range(5)]
    assert {len(o) for o in outs} == {audio.mix_samples(tl) * 4} and len({_md5(o) for o in outs}) == 1
    last = mixer.graph(tl, m, *mixer.inputs(tl, p, m, 0)[1:], norm)[-1]
    assert last.index("amix") < last.index(f"atrim=end_sample={audio.mix_samples(tl)}") < last.index("loudnorm")


def test_bad_cue_past_the_end_does_not_reach_loudness(project, tmp_path):
    p, tl, m = _sound_mix(project, tmp_path, 3.0)
    late = mixer.Mix(m.music, m.music_gain_db, m.duck, m.fade_in_s, m.fade_out_s,
                     [m.cues[0].model_copy(update={"time_s": tl.total_frames / 30 + 0.2})], m.sounds)
    none = mixer.Mix(m.music, m.music_gain_db, m.duck, m.fade_in_s, m.fade_out_s, [], {})
    assert audio.measure_mix(tl, p, late) == audio.measure_mix(tl, p, none)   # loudness sees only the exact span
    assert len(_sound_pcm(p, tl, late, "")) == audio.mix_samples(tl) * 4
