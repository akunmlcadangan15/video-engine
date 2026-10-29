"""Pinned speech-to-text: `engine words`, build-time pinning, gate W01 (good + bad)."""
import json
import shutil

import pytest

from video_engine import audio, timeline
from video_engine.cli import main
from video_engine.gates import layer1
from video_engine.util import EngineError

RAW = [{"word": " Surabaya", "start": 0.10, "end": 0.60, "prob": 0.9},
       {"word": " bergadir", "start": 0.70, "end": 1.00, "prob": 0.5},
       {"word": " malabi", "start": 1.00, "end": 1.40, "prob": 0.5},
       {"word": " tewas.", "start": 1.45, "end": 1.90, "prob": 0.9},
       {"word": " akhir", "start": 5.80, "end": 6.40, "prob": 0.9}]   # crosses the 6.0 s timeline end


@pytest.fixture
def fake_whisper(monkeypatch):
    calls = []

    def tw(path, lang="id", model="small", prompt=None, threads=None, use_cache=True):
        calls.append((str(path), use_cache))
        return [dict(w) for w in RAW], use_cache
    monkeypatch.setattr(audio, "transcribe_words", tw)
    return calls


def vo_project(project, extra=None):
    def mut(d):
        d["audio_chain"] = {"voiceover_path": "assets/tone.wav",
                            "caption_fixes": [{"from": "bergadir malabi", "to": "Brigadir Mallaby"}], **(extra or {})}
    return project(mut)


def test_good_pin_writes_words_once_and_W01_passes(project, fake_whisper):
    p, _ = vo_project(project)
    tl, meta = audio.pin_captions(p)
    disk = timeline.load(p)
    assert [w.word for w in disk.captions] == ["Surabaya", "Brigadir", "Mallaby", "tewas.", "akhir"]
    assert (disk.captions[0].start_frame, disk.captions[0].end_frame) == (3, 18)
    assert disk.captions[-1].end_frame == 180 and meta.clipped_words == 1
    assert disk.captions_meta.fixes_blake3 == audio.fixes_digest(disk.audio_chain.caption_fixes)
    assert {f.level for f in layer1.w01_pinned_words(disk, p)} == {"pass"}
    before = (p / "master_timeline.json").read_text()
    audio.pin_captions(p)   # idempotent: same input -> byte-identical file
    assert (p / "master_timeline.json").read_text() == before
    assert not (p / "master_timeline.json.part").exists()


def test_good_build_path_uses_stored_words_without_transcribing(project, monkeypatch, fake_whisper):
    p, _ = vo_project(project)
    audio.pin_captions(p)
    monkeypatch.setattr(audio, "transcribe_words", lambda *a, **k: pytest.fail("re-transcribed"))
    tl = timeline.load(p)
    words, src = audio.captions_for(tl, p, pin=True)
    assert src == "timeline" and words == tl.captions


def test_good_captions_for_pins_when_missing(project, fake_whisper):
    p, tl = vo_project(project)
    tl.segments[0].camera_move.intensity = 5.5          # in-memory gate fix must not be persisted by pinning
    words, src = audio.captions_for(tl, p, pin=True)
    assert src.endswith("+pinned") and len(words) == 5 and tl.captions_meta is not None
    disk = timeline.load(p)
    assert len(disk.captions) == 5 and disk.segments[0].camera_move.intensity == 1.0


def test_good_preflight_path_does_not_write(project, fake_whisper):
    p, tl = vo_project(project)
    before = (p / "master_timeline.json").read_text()
    words, src = audio.captions_for(tl, p, pin=False)
    assert len(words) == 5 and src == "whisper-cache"
    assert (p / "master_timeline.json").read_text() == before


def test_bad_W01_voiceover_changed(project, fake_whisper, assets):
    p, _ = vo_project(project)
    audio.pin_captions(p)
    (p / "assets" / "tone.wav").unlink()
    shutil.copyfile(assets / "still.jpg", p / "assets" / "tone.wav")   # different bytes
    fs = layer1.w01_pinned_words(timeline.load(p), p)
    assert any(f.level == "fail" and "voiceover audio changed" in f.message for f in fs)


def test_bad_W01_fixes_or_fps_changed(project, fake_whisper):
    p, _ = vo_project(project)
    audio.pin_captions(p)
    d = json.loads((p / "master_timeline.json").read_text())
    d["audio_chain"]["caption_fixes"].append({"from": "akhir", "to": "Akhir"})
    d["settings"]["fps"] = 25
    (p / "master_timeline.json").write_text(json.dumps(d))
    msgs = [f.message for f in layer1.w01_pinned_words(timeline.load(p), p) if f.level == "fail"]
    assert any("caption_fixes changed" in m for m in msgs) and any("settings.fps changed" in m for m in msgs)


def test_bad_W01_timeline_grew_past_clipped_words(project, fake_whisper):
    p, _ = vo_project(project)
    audio.pin_captions(p)
    d = json.loads((p / "master_timeline.json").read_text())
    d["segments"][-1]["end_frame"] = 240
    (p / "master_timeline.json").write_text(json.dumps(d))
    fs = layer1.w01_pinned_words(timeline.load(p), p)
    assert any(f.level == "fail" and "timeline grew" in f.message for f in fs)


def test_good_W01_handwritten_and_unpinned(project):
    p, tl = project(lambda d: d.update(captions=[{"word": "Halo", "start_frame": 0, "end_frame": 10}]))
    assert "hand-written" in layer1.w01_pinned_words(tl, p)[0].message
    p, tl = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav"}, captions=[]))
    assert layer1.w01_pinned_words(tl, p)[0].level == "skip"


def test_good_cli_words(project, fake_whisper, capsys):
    p, _ = vo_project(project)
    assert main(["words", str(p), "--retranscribe"]) == 0
    assert fake_whisper[-1][1] is False and "pinned 5 words" in capsys.readouterr().out
    assert len(timeline.load(p).captions) == 5


def test_bad_cli_words_without_voiceover(project, capsys):
    p, _ = project()
    assert main(["words", str(p)]) == 1
    assert "voiceover_path is empty" in capsys.readouterr().err


def test_bad_transcribe_missing_voiceover(project):
    p, tl = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/missing.wav"}))
    with pytest.raises(EngineError, match="voiceover not found"):
        audio.transcribe_captions(tl, p)
