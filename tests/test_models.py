"""3D model props: models.py renders (three.js on SwiftShader) to deterministic RGBA sprites and turntable sheets,
cached by content hash; Prop.model validation; props.py sheet cells; hf/props.js shows the right sheet cell; F01 on the
rendered sprite. Fixtures are small hand-written glTF / OBJ boxes."""
import json
import struct

import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import hyperframes, models, props, timeline
from video_engine.gates import props_gates
from video_engine.util import EngineError

from test_props import URL, _fs

BOX = (1.0, 0.5, 2.0)    # half extents: a long flat box, so its silhouette changes with yaw


def _box_mesh():
    hx, hy, hz = BOX
    pos, nrm, idx = [], [], []
    for axis in range(3):
        for sgn in (1, -1):
            n = [0.0, 0.0, 0.0]; n[axis] = sgn
            u, v = [(1, 2), (0, 2), (0, 1)][axis]
            base = len(pos)
            for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = [0.0, 0.0, 0.0]; p[axis] = sgn; p[u] = a; p[v] = b
                pos.append([p[0] * hx, p[1] * hy, p[2] * hz]); nrm.append(n)
            q = [base, base + 1, base + 2, base, base + 2, base + 3]
            if (sgn > 0) != (axis == 1):
                q = [base, base + 2, base + 1, base, base + 3, base + 2]
            idx += q
    return pos, nrm, idx


def _gltf(path, color=(0.8, 0.2, 0.1)):
    """A glTF box with its buffer in a separate .bin (a model dependency)."""
    pos, nrm, idx = _box_mesh()
    blob = b"".join(struct.pack("<3f", *p) for p in pos) + b"".join(struct.pack("<3f", *n) for n in nrm) + \
        b"".join(struct.pack("<H", i) for i in idx)
    blob += b"\0" * (-len(blob) % 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / (path.stem + ".bin")).write_bytes(blob)
    nv = len(pos)
    g = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0}]}],
         "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [*color, 1.0], "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
         "buffers": [{"uri": path.stem + ".bin", "byteLength": len(blob)}],
         "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": nv * 12},
                         {"buffer": 0, "byteOffset": nv * 12, "byteLength": nv * 12},
                         {"buffer": 0, "byteOffset": nv * 24, "byteLength": len(idx) * 2}],
         "accessors": [{"bufferView": 0, "componentType": 5126, "count": nv, "type": "VEC3",
                        "min": [-BOX[0], -BOX[1], -BOX[2]], "max": list(BOX)},
                       {"bufferView": 1, "componentType": 5126, "count": nv, "type": "VEC3"},
                       {"bufferView": 2, "componentType": 5123, "count": len(idx), "type": "SCALAR"}]}
    path.write_text(json.dumps(g))
    return path


def _obj(path, color="0.2 0.5 0.9", body=True):
    pos, _, idx = _box_mesh()
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / (path.stem + ".mtl")).write_text(f"newmtl m\nKd {color}\n")
    lines = [f"mtllib {path.stem}.mtl"]
    if body:
        lines += ["usemtl m"] + [f"v {x} {y} {z}" for x, y, z in pos] + \
            [f"f {idx[i] + 1} {idx[i + 1] + 1} {idx[i + 2] + 1}" for i in range(0, len(idx), 3)]
    path.write_text("\n".join(lines) + "\n")
    return path


def _alpha(p):
    with Image.open(p) as im:
        return im.mode, im.size, im.getchannel("A")


# render --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("make", [_gltf, _obj], ids=["gltf", "obj"])
def test_good_model_renders_to_an_rgba_sprite_with_transparent_border(tmp_path, make):
    src = make(tmp_path / "m" / f"box.{'gltf' if make is _gltf else 'obj'}")
    [f] = models.render(src, size=128)
    mode, size, a = _alpha(f)
    assert (mode, size) == ("RGBA", (128, 128))
    assert a.getpixel((0, 0)) == 0 and a.getpixel((64, 64)) == 255
    bb = a.point(lambda v: 255 if v > 8 else 0).getbbox()
    assert bb[0] > 0 and bb[1] > 0 and bb[2] < 128 and bb[3] < 128      # bounding-sphere fit leaves a border


def test_good_render_is_byte_identical_on_a_fresh_render(tmp_path):
    src = _gltf(tmp_path / "m" / "box.gltf")
    [f] = models.render(src, size=96, yaw=40)
    first = f.read_bytes()
    f.unlink()
    [g] = models.render(src, size=96, yaw=40)
    assert g.read_bytes() == first


def test_good_obj_material_colour_is_in_the_sprite(tmp_path):
    [f] = models.render(_obj(tmp_path / "m" / "box.obj", color="0.1 0.3 0.95"), size=96)
    r, g, b, a = Image.open(f).getpixel((48, 48))
    assert a == 255 and b > r + 60


def test_good_cache_hit_skips_the_render_and_key_follows_bytes_params_and_deps(tmp_path, monkeypatch):
    src = _gltf(tmp_path / "m" / "box.gltf")
    p = models.params(size=96)
    k0 = models.key(src, p)
    models.render(src, size=96)
    monkeypatch.setattr(models, "run", lambda *a, **k: pytest.fail("cache hit must not render"))
    models.render(src, size=96)
    assert models.key(src, models.params(size=96, yaw=31)) != k0
    (tmp_path / "m" / "box.bin").write_bytes((tmp_path / "m" / "box.bin").read_bytes()[:-4] + b"\0\0\0\1")
    assert models.key(src, p) != k0                                       # a referenced buffer changed


def test_good_turntable_frames_turn_and_compose_a_sheet(tmp_path):
    src = _gltf(tmp_path / "m" / "box.gltf")
    fs = models.render(src, size=64, frames=4, turn=360)
    assert len(fs) == 4 and len({f.read_bytes() for f in fs}) >= 2     # 0 and 90 degrees differ for a long box
    sh, n, cols = models.sheet(src, size=64, frames=4, turn=360)
    assert (n, cols) == (4, 2) and Image.open(sh).size == (128, 128)
    assert list(Image.open(sh).crop((64, 0, 128, 64)).get_flattened_data()) == list(Image.open(fs[1]).get_flattened_data())


def test_bad_missing_or_empty_model_raises(tmp_path):
    with pytest.raises(EngineError, match="not found"):
        models.render(tmp_path / "nope.glb")
    with pytest.raises(EngineError, match="empty model"):
        models.render(_obj(tmp_path / "m" / "empty.obj", body=False), size=64)
    with pytest.raises(EngineError, match="frames"):
        models.params(frames=models.FRAMES_MAX + 1)


def test_bad_transparent_render_names_the_missing_textures(tmp_path):
    src = _obj(tmp_path / "m" / "ghost.obj")
    src.with_suffix(".mtl").write_text("newmtl m\nKd 0.2 0.5 0.9\nd 0\nmap_d C:/Flowers.png\n")   # Windows export path
    assert models.missing(src) == [str(src.parent / "C:/Flowers.png")]
    assert src.with_suffix(".mtl") in models.deps(src)
    with pytest.raises(EngineError, match="fully transparent; missing referenced files: .*Flowers.png"):
        models.render(src, size=64)


# Prop.model ----------------------------------------------------------------------------------------------------------
MP = {"asset": "assets/box.gltf", "asset_id": "prop", "x": 0.5, "y": 0.45, "width_px": 200}


def test_good_model_prop_defaults_and_values():
    assert timeline.Prop(**MP).model == timeline.PropModel()
    timeline.Prop(**{**MP, "asset": "a.obj", "model": {"yaw": -90, "pitch": 45, "fov": 35, "size_px": 256,
                                                      "turntable_s": 4, "turn_deg": -360}})


@pytest.mark.parametrize("p", [{**MP, "asset": "assets/prop.png", "model": {}}, {**MP, "asset": "a.fbx"},
                               {**MP, "model": {"pitch": 95}}, {**MP, "model": {"turntable_s": 0.1}},
                               {**MP, "model": {"size_px": 32}}])
def test_bad_model_prop_values_rejected(p):
    with pytest.raises(ValidationError):
        timeline.Prop(**p)


def test_good_cell_follows_the_turntable_period():
    e = {"f_in": 10, "seq": {"n": 4, "cols": 2, "period": 8.0}}
    assert [props.cell(e, f) for f in range(10, 20)] == [0, 0, 1, 1, 2, 2, 3, 3, 0, 0]
    assert props.cell({"f_in": 0}, 33) == 0


# props integration ----------------------------------------------------------------------------------------------------
def _mproj(project, *ps, empty=False):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["props"] = list(ps)
        d["segments"][2]["gsap_scene_config"]["camera_preset"] = "zoom_in"
        d["credits_manifest"].append({"asset_id": "prop", "path": "assets/box.gltf", "source_url": URL,
                                      "license": "CC0-1.0", "watermark_free": True})
    d, _ = project(mut)
    _gltf(d / "assets" / "box.gltf")
    _obj(d / "assets" / "empty.obj", body=False)
    return d, timeline.load(d)


def test_good_turntable_prop_shows_the_cell_of_each_frame_in_chrome(project, tmp_path):
    d, tl = _mproj(project, {**MP, "model": {"size_px": 64, "turntable_s": 1.0}})
    out, sc = hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")
    e = sc["props"][0]
    assert e["seq"]["n"] == sc["fps"] and e["size"] == [64, 64] and (out / e["img"]).is_file()
    frames = [0, 7, 13, 29]
    res = hyperframes.probe_props(out, [f / sc["fps"] for f in frames], 1080, 1920)
    for f, item in zip(frames, res["times"]):
        g, k = item["props"][0], props.cell(e, f)
        assert g["cell"] == [k % e["seq"]["cols"], k // e["seq"]["cols"]], f
        assert g["bw"] == pytest.approx(props.state(e, sc["camera"], f, 1080, 1920)["w"], abs=1.0)


def test_good_f01_passes_a_rendered_model_and_f02_uses_the_sprite_size(project):
    d, tl = _mproj(project, {**MP, "model": {"size_px": 128}, "width_px": 300})
    f01 = _fs(d, tl, "F01")
    assert [f.level for f in f01] == ["pass"] and "3D model" in f01[0].message
    assert any(f.level == "fail" and "128 px" in f.message for f in _fs(d, tl, "F02"))


def test_bad_f01_fails_an_empty_model(project):
    d, tl = _mproj(project, {**MP, "asset": "assets/empty.obj"})
    [f] = _fs(d, tl, "F01")
    assert f.level == "fail" and "empty model" in f.message


def test_bad_turntable_sheet_wider_than_the_limit_fails_f01(project):
    d, tl = _mproj(project, {**MP, "model": {"size_px": 2048, "turntable_s": 2.0}})
    [f] = _fs(d, tl, "F01")
    assert f.level == "fail" and "wider than" in f.message


def test_good_prune_keeps_the_sprites_of_model_props(project, tmp_path):
    from video_engine import cache, cache_prune
    d, tl = _mproj(project, {**MP, "model": {"size_px": 64, "turntable_s": 0.5}})
    hyperframes.build_dir(tl.segments[2], tl, d, tmp_path / "hf")          # renders frames, sheet and index
    names = {f.name for f in (cache.config.CACHE_DIR / "models").iterdir()
             if f.name.startswith(props.model_cache_names(tl.segments[2], d, 30).pop()[:64])}
    keep, problems = cache_prune.referenced([d])
    assert not problems and len(names) == 15 + 2 and names <= keep             # 15 cells, the sheet, the index
