"""P03 camera path metrics: path length, peak screen speed, largest one-frame speed change; info unless step_warn."""
from video_engine import profiles
from video_engine.gates import style


def _whip(d):
    d["segments"][2]["gsap_scene_config"]["camera_path"] = [
        {"at_frame": 0, "focus": [0.3, 0.4], "zoom": 1.5}, {"at_frame": 45, "focus": [0.7, 0.4], "zoom": 1.5, "move": "whip"}]


def test_good_metrics_for_native_and_hyperframes_segments(project):
    p, tl = project()
    ms = {m["seg"]: m for m in style.camera_path_metrics(tl, style.motion_frames(tl, p))}
    assert set(ms) == {0, 2}                     # native Ken Burns and the map scene; seg 1 has no camera
    for m in ms.values():
        assert m["length_fw"] > 0 and m["peak_fw_s"] > 0 and m["max_step"] < 0.2   # smooth eases
    fs = style.p03_camera_path(tl, profiles.CameraGate(), style.motion_frames(tl, p))
    assert [(f.gate, f.level) for f in fs] == [("P03", "info")] * 2


def test_good_whip_is_fast_and_reported_as_info_by_default(project):
    p, tl = project(_whip)
    mf = style.motion_frames(tl, p)
    m = next(m for m in style.camera_path_metrics(tl, mf) if m["seg"] == 2)
    assert m["peak_fw_s"] > 3 and m["max_step"] > 0.5
    assert all(f.level == "info" for f in style.p03_camera_path(tl, profiles.CameraGate(), mf))


def test_bad_speed_jump_warns_when_step_warn_is_set(project):
    p, tl = project(_whip)
    fs = style.p03_camera_path(tl, profiles.CameraGate(step_warn=0.5), style.motion_frames(tl, p))
    warn = [f for f in fs if f.level == "warn"]
    assert len(warn) == 1 and warn[0].seg == 2 and "step_warn 50%" in warn[0].message and warn[0].frames
