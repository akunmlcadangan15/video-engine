"""Routes: a dashed line that draws itself along its points with an arrowhead riding the tip; one on-screen line, dash
and arrow size at every camera key; T02 checks that the drawing ends inside the segment."""
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import hyperframes, profiles
from video_engine.gates import layer1, style

ROUTE = {"points": [[0.4, 0.5], [0.6, 0.5]], "at_frame": 15, "draw_s": 1.0}


def _seg2_frames(project):
    return project()[1].segments[2].n_frames


def _scene(routes, path):
    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update(pins=[], cards=[], camera_path=path)
        if routes is not None:
            sc["routes"] = routes
    return mut


def _hold(n, zoom=1.5):
    return [{"at_frame": 0, "focus": [0.5, 0.5], "zoom": zoom}, {"at_frame": n - 1, "focus": [0.5, 0.5], "zoom": zoom}]


def _snap(project, out, routes, path, t):
    """Screenshot of segment 2 at t in a fresh page, and the scene."""
    p, tl = project(_scene(routes, path))
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, out / "hf")
    res = hyperframes.probe_dom(d, [t], 1080, 1920, out / "snap")
    return np.asarray(Image.open(res["times"][0]["png"]).convert("RGB")).astype(int), sc


def _route_mask(project, tmp_path, path, t, routes=(ROUTE,)):
    """Pixels the route changes at t (route render vs the same scene without routes) and the scene."""
    with_r, sc = _snap(project, tmp_path / "r", list(routes), path, t)
    without, _ = _snap(project, tmp_path / "n", None, path, t)
    return np.abs(with_r - without).max(axis=2) > 40, sc


def _screen(sc, pt, cam):
    mw, mh = sc["map_size"]
    return pt[0] * mw * cam["s"] + cam["x"], pt[1] * mh * cam["s"] + cam["y"]


# good ---------------------------------------------------------------------------------------------------------------
def test_good_route_draws_itself_with_arrowhead_at_the_tip(project, tmp_path):
    n = _seg2_frames(project)
    path = _hold(n)
    before, sc = _route_mask(project, tmp_path / "a", path, 0.4)
    mid, _ = _route_mask(project, tmp_path / "b", path, 0.5 + 0.5)
    done, _ = _route_mask(project, tmp_path / "c", path, 0.5 + 1.2)
    cam = sc["camera"]["start"]
    (x0, y), (x1, _) = _screen(sc, ROUTE["points"][0], cam), _screen(sc, ROUTE["points"][1], cam)
    xm = (x0 + x1) / 2                                   # power1.inOut: half the length at half the time
    band = lambda m, a, b: m[round(y) - 30:round(y) + 30, round(a):round(b)].any(axis=0)
    assert not before[round(y) - 60:round(y) + 60, round(x0) - 60:round(x1) + 60].any()
    assert band(mid, x0 + 5, xm - 40).mean() > 0.4 and not band(mid, xm + 40, x1).any()
    assert mid[round(y) - 15:round(y) + 15, round(xm) - 36:round(xm)].mean() > 0.3        # arrowhead body behind the tip
    row = done[round(y), round(x0) + 5:round(x1) - 60]
    runs = np.count_nonzero(np.diff(row.astype(int)) == 1)
    assert 0.4 < row.mean() < 0.85 and runs >= (x1 - x0 - 65) / 38 - 2   # dashes and gaps all the way
    assert done[round(y) - 15:round(y) + 15, round(x1) - 36:round(x1)].mean() > 0.3


def test_good_line_and_dashes_keep_screen_size_through_zoom(project, tmp_path):
    n = _seg2_frames(project)
    path = [{"at_frame": 0, "focus": [0.5, 0.5], "zoom": 1.2}, {"at_frame": 20, "focus": [0.5, 0.5], "zoom": 1.2},
            {"at_frame": 40, "focus": [0.5, 0.5], "zoom": 2.4}]
    assert n > 41
    route = {"points": [[0.3, 0.5], [0.7, 0.5]], "at_frame": 0, "draw_s": 0.5}
    out = []
    for f, k in ((20, 0), (40, 1)):                        # key frames: the camera sits on leg k's end transform
        m, sc = _route_mask(project, tmp_path / str(k), path, f / 30, [route])
        cam = sc["camera"]["legs"][k]["to"]
        _, y = _screen(sc, route["points"][0], cam)
        row = m[round(y), 100:980]
        col = m[round(y) - 20:round(y) + 21, 100:980].sum(axis=0)
        out.append((np.count_nonzero(np.diff(row.astype(int)) == 1), np.median(col[col > 0])))
    (runs1, w1), (runs2, w2) = out
    assert abs(runs1 - runs2) <= 2                        # same dash period on screen at zoom 1.2 and 2.4
    assert abs(w1 - w2) <= 2                              # same line width on screen


def test_good_route_counts_as_a_novelty_event(project):
    p, tl = project(_scene([ROUTE], _hold(_seg2_frames(project))))
    s = tl.segments[2]
    assert (s.start_frame + 15, "") in style.element_events(s, tl.settings.fps)


def test_good_no_routes_no_route_data(project):
    p, tl = project(_scene(None, _hold(_seg2_frames(project))))
    assert "routes" not in hyperframes.scene_data(tl.segments[2], tl, p)


# bad ----------------------------------------------------------------------------------------------------------------
def test_bad_route_drawing_past_segment_end_fails_T02(project):
    n = _seg2_frames(project)
    p, tl = project(_scene([{**ROUTE, "at_frame": n - 10, "draw_s": 1.0}], _hold(n)))
    f = [x for x in layer1.run_layer1(tl, p, None) if x.gate == "T02" and "route" in x.message]
    assert f and f[0].level == "fail" and "after the segment's last frame" in f[0].message


@pytest.mark.parametrize("pts, msg", [([[0.4, 0.5]], "at least 2"), ([[0.4, 0.5], [1.2, 0.5]], "within 0..1"),
                                      ([[0.4, 0.5], [0.4, 0.5]], "must not all be the same")])
def test_bad_route_points(project, pts, msg):
    with pytest.raises(ValidationError, match=msg):
        project(_scene([{**ROUTE, "points": pts}], _hold(_seg2_frames(project))))


def test_bad_route_colour_not_hex():
    with pytest.raises(ValidationError, match="#rrggbb"):
        profiles.Theme(**{**profiles.load().theme.model_dump(), "route_colour": "white"})
