"""Master-audio AAC bitrate: profile `[audio] aac_bitrate` (default 192, range 64-320) and the render override."""
import pytest
from pydantic import ValidationError

from video_engine import audio, profiles


def test_good_default_command_uses_192k(project):
    p, tl = project()
    assert profiles.aac_bitrate(tl) == 192 and profiles.aac_bitrate_arg(tl) == "192k"
    cmd = audio.audio_cmd(tl, p, p / "out.m4a", None)
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    assert "aac_bitrate" not in profiles.load().data.audio.model_dump()


def test_good_render_override_wins_over_the_audio_profile(project, monkeypatch):
    p, tl = project()
    base = profiles.of(tl)
    low = base.data.model_copy(update={"audio": base.audio.model_copy(update={"aac_bitrate": 128})})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile(base.name, low, base.blake3))
    assert profiles.aac_bitrate(tl) == 128
    cmd = audio.audio_cmd(tl, p, p / "out.m4a", None)
    assert cmd[cmd.index("-b:a") + 1] == "128k"
    both = low.model_copy(update={"render": base.render.model_copy(update={"aac_bitrate": 256})})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile(base.name, both, base.blake3))
    assert profiles.aac_bitrate(tl) == 256
    cmd = audio.audio_cmd(tl, p, p / "out.m4a", None)
    assert cmd[cmd.index("-b:a") + 1] == "256k"
    assert profiles.quality_report(tl)["aac_bitrate"] == 256


@pytest.mark.parametrize("value", [63, 321, 0])
def test_bad_bitrate_outside_64_to_320_is_rejected(value):
    with pytest.raises(ValidationError):
        profiles.AudioProfile.model_validate({"target_lufs": -14, "true_peak": -1.5, "lra": 11,
                                              "tolerance_lu": 0.2, "aac_bitrate": value})
    with pytest.raises(ValidationError):
        profiles.RenderProfile.model_validate({"aac_bitrate": value})
