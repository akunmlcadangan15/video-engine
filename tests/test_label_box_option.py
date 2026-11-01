"""Stage 2 label boxes: plain labels by default, a dark box only where a pin / region / figure / area asks for it."""
from video_engine import entities, hyperframes, profiles
from video_engine.timeline import AreaLabel

SQ = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]


def _scene(**kw):
    def mut(d):
        d["profile"] = "geoverum"
        sc = d["segments"][2]["gsap_scene_config"]
        sc.update({"pins": [], "cards": [], **kw})
    return mut


def _box_profile(monkeypatch):
    p = profiles.load("geoverum")
    th = p.data.theme.model_copy(update={"label_style": "box"})
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile(p.name, p.data.model_copy(update={"theme": th}),
                                                                     p.blake3 + "-box"))


def test_good_geoverum_plain_labels_have_no_box(project):
    p, tl = project(_scene(pins=[{"label": "A", "x": 0.4, "y": 0.4}],
                           regions=[{"entity": "A", "polygon": SQ, "label": "ALPHA"}],
                           figures=[{"kind": "year", "value": 1893}]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    assert sc["pins"][0].get("box") is None
    assert "box" not in sc["regions"][0] and "box" not in sc["figures"][0]
    assert profiles.of(tl).theme.label_style == "plain"


def test_good_opt_in_boxes_for_pin_region_and_figure(project):
    p, tl = project(_scene(pins=[{"label": "A", "x": 0.4, "y": 0.4, "box": True}],
                           regions=[{"entity": "A", "polygon": SQ, "label": "ALPHA", "box": True}],
                           figures=[{"kind": "year", "value": 1893, "box": True}]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    th = profiles.of(tl).theme
    assert sc["pins"][0]["box"] is True
    assert sc["regions"][0]["box"] == entities.box_colour(entities.colours(tl)["A"], th.model_dump())
    assert sc["figures"][0]["box"] == th.label_box_colour


def test_good_box_false_suppresses_a_box_profile(project, monkeypatch):
    _box_profile(monkeypatch)
    p, tl = project(_scene(pins=[{"label": "A", "x": 0.4, "y": 0.4, "box": False}],
                           regions=[{"entity": "A", "polygon": SQ, "label": "ALPHA", "box": False}],
                           figures=[{"kind": "year", "value": 1893, "box": False}]))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    assert sc["pins"][0]["box"] is False
    assert "box" not in sc["regions"][0] and "box" not in sc["figures"][0]


def test_good_area_label_box_option_reaches_the_template():
    assert AreaLabel(text="Sekutu", region=0, box=True).box is True
    html = (hyperframes.template_dir() / "index.html").read_text() if hasattr(hyperframes, "template_dir") else \
        (__import__("pathlib").Path(hyperframes.__file__).parent / "template" / "index.html").read_text()
    assert "a.box===undefined?BOXED:a.box" in html and "p.box===undefined?BOXED:p.box" in html
