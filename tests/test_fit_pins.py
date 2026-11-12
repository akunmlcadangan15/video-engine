"""Camera key `fit` / `keep_in_frame`: a hold's push-in and pulses stay inside the safe area around the named pins."""
import pytest

from video_engine import hyperframes, timeline
from video_engine.layout import camera_at
from video_engine.units import units
from video_engine.util import EngineError

W, H, MW, MH, N, FPS = 1080, 1920, 3840.0, 1920.0, 180, 30
PULSE = {"zoom": 0.18, "s": 0.8, "gap_s": 0.6, "pan": 0.02, "ease": [0.3, 0.0, 0.15, 1.0],
         "anchor": [0.5, 0.48], "dir": 1}
THEME = {"camera_drift_per_s": 0.06, "camera_start_speed": 0.0, "camera_end_speed": 0.0,
         "camera_pulse": PULSE, "whip_s": 0.45, "whip_blur_px": 0}


def _scene(fit):
    return timeline.GsapScene(
        map_asset="assets/map.jpg",
        pins=[timeline.Pin(label="Kupang", x=0.42, y=0.50), timeline.Pin(label="Darwin", x=0.58, y=0.50)],
        camera_path=[
            timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], zoom=1.0),
            timeline.CameraKey(at_frame=12, focus=[0.5, 0.5], zoom=1.0, fit=fit),
        ])


def _plan(sc):
    return hyperframes.camera_plan(sc, W, H, MW, MH, N, FPS, THEME)


def _box():
    u = units((W, H))
    sx, top, bottom = u.safe()
    pad = hyperframes.FIT_PAD_REF * u.k
    return sx + pad, top + pad, W - sx - pad, bottom - pad


def _outside(cam, points, f0, f1):
    x0, y0, x1, y1 = _box()
    bad = []
    for f in range(f0, f1 + 1):
        st = camera_at(cam, f)
        for px, py in points:
            x, y = st["x"] + st["s"] * px, st["y"] + st["s"] * py
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                bad.append(f)
                break
    return bad


def test_good_fit_keeps_both_pins_inside_for_the_whole_hold():
    wide = _scene(None)
    fitted = _scene(["Kupang", "Darwin"])
    points = [hyperframes._pin_xy(p, fitted, MW, MH) for p in fitted.pins]
    plain, held = _plan(wide), _plan(fitted)
    assert _outside(plain, points, 12, N)          # the default push-in leaves the safe area
    assert _outside(held, points, 12, N) == []
    again = _plan(fitted)
    assert held["legs"] == again["legs"] and held["start"] == again["start"]


def test_good_keep_in_frame_is_fit_and_indexes_match_labels():
    key = timeline.CameraKey.model_validate(
        {"at_frame": 0, "focus": [0.5, 0.5], "keep_in_frame": ["Kupang", "Darwin"]})
    assert key.fit == ["Kupang", "Darwin"]
    assert key.model_dump(exclude_none=True)["fit"] == ["Kupang", "Darwin"]
    by_name = _plan(_scene(["Kupang", "Darwin"]))
    by_index = _plan(_scene([0, 1]))
    assert by_name["legs"] == by_index["legs"]


def test_bad_unknown_pin_and_empty_fit():
    with pytest.raises(EngineError, match="Nowhere"):
        _plan(_scene(["Nowhere"]))
    with pytest.raises(ValueError, match="at least one pin"):
        timeline.CameraKey(at_frame=0, focus=[0.5, 0.5], fit=[])
