"""P16 stacked cross-section and photo insets: schema, credits, scene wiring, layer math and the DOM boxes."""
import pytest
from pydantic import ValidationError

from video_engine import assets, fx2, hyperframes, profiles, timeline
from video_engine.gates import layer1

LAYERS = [{"label": "Kerak", "colour": "#8B5A2B", "thickness": 0.2},
          {"label": "Mantel", "colour": "#C1440E", "thickness": 0.5},
          {"label": "Inti", "colour": "#F2D04B", "thickness": 0.3}]


def _registry(d):
    (d / "index.csv").write_text("file,licence,source,source_url,author\n"
                                 "assets/still.jpg,CC0,test,https://example.com/still,Test\n")


def _scene(d):
    sc = d["segments"][2]["gsap_scene_config"]
    sc["cutaway"] = {"layers": LAYERS, "xy": [0.5, 0.55], "width_pct": 42, "at_frame": 0, "reveal_s": 0.6}
    sc["insets"] = [{"asset": "assets/still.jpg", "anchor": {"pin": 0}, "size_pct": 28,
                     "at_frame": 0, "out_frame": 90}]


def test_good_cutaway_and_inset_models_and_bad_values():
    c = timeline.Cutaway(layers=LAYERS, width_pct=42, reveal_s=0.6)
    assert len(c.layers) == 3
    assert timeline.Inset(asset="assets/still.jpg", anchor={"pin": 0}, size_pct=28).anchor.pin == 0
    assert timeline.Inset(asset="assets/still.jpg", anchor={"xy": [0.2, 0.3]}).anchor.xy == [0.2, 0.3]
    for bad in ({"layers": []}, {"layers": LAYERS, "xy": [1.2, 0.5]}, {"layers": LAYERS, "width_pct": 0},
                {"layers": [{"label": "X", "colour": "#ffffff", "thickness": 0}]}):
        with pytest.raises(ValidationError):
            timeline.Cutaway(**bad)
    for bad in ({"asset": "a.jpg", "anchor": {}}, {"asset": "a.jpg", "anchor": {"pin": 0, "xy": [0.5, 0.5]}},
                {"asset": "a.jpg", "anchor": {"pin": 0}, "at_frame": 20, "out_frame": 20}):
        with pytest.raises(ValidationError):
            timeline.Inset(**bad)
    with pytest.raises(ValidationError, match="pin index 1 out of range"):
        timeline.GsapScene(map_asset="m.jpg", insets=[{"asset": "a.jpg", "anchor": {"pin": 1}}])


def test_good_scene_layers_inset_credits_and_dom(project, tmp_path):
    path, tl = project(_scene)
    _registry(path)
    credited, _ = assets.ensure_credits(tl, path)
    assert any(r[3] == "inset image" for r in layer1.visual_refs(credited))
    build, scene = hyperframes.build_dir(credited.segments[2], credited, path, tmp_path / "hf")
    cut, ins = scene["cutaway"], scene["insets"][0]
    assert sum(layer["h"] for layer in cut["layers"]) == pytest.approx(cut["h"])
    assert cut["layers"][0]["y"] == 0 and cut["layers"][-1]["y"] + cut["layers"][-1]["h"] == pytest.approx(cut["h"])
    assert (build / ins["img"]).is_file() and (build / "fx2.js").is_file()
    res = hyperframes.probe_dom(build, [15 / scene["fps"]], 1080, 1920)
    rects = {r["id"]: r for r in res["times"][0]["rects"]}
    assert rects["cutaway"]["w"] > 0 and rects["cutaway"]["opacity"] > 0.5
    assert rects["inset:0"]["w"] > 0 and rects["inset:0"]["opacity"] > 0.5
    assert not res.get("errors")


def test_good_cutaway_is_deterministic_and_default_off_is_identical(project):
    path, tl = project()
    seg = tl.segments[2]
    before = hyperframes.scene_data(seg, tl, path), hyperframes.keys(seg, tl, path)
    seg.gsap_scene_config.cutaway = timeline.Cutaway(layers=LAYERS)
    a = hyperframes.scene_data(seg, tl, path)
    hyperframes.clear_scene_cache()
    b = hyperframes.scene_data(seg, tl, path)
    assert a["cutaway"] == b["cutaway"]
    assert fx2.cutaway_block(seg, tl, profiles.of(tl)) == a["cutaway"]
    seg.gsap_scene_config.cutaway = None
    hyperframes.clear_scene_cache()
    assert (hyperframes.scene_data(seg, tl, path), hyperframes.keys(seg, tl, path)) == before


def test_good_pin_inset_follows_the_pin_on_screen(project, tmp_path):
    """Pin anchors are map pixels: the leader must end on the pin's on-screen point at every probed time (the camera
    moves), and the framed photo stays inside the frame."""
    import json
    import subprocess

    from video_engine import config
    path, tl = project(_scene)
    _registry(path)
    credited, _ = assets.ensure_credits(tl, path)
    build, scene = hyperframes.build_dir(credited.segments[2], credited, path, tmp_path / "hf")
    assert scene["insets"][0]["pin"] == 0
    script = hyperframes.PROBE_JS.read_text().replace(
        "return { rects, paints, lines };",
        """const a = document.querySelector('[data-qa="anchor:pin:0"]').getBoundingClientRect(),
              ln = document.querySelector('#fx2 svg line:last-of-type'), b = document.querySelector('.fxinset').getBoundingClientRect(),
              l = document.querySelector('[data-qa="pin:0"]').getBoundingClientRect();
        rects.push({id:'leader', pin:[a.left, a.top], end:[+ln.getAttribute('x2'), +ln.getAttribute('y2')],
                    box:[b.left, b.top, b.right, b.bottom], lab:[l.left, l.top, l.right, l.bottom]});
        return { rects, paints, lines };""")
    probe = tmp_path / "inset_probe.mjs"
    probe.write_text(script)
    env = config.hf_env()
    env.update(ENGINE_PUPPETEER=str(config.PUPPETEER), ENGINE_CHROME=str(config.CHROME))
    n = credited.segments[2].n_frames
    ts = [round(f / scene["fps"], 4) for f in (10, n // 2, n - 10)]
    with hyperframes.serve(build) as url:
        res = subprocess.run([str(config.NODE), str(probe), url + "/index.html", "-", ",".join(map(str, ts)),
                              "1080", "1920"], env=env, text=True, capture_output=True, check=True, timeout=60)
    for t in json.loads(res.stdout)["times"]:
        o = next(r for r in t["rects"] if r["id"] == "leader")
        assert o["end"] == pytest.approx(o["pin"], abs=1.5)
        x0, y0, x1, y1 = o["box"]
        assert x0 >= 0 and y0 >= 0 and x1 <= 1080 and y1 <= 1920
        lx0, ly0, lx1, ly1 = o["lab"]
        assert lx1 <= x0 or lx0 >= x1 or ly1 <= y0 or ly0 >= y1          # the photo never covers the pin label
