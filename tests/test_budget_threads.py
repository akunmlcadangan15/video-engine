"""The build budget reaches audio, QA, transitions and whisper, with explicit ffmpeg -threads values."""
from types import SimpleNamespace

import numpy as np

from video_engine import audio, qa, resources, transitions


def test_good_budget_exposes_whisper_threads_within_its_cap():
    assert resources.plan(2).whisper_threads() == 2
    assert resources.plan(8).whisper_threads() == 8
    assert resources.plan(64).whisper_threads() == 8


def test_good_audio_command_carries_the_budget_thread_count(project):
    p, tl = project()
    cmd = audio.audio_cmd(tl, p, p / "out.m4a", None, threads=3)
    assert cmd[cmd.index("-threads") + 1] == "3"


def test_good_qa_sample_frames_carries_the_budget_thread_count(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(stdout=b"")
    monkeypatch.setattr(qa.subprocess, "run", fake)
    qa.sample_frames(qa.Path("x.mp4"), 64, 96, threads=2)
    assert seen["cmd"][seen["cmd"].index("-threads") + 1] == "2"


def test_good_transition_clip_and_decode_carry_the_budget_thread_count(monkeypatch, tmp_path):
    cmds = []

    def fake(cmd, **kw):
        cmds.append(cmd)
        return SimpleNamespace(stdout=b"", returncode=0)
    monkeypatch.setattr(transitions.subprocess, "run", fake)
    frame = np.zeros((4, 4, 3), np.uint8)
    transitions.write_clip([frame], tmp_path / "c.mkv", 30, threads=3)
    assert cmds[0][cmds[0].index("-threads") + 1] == "3"
    cmds.clear()
    transitions.decode(tmp_path / "none.mp4", 0, 0, 4, 4, threads=3)
    assert cmds[0][cmds[0].index("-threads") + 1] == "3"


def test_good_captions_for_passes_the_whisper_thread_count(project, monkeypatch):
    p, tl = project(lambda d: d.update(audio_chain={"voiceover_path": "assets/tone.wav", "auto_captions": True}))
    seen = {}

    def fake(*a, **k):
        seen.update(k)
        return [], SimpleNamespace(words_cache_hit=False)
    monkeypatch.setattr(audio, "transcribe_captions", fake)
    audio.captions_for(tl, p, threads=5)
    assert seen["threads"] == 5
