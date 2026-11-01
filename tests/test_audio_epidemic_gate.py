"""Stage 2 audio source gate: Geoverum accepts only Epidemic Sound music/SFX and appends its credits."""
from pathlib import Path

from test_style_gates import layout

from video_engine import profiles
from video_engine.gates.audio_source import a02_audio_source
from video_engine.mixer import Mix, Resolved
from video_engine.sound import cache
from video_engine.timeline import SfxCue


def _prov(source: str, id_: str = "id-1") -> dict:
    return {"source": source, "id": id_, "title": "T", "artist": "A",
            "credit": "line" if source == "epidemic" else ""}


def _cue(kind: str = "pop") -> SfxCue:
    return SfxCue(time_s=0.0, kind=kind, gain_db=-12, asset_id=f"id-{kind}", event="pin", frame=0)


def _mix(music_source: str | None, sfx_source: str) -> Mix:
    music = Resolved(Path("m.wav"), _prov(music_source, "music")) if music_source else None
    s = Resolved(Path("s.wav"), _prov(sfx_source, "id-pop"))
    return Mix(music, -10.0, None, 0.0, 0.0, cues=[_cue()], sounds={"id-pop": s}, kinds={"id-pop": "pop"})


def test_good_geoverum_requires_epidemic_and_default_does_not(project):
    _, tl = project(layout(("map", 10, None)))
    assert profiles.of(tl).audio.require_source == "epidemic"
    _, tl0 = project(layout(("map", 10, None), profile="default"))
    assert profiles.of(tl0).audio.require_source is None


def test_bad_non_epidemic_music_or_sfx_fails_a02(project):
    _, tl = project(layout(("map", 10, None)))
    assert [f.level for f in a02_audio_source(tl, _mix("epidemic", "epidemic"))] == ["pass"]
    assert [f.level for f in a02_audio_source(tl, _mix("local-test", "epidemic"))] == ["fail"]
    assert [f.level for f in a02_audio_source(tl, _mix("epidemic", "local-test"))] == ["fail"]
    fs = a02_audio_source(tl, _mix("local-test", "local-test"))
    assert fs[0].level == "fail" and "local-test" in fs[0].message


def test_good_local_test_tones_exist_only_as_a_test_fallback(project):
    _, tl = project(layout(("map", 10, None)))
    r = cache.resolve("pop", profiles.of(tl))
    assert r.provenance["source"] == cache.LOCAL_TEST
    assert [f.level for f in a02_audio_source(tl, _mix(None, r.provenance["source"]))] == ["fail"]


def test_good_epidemic_credits_carry_the_track_id(project, tmp_path):
    project(layout(("map", 10, None)))
    d = tmp_path / "credits"
    d.mkdir(exist_ok=True)
    (d / "credits.txt").write_text("Kredit uji\n")
    prov = _prov("epidemic", "es-123")
    prov["credit"] = cache.credit_line("epidemic", "es-123", "T", "A")
    added = cache.append_credits(d, [prov, _prov("local-test", "local")])
    text = (d / "credits.txt").read_text()
    assert added == [prov["credit"]]
    assert cache.CREDITS_HEADER in text and "es-123" in text and "local" not in text
    assert "Music/SFX" not in text          # the header is the existing Audio (Epidemic Sound) line
