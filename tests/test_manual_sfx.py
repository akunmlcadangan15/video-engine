"""Manual timeline SFX cues, and the rumble kind."""
import pytest
from pydantic import ValidationError

from video_engine import mixer, sfx
from video_engine.sound import epidemic
from video_engine.timeline import SFX_KINDS, ManualSfx
from video_engine.util import EngineError


def _with_sfx(cues):
    def mut(d):
        d["profile"] = "geoverum"
        d["audio_chain"] = {"voiceover_path": "assets/tone.wav", "bgm_path": "assets/tone.wav",
                            "bgm_gain_db": -8, "sfx": cues}
    return mut


def test_good_rumble_is_a_kind_with_a_tone_and_a_search(project):
    project()
    assert "rumble" in SFX_KINDS
    assert set(mixer.LOCAL_TONES) == set(SFX_KINDS)
    tone = mixer.local_sfx("rumble")
    assert tone.path.is_file() and tone.provenance["source"] == "local-test" and tone.path.stat().st_size > 0
    args = epidemic.sfx_search_args("rumble")
    assert args["query"]["term"] == "deep rumble" and args["filter"]["duration"]["max"] == 4000


def test_good_manual_kind_and_asset_cues_reach_the_mix(project):
    p, tl = project(_with_sfx([
        {"kind": "rumble", "at_frame": 12, "gain_db": -8},
        {"asset": "assets/tone.wav", "time_s": 0.4, "gain_db": -6, "id": "hit", "max_s": 0.3},
    ]))
    assert "rumble" in sfx.kinds_used(tl)
    mix = mixer.plan(tl, p, [])
    manual = [c for c in mix.cues if c.event == "manual"]
    rumble, hit = manual
    assert rumble.kind == "rumble" and rumble.gain_db == -8 and rumble.frame == 12
    assert rumble.asset_id == mix.sounds[rumble.asset_id].provenance["id"]
    assert hit.kind == "file" and hit.gain_db == -6 and hit.time_s == 0.4 and hit.max_s == 0.3 and hit.asset_id == "hit"
    assert mix.sounds["hit"].path.resolve() == (p / "assets" / "tone.wav").resolve()


def test_bad_manual_cue_needs_one_source_and_one_time():
    with pytest.raises(ValidationError, match="exactly one of kind or asset"):
        ManualSfx(kind="rumble", asset="assets/tone.wav", at_frame=1)
    with pytest.raises(ValidationError, match="exactly one of at_frame or time_s"):
        ManualSfx(kind="rumble")
    with pytest.raises(ValidationError, match="exactly one of at_frame or time_s"):
        ManualSfx(kind="pop", at_frame=1, time_s=0.2)


def test_bad_manual_cue_past_the_timeline(project):
    with pytest.raises(EngineError, match="past the timeline"):
        project(_with_sfx([{"kind": "rumble", "at_frame": 99999, "gain_db": -8}]))
