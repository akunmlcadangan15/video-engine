"""Props: free-positioned images over a HyperFrames map segment (gsap_scene_config.props and beats[].props).

`scene_props` builds the scene block hf/props.js renders; `state` is the Python mirror of hf/props.js (one prop at one
frame), used by the props gates, the caption avoid-list and the tests.

Frame f of a prop visible over [f_in, f_out) with camera (x, y, s) = layout.camera_at(scene camera, f), frame size
W x H, frame centre C = (W/2, H/2) and frame-centre map point c(f) = ((W/2 - x)/s, (H/2 - y)/s):
  map anchor, map point m (map px), depth d, reference frame r = f_in:
    k(f)    = (s(f) / s(r)) ^ (1 + d)
    centre  = C + s(r) * k(f) * [(m - c(r)) - (1 + d) * (c(f) - c(r))]
    width   = w_ref * k(f)          w_ref = width_px, or width_map * map width * s(r)
  (d = 0: centre = (x + s*mx, y + s*my), the map projection of m on every frame)
  screen anchor: centre = (x, y) frame px, width = width_px
  height = width * source height / source width
Enter (u = f - f_in, p = min(1, u / F_enter)) and exit (p = clamp((f - f_out + F_exit) / F_exit)), e = layout.EASES[ease](p):
  fade: opacity * e (enter) or * (1 - e) (exit); pop: size * e or * (1 - e);
  slide: offset -(1 - e) * distance * dir (enter) or e * distance * dir (exit), with the fade.
Idle (phase 0 at f_in, period P frames): bob: centre y + A sin(2 pi u / P); rotate: rotation + A sin(2 pi u / P).
Tilted camera (scene camera `t`): map props sit in the tilted layer; `state` keeps the flat-picture values plus
`tilt` [t, W, H] and `box` projects the flat box corners (camera3d.project).
Model props show the sprite models.py renders (`sprite`); a turntable shows cell floor(u * n / T) mod n of its n-cell
sheet (T = turntable_s x fps frames, n = min(models.FRAMES_MAX, round(T))), every cell the same size; the ink box is
the union over the cells.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from . import camera3d, config, projection
from .timeline import MODEL_EXT, Prop, Segment, Timeline, sec_to_frame
from .util import EngineError, blake3_files

SLIDE_PX = 80.0
SLIDE_DIR = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}
ALPHA_INK = 8          # alpha above this is prop ink (ink box)
CHUNK = 6              # frames per caption avoid box
SHEET_MAX = 8192       # widest turntable sheet (px)


def chip(title: str, out: Path, *, subtitle: str = "", year: str = "", style: str = "parchment",
         width: int = 620) -> Path:
    """Render an archival or ink badge PNG with transparent corners and 2x supersampling using vendored fonts
    (single `wght` axis on Playfair Display, Cinzel and EB Garamond; never `opsz`)."""
    from PIL import ImageDraw, ImageFont
    if style not in ("parchment", "ink"):
        raise EngineError(f"props.chip: unknown style {style!r} (expected 'parchment' or 'ink')")
    ss = 2
    W = max(240, int(width)) * ss
    pad_x, pad_y = 36 * ss, 26 * ss

    def vfont(filename: str, px: int, weight: int | None = None):
        f = ImageFont.truetype(str(config.FONTS_DIR / filename), px)
        if weight is not None:
            f.set_variation_by_axes([weight])
        return f

    f_year = vfont("Cinzel[wght].ttf", 28 * ss, 700) if year else None
    f_title = vfont("PlayfairDisplay[wght].ttf", 44 * ss, 700)
    f_sub = vfont("EBGaramond[wght].ttf", 30 * ss, 500) if subtitle else None

    inner_w = W - 2 * pad_x

    def wrap(txt: str, font) -> list[str]:
        lines, cur = [], ""
        for w in txt.split():
            cand = f"{cur} {w}".strip()
            if font.getlength(cand) <= inner_w or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    t_lines = wrap(title.strip(), f_title)
    s_lines = wrap(subtitle.strip(), f_sub) if subtitle.strip() and f_sub else []
    y_h = (38 * ss) if year.strip() else 0
    t_h = len(t_lines) * (52 * ss)
    s_h = (len(s_lines) * (36 * ss) + 12 * ss) if s_lines else 0
    H = pad_y * 2 + y_h + t_h + s_h

    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    rad = 18 * ss
    if style == "parchment":
        bg, border, c_year, c_title, c_sub = (
            (244, 235, 217, 242), (168, 132, 74, 255), (140, 59, 43, 255), (42, 35, 29, 255), (91, 58, 35, 255)
        )
    else:
        bg, border, c_year, c_title, c_sub = (
            (20, 24, 31, 232), (201, 162, 75, 235), (229, 184, 92, 255), (247, 242, 230, 255), (214, 206, 190, 255)
        )
    d.rounded_rectangle([2 * ss, 2 * ss, W - 2 * ss - 1, H - 2 * ss - 1], radius=rad, fill=bg,
                        outline=border, width=2 * ss)
    y = pad_y
    if year.strip() and f_year:
        yt = year.strip().upper()
        yw = f_year.getlength(yt)
        d.text(((W - yw) / 2, y), yt, font=f_year, fill=c_year)
        y += y_h
    for ln in t_lines:
        lw = f_title.getlength(ln)
        d.text(((W - lw) / 2, y), ln, font=f_title, fill=c_title)
        y += 52 * ss
    if s_lines and f_sub:
        y += 12 * ss
        for ln in s_lines:
            lw = f_sub.getlength(ln)
            d.text(((W - lw) / 2, y), ln, font=f_sub, fill=c_sub)
            y += 36 * ss

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.resize((W // ss, H // ss), Image.LANCZOS).save(out)
    return out


def flat(seg: Segment) -> list[tuple[str, Prop, int, int]]:
    """(source path, prop, f_in, f_out) of every prop of a segment in render order (segment props, then beat props),
    frames relative to the segment start; f_out exclusive."""
    sc = seg.gsap_scene_config
    if sc is None:
        return []
    n = seg.n_frames
    out = [(f"props[{i}]", p, p.t_in, p.t_out if p.t_out is not None else n) for i, p in enumerate(sc.props or [])]
    beats = sc.beats or []
    for j, b in enumerate(beats):
        end = beats[j + 1].at_frame if j + 1 < len(beats) else n
        for i, p in enumerate(b.props or []):
            out.append((f"beats[{j}].props[{i}]", p, b.at_frame + p.t_in,
                        b.at_frame + (p.t_out if p.t_out is not None else end - b.at_frame)))
    return out


def has_props(seg: Segment) -> bool:
    return bool(flat(seg))


def asset_name(src: Path) -> str:
    """Build-dir name of a prop image: its content hash (same scheme as card images)."""
    return f"prop_{blake3_files([src])[0][:32]}{src.suffix.lower()}"


def is_model(p: Prop) -> bool:
    return Path(p.asset).suffix.lower() in MODEL_EXT


def model_frames(p: Prop, fps: int) -> tuple[int, float]:
    """(cells, period in frames) of a model prop; (1, 0) for a still."""
    assert p.model is not None
    t = p.model.turntable_s
    if t is None:
        return 1, 0.0
    from . import models
    return max(2, min(models.FRAMES_MAX, round(t * fps))), t * fps


def sprite(p: Prop, project_dir: Path, fps: int) -> tuple[Path, int, int]:
    """(image, cells, columns) a prop shows: the asset itself, or the rendered model sprite or turntable sheet."""
    src = Path(project_dir) / p.asset
    if not src.is_file():
        raise EngineError(f"prop asset '{p.asset}' not found")
    if not is_model(p):
        return src, 1, 1
    from . import models
    assert p.model is not None
    m, (n, _) = p.model, model_frames(p, fps)
    if n > 1 and models.sheet_cols(n) * m.size_px > SHEET_MAX:
        raise EngineError(f"prop '{p.asset}': turntable sheet {models.sheet_cols(n)} x {m.size_px} px is wider than "
                          f"{SHEET_MAX}; lower model.size_px or turntable_s")
    return models.sheet(src, yaw=m.yaw, pitch=m.pitch, fov=m.fov, size=m.size_px, frames=n,
                        turn=m.turn_deg if n > 1 else 0.0)


def model_cache_names(seg: Segment, project_dir: Path, fps: int) -> set[str]:
    """Cache file names (kind `models`) of every model prop sprite of a segment; renders nothing."""
    from . import models
    out = set()
    for _, p, _, _ in flat(seg):
        if is_model(p) and (Path(project_dir) / p.asset).is_file():
            assert p.model is not None
            m, (n, _) = p.model, model_frames(p, fps)
            out |= models.cache_names(Path(project_dir) / p.asset, yaw=m.yaw, pitch=m.pitch, fov=m.fov,
                                      size=m.size_px, frames=n, turn=m.turn_deg if n > 1 else 0.0)
    return out


def sources(seg: Segment, project_dir: Path, fps: int) -> dict[str, Path]:
    """Build-dir name -> image of every prop of a segment."""
    out = {}
    for _, p, _, _ in flat(seg):
        img, _, _ = sprite(p, project_dir, fps)
        out[asset_name(img)] = img
    return out


def image_info(src: Path, cells: int = 1, cols: int = 1) -> tuple[int, int, bool, list[float] | None]:
    """(width, height, has alpha with transparent pixels, ink box [x0, y0, x1, y1] as fractions or None) of an image,
    or of one cell of a sheet of `cells` cells in `cols` columns (ink box: the union over the cells)."""
    with Image.open(src) as im:
        im.load()
        W, H = im.size
        rows = math.ceil(cells / cols)
        w, h = W // cols, H // rows
        alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        if not alpha:
            return w, h, False, None
        a = im.convert("RGBA").getchannel("A")
        lo, _ = a.getextrema()
        ink = None
        for i in range(cells):
            x, y = (i % cols) * w, (i // cols) * h
            bb = a.crop((x, y, x + w, y + h)).point(lambda v: 255 if v > ALPHA_INK else 0).getbbox()
            if bb:
                b = [bb[0] / w, bb[1] / h, bb[2] / w, bb[3] / h]
                ink = b if ink is None else [min(ink[0], b[0]), min(ink[1], b[1]), max(ink[2], b[2]), max(ink[3], b[3])]
        return w, h, lo < 255, ink


def cell(e: dict, f: float) -> int:
    """Sheet cell a turntable prop shows on frame f (0 for a still)."""
    q = e.get("seq")
    if not q:
        return 0
    return math.floor((f - e["f_in"]) * q["n"] / q["period"] + 1e-4) % q["n"]


def _motion(m, fps: int) -> dict | None:
    if m is None:
        return None
    out = {"kind": m.kind, "f": max(1, sec_to_frame(m.s, fps)), "ease": m.ease}
    if m.kind == "slide":
        v = SLIDE_DIR[m.dir or "up"]
        d = m.distance_px or SLIDE_PX
        out["dx"], out["dy"] = round(v[0] * d, 3), round(v[1] * d, 3)
    return out


def map_point(p: Prop, sc, mw: float, mh: float) -> tuple[float, float]:
    if p.path is not None:
        return p.path[0][0] * mw, p.path[0][1] * mh
    if p.x is not None:
        assert p.y is not None
        return p.x * mw, p.y * mh
    if not sc.map_bounds:
        raise EngineError(f"prop '{p.asset}' uses lat/lng but gsap_scene_config.map_bounds is missing")
    assert p.lat is not None and p.lng is not None
    x, y = projection.to_map(p.lat, p.lng, sc.map_bounds, projection.of(sc))
    return x * mw, y * mh


def scene_props(seg: Segment, tl: Timeline, project_dir: Path, cam: dict, mw: float, mh: float) -> list[dict]:
    """Scene block of hf/props.js: one entry per prop in render order. Images by content-hash name."""
    from . import layout
    sc, fps = seg.gsap_scene_config, tl.settings.fps
    out = []
    for i, (where, p, f_in, f_out) in enumerate(flat(seg)):
        try:
            src, n, cols = sprite(p, project_dir, fps)
        except EngineError as err:
            raise EngineError(f"{where}: {err}") from err
        w, h, _, ink = image_info(src, n, cols)
        e = {"id": f"prop:{i}", "img": "assets/" + asset_name(src), "size": [w, h], "ink": [round(v, 5) for v in ink or [0, 0, 1, 1]],
             "anchor": p.anchor, "f_in": f_in, "f_out": f_out, "rot": p.rotation, "op": p.opacity, "z": p.z}
        if n > 1:
            e["seq"] = {"n": n, "cols": cols, "period": round(model_frames(p, fps)[1], 6)}
        if p.anchor == "map":
            mx, my = map_point(p, sc, mw, mh)
            r = layout.camera_at(cam, f_in)
            if p.width_px is not None:
                pw = p.width_px
            else:
                assert p.width_map is not None
                pw = p.width_map * mw * r["s"]
            e.update(m=[round(mx, 3), round(my, 3)], d=p.depth, w=round(pw, 3))
            if p.path is not None:
                e["path"] = {"pts": [[round(x * mw, 3), round(y * mh, 3)] for x, y in p.path], "heading": p.heading,
                             "f": max(1, sec_to_frame(p.path_s, fps) if p.path_s else f_out - f_in - 1)}
        else:
            e.update(p=[p.x, p.y], d=0.0, w=p.width_px)
        for k in ("enter", "exit"):
            if (m := _motion(getattr(p, k), fps)) is not None:
                e[k] = m
        if p.idle:
            e["idle"] = {"kind": p.idle.kind, "a": p.idle.amplitude, "period": round(p.idle.period_s * fps, 6)}
        if p.shadow:
            e["shadow"] = p.shadow.model_dump()
        out.append(e)
    return out


def _clamp(v: float) -> float:
    return min(1.0, max(0.0, v))


def path_at(path: dict, u: float) -> tuple[float, float, float]:
    """Map point (x, y) and direction (deg, clockwise from +x) of a path prop u frames after its first frame: arc
    length share glide(u / f) of the polyline; the direction is the segment's under that point."""
    from . import layout
    pts = path["pts"]
    segs = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    d = layout.EASES["glide"](_clamp(u / path["f"])) * sum(segs)
    for (a, b), L in zip(zip(pts, pts[1:]), segs):
        if L > 0 and d <= L:
            return a[0] + (b[0] - a[0]) * d / L, a[1] + (b[1] - a[1]) * d / L, math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        d -= L
    a, b = next((q, r) for q, r in reversed(list(zip(pts, pts[1:]))) if q != r)
    return pts[-1][0], pts[-1][1], math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def state(e: dict, cam: dict, f: float, W: int, H: int) -> dict | None:
    """Centre (x, y), size (w, h), rotation (deg) and opacity of scene prop `e` at frame f; None = not shown."""
    from . import layout
    if not e["f_in"] <= f < e["f_out"]:
        return None
    u = f - e["f_in"]
    op, size, ox, oy, rot = e["op"], 1.0, 0.0, 0.0, e["rot"]
    for k in ("enter", "exit"):
        m = e.get(k)
        if not m:
            continue
        p = _clamp(u / m["f"]) if k == "enter" else _clamp((f - e["f_out"] + m["f"]) / m["f"])
        v = layout.EASES[m["ease"]](p)
        a = v if k == "enter" else 1.0 - v
        if m["kind"] == "pop":
            size *= a
        else:
            op *= a
        if m["kind"] == "slide":
            t = -(1.0 - v) if k == "enter" else v
            ox, oy = ox + t * m["dx"], oy + t * m["dy"]
    tilt = 0.0
    if e["anchor"] == "map":
        c = layout.camera_at(cam, f)
        r = layout.camera_at(cam, e["f_in"])
        d = e["d"]
        k = (c["s"] / r["s"]) ** (1 + d)
        cx, cy = (W / 2 - c["x"]) / c["s"], (H / 2 - c["y"]) / c["s"]
        rx, ry = (W / 2 - r["x"]) / r["s"], (H / 2 - r["y"]) / r["s"]
        sig = r["s"] * k
        mx, my = e["m"]
        if (pa := e.get("path")):
            mx, my, ang = path_at(pa, u)
            rot += ang if pa["heading"] else 0.0
        x = W / 2 + sig * ((mx - rx) - (1 + d) * (cx - rx))
        y = H / 2 + sig * ((my - ry) - (1 + d) * (cy - ry))
        w = e["w"] * k
        tilt = c.get("t", 0.0)
    else:
        x, y = e["p"]
        w = e["w"]
    if (i := e.get("idle")):
        sn = math.sin(2 * math.pi * u / i["period"])
        if i["kind"] == "bob":
            oy += i["a"] * sn
        else:
            rot += i["a"] * sn
    w *= size
    st = {"x": x + ox, "y": y + oy, "w": w, "h": w * e["size"][1] / e["size"][0], "rot": rot, "opacity": op}
    return {**st, "tilt": [tilt, W, H]} if tilt else st


def box(e: dict, st: dict) -> tuple[float, float, float, float]:
    """Screen box (x0, y0, x1, y1) of a prop state: its rotated ink box, grown by the shadow reach."""
    x0, y0, x1, y1 = e["ink"]
    w, h = st["w"], st["h"]
    pts = [((a - 0.5) * w, (b - 0.5) * h) for a, b in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    t = math.radians(st["rot"])
    cs, sn = math.cos(t), math.sin(t)
    xs = [st["x"] + px * cs - py * sn for px, py in pts]
    ys = [st["y"] + px * sn + py * cs for px, py in pts]
    b = [min(xs), min(ys), max(xs), max(ys)]
    if (s := e.get("shadow")):
        sh = (b[0] + s["dx"] - s["blur"], b[1] + s["dy"] - s["blur"], b[2] + s["dx"] + s["blur"], b[3] + s["dy"] + s["blur"])
        b = [min(b[0], sh[0]), min(b[1], sh[1]), max(b[2], sh[2]), max(b[3], sh[3])]
    if st.get("tilt"):   # map props of a tilted camera: the flat box corners through the tilt (camera3d.project)
        t, W, H = st["tilt"]
        q = [camera3d.project(x, y, t, W, H)[:2] for x, y in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]))]
        b = [min(p[0] for p in q), min(p[1] for p in q), max(p[0] for p in q), max(p[1] for p in q)]
    return tuple(b)


def frames(e: dict, cam: dict, W: int, H: int, min_opacity: float = 0.05):
    """(frame, state, box) of every frame the prop shows with opacity > min_opacity."""
    for f in range(e["f_in"], e["f_out"]):
        st = state(e, cam, f, W, H)
        if st and st["opacity"] > min_opacity and st["w"] > 0:
            yield f, st, box(e, st)


def avoid_boxes(seg: Segment, scene: dict, clear: float) -> list[dict]:
    """Caption avoid-list entries of a segment's props: one box per CHUNK frames (union of the frame boxes) with the
    absolute frame range it holds for (`frames`, end exclusive)."""
    W, H, cam = scene["width"], scene["height"], scene["camera"]
    out = []
    for e in scene.get("props", []):
        cur = None
        for f, _, b in frames(e, cam, W, H):
            c = (f - e["f_in"]) // CHUNK
            if cur is None or cur[0] != c:
                if cur:
                    out.append(cur[1])
                cur = (c, {"id": e["id"], "text": "", "x": b[0], "y": b[1], "x1": b[2], "y1": b[3], "clear": clear,
                           "frames": [seg.start_frame + f, seg.start_frame + f + 1]})
            r = cur[1]
            r["x"], r["y"], r["x1"], r["y1"] = min(r["x"], b[0]), min(r["y"], b[1]), max(r["x1"], b[2]), max(r["y1"], b[3])
            r["frames"][1] = seg.start_frame + f + 1
        if cur:
            out.append(cur[1])
    for r in out:
        r["w"], r["h"] = r.pop("x1") - r["x"], r.pop("y1") - r["y"]
    return out
