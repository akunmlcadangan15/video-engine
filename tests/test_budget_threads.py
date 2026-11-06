"""The build budget reaches audio, QA and transitions, with explicit ffmpeg -threads values."""
from types import SimpleNamespace

import numpy as np

from video_engine import audio, qa, transitions


def test_good_audio_command_carries_the_budget_thread_count(project):
    p, tl = project()
    cmd = audio.audio_cmd(tl, p, p / "out.m4a", None, threads=3)
    assert cmd[cmd.index("-threads") + 1] == "3"


def test_good_qa_sample_frames_carries_the_budget_thread_count(monkeypatch):
    import io
    seen = {}

    class Proc:
        stdout, stderr = io.BytesIO(b""), io.BytesIO(b"")

        def wait(self):
            return 0

        def poll(self):
            return 0

        def kill(self):
            pass

    def fake(cmd, **kw):
        seen["cmd"] = cmd
        return Proc()
    monkeypatch.setattr(qa.subprocess, "Popen", fake)
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
