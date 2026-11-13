"""Counter-scaled labels under camera zooms: a map label's on-screen size is its planned size on every frame of every
camera leg (drift = constant ln rate, glide, whip = power3.inOut, pulse cubic-bezier). Probed in headless Chrome on a 1 -> 4
zoom at 0/25/50/75/100 % of the leg. The bad case runs the same scene with the counter-scale tween taking the
camera ease between the reciprocal endpoints (1/a -> 1/z), which is 1.5x oversize at the middle of a drift leg."""
import json

import pytest

from video_engine import hyperframes
from test_pins import TEAR, _pins, _theme

PIN = {"label": "Pusat Kota", "x": 0.5, "y": 0.5, "at_frame": 0}
F0, F1, Z0, Z1 = 30, 60, 1.0, 4.0                 # the zoom leg: frames [30, 60), scale 1 -> 4
FRACS = (0.0, 0.25, 0.5, 0.75, 1.0)
PULSE = [0.3, 0.0, 0.25, 1.0]                     # a pulse leg's cubic-bezier (profile [camera] form)
TOL = 0.01
MAIN = "\n</script>\n<script>\n(function(){\nconst S=window.SCENE, FPS=S.fps, f2t"   # end of the helpers, main block
ENDPOINT_EASE = "\nwindow.__inv=function(a,z,E){return window.__ease(E);};" + MAIN


def _scene(tl, p, out, ease, bez=None):
    """The fixture's map segment with its camera replaced by a hold on scale 1, then one 1 -> 4 leg on the pin."""
    d, sc = hyperframes.build_dir(tl.segments[2], tl, p, out / "hf")
    mw, mh = sc["map_size"]
    at = lambda s: {"x": 540 - s * 0.5 * mw, "y": 960 - s * 0.5 * mh, "s": s}
    leg = {"f0": F0, "f1": F1, "to": at(Z1), "ease": ease}
    if bez:
        leg["bez"] = bez
    sc["camera"] = {**sc["camera"], "start": at(Z0),
                    "legs": [{"f0": 0, "f1": F0, "to": at(Z0), "ease": "drift"}, leg]}
    (d / "scene.js").write_text("window.SCENE=" + json.dumps(sc, ensure_ascii=False) + ";\n")
    return d, sc


def _errors(d, fps, ids):
    """Relative on-screen size error of each label at the leg fractions, against its size before the leg."""
    times = [F0 / fps - 0.2] + [(F0 + f * (F1 - F0)) / fps for f in FRACS]
    res = hyperframes.probe_dom(d, times, 1080, 1920)
    rects = [{r["id"]: r for r in it["rects"]} for it in res["times"]]
    out = {}
    for i in ids:
        w0, h0 = rects[0][i]["w"], rects[0][i]["h"]
        assert w0 > 20 and h0 > 5
        out[i] = [max(abs(r[i]["w"] / w0 - 1), abs(r[i]["h"] / h0 - 1)) for r in rects[1:]]
    return out


LEGS = [("drift", None), ("glide", None), ("whip", None), ("drift", PULSE)]


# good ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ease, bez", LEGS, ids=["drift", "glide", "whip", "pulse"])
def test_good_dot_pin_label_keeps_planned_size_through_a_1_to_4_zoom(project, tmp_path, ease, bez):
    p, tl = project(_pins([PIN]))
    d, sc = _scene(tl, p, tmp_path, ease, bez)
    err = _errors(d, sc["fps"], ["pin:0"])["pin:0"]
    assert max(err) <= TOL, err


@pytest.mark.parametrize("ease, bez", LEGS, ids=["drift", "glide", "whip", "pulse"])
def test_good_teardrop_head_and_label_keep_planned_size_through_a_1_to_4_zoom(project, tmp_path, monkeypatch, ease,
                                                                               bez):
    _theme(monkeypatch, **TEAR)
    p, tl = project(_pins([PIN]))
    d, sc = _scene(tl, p, tmp_path, ease, bez)
    for i, err in _errors(d, sc["fps"], ["pin:0", "pinhead:0"]).items():
        assert max(err) <= TOL, (i, err)


# bad ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ease", ["drift", "glide", "whip"])
def test_bad_camera_ease_between_reciprocal_endpoints_inflates_the_label(project, tmp_path, ease):
    """The counter-scale tween with the camera's own ease (1/a -> 1/z) leaves the label oversize mid-leg; on a
    1 -> 4 drift leg (constant ln rate, ease (4^t - 1) / 3) the midpoint is 2 x (1 - 0.75 / 3) = 1.5."""
    p, tl = project(_pins([PIN]))
    d, sc = _scene(tl, p, tmp_path, ease)
    html = (d / "index.html").read_text()
    assert html.count(MAIN) == 1
    (d / "index.html").write_text(html.replace(MAIN, ENDPOINT_EASE))
    err = _errors(d, sc["fps"], ["pin:0"])["pin:0"]
    assert err[0] <= TOL and err[-1] <= TOL                  # the endpoints agree
    assert max(err) > 0.2                                    # mid-leg far outside the 1 % tolerance
    if ease == "drift":
        assert err[2] == pytest.approx(0.5, abs=0.02)
