"""The [sound] mixer: per-cue gain, keyframed music ducking under the voiceover, resolver adapter with local test
audio, loudness of the mixed result."""
import math
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from video_engine import audio, mixer, profiles, sfx
from video_engine.timeline import SfxCue
from video_engine.util import EngineError

FPS = 30
SR = 48000


def _geo(extra=None):
    def mut(d):
        d["profile"] = "geoverum"
        d["audio_chain"] = {"voiceover_path": "assets/tone.wav", "bgm_path": "assets/tone.wav", "bgm_gain_db": -8.0}
        sc = d["segments"][2]["gsap_scene_config"]
        sc["pins"] = [{"label": "A", "x": 0.4, "y": 0.3, "at_frame": 3}]
        sc["regions"] = [{"entity": "Sekutu", "polygon": [[0.1, 0.1], [0.5, 0.1], [0.3, 0.5]], "at_frame": 6}]
        sc["routes"] = [{"points": [[0.1, 0.1], [0.6, 0.6]], "at_frame": 10, "draw_s": 1.0}]
        sc["camera_path"] = [{"at_frame": 0, "focus": [0.3, 0.4], "zoom": 1.5},
                             {"at_frame": 45, "focus": [0.7, 0.4], "zoom": 1.5, "move": "whip"}]
        sc["title"] = "SEKUTU"
        sc["title_at_frame"] = 8
        sc["figures"] = [{"kind": "number", "value": 6, "motion": "counter", "at_frame": 12, "count_s": 0.5,
                          "position": [0.5, 0.6]}]
        if extra:
            extra(d)
    return mut


def _decode(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def _render(tl, mix, out, norm="", project_dir=None):
    args, vi, mi, si = mixer.inputs(tl, project_dir or out.parent, mix, 0)
    fa = mixer.graph(tl, mix, vi, mi, si, norm)
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args), "-filter_complex", ";".join(fa), "-map", "[a]",
                    "-c:a", "pcm_f32le", str(out)], check=True)
    return _decode(out)


def _db(x):
    return 20 * math.log10(max(1e-9, float(np.sqrt(np.mean(np.square(x))))))


# good ---------------------------------------------------------------------------------------------------------------
def test_good_speech_spans_merge_short_gaps(W):
    ws = W(("a", 0, 10), ("b", 15, 30), ("c", 60, 75))
    assert mixer.speech_spans(ws, FPS, 0.5) == [(0.0, 1.0), (2.0, 2.5)]
    assert mixer.speech_spans(ws, FPS, 0.1) == [(0.0, 0.333), (0.5, 1.0), (2.0, 2.5)]


def test_good_duck_expression_ramps_between_unity_and_duck_gain():
    e = mixer.duck_expr([(1.0, 2.0), (4.0, 5.0)], -12.0, 0.2, 0.5)
    f = lambda t: eval(e.replace("clip", "_c"), {"_c": lambda x, a, b: min(max(x, a), b), "t": t,  # noqa: E731
                                                  "min": min, "max": max})
    g = 10 ** (-12 / 20)
    assert f(0.5) == pytest.approx(1) and f(3.2) == pytest.approx(1) and f(6) == pytest.approx(1)
    assert f(1.5) == pytest.approx(g, abs=1e-5) and f(4.5) == pytest.approx(g, abs=1e-5)
    assert f(0.9) == pytest.approx(1 - (1 - g) * 0.5, abs=1e-5) and f(2.25) == pytest.approx(1 - (1 - g) * 0.5, abs=1e-5)
    assert mixer.duck_expr([], -12, 0.2, 0.5) is None


def test_good_mix_ducks_music_under_speech_and_places_cues_at_gain(project, tmp_path):
    p, tl = project(_geo())
    music = tmp_path / "music.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=6:r=48000", str(music)], check=True)
    pop = mixer.local_sfx("pop")
    cue = SfxCue(time_s=4.0, kind="pop", gain_db=-6.0, asset_id="t:pop", event="pin", frame=120)
    loud = SfxCue(time_s=5.0, kind="pop", gain_db=0.0, asset_id="t:pop", event="pin", frame=150)
    m = mixer.Mix(mixer.Resolved(music, {}), 0.0, mixer.duck_expr([(1.0, 2.0)], -12, 0.1, 0.1), 0, 0,
                  [cue, loud], {"t:pop": pop})
    x = _render(tl.model_copy(update={"audio_chain": tl.audio_chain.model_copy(update={"voiceover_path": None})}),
                m, tmp_path / "mix.wav")
    seg = lambda a, b: x[int(a * SR):int(b * SR)]   # noqa: E731
    assert _db(seg(1.3, 1.8)) - _db(seg(0.3, 0.8)) == pytest.approx(-12, abs=0.3)
    on = lambda t0: t0 + np.argmax(np.abs(seg(t0, t0 + 0.2) - seg(t0 - 1.0, t0 - 0.8)) > 0.05) / SR   # noqa: E731
    assert on(4.0) == pytest.approx(4.0, abs=0.004) and on(5.0) == pytest.approx(5.0, abs=0.004)
    # per-cue gain: the 0 dB cue is 6 dB above the -6 dB cue (music removed by subtracting a cue-free second)
    a, b = seg(4.0, 4.08) - seg(3.0, 3.08), seg(5.0, 5.08) - seg(3.0, 3.08)
    assert _db(b) - _db(a) == pytest.approx(6.0, abs=0.5)


def test_good_mixed_result_meets_profile_loudness(project, tmp_path, W):
    p, tl = project(_geo())
    words = W(("a", 0, 40), ("b", 90, 150))
    m = mixer.plan(tl, p, words)
    assert m.cues and m.duck
    measured = audio.measure_mix(tl, p, m)
    x = _render(tl, m, tmp_path / "norm.wav", audio.norm_filter(tl, measured), p)
    assert len(x) == pytest.approx(tl.total_frames / FPS * SR, abs=2)
    out = tmp_path / "norm.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp_path / "norm.wav"), "-c:a", "aac", str(out)], check=True)
    L, got = profiles.loudness(tl), audio.loudness(out)
    assert abs(got["I"] - L.target_lufs) <= L.tolerance_lu and got["TP"] <= L.true_peak


def test_good_adapter_uses_the_sound_package_resolver_when_present(project, monkeypatch, tmp_path):
    p, tl = project(_geo(lambda d: d["audio_chain"].update(
        {"music_brief": {"mood": "tense", "energy": "medium", "bpm_min": 80, "bpm_max": 120, "duration_s": 6.0}})))
    f = mixer.local_sfx("tick").path
    prov = lambda i: {"source": "epidemic", "id": i, "title": "T", "artist": "A", "credit": "C"}   # noqa: E731
    seen = {}

    def resolve(kind, profile):
        seen.setdefault("kinds", []).append(kind)
        return SimpleNamespace(path=f, provenance=prov(f"es-{kind}"))

    def resolve_music(brief, profile):
        seen["brief"] = brief
        return SimpleNamespace(path=f, provenance=prov("es-music"))
    monkeypatch.setattr(mixer, "_sound_cache", lambda: SimpleNamespace(resolve=resolve, resolve_music=resolve_music))
    m = mixer.plan(tl, p, [])
    assert seen["brief"] == {"mood": "tense", "energy": "medium", "bpm_min": 80, "bpm_max": 120, "duration_s": 6.0}
    assert sorted(seen["kinds"]) == sfx.kinds_used(tl)
    assert m.music.provenance["id"] == "es-music" and all(c.asset_id == f"es-{c.kind}" for c in m.cues)
    assert m.report()["sfx"]["es-pop"]["source"] == "epidemic"


def test_good_without_the_sound_package_local_test_audio_is_marked(project, monkeypatch):
    p, tl = project(_geo())
    monkeypatch.setattr(mixer, "_sound_cache", lambda: None)
    m = mixer.plan(tl, p, [])
    assert m.music.provenance["source"] == mixer.LOCAL_SOURCE
    assert {r.provenance["source"] for r in m.sounds.values()} == {mixer.LOCAL_SOURCE}
    a, b = mixer.local_sfx("whoosh").path.read_bytes(), mixer.local_sfx("whoosh").path.read_bytes()
    assert a == b


def test_good_default_profile_has_no_mix_plan(project):
    p, tl = project()
    assert mixer.plan(tl, p, []) is None and sfx.cues(tl, lambda k: ("x", 0)) == []


# bad ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("field, value", [("duck_db", 0), ("duck_db", -50), ("duck_attack_s", 0), ("fade_out_s", 9)])
def test_bad_duck_and_fade_values_are_rejected(field, value):
    with pytest.raises(ValidationError, match=field):
        profiles.SoundProfile.model_validate({"events": {}, "kinds": {}, field: value})


def test_bad_music_brief_with_inverted_bpm_is_rejected(project):
    with pytest.raises(ValidationError):
        project(_geo(lambda d: d["audio_chain"].update(
            {"music_brief": {"mood": "x", "energy": "low", "bpm_min": 120, "bpm_max": 80, "duration_s": 5}})))


def test_bad_resolver_without_a_file_fails_the_plan(project, monkeypatch):
    p, tl = project(_geo())
    monkeypatch.setattr(mixer, "_sound_cache", lambda: SimpleNamespace(resolve=lambda k, pr: None,
                                                                         resolve_music=lambda b, pr: None))
    with pytest.raises(EngineError, match="no file for SFX kind"):
        mixer.plan(tl, p, [])
