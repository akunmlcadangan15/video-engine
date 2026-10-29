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
    assert "linear=true" in flt and "measured_I=-20.1" in flt and "alimiter" in flt


# caption word fixes --------------------------------------------------------------------------------------------------
def test_good_caption_fix_multiword():
    ws = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.3} for i, w in enumerate(["bergadir", "Malabi", "Tewas", "di"])]
    from video_engine.timeline import CaptionFix
    out = audio.apply_fixes(ws, [CaptionFix.model_validate({"from": "bergadir Malabi Tewas", "to": "Brigadir Mallaby tewas"})])
    assert [w["word"] for w in out] == ["Brigadir", "Mallaby", "tewas", "di"]
    assert out[0]["start"] == 0.0 and out[2]["end"] == pytest.approx(0.9)


def test_good_words_to_frames(project):
    _, tl = project()
    cw = audio.words_to_captions([{"word": "a", "start": 0.5, "end": 1.0}, {"word": "b", "start": 1.0, "end": 9.0}],
                                 30, tl.total_frames)
    assert (cw[0].start_frame, cw[0].end_frame) == (15, 30) and cw[1].end_frame <= tl.total_frames
