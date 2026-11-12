"""Path-supplied music passes the epidemic licence check when a sidecar or a sound lock names the track and
the file hash. A hash mismatch is rejected. A path with no claim stays project audio."""
import json

import pytest

from video_engine import manifest, mixer
from video_engine.gates.audio_source import a02_audio_source
from video_engine.sound import cache, provenance
from video_engine.util import EngineError


def _geo(d):
    d["profile"] = "geoverum"
    d["segments"] = [d["segments"][0]]   # a still: no render-event SFX, so the mix's only sourced file is the bed
    d["audio_chain"] = {"voiceover_path": "assets/tone.wav", "bgm_path": "assets/tone.wav", "bgm_gain_db": -8}


def _side(path, sha, **over):
    doc = {"source": "epidemic", "id": "track-1", "title": "Copper", "artist": "A", "sha256": sha}
    doc.update(over)
    path.with_suffix(".json").write_text(json.dumps(doc))


def test_good_sidecar_makes_bgm_path_epidemic(project):
    p, tl = project(_geo)
    audio = p / "assets" / "tone.wav"
    sha = manifest.sha256(audio)
    _side(audio, sha)
    got = provenance.for_file(audio, p)
    assert got["source"] == "epidemic" and got["id"] == "track-1"
    assert got["credit"] == cache.credit_line("epidemic", "track-1", "Copper", "A")
    mix = mixer.plan(tl, p, [])
    assert mix.music.provenance["source"] == "epidemic"
    assert [f.level for f in a02_audio_source(tl, mix, require="epidemic")] == ["pass"]


def test_good_lock_entry_without_a_sidecar(project):
    p, tl = project(_geo)
    audio = p / "assets" / "tone.wav"
    sha = manifest.sha256(audio)
    man = p / "out" / "build_manifest.json"
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps({"version": 1, "sounds": {"music": {
        "path": "assets/tone.wav", "sha256": sha,
        "provenance": {"source": "epidemic", "id": "lock-9", "title": "T", "artist": "B", "credit": "kept"},
    }, "sfx": {}}}))
    got = provenance.for_file(audio, p)
    assert got["id"] == "lock-9" and got["credit"] == "kept"
    assert mixer.resolve_music(tl, p, __import__("video_engine.profiles", fromlist=["of"]).of(tl)).provenance["id"] == "lock-9"


def test_good_plain_bgm_path_stays_local_test(project):
    p, tl = project(_geo)
    mix = mixer.plan(tl, p, [])
    assert mix.music.provenance["source"] == "local-test"
    fs = a02_audio_source(tl, mix, require="epidemic")
    assert fs[0].level == "warn" and "local-test" in fs[0].message


def test_bad_sidecar_hash_mismatch_is_rejected(project):
    p, _ = project(_geo)
    audio = p / "assets" / "tone.wav"
    _side(audio, "0" * 64)
    with pytest.raises(EngineError, match="sha256"):
        provenance.for_file(audio, p)


def test_bad_sidecar_and_lock_disagree_on_the_track(project):
    p, _ = project(_geo)
    audio = p / "assets" / "tone.wav"
    sha = manifest.sha256(audio)
    _side(audio, sha, id="side-1")
    man = p / "out" / "build_manifest.json"
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps({"sounds": {"music": {
        "sha256": sha, "provenance": {"source": "epidemic", "id": "lock-2", "title": "T", "artist": "B"},
    }}}))
    with pytest.raises(EngineError, match="does not match"):
        provenance.for_file(audio, p)
