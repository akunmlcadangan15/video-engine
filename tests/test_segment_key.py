"""HyperFrames segment cache key: every input of the segment's pixels is in the key (scene and profile values, the
map derivative recipe and source, fonts, textures, card images, toolchain identity); identical inputs give the same
key and a cache hit; card images are copied under content-hash names, so equal basenames never collide."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from video_engine import config, hyperframes, maps, profiles, toolchain

REAL_TEMPLATE = config.TEMPLATE_DIR
REAL_GSAP = config.GSAP_JS
REAL_PROBE = config.PKG / "hf" / "dom_probe.mjs"


@pytest.fixture(autouse=True)
def fresh_identity():
    toolchain._identity.cache_clear()
    yield
    toolchain._identity.cache_clear()


def key(p, tl):
    return hyperframes.keys(tl.segments[2], tl, p)[0]


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """A complete stand-in toolchain tree (small files) wired into config; returns its root."""
    t = tmp_path / "vendor"
    files = {"VERSIONS.json": json.dumps({"node": {"version": "v1"}, "npm": {"hyperframes": "1.0.0"}}),
             "node/bin/node": "node-binary-1",
             "chrome/chrome-headless-shell-linux64/chrome-headless-shell": "chrome-binary-1",
             "chrome/chrome-headless-shell-linux64/headless_lib_data.pak": "pak-1",
             "npm/node_modules/hyperframes/package.json": json.dumps({"version": "1.0.0"}),
             "npm/node_modules/puppeteer-core/package.json": json.dumps({"version": "2.0.0"})}
    for rel, text in files.items():
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        (t / rel).write_text(text)
    (t / "js").mkdir()
    shutil.copyfile(REAL_GSAP, t / "js" / "gsap.min.js")
    shutil.copytree(REAL_TEMPLATE, t / "template")
    (t / "pkg" / "hf").mkdir(parents=True)
    shutil.copyfile(REAL_PROBE, t / "pkg" / "hf" / "dom_probe.mjs")
    shutil.copytree(config.FONTS_DIR, t / "fonts")
    for name, v in {"VENDOR": t, "NODE": t / "node/bin/node",
                    "CHROME": t / "chrome/chrome-headless-shell-linux64/chrome-headless-shell",
                    "PUPPETEER": t / "npm/node_modules/puppeteer-core", "GSAP_JS": t / "js/gsap.min.js",
                    "TEMPLATE_DIR": t / "template", "PKG": t / "pkg", "FONTS_DIR": t / "fonts"}.items():
        monkeypatch.setattr(config, name, v)
    monkeypatch.setattr(toolchain, "ffmpeg_version", lambda exe: "ffmpeg version 7.1")
    return t


def _append(f: Path, text: str):
    with open(f, "a") as fh:
        fh.write(text)


TOOL_CHANGES = {
    "pinned-versions": lambda t, mp: (t / "VERSIONS.json").write_text(json.dumps({"node": {"version": "v2"}})),
    "node-binary": lambda t, mp: (t / "node/bin/node").write_text("node-binary-2"),
    "chrome-binary": lambda t, mp: (t / "chrome/chrome-headless-shell-linux64/chrome-headless-shell").write_text("chrome-binary-2"),
    "chrome-resource": lambda t, mp: (t / "chrome/chrome-headless-shell-linux64/headless_lib_data.pak").write_text("pak-2"),
    "hyperframes-package": lambda t, mp: (t / "npm/node_modules/hyperframes/package.json").write_text(json.dumps({"version": "1.0.1"})),
    "npm-dependency-added": lambda t, mp: ((t / "npm/node_modules/dep").mkdir(), (t / "npm/node_modules/dep/index.js").write_text("x")),
    "gsap": lambda t, mp: _append(t / "js/gsap.min.js", "\n//2"),
    "template": lambda t, mp: _append(t / "template/index.html", "\n<!-- 2 -->"),
    "dom-probe": lambda t, mp: _append(t / "pkg/hf/dom_probe.mjs", "\n// 2"),
    "ffmpeg": lambda t, mp: mp.setattr(toolchain, "ffmpeg_version", lambda exe: "ffmpeg version 7.2"),
    "font": lambda t, mp: _append(t / "fonts/Inter-Bold.otf", "\0"),
}


# ---------------------------------------------------------------------------------------------------------- good
@pytest.mark.parametrize("change", list(TOOL_CHANGES))
def test_good_toolchain_change_invalidates_the_segment_key(project, tools, monkeypatch, change):
    p, tl = project()
    k0 = key(p, tl)
    TOOL_CHANGES[change](tools, monkeypatch)
    toolchain._identity.cache_clear()                             # a second process
    assert key(p, tl) != k0


def test_good_map_grade_change_invalidates_the_segment_key(project, monkeypatch):
    p, tl = project()
    geo = profiles.load("geoverum")
    monkeypatch.setattr(profiles, "of", lambda _tl: geo)
    k0 = key(p, tl)
    pr = geo.data.print.model_copy(update={"grade_saturate": geo.data.print.grade_saturate - 0.02})
    graded = profiles.Profile("geoverum", geo.data.model_copy(update={"print": pr}), "x")
    monkeypatch.setattr(profiles, "of", lambda _tl: graded)
    assert key(p, tl) != k0


@pytest.mark.parametrize("change", [("MAP_VERSION", "map-x"), ("MAP_JPEG_QUALITY", 80), ("MAP_MAX_SIDE", 1000)])
def test_good_map_derivative_recipe_change_invalidates_both_keys(project, monkeypatch, change):
    p, tl = project()
    k0 = hyperframes.keys(tl.segments[2], tl, p)
    monkeypatch.setattr(maps, *change)
    k1 = hyperframes.keys(tl.segments[2], tl, p)
    assert k1[0] != k0[0] and k1[1] != k0[1]


def test_good_pillow_version_is_part_of_the_map_derivative(project, monkeypatch):
    import PIL
    p, tl = project()
    k0 = key(p, tl)
    monkeypatch.setattr(PIL, "__version__", PIL.__version__ + ".post1")
    assert key(p, tl) != k0


def test_good_map_crop_invalidates_the_segment_key(project):
    p, tl = project()
    k0 = hyperframes.keys(tl.segments[2], tl, p)
    m = p / "assets" / "map.jpg"
    with Image.open(m) as im:
        crop = im.crop((10, 0, im.width, im.height))
    m.unlink()                                   # a symlink into the shared test assets
    crop.save(m, quality=95)
    _, tl2 = project()
    k1 = hyperframes.keys(tl2.segments[2], tl2, p)
    assert k1[0] != k0[0] and k1[1] != k0[1]


def test_good_identity_is_memoised_per_process_and_on_disk(tools, monkeypatch):
    calls = []
    real = toolchain.blake3_files
    monkeypatch.setattr(toolchain, "blake3_files", lambda fs: calls.append(len(fs)) or real(fs))
    a = toolchain.hf_identity()
    hashed = len(calls)
    assert hashed and toolchain.hf_identity() == a and len(calls) == hashed          # per-process memo
    toolchain._identity.cache_clear()
    assert toolchain.hf_identity() == a and len(calls) == hashed                     # stat-keyed disk memo


def test_good_identity_does_not_depend_on_where_the_toolchain_lives(tools, tmp_path, monkeypatch):
    a = toolchain.hf_identity()
    moved = tmp_path / "elsewhere"
    shutil.copytree(tools, moved)
    for name, rel in {"VENDOR": "", "NODE": "node/bin/node",
                      "CHROME": "chrome/chrome-headless-shell-linux64/chrome-headless-shell",
                      "PUPPETEER": "npm/node_modules/puppeteer-core", "GSAP_JS": "js/gsap.min.js",
                      "TEMPLATE_DIR": "template", "PKG": "pkg"}.items():
        monkeypatch.setattr(config, name, moved / rel if rel else moved)
    assert toolchain.hf_identity() == a


def test_good_same_size_rewrite_of_a_toolchain_file_rehashes(tools):
    a = toolchain.hf_identity()
    (tools / "node/bin/node").write_text("node-binary-9")          # same size, content differs
    toolchain._identity.cache_clear()
    assert toolchain.hf_identity() != a


def test_good_unchanged_inputs_keep_the_key_and_the_second_render_is_a_cache_hit(project, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    p, tl = project()
    seg = tl.segments[2]
    out1, hit1 = hyperframes.render(seg, tl, p, tmp_path / "w1", workers=1)
    k1 = key(p, tl)
    monkeypatch.setattr(hyperframes, "run", lambda *a, **k: pytest.fail("re-rendered an unchanged segment"))
    out2, hit2 = hyperframes.render(seg, tl, p, tmp_path / "w2", workers=1)
    assert (hit1, hit2) == (False, True) and key(p, tl) == k1 and out2 == out1 and out2.name == f"{k1}.mp4"


def _two_cards(project, tmp_path):
    """Two cards whose images share the basename a.png: flat magenta in people/, flat teal in maps/."""
    imgs = {"people": (200, 30, 160), "maps": (20, 180, 170)}

    def mut(d):
        for folder, rgb in imgs.items():
            f = tmp_path / "proj" / "assets" / folder / "a.png"
            f.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (640, 360), rgb).save(f)
            d["credits_manifest"].append({"asset_id": f"card_{folder}", "path": f"assets/{folder}/a.png",
                                          "source_url": f"https://example.org/{folder}", "license": "CC0-1.0",
                                          "watermark_free": True})
        d["segments"][2]["gsap_scene_config"]["cards"] = [
            {"title": "People", "asset_ref": "card_people", "position": "top", "at_frame": 0},
            {"title": "Maps", "asset_ref": "card_maps", "position": "bottom", "at_frame": 0}]
    p, tl = project(mut)
    return p, tl, imgs


def test_good_cards_with_one_basename_render_their_own_images(project, tmp_path):
    p, tl, imgs = _two_cards(project, tmp_path)
    d, scene = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    res = hyperframes.probe_dom(d, [1.0], 1080, 1920, tmp_path / "snap")
    px = np.asarray(Image.open(res["times"][0]["png"]).convert("RGB")).astype(int)

    def share(region, rgb):
        return float((np.abs(region - np.array(rgb)).max(axis=2) <= 12).mean())
    top, bottom = px[:960], px[960:]
    assert share(top, imgs["people"]) > 0.03 and share(bottom, imgs["maps"]) > 0.03
    assert share(top, imgs["maps"]) < 0.001 and share(bottom, imgs["people"]) < 0.001
    assert len({c["img"] for c in scene["cards"]}) == 2


def test_good_card_build_name_follows_content_not_path(tmp_path):
    a, b, c = tmp_path / "x" / "a.png", tmp_path / "y" / "b.png", tmp_path / "z" / "a.png"
    for f, rgb in ((a, 1), (b, 1), (c, 2)):
        f.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4), (rgb, 0, 0)).save(f)
    assert hyperframes._card_name(a) == hyperframes._card_name(b) != hyperframes._card_name(c)


# ----------------------------------------------------------------------------------------------------------- bad
def test_bad_cards_with_one_basename_never_share_a_build_file(project, tmp_path):
    p, tl, _ = _two_cards(project, tmp_path)
    d, scene = hyperframes.build_dir(tl.segments[2], tl, p, tmp_path / "hf")
    built = [(d / c["img"]).read_bytes() for c in scene["cards"]]
    assert built[0] != built[1]
    assert built == [(p / "assets" / f / "a.png").read_bytes() for f in ("people", "maps")]


def test_bad_swapping_two_same_basename_cards_changes_the_key(project, tmp_path):
    p, tl, _ = _two_cards(project, tmp_path)
    k0 = key(p, tl)
    a, b = p / "assets/people/a.png", p / "assets/maps/a.png"
    da, db = a.read_bytes(), b.read_bytes()
    a.write_bytes(db)
    b.write_bytes(da)
    assert key(p, tl) != k0


def test_bad_missing_toolchain_parts_hash_as_missing_and_change_the_identity(tools):
    a = toolchain.hf_identity()
    shutil.rmtree(tools / "chrome")
    toolchain._identity.cache_clear()
    assert toolchain.tree_hash(config.CHROME.parent) == toolchain.MISSING and toolchain.hf_identity() != a


def test_bad_unrelated_files_do_not_change_the_key(project, tools, tmp_path):
    p, tl = project()
    k0 = key(p, tl)
    (tools / "fonts" / "Unused.ttf").write_text("x")              # a font no scene role uses
    (p / "notes.txt").write_text("x")
    assert key(p, tl) == k0
