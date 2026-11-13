"""Full 3D camera (video_engine/camera_full3d.py, gsap_scene_config.camera3d, hf/scene3d.js)."""
from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import pytest

from video_engine import camera_full3d as c3, hyperframes, profiles
from video_engine.gates import layer2

W, H = 1080, 1920
MW, MH = 2025.0, 2160.0


class K:   # a camera key without the timeline model (sampler unit tests)
    def __init__(self, at_frame, eye, target=(0.5, 0.5, 0.0), roll=0.0, fov=50.0, move="glide", hold=0):
        self.at_frame, self.eye, self.target, self.roll, self.fov, self.move, self.hold = \
            at_frame, list(eye), list(target), roll, fov, move, hold


def _top_down(h=0.5, fov=50.0, roll=0.0, u=0.5, v=0.5):
    p = c3.Path3D([K(0, (u, v, h), (u, v, 0.0), roll, fov)], MW, MH)
    return p.sample(0)


def test_good_top_down_view_matches_the_pinhole_formula():
    st = _top_down(h=0.5, fov=50.0)
    centre = c3.world((0.5, 0.5, 0.0), MW, MH)
    x, y, d = c3.project(st, centre, W, H)
    assert x == pytest.approx(W / 2) and y == pytest.approx(H / 2) and d == pytest.approx(0.5 * MW)
    # half the frame height covers h * tan(fov / 2) of map
    half = 0.5 * MW * math.tan(math.radians(25))
    _, y_top, _ = c3.project(st, (centre[0], centre[1] + half, 0.0), W, H)
    assert y_top == pytest.approx(0.0, abs=1e-6)
    # north (+y) is up on screen, east (+x) to the right
    xe, ye, _ = c3.project(st, (centre[0] + 10, centre[1], 0.0), W, H)
    assert xe > W / 2 and ye == pytest.approx(H / 2)


def test_good_roll_turns_the_map_clockwise():
    st = _top_down(roll=90.0)
    centre = c3.world((0.5, 0.5, 0.0), MW, MH)
    x, y, _ = c3.project(st, (centre[0] + 50, centre[1], 0.0), W, H)   # a point east of the centre
    assert x == pytest.approx(W / 2, abs=1e-6) and y > H / 2               # clockwise: east goes down


def test_good_ray_to_map_inverts_project():
    st = c3.Path3D([K(0, (0.3, 0.9, 0.4), (0.55, 0.45, 0.0), 12.0, 60.0)], MW, MH).sample(0)
    for sx, sy in ((10, 20), (W / 2, H / 2), (W - 5, H - 7)):
        p = c3.ray_to_map(st, sx, sy, W, H)
        assert p is not None and p[2] == pytest.approx(0.0, abs=1e-6)
        x, y, _ = c3.project(st, p, W, H)
        assert x == pytest.approx(sx, abs=1e-6) and y == pytest.approx(sy, abs=1e-6)


def test_good_slerp_is_unit_and_hits_its_ends():
    a = c3.look_at((0, 0, 10), (0, 0, 0))
    b = c3.look_at((10, -5, 4), (0, 0, 0), 30.0)
    for t in (0.0, 0.25, 0.5, 1.0):
        q = c3.slerp(a, b, t)
        assert math.sqrt(sum(v * v for v in q)) == pytest.approx(1.0)
    assert c3.slerp(a, b, 0) == pytest.approx(a) and c3.slerp(a, b, 1) == pytest.approx(b, abs=1e-9) or \
        c3.slerp(a, b, 1) == pytest.approx(tuple(-v for v in b), abs=1e-9)


def test_good_linear_leg_moves_at_constant_speed_along_the_spline():
    keys = [K(0, (0.2, 0.8, 0.5)), K(30, (0.5, 0.3, 0.3), move="linear"), K(60, (0.8, 0.6, 0.5), move="linear")]
    p = c3.Path3D(keys, MW, MH)
    pos = [p.sample(f).position for f in range(31, 60)]
    steps = [math.dist(a, b) for a, b in zip(pos, pos[1:])]
    assert max(steps) / min(steps) < 1.03                     # arc-length parameterisation
    assert p.sample(30).position == pytest.approx(c3.world(keys[1].eye, MW, MH))


def test_good_glide_eases_and_hold_stays():
    keys = [K(0, (0.5, 0.5, 0.6), hold=10), K(40, (0.5, 0.5, 0.2))]
    p = c3.Path3D(keys, MW, MH)
    assert p.sample(10).position == p.sample(0).position
    z = [p.sample(f).position[2] for f in range(10, 41)]
    d = [abs(b - a) for a, b in zip(z, z[1:])]
    assert d[0] < d[len(d) // 2] and d[-1] < d[len(d) // 2]  # slow in, slow out


def test_bad_view_parallel_to_north_is_refused():
    with pytest.raises(ValueError, match="parallel to north"):
        c3.look_at((0, -10, 0), (0, 0, 0))


def test_good_uncovered_flags_a_view_toward_the_horizon():
    good = [_top_down(h=0.3)]
    bad = [c3.Path3D([K(0, (0.5, 1.0, 0.05), (0.5, 0.0, 0.0), fov=70)], MW, MH).sample(0)]
    assert c3.uncovered(good, W, H, MW, MH) == []
    f, corners = c3.uncovered(bad, W, H, MW, MH)[0]
    assert f == 0 and "top-left" in corners and "top-right" in corners


def test_good_magnification_grows_as_the_camera_descends():
    hi, lo = _top_down(h=0.8), _top_down(h=0.1)
    assert c3.magnification(lo, W, H) > 4 * c3.magnification(hi, W, H)


def test_good_metrics_report_motion_speed_and_turn():
    keys = [K(0, (0.5, 0.5, 0.6)), K(30, (0.5, 0.5, 0.25))]
    m = c3.metrics(c3.frames(keys, 31, MW, MH), W, H, 30)
    assert m["motion_px"] > 100 and m["peak_speed"] > 0 and m["peak_ang_speed"] == pytest.approx(0.0, abs=1e-3)
    orbit = [K(0, (0.5, 0.9, 0.4)), K(30, (0.9, 0.5, 0.4))]
    assert c3.metrics(c3.frames(orbit, 31, MW, MH), W, H, 30)["peak_ang_speed"] > 10


# ------------------------------------------------------------------------------------------- timeline and gates
DOLLY = [{"at_frame": 0, "eye": [0.5, 0.5, 0.6]}, {"at_frame": 50, "eye": [0.5, 0.55, 0.25], "fov": 45}]


def _scene3d(keys=DOLLY, **extra):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc["camera_mode"] = "3d"
        sc["camera3d"] = keys
        sc.update(extra)
    return mut


def test_good_3d_scene_carries_frames_and_projected_pins(project):
    d, tl = project(_scene3d())
    s = tl.segments[2]
    scene = hyperframes.scene_data(s, tl, d)
    C = scene["cam3d"]
    assert len(C["frames"]) == s.n_frames and len(C["frames"][0]) == 8
    assert scene["camera"]["legs"][0]["to"] == {"x": 0.0, "y": 0.0, "s": 1.0}
    pin = scene["pins"][0]
    assert 0 <= pin["px"] <= W and 0 <= pin["py"] <= H and "mx" in pin
    assert hyperframes.SCENE3D_JS in hyperframes.script_files(scene)
    assert scene["motion_px"] == pytest.approx(C["metrics"]["motion_px"], abs=0.01)


def test_good_3d_gates_pass_on_a_dolly(project):
    d, tl = project(_scene3d())
    assert [f.level for f in layer2.m03_tilt_cover(tl, d)] == ["pass"]
    r56 = [f for f in layer2.r56_camera(tl, d)[0] if f.seg == 2]
    assert r56 and r56[0].level == "pass" and "3D camera moves" in r56[0].message


def test_bad_3d_camera_looking_past_the_map_fails_m03(project):
    keys = [{"at_frame": 0, "eye": [0.5, 1.0, 0.05], "target": [0.5, 0.0, 0.0], "fov": 70},
            {"at_frame": 50, "eye": [0.5, 0.9, 0.05], "target": [0.5, 0.0, 0.0], "fov": 70}]
    d, tl = project(_scene3d(keys))
    out = layer2.m03_tilt_cover(tl, d)
    assert out[0].level == "fail" and "3D camera shows no map" in out[0].message


def test_bad_still_3d_camera_fails_r56(project):
    keys = [{"at_frame": 0, "eye": [0.5, 0.5, 0.4]}, {"at_frame": 50, "eye": [0.5, 0.5, 0.4]}]
    d, tl = project(_scene3d(keys))
    r56 = [f for f in layer2.r56_camera(tl, d)[0] if f.seg == 2]
    assert r56[0].level == "warn" and "moves 0px <" in r56[0].message   # fail lowered: no motion intent declared


def test_good_m02_sizes_the_map_by_the_deepest_3d_magnification(project):
    d, tl = project(_scene3d())
    scene = hyperframes.scene_data(tl.segments[2], tl, d)
    from video_engine import maps
    assert maps._need_w(scene) == scene["cam3d"]["need_w"] > scene["map_size"][0]


@pytest.mark.parametrize("mut,msg", [
    (_scene3d(camera_mode=None), "camera3d keys need camera mode '3d'"),
    (lambda d: d["segments"][2]["gsap_scene_config"].update(camera_mode="3d"), "needs gsap_scene_config.camera3d"),
    (_scene3d([{"at_frame": 5, "eye": [0.5, 0.5, 0.5]}]), "first key is the opening view"),
    (_scene3d([{"at_frame": 0, "eye": [0.5, 0.5, -0.1]}]), "must be above the map"),
    (_scene3d(routes=[{"points": [[0.1, 0.1], [0.2, 0.2]]}]), "3D camera scenes take only"),
    (_scene3d([{"at_frame": 0, "eye": [0.5, 0.5, 0.5]}, {"at_frame": 500, "eye": [0.5, 0.5, 0.4]}]), "past the segment"),
])
def test_bad_3d_fields_outside_their_mode_or_scope(project, mut, msg):
    with pytest.raises(Exception, match=msg):
        project(mut)


def test_good_planar_scenes_and_profile_hashes_do_not_change(project):
    d, tl = project()
    scene = hyperframes.scene_data(tl.segments[2], tl, d)
    assert "cam3d" not in scene and hyperframes.SCENE3D_JS not in hyperframes.script_files(scene)
    assert "camera3d" not in tl.segments[2].gsap_scene_config.model_dump()
    assert "camera_mode" not in tl.segments[2].gsap_scene_config.model_dump()
    assert profiles.load("geoverum").blake3 == profiles.load("geoverum").blake3 != profiles.load().blake3


# --------------------------------------------------------------------------- browser twin (three.js, when present)
THREE = Path(__file__).resolve().parents[1] / "vendor" / "npm" / "node_modules" / "three" / "build" / "three.module.js"


@pytest.mark.skipif(not (THREE.is_file() and shutil.which("node")), reason="three.js (vendor/npm) or node not installed")
def test_good_three_js_projects_like_the_python_mirror(tmp_path):
    keys = [K(0, (0.3, 0.85, 0.35), (0.55, 0.4, 0.0), 15.0, 55.0), K(40, (0.7, 0.6, 0.2), (0.5, 0.45, 0.0), -10.0, 40.0)]
    states = c3.frames(keys, 41, MW, MH)
    pts = [c3.world(p, MW, MH) for p in ((0.5, 0.5), (0.4, 0.3), (0.62, 0.55))]
    want = [[c3.project(states[f], p, W, H)[:2] for p in pts] for f in (0, 17, 40)]
    js = tmp_path / "t.mjs"
    js.write_text(f"""import * as THREE from '{THREE.as_uri()}';
const F={[states[f].as_list() for f in (0, 17, 40)]}, P={[list(p) for p in pts]};
const out=F.map(v=>{{const c=new THREE.PerspectiveCamera(v[7],{W}/{H},1,1e7);c.position.set(v[0],v[1],v[2]);
  c.quaternion.set(v[3],v[4],v[5],v[6]);c.updateProjectionMatrix();c.updateMatrixWorld();
  return P.map(p=>{{const q=new THREE.Vector3(...p).project(c);return [(q.x+1)/2*{W},(1-q.y)/2*{H}];}});}});
console.log(JSON.stringify(out));""")
    got = __import__("json").loads(subprocess.run(["node", str(js)], capture_output=True, text=True, check=True).stdout)
    for gf, wf in zip(got, want):
        for g, w in zip(gf, wf):
            assert g[0] == pytest.approx(w[0], abs=0.05) and g[1] == pytest.approx(w[1], abs=0.05)


# ---------------------------------------------------------------------------------------------------------- presets
PN, PFPS = 120, 30


def _preset_states(name, **kw):
    from video_engine import timeline
    keys = [timeline.CameraKey3D(**k) for k in c3.preset_keys(name, PN, PFPS, aspect=MW / MH, **kw)]
    return keys, c3.frames(keys, PN, MW, MH)


def _dist(st, target=(0.5, 0.5, 0.0)):
    return math.dist(st.position, c3.world(target, MW, MH)) / MW


@pytest.mark.parametrize("name", list(c3.PRESETS))
def test_good_preset_keys_are_valid_ordered_and_keep_the_target_centred(name):
    keys, states = _preset_states(name)
    at = [k.at_frame for k in keys]
    assert at[0] == 0 and at[-1] == PN - 1 and at == sorted(set(at))
    assert c3.preset_keys(name, PN, PFPS, aspect=MW / MH) == c3.preset_keys(name, PN, PFPS, aspect=MW / MH)
    centre = c3.world((0.5, 0.5, 0.0), MW, MH)
    for st in states:
        x, y, d = c3.project(st, centre, W, H)
        assert x == pytest.approx(W / 2, abs=0.25) and y == pytest.approx(H / 2, abs=0.25) and d > 0


def test_good_push_in_closes_the_distance_fast_then_eases_and_pitches():
    _, st = _preset_states("push_in", height=0.6, depth=0.35, pitch=3.0)
    d = [_dist(s) for s in st]
    assert d[0] == pytest.approx(0.6) and d[-1] == pytest.approx(0.6 * 0.65, rel=1e-3)
    assert all(b <= a + 1e-9 for a, b in zip(d, d[1:]))
    assert d[0] - d[PN // 4] > 0.8 * (d[0] - d[-1])                      # easeOutExpo: most of the travel early
    off = math.degrees(math.asin(math.hypot(st[-1].position[0] - 0.5 * MW, st[-1].position[1] + 0.5 * MH) / (d[-1] * MW)))
    assert off == pytest.approx(3.0, abs=0.05)


def test_good_orbit_sweeps_the_yaw_at_constant_distance():
    _, st = _preset_states("orbit", yaw=24.0, tilt=35.0)
    az = [math.degrees(math.atan2(s.position[0] - 0.5 * MW, -(s.position[1] + 0.5 * MH))) for s in st]
    assert az[0] == pytest.approx(-12.0, abs=0.05) and az[-1] == pytest.approx(12.0, abs=0.05)
    assert all(b >= a - 1e-6 for a, b in zip(az, az[1:]))
    assert all(_dist(s) == pytest.approx(0.6, rel=2e-3) for s in st)


def test_good_crash_zoom_overshoots_then_settles():
    _, st = _preset_states("crash_zoom", height=0.6, depth=0.55, push_s=0.4)
    d = [_dist(s) for s in st]
    end, push = 0.6 * 0.45, round(0.4 * PFPS)
    assert d[push] == pytest.approx(end, rel=1e-2)                        # the push lands on the end distance
    assert min(d) < end * 0.99                                            # spring overshoot past it
    assert all(x == pytest.approx(end, rel=2e-3) for x in d[-10:])        # settled


def test_good_vertigo_keeps_the_target_size_while_the_fov_narrows():
    _, st = _preset_states("vertigo", pull=1.6)
    size = [_dist(s) * math.tan(math.radians(s.fov) / 2) for s in st]
    assert all(x == pytest.approx(size[0], rel=2e-3) for x in size)
    assert st[-1].fov < st[0].fov and _dist(st[-1]) == pytest.approx(0.6 * 1.6, rel=1e-3)


def test_bad_preset_unknown_name_option_or_out_of_bounds_view():
    with pytest.raises(ValueError, match="unknown camera3d preset"):
        c3.preset_keys("whirl", PN, PFPS)
    with pytest.raises(ValueError, match="has no option"):
        c3.preset_keys("orbit", PN, PFPS, depth=0.2)
    with pytest.raises(ValueError, match="leaves the camera bounds"):
        c3.preset_keys("vertigo", PN, PFPS, fov=12.0, pull=3.0)
    with pytest.raises(ValueError, match="n_frames"):
        c3.preset_keys("push_in", 1, PFPS)
