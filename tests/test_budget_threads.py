"""The build budget reaches audio, QA and transitions, with explicit ffmpeg -threads values."""
import importlib.util
from types import SimpleNamespace

import numpy as np

from video_engine import audio, config, qa, transitions


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


def test_good_hyperframes_ffmpeg_wrapper_matches_the_repeatable_params():
    path = config.PKG / "hf" / "ffmpeg_repeatable.py"
    spec = importlib.util.spec_from_file_location("ffmpeg_repeatable", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.REPEATABLE == config.X264_REPEATABLE
    out = mod._with_repeatable(["-c:v", "libx264", "-threads", "8", "-x264-params", "aq-mode=3"])
    assert out[out.index("-threads") + 1] == "1"
    assert out[out.index("-x264-params") + 1].endswith(":" + config.X264_REPEATABLE)
    assert mod._with_repeatable(["-version"]) == ["-version"]
