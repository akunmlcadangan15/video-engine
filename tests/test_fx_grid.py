"""P12 blue grid backdrop and optional graticule: model, scene data, default-off scene, determinism and DOM marker."""
import json

import pytest
from pydantic import ValidationError

from video_engine import hyperframes, timeline


def _mut(extra):
    def mut(d):
        d["segments"][2]["gsap_scene_config"].update(extra)
    return mut


GRID = {"backdrop": {"grid": {"colour": "#2F6FD6", "bg": "#0B2A5B", "cell_pct": 4.0, "line_px": 2,
                             "alpha": 0.5, "graticule": True}}}


def test_good_grid_values():
    sc = timeline.GsapScene(map_asset="assets/map.jpg", map_bounds=[-10, 95, 10, 115], **GRID)
    assert sc.backdrop.grid.graticule is True


@pytest.mark.parametrize("bad", [{"cell_pct": 0}, {"cell_pct": 51}, {"line_px": 0}, {"alpha": 1.5},
                                 {"colour": "blue"}, {"bg": "#12345"}])
def test_bad_grid_values_rejected(bad):
    with pytest.raises(ValidationError):
        timeline.GsapScene(map_asset="assets/map.jpg", backdrop={"grid": bad})


def test_good_grid_scene_data_scales_with_the_frame(project):
    d, tl = project(_mut(GRID))
    sc = hyperframes.scene_data(tl.segments[2], tl, d)
    g = sc["backdrop"]["grid"]
    assert g["cell_px"] == pytest.approx(0.04 * 1920)
    assert g["colour"] == "#2f6fd6" and g["bg"] == "#0b2a5b"
    assert g["lines"] and len(g["lines"]) == 13 + 7       # 30 deg steps over the default whole world
    assert g["graticule_colour"] == "#ffffff" and g["graticule_alpha"] == 0.35   # light lines, visible on dark ocean


def test_good_scene_without_grid_is_unchanged(project, tmp_path):
    d, tl = project()
    assert "backdrop" not in hyperframes.scene_data(tl.segments[2], tl, d)
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    html = (out / "index.html").read_text()
    assert "fx_backdrop.js" not in html


def test_good_grid_scene_is_deterministic(project):
    d, tl = project(_mut(GRID))
    a = hyperframes.scene_data(tl.segments[2], tl, d)
    hyperframes.clear_scene_cache()
    b = hyperframes.scene_data(tl.segments[2], tl, d)
    assert json.dumps(a["backdrop"], sort_keys=True) == json.dumps(b["backdrop"], sort_keys=True)


def test_good_grid_dom_marker(project, tmp_path):
    d, tl = project(_mut(GRID))
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    assert (out / "fx_backdrop.js").is_file()
    res = hyperframes.probe_dom(out, [1.0], 1080, 1920)
    ids = {r["id"] for r in res["times"][0]["rects"]}
    assert "fx:backdrop" in ids


def test_good_grid_sits_behind_the_globe_canvas(project, tmp_path):
    """The backdrop canvas is a plain script and globe.js a module that runs later: the globe canvas must land just
    after the backdrop, not in front of it (else the grid or stars paint over the globe)."""
    import subprocess

    from PIL import Image

    from video_engine import config

    def mut(d):
        sc = d["segments"][2]["gsap_scene_config"]
        for k in ("camera_path", "beats", "routes", "borders", "morph", "props", "layers", "map_bounds"):
            sc.pop(k, None)
        sc.update(map_asset="assets/globe.jpg", pins=[], backdrop=GRID["backdrop"] | {"stars": {"count": 50}},
                  globe={"keys": [{"at_frame": 0, "lat": 0, "lng": 0, "altitude": 3.0}]})
    d, tl = project(mut)
    Image.new("RGB", (1024, 512), (30, 60, 120)).save(d / "assets" / "globe.jpg")
    out, _ = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    script = hyperframes.PROBE_JS.read_text().replace(
        "return { rects, paints, lines };",
        """const kids = [...document.getElementById('root').children];
        rects.push({id:'order', bd:kids.findIndex(e => e.hasAttribute('data-fx-backdrop')),
                    gl:kids.findIndex(e => e.hasAttribute('data-globe'))});
        return { rects, paints, lines };""")
    probe = tmp_path / "order_probe.mjs"
    probe.write_text(script)
    env = config.hf_env()
    env.update(ENGINE_PUPPETEER=str(config.PUPPETEER), ENGINE_CHROME=str(config.CHROME), ENGINE_WEBGL="1")
    with hyperframes.serve(out) as url:
        res = subprocess.run([str(config.NODE), str(probe), url + "/index.html", "-", "1", "1080", "1920"], env=env,
                             text=True, capture_output=True, check=True, timeout=30)
    o = next(r for r in json.loads(res.stdout)["times"][0]["rects"] if r["id"] == "order")
    assert o["bd"] == 0 and o["gl"] == 1
