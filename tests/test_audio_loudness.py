"""A01 loudness delivery gate: -14.0 LUFS +/- 0.2 LU, true peak <= -1.5 dBTP; corrective gain pass; word fixes."""
import subprocess

import pytest

from video_engine import audio


def lv(fs):
    return [f.level for f in fs]


# pure gate --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("I,TP", [(-14.0, -2.0), (-14.2, -1.5), (-13.8, -3.0)])
def test_A01_good(I, TP):
    assert lv(audio.loudness_findings({"I": I, "TP": TP}, -14.0, -1.5, 0.2)) == ["pass"]


@pytest.mark.parametrize("I,TP,why", [(-13.4, -2.0, "outside"), (-14.3, -2.0, "outside"), (-14.0, -1.4, "true peak"),
                                      (None, -2.0, "not measurable")])
def test_A01_bad(I, TP, why):
    fs = audio.loudness_findings({"I": I, "TP": TP}, -14.0, -1.5, 0.2)
    assert "fail" in lv(fs) and any(why in f.message for f in fs)


# measured on real files ---------------------------------------------------------------------------------------------
def _mp4(path, audio_src):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=320x568:rate=30", "-f",
                    "lavfi", "-i", audio_src, "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-b:a", "192k", "-ar", "48000", "-shortest", str(path)], check=True)
    return path


def test_A01_good_quiet_mix_corrected_to_target(project, tmp_path):
    _, tl = project()
    f = _mp4(tmp_path / "quiet.mp4", "sine=frequency=330:sample_rate=48000,volume=-24dB")
    assert audio.loudness(f)["I"] < -20
    fs, hist = audio.enforce_loudness(f, tl)
    assert lv(fs) == ["pass"], hist
    assert abs(hist[-1]["I"] + 14.0) <= 0.2 and hist[-1]["TP"] <= -1.5 and len(hist) >= 2
    assert int(subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                               "stream=nb_read_packets", "-of", "csv=p=0", str(f)], capture_output=True, text=True)
               .stdout.strip()) == 180   # video untouched


def test_A01_good_already_in_tolerance_no_extra_pass(project, tmp_path):
    _, tl = project()
    f = _mp4(tmp_path / "ok.mp4", "sine=frequency=330:sample_rate=48000,volume=-24dB")
    audio.enforce_loudness(f, tl)
    fs, hist = audio.enforce_loudness(f, tl)
    assert lv(fs) == ["pass"] and len(hist) == 1


def test_A01_bad_silence_fails_build_gate(project, tmp_path):
    _, tl = project()
    f = _mp4(tmp_path / "silent.mp4", "anullsrc=r=48000:cl=stereo")
    fs, hist = audio.enforce_loudness(f, tl)
    assert "fail" in lv(fs)


def test_good_two_pass_norm_filter_uses_measurement(project):
    _, tl = project()
    flt = audio.norm_filter(tl, {"input_i": "-20.1", "input_tp": "-6.0", "input_lra": "3.0", "input_thresh": "-30.5",
                                 "target_offset": "0.1"})
    assert "linear=true" in flt and "measured_I=-20.1" in flt and audio.tp_limiter(-1.5 - audio.TP_MARGIN_DB) in flt


# true peak: oversampled limiter, closed-loop ceiling ---------------------------------------------------------------
ISP_TONE = "aevalsrc='sin(PI/2*n+PI/4)':s=48000"   # 12 kHz at fs/4, phase 45 deg: samples -3 dBFS, true peak 0 dBTP


def test_good_gain_pass_limits_inter_sample_peaks(project, tmp_path):
    """A sample-peak limiter at 48 kHz leaves these samples (-3 dBFS) untouched and the true peak at 0 dBTP; the
    oversampled limiter brings the encoded true peak under the limit (project-6 build-11: -1.3 dBTP > -2.0)."""
    _, tl = project()
    f = _mp4(tmp_path / "isp.mp4", ISP_TONE)
    assert audio.loudness(f)["TP"] > -0.5
    dst = audio.apply_gain(f, tmp_path / "isp.lim.mp4", 0.0, -2.0)
    assert audio.loudness(dst)["TP"] <= -2.0


def test_good_tp_limiter_keeps_timing_and_length():
    """latency=1: the limiter's lookahead does not delay the audio or drop its tail (no A/V drift per pass)."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "aevalsrc='if(eq(n,4800),0.9,0)':s=48000:d=0.5",
                        "-af", audio.tp_limiter(-6.0), "-f", "f32le", "-ac", "1", "-"], capture_output=True, check=True)
    x = np.frombuffer(r.stdout, np.float32)
    assert len(x) == 24000 and int(np.abs(x).argmax()) == 4800


def test_good_next_ceiling_lowers_by_measured_excess():
    assert audio.next_ceiling(-2.5, {"TP": -2.1}, -2.0) == -2.5
    assert audio.next_ceiling(-2.5, {"TP": -1.3}, -2.0) == pytest.approx(-2.5 - 0.7 - audio.TP_STEP_DB)


def test_good_correction_passes_lower_the_ceiling_until_tp_passes(project, tmp_path, monkeypatch):
    """An encoder that overshoots the limiter ceiling by 1 dB: each pass lowers the ceiling by the measured excess
    (it never repeats the setting that just failed) until the encoded true peak is under the limit."""
    _, tl = project()
    L = audio.profiles.loudness(tl)
    ceilings = []
    monkeypatch.setattr(audio, "apply_gain", lambda src, dst, g, tp, threads=None, ceiling=None:
                        (ceilings.append(ceiling), dst.write_bytes(b""))[1])
    monkeypatch.setattr(audio, "loudness",
                        lambda p, threads=None: {"I": L.target_lufs, "LRA": 1.0,
                                                 "TP": (ceilings[-1] if ceilings else L.true_peak + 1.5) + 1.0})
    f = tmp_path / "a.m4a"
    f.write_bytes(b"")
    fs, hist = audio.enforce_loudness(f, tl)
    assert lv(fs) == ["pass"], hist
    assert ceilings[0] == L.true_peak - audio.TP_MARGIN_DB and all(b < a for a, b in zip(ceilings, ceilings[1:]))
    assert hist[-1]["TP"] <= L.true_peak and [h["ceiling"] for h in hist[1:]] == [round(c, 2) for c in ceilings]


# caption word fixes --------------------------------------------------------------------------------------------------
def test_good_caption_fix_multiword():
    ws = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.3} for i, w in enumerate(["bergadir", "Malabi", "Tewas", "di"])]
    from video_engine.timeline import CaptionFix
    out = audio.apply_fixes(ws, [CaptionFix.model_validate({"from": "bergadir Malabi Tewas", "to": "Brigadir Mallaby tewas"})])
    assert [w["word"] for w in out] == ["Brigadir", "Mallaby", "tewas", "di"]
    assert out[0]["start"] == 0.0 and out[2]["end"] == pytest.approx(0.9)


def test_good_caption_fix_blank_to_removes_the_words():
    ws = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.3} for i, w in enumerate(["eh", "Surabaya", "eh", "eh"])]
    from video_engine.timeline import CaptionFix
    out = audio.apply_fixes(ws, [CaptionFix.model_validate({"from": "eh", "to": ""})])   # was ZeroDivisionError
    assert [w["word"] for w in out] == ["Surabaya"] and out[0]["start"] == pytest.approx(0.3)


@pytest.mark.parametrize("pattern", ["", "   "])
def test_bad_caption_fix_blank_from_is_rejected(pattern):
    from pydantic import ValidationError

    from video_engine.timeline import CaptionFix
    with pytest.raises(ValidationError, match="at least one word"):   # was an endless loop in apply_fixes
        CaptionFix.model_validate({"from": pattern, "to": "x"})
    ws = [{"word": "Halo", "start": 0.0, "end": 0.4}]
    blank = CaptionFix.model_construct(match=pattern, to="x")       # the function itself does not loop either
    assert audio.apply_fixes(ws, [blank]) == ws


def test_good_words_to_frames(project):
    _, tl = project()
    cw = audio.words_to_captions([{"word": "a", "start": 0.5, "end": 1.0}, {"word": "b", "start": 1.0, "end": 9.0}],
                                 30, tl.total_frames)
    assert (cw[0].start_frame, cw[0].end_frame) == (15, 30) and cw[1].end_frame <= tl.total_frames


# audio cache --------------------------------------------------------------------------------------------------------
def test_good_master_audio_cache_hit_skips_loudness_pass1(project, tmp_path, monkeypatch):
    p, tl = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False}))
    first = audio.master_audio(tl, p, tmp_path / "a.m4a", use_cache=True, correct=True)
    assert not first[1] and first[0].is_file()
    monkeypatch.setattr(audio, "measure_mix", lambda *a, **k: pytest.fail("loudnorm pass 1 ran on an audio cache hit"))
    second = audio.master_audio(tl, p, tmp_path / "b.m4a", use_cache=True, correct=True)
    assert second[1] and second[2] == first[2] and second[0].read_bytes() == first[0].read_bytes()
    assert audio.cached_master(tl, p, tmp_path / "c.m4a", use_cache=True, correct=True) is not None


def test_good_build_skips_measure_mix_when_the_audio_cache_hits(project, tmp_path, monkeypatch):
    from video_engine import build

    class Rep:
        ok, timings, subtimings, fixes, ai, caption_y_overrides, findings = True, {}, {}, [], None, [], []

        def summary(self):
            return "preflight: OK (stub)"

    def all_native(d):
        d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": False})
        d["segments"][2] = {"seg_index": 2, "engine_type": "NATIVE_RUST_FFMPEG", "start_frame": 120, "end_frame": 180,
                            "source_video": "assets/clip_1080x1920_30.mp4", "asset_id": "clip"}
    p, _ = project(all_native)
    monkeypatch.setattr(build, "run_preflight", lambda *a, **k: Rep())
    assert build.build(p).ok
    monkeypatch.setattr(build.audio, "measure_mix",
                        lambda *a, **k: pytest.fail("build measured the mix despite an audio cache hit"))
    r = build.build(p)
    assert r.ok and r.final.is_file()


def test_bad_measure_mix_without_json_is_an_engine_error(project, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from video_engine.util import EngineError
    _, tl = project()
    monkeypatch.setattr(audio, "_mix_inputs", lambda *a, **k: ([], ["anull"]))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="no json"))
    with pytest.raises(EngineError, match="no usable JSON"):
        audio.measure_mix(tl, Path("/tmp"))
