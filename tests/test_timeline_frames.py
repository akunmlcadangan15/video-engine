import pytest
from pydantic import ValidationError

import hypoverum_core_rs as core
from video_engine import timeline


@pytest.mark.parametrize("s,fps,f", [(8.0, 30, 240), (0.5, 30, 15), (1 / 3, 30, 10), (2.0, 25, 50), (0.0166, 30, 0)])
def test_good_sec_to_frame_is_round(s, fps, f):
    assert timeline.sec_to_frame(s, fps) == f == core.sec_to_frame(s, fps)


def test_good_ass_time_is_frame_exact():
    # start = frame 3 at 30 fps = 0.10 s exactly (ASS centiseconds)
    ass, ev = core.compile_ass([("Halo", 3, 33, False)], 1080, 1920, 30, None, None)
    assert "Dialogue: 1,0:00:00.10," in ass and ev[0][0] == 3


def test_bad_segment_end_before_start(project):
    with pytest.raises(ValidationError, match="end_frame"):
        project(lambda d: d["segments"][0].update(end_frame=0))


def test_bad_native_needs_one_source(project):
    def mut(d):
        d["segments"][0]["source_video"] = "assets/clip_1080x1920_30.mp4"
    with pytest.raises(ValidationError, match="exactly one"):
        project(mut)


def test_bad_unknown_field_rejected(project):
    with pytest.raises(ValidationError):
        project(lambda d: d["segments"][0].update(speed=2))


def test_good_total_frames(project):
    _, tl = project()
    assert tl.total_frames == 180 and tl.segments[2].n_frames == 60
