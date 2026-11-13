"""HyperFrames scene dictionary builder, theme/font helpers, counter math, and split-panel decomposition."""
from __future__ import annotations

import copy
import math
import re
import sys
from pathlib import Path
from urllib.parse import quote

from . import (
    assets,
    camera3d,
    camera_full3d,
    components,
    config,
    entities,
    fx,
    globe,
    legibility,
    parallax,
    printstyle,
    profiles,
    props,
)
from .hf_camera import (
    _pin_xy,
    camera_frames,
    camera_frames_sig,
    camera_plan,
    card_frame,
    motion_px,
    pin_frame,
)
from .hf_probe import _INKS, probe_dom
from .maps import (
    MAP_JPEG_QUALITY,
    _q,
    deep_entries,
    deep_plan,
    map_image_size,
    morph_data,
    texture_dim,
)
from .timeline import HYPERFRAMES, GsapScene, Segment, SplitView, Timeline
from .units import units
from .util import EngineError, blake3_files, blake3_json

HF_VERSION = "hf-2"   # hf-2: capture/encode profile HF_QUALITY + HF_CRF
HF_FONTS = ("Inter-Bold.otf", "Inter-SemiBold.otf")   # copied into every HF build dir; hashed into the cache key
THEME_FONTS = {"script": ("EngScript", "KaushanScript-Regular.ttf", "400"),   # optional theme font roles: name, filename, weight
               "title": ("EngTitle", "ArchivoBlack-Regular.ttf", "400"),
               "serif": ("EngSerif", "PlayfairDisplay[wght].ttf", "400 900"),
               "garamond": ("EngGaramond", "EBGaramond[wght].ttf", "400 800"),
               "garamond_italic": ("EngGaramondI", "EBGaramond-Italic[wght].ttf", "400 800"),
               "cinzel": ("EngCinzel", "Cinzel[wght].ttf", "400 900"),
               "hand": ("EngHand", "Kalam-Bold.ttf", "700")}

SCENE_CACHE_MAX = 64     # memo entries of scene_data() (one build calls it several times per segment)
INK_CACHE_MAX = 64       # memo entries of the adaptive ink choices
HF_QUALITY = "standard"  # defaults; profile [render] hf_quality / hf_crf override them (encode_settings)
HF_CRF = 14
CARD_TOP = {"top": 0.12, "bottom": 0.77}
TITLE_TOP = 0.11                       # big title top edge (fraction of the frame height)
CARD_TOP_LANDSCAPE = {"top": 0.10, "bottom": 0.70}   # landscape (units.py): cards fit the z1 / z2 bands
TITLE_TOP_LANDSCAPE = 0.10
FIGURE_POP_S = {"elastic": 0.9, "back": 0.45}   # entry of a non-counter figure by theme figure_pop (template twin)
TITLE_GAP_EM = 0.5                      # theme title_anchor "region": gap between the title and its region (title em)
TEMPLATE_SCALED = {"label_box_radius_px"}   # the template scales these itself (S.width / reference width)

PROPS_JS = config.PKG / "hf" / "props.js"   # props layer script; in the build dir and the key of prop segments only
FX_BACKDROP_JS = config.PKG / "hf" / "fx_backdrop.js"
FX_TIMEZONE_JS = config.PKG / "hf" / "fx_timezones.js"
FX_BUBBLE_JS = config.PKG / "hf" / "fx_bubbles.js"
SCENE3D_JS = config.PKG / "hf" / "scene3d.js"   # full 3D camera (camera_full3d.py), an ES module like globe.js
FX2_JS = fx.FX2_JS

_SCENES: dict[str, dict] = {}   # process-local: scene key -> scene_data, bounded by SCENE_CACHE_MAX


def tint(hex_colour: str, t: float) -> str:
    """`hex_colour` mixed `t` (0-1) of the way toward white, "#rrggbb" lower case."""
    rgb = [int(hex_colour[k:k + 2], 16) for k in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (255 - c) * t):02x}" for c in rgb)


def font_roles(scene: dict) -> list[str]:
    """Font roles a scene uses: the theme's label and title fonts, the serif when it shows a year; with a profile
    [print] the roles of its surface instead of the area-label title font; the title font for map texts."""
    th = scene.get("theme", {})
    used = {th.get("label_font"), th.get("title_font")}
    if any(f["kind"] == "year" for f in scene.get("figures", [])):
        used.add("serif")
    if pr := scene.get("print"):
        used |= {r[0] for r in pr["roles"].values()}
    elif scene.get("layout", {}).get("areas"):
        used.add("title")
    if scene.get("map_texts"):
        used.add("title")
    return [r for r in THEME_FONTS if r in used]


def font_files(scene: dict) -> list[str]:
    return [*HF_FONTS, *(THEME_FONTS[r][1] for r in font_roles(scene))]


def font_faces(scene: dict) -> str:
    """@font-face rules of the scene's theme fonts (empty when it uses none)."""
    return "".join(f"@font-face{{font-family:'{fam}';src:url('assets/{quote(f)}') format('truetype');font-weight:{w}}}"
                   for fam, f, w in (THEME_FONTS[r] for r in font_roles(scene)))


def landscape_theme(theme: dict, k: float) -> dict:
    """Theme px lengths (keys ending in `_px`, px at the 1920x1080 reference canvas) in output px for a landscape
    frame (units.k; 1 at 1920x1080). Portrait themes are passed unchanged (the template takes them as output px)."""
    if k == 1.0:
        return theme
    return {key: (v * k if key.endswith("_px") and key not in TEMPLATE_SCALED and isinstance(v, (int, float))
                  and not isinstance(v, bool) else v)
            for key, v in theme.items()}


def _bounded_put(memo: dict, key: str, value, limit: int) -> None:
    """Insert key -> value and evict the oldest entry when the memo is over `limit` (in-process memo, never grows
    without bound in a long-lived engine process)."""
    memo.pop(key, None)
    memo[key] = value
    while len(memo) > limit:
        memo.pop(next(iter(memo)))


def _scene_key(seg: Segment, tl: Timeline, project_dir: Path) -> str:
    """Inputs of scene_data: the scene config, the timeline outside the segments, the profile and the map file.
    A beats scene lets apply_segment derive camera_path and element at_frame values, so those derived fields are
    excluded; every other scene keeps them, and the project directory is part of the key because relative assets
    (morph, cards, props) live there even when two projects share one symlinked map."""
    from . import layout

    assert seg.gsap_scene_config is not None
    scfg: GsapScene = seg.gsap_scene_config
    if layout.is_beats(seg, tl):
        sc = scfg.model_dump(mode="json", exclude={"camera_path", "title_at_frame"})
        for k in ("pins", "regions", "routes", "figures", "cards"):
            for e in sc.get(k) or []:
                e.pop("at_frame", None)
    else:
        sc = scfg.model_dump(mode="json")
    for item in [*(sc.get("pins") or []), *(sc.get("regions") or [])]:
        if item.get("flag") is None:
            item.pop("flag", None)
        for change in item.get("changes", []):
            if change.get("flag") is None:
                change.pop("flag", None)
    p = project_dir / scfg.map_asset
    try:
        st = p.stat()
        src = [str(p.resolve()), st.st_mtime_ns, st.st_size]
    except OSError:
        src = [str(p.resolve()), None, None]
    deps = [scfg.map_asset]
    if scfg.morph:
        deps.append(scfg.morph.asset)
    deps += [L.asset for L in (scfg.layers or [])]
    deps += [pth.asset for _, pth, _, _ in props.flat(seg)]
    for c in scfg.cards:
        if c.asset_ref and (cr := tl.credit_for(ref=c.asset_ref)) and cr.path:
            deps.append(cr.path)
    deps += [str(assets.resolve(i.asset, project=project_dir, kind="image").path) for i in scfg.insets or []]
    flags = [p.flag for p in scfg.pins]
    flags += [f for r in scfg.regions or [] for f in [r.flag, *(c.flag for c in r.changes)]]
    flags += [c.flag for c in scfg.flag_characters or []]
    deps += [str(assets.resolve(assets.flag_for(f.code, f.variant), project=project_dir, kind="flag").path)
             for f in flags if f]
    files = sorted({str((Path(project_dir) / d).resolve()) for d in deps})
    asset_hashes = [blake3_files([Path(f)])[0] if Path(f).is_file() else None for f in files]
    _hf = sys.modules.get("video_engine.hyperframes")
    cur_deep = getattr(_hf, "deep_plan", deep_plan)
    cur_probe = getattr(_hf, "probe_dom", probe_dom)
    hooks = [id(fn) for fn in (cur_deep, morph_data, map_image_size, cur_probe, props.scene_props,
                               parallax.scene_layers, layout.apply_segment, legibility.all_times,
                               legibility.collect, legibility.elements, legibility.choose, legibility.scene_inks)]
    prof = profiles.of(tl)
    ov_dump = [o.model_dump(mode="json") for o in seg.overlays] if seg.overlays else None
    return blake3_json({"scene": sc, **({"overlays": ov_dump} if ov_dump else {}), "engine": seg.engine_type, "n": seg.n_frames,
                        "project": str(Path(project_dir).resolve()), "assets": [files, asset_hashes], "hooks": hooks,
                        "tl": tl.model_dump(mode="json", exclude={"segments", "captions", "captions_meta"}),
                        "profile": [prof.name, prof.blake3, prof.data.model_dump(mode="json", exclude={"fx"})],
                        "legibility": legibility.code_hash(), "map": src})


def clear_scene_cache() -> None:
    """Drop the in-process scene memo (tests; a caller that mutates a timeline outside apply_segment)."""
    _SCENES.clear()
    _INKS.clear()


def _flag_data(flag, prof, project_dir: Path, region_aspect: float | None = None) -> dict:
    asset = assets.resolve(assets.flag_for(flag.code, flag.variant), project=project_dir, kind="flag")
    out = {**prof.data.flags.model_dump(), **flag.model_dump(exclude_none=True, exclude={"code", "variant"}),
           "href": f"assets/{asset.sha256[:16]}.svg", "aspect": _svg_aspect(asset.path)}
    loss = out.pop("auto_max_loss")
    if out["fit"] == "auto":
        out["fit"] = "cover" if region_aspect is None else flag_fit(region_aspect, out["aspect"], loss)
    return out


def flag_fit(region_aspect: float, flag_aspect: float, max_loss: float) -> str:
    """Automatic flag fit of one region: `cover` keeps the region filled and crops the flag; the share of the flag
    cut away is 1 - min(r/a, a/r) for region bbox aspect r and flag aspect a. Past `max_loss` (a tall or very wide
    region: Myanmar or Chile against a 4:3 flag) the crop would leave a strip nobody recognises, so the flag is
    `contain`ed whole inside the region bbox instead."""
    if not (region_aspect > 0 and flag_aspect > 0):
        return "cover"
    return "contain" if 1 - min(region_aspect / flag_aspect, flag_aspect / region_aspect) > max_loss else "cover"


def region_aspect(polygon: list[list[float]], mw: float, mh: float, globe_scene: bool) -> float:
    """Width/height of a region's bbox as drawn: map px on a flat map; on the globe the lng span shrinks by
    cos(mean lat) (the orthographic view near the region's centre), the polygon being equirectangular 0..1."""
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    if dy <= 0:
        return 0.0
    if not globe_scene:
        return dx * mw / (dy * mh)
    lat = math.radians(90 - (max(ys) + min(ys)) / 2 * 180)
    return dx * 360 * math.cos(lat) / (dy * 180)


def _svg_aspect(path) -> float:
    """width/height of an SVG's viewBox (4:3 fallback). The template fits flags itself: Chrome treats an
    `<image>`-referenced SVG as `meet` whatever its preserveAspectRatio says, so `slice` alone never covers."""
    m = re.search(r'viewBox\s*=\s*"\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', Path(path).read_text()[:2000])
    return round(float(m[1]) / float(m[2]), 4) if m and float(m[2]) > 0 else round(4 / 3, 4)


def scene_data(seg: Segment, tl: Timeline, project_dir: Path) -> dict:
    key = _scene_key(seg, tl, project_dir)
    if (hit := _SCENES.get(key)) is not None:
        return copy.deepcopy(hit)
    from . import layout

    lay = layout.apply_segment(seg, tl, project_dir)   # beats scenes: derived camera_path and frames first
    S = tl.settings
    assert seg.gsap_scene_config is not None
    sc: GsapScene = seg.gsap_scene_config
    n = seg.n_frames
    prof = profiles.of(tl)
    theme = prof.render_values("hf")
    U = units(tl)
    if U.landscape:
        theme = landscape_theme(theme, U.k)
    card_top, title_top = (CARD_TOP_LANDSCAPE, TITLE_TOP_LANDSCAPE) if U.landscape else (CARD_TOP, TITLE_TOP)
    G = C3 = None
    cam: dict
    if sc.globe:   # the 2D camera stays at identity over a frame-sized #cam; hf/globe.js draws the globe and moves pins
        G = globe.scene_block(seg, tl, project_dir, theme)
        if sc.globe.halo:
            G["halo"] = fx.halo_data(prof.fx.halo, sc.globe.halo)
        if sc.geoid:
            G["geoid"] = fx.geoid_block(seg, tl, prof)
        mw, mh = float(S.width), float(S.height)
        still = {"x": 0.0, "y": 0.0, "s": 1.0}
        cam = {"start": still, "end": still, "legs": [{"f0": 0, "f1": n, "to": still, "ease": "glide"}], "leg_motion": []}
        km = globe.key_motion(G, S.width, S.height)
        motion = min(km) if km else 0.0
    elif sc.camera3d:   # full 3D camera: #cam stays at identity; hf/scene3d.js draws the map plane and moves pins
        iw, ih = map_image_size(project_dir / sc.map_asset)
        cover = max(S.width / iw, S.height / ih)
        mw, mh = iw * cover, ih * cover
        states = camera_full3d.frames(sc.camera3d, n, mw, mh)
        still = {"x": 0.0, "y": 0.0, "s": 1.0}
        cam = {"start": still, "end": still, "legs": [{"f0": 0, "f1": n, "to": still, "ease": "glide"}], "leg_motion": []}
        met = camera_full3d.metrics(states, S.width, S.height, S.fps)
        motion = met["motion_px"]
        C3 = {"frames": [st.as_list() for st in states], "near_frac": camera_full3d.NEAR_FRAC,
              "need_w": round(camera_full3d.need_w(states, S.width, S.height, mw), 2),
              "metrics": {k: round(v, 3) for k, v in met.items()}}
    else:
        iw, ih = map_image_size(project_dir / sc.map_asset)
        cover = max(S.width / iw, S.height / ih)
        mw, mh = iw * cover, ih * cover
        cam = camera_plan(sc, S.width, S.height, mw, mh, n, S.fps, theme)
        motion = min(cam["leg_motion"]) if cam["leg_motion"] else motion_px(cam["start"], cam["end"], S.width, S.height)
    pins = []
    for i, p in enumerate(sc.pins):
        f = pin_frame(p, i, n, S.fps)
        if G:
            assert p.lat is not None and p.lng is not None
            lat: float = p.lat
            lng: float = p.lng
            xy = globe.project(globe.camera_at(G, f, S.fps), lat, lng, S.width, S.height) or (-1000.0, -1000.0)
            px, py = xy
        elif C3:
            x0, y0 = _pin_xy(p, sc, mw, mh)
            st = states[min(f, n - 1)]
            px, py, _ = camera_full3d.project(st, (x0, -y0, 0.0), S.width, S.height)
            px, py = (-1000.0, -1000.0) if px is None else (px, py)
        else:
            px, py = _pin_xy(p, sc, mw, mh)
        pins.append({"label": p.label, "px": round(px, 2), "py": round(py, 2),
                     "at_frame": f, **({"label_side": p.label_side} if p.label_side else {}),
                     **({"box": p.box} if p.box is not None else {}),
                     **({"lat": p.lat, "lng": p.lng} if G else {}),
                     **({"mx": round(x0, 3), "my": round(y0, 3)} if C3 else {}),
                     **({"flag": _flag_data(p.flag, prof, project_dir)} if p.flag else {})})
    cards = []
    for i, c in enumerate(sc.cards):
        img = None
        if c.asset_ref:
            cr = tl.credit_for(ref=c.asset_ref)
            if cr is None or not cr.path:
                raise EngineError(f"card '{c.title}': asset_ref '{c.asset_ref}' has no credits_manifest entry with a path")
            img = cr.path
        cards.append({"title": c.title, "subtitle": c.subtitle, "img": img, "top": round(card_top[c.position] * S.height),
                      "at_frame": card_frame(c, n, S.fps)})
    ecol = entities.scene_colours(seg, tl, project_dir)   # vivid on a dark map (theme vivid_chroma_scale)
    if theme["label_style"] == "box":
        for c, cd in zip(sc.cards, cards):
            cd["box"] = entities.box_colour(ecol[c.entity], theme) if c.entity else theme["label_box_colour"]
    out: dict = {"fps": S.fps, "n_frames": n, "width": S.width, "height": S.height, "map": sc.map_asset,
                 "map_size": [round(mw, 2), round(mh, 2)],
                 "camera": {"start": cam["start"], "end": cam["end"], "legs": cam["legs"],
                            **({"tilt": {"p": camera3d.perspective_px(S.height)}} if "t" in cam["start"] else {})},
                 "motion_px": round(motion, 2), "pins": pins, "cards": cards, "theme": theme}
    if not G and not C3:
        out["camera_frames"] = camera_frames(out["camera"], n)
        out["camera_frames_sig"] = camera_frames_sig(out["camera"], S.fps, n)
    if C3:
        out["cam3d"] = C3
    if U.landscape:   # JS reads S.units (k, ref, safe) instead of the portrait W/1080, H/1920; portrait scenes keep their key
        out["units"] = U.scene()
    if (mq := prof.render.map_jpeg_quality) and mq != MAP_JPEG_QUALITY:
        out["map_q"] = mq
    if sc.regions:
        out["regions"] = [region_data(r, ecol, theme, mw, mh, G, S, lay is not None, prof.fx) for r in sc.regions]
        for r, data in zip(sc.regions, out["regions"]):
            ra = region_aspect([p for ring in r.all_rings() for p in ring], mw, mh, G is not None)
            if r.flag:
                data["flag"] = _flag_data(r.flag, prof, project_dir, ra)
            for change, target in zip(r.changes, data["changes"]):
                if change.flag:
                    target["flag"] = _flag_data(change.flag, prof, project_dir, ra)
    if sc.borders:
        out["borders"] = [{"points": [[round(x * mw, 2), round(y * mh, 2)] for x, y in b.points], "at_frame": b.at_frame,
                           "draw_s": theme["border_draw_s"] if b.draw_s is None else b.draw_s, "colour": b.colour.lower(),
                           "dashed": b.dashed, "closed": b.closed,
                           **({"w": b.width_px} if b.width_px is not None else {}),
                           **({"glow": b.glow_px} if b.glow_px is not None else {}),
                           **({"grow": b.grow} if b.grow else {}),
                           **({"nocase": True} if not b.casing else {})} for b in sc.borders]
    if sc.map_texts:
        out["map_texts"] = [{"text": m.text, "points": [[round(x * mw, 2), round(y * mh, 2)] for x, y in m.points],
                             "at_frame": m.at_frame, "out_frame": m.out_frame, "reveal_s": m.reveal_s, "px": m.px,
                             "colour": m.colour.lower(), "depth": m.depth_px} for m in sc.map_texts]
    col = ecol
    if sc.title:
        out["title"] = {"text": sc.title, "entity": sc.title_entity,
                        "colour": tint(col.get(sc.title_entity or "", "#ffffff"), theme["title_tint"]),
                        "at_frame": sc.title_at_frame or 0, "top": round(title_top * S.height)}
        if prof.theme.title_anchor == "region" and lay is None and not G and not C3:
            if (at := title_place(sc, cam, mw, mh, tl, theme["title_px"])) is not None:
                out["title"]["left"], out["title"]["top"] = at
    if sc.figures:
        out["figures"] = [{"kind": f.kind, "text": figure_text(f, f.value, theme), "value": f.value, "from": f.count_from,
                           "decimals": f.decimals, "prefix": f.prefix, "suffix": f.suffix, "motion": f.motion,
                           "count_s": f.count_s or theme["counter_s"], "at_frame": f.at_frame,
                           "x": round(f.position[0] * (mw if f.anchor == "map" else S.width), 2),
                           "y": round(f.position[1] * (mh if f.anchor == "map" else S.height), 2),
                           **({"px": f.px} if f.px else {}), **({"map": True} if f.anchor == "map" else {}),
                           "entity": f.entity, "colour": col.get(f.entity or "") or _accent(f, prof.theme) or "#ffffff",
                           **({"accent": _accent(f, prof.theme)} if not f.entity and _accent(f, prof.theme) else {})}
                          for f in sc.figures]
    if sc.thermometers:
        starts = components.thermo_starts(sc.thermometers)
        out["thermometers"] = [components.thermo_data(t, S.width, S.height, start)
                               for t, start in zip(sc.thermometers, starts)]
    if sc.flows:
        out["flows"] = [components.flow_data(f, mw, mh, fx._seed(tl, seg)) for f in sc.flows]
        out["flow_legend"] = sc.flow_legend
    boxed = theme["label_style"] == "box" and (lay is None or prof.print is None)
    if boxed and out.get("title"):
        t = out["title"]
        t["box"] = entities.box_colour(col[t["entity"]], theme) if t["entity"] else theme["label_box_colour"]
    for fm, f in zip(sc.figures or [], out.get("figures", [])):
        on = fm.box if fm.box is not None else boxed
        if not on:
            continue
        if fm.box is None and f["kind"] == "year" and theme["year_box_colour"]:
            f.update(box=theme["year_box_colour"], ink=theme["year_box_ink"], px=theme["year_box_px"],
                     texture=theme["year_box_texture"], dim=texture_dim(theme["year_box_texture"]))
        else:
            f["box"] = theme["label_box_colour"]
    if prof.legibility:
        out["legibility"] = {**prof.legibility.model_dump(), "v": legibility.code_hash()}
        for t in [out.get("title"), *out.get("figures", [])]:
            if t:
                t["entity_colour"] = col.get(t["entity"]) if t.get("entity") else None
        for r in out.get("regions", []):
            for x in (r, *r["changes"]):
                x["casing"] = legibility.line_casing(r["edge_colour"] if r["style"] == "block" else x["colour"],
                                                     out["legibility"])
        for b in out.get("borders", []):
            b["casing"] = legibility.line_casing(b["colour"], out["legibility"])
    if prof.print:
        surf = printstyle.surface(project_dir / sc.map_asset, prof.print)
        out["print"] = printstyle.scene_block(prof.print, surf)
        for r in out.get("regions", []):
            for x in (r, *r["changes"]):
                x["edge_ink"] = printstyle.edge_ink(x["colour"], prof.print, surf)
    if sc.routes:
        out["routes"] = [{"points": [[round(x * mw, 2), round(y * mh, 2)] for x, y in r.points], "at_frame": r.at_frame,
                          "draw_s": r.draw_s or theme["route_draw_s"]} for r in sc.routes]
    if lay is not None:
        out["layout"] = layout.scene_layout(lay)
        for r, pts in zip(out.get("routes", []), lay.routes):
            r["points"] = [[round(x, 2), round(y, 2)] for x, y in pts]
    if sc.layers:
        out["layers"] = parallax.scene_layers(seg, tl, project_dir)
    if props.has_props(seg):
        out["props"] = props.scene_props(seg, tl, project_dir, out["camera"], mw, mh)
    _hf = sys.modules.get("video_engine.hyperframes")
    cur_deep = getattr(_hf, "deep_plan", deep_plan)
    if G:
        out["globe"] = G
    elif (dp := cur_deep(out, (iw, ih))) is not None:
        out["deep"] = deep_entries(project_dir / sc.map_asset, dp, _q(out))
    if sc.morph:
        out["morph"] = morph_data(sc.morph, out, project_dir, (iw, ih))
    out.update(fx.scene_block(seg, tl, project_dir, out, sc, prof, pins))
    if seg.overlays:
        from . import overlays as _ov

        drawn = _ov.drawn(seg)
        if drawn:
            out["overlays"] = [
                {"id": o.id, "kind": o.kind, "box": [round(v, 2) for v in o.box], "text": o.text,
                 **({"subtitle": o.subtitle} if o.subtitle else {}),
                 **({"on": o.on} if o.on else {}),
                 "start_frame": _ov.frame_range(o, seg)[0], "end_frame": _ov.frame_range(o, seg)[1]}
                for o in drawn
            ]
    _bounded_put(_SCENES, key, copy.deepcopy(out), SCENE_CACHE_MAX)
    return out


def _centroid(poly: list[list[float]]) -> tuple[float, float]:
    """Area centroid of a polygon (vertex mean when its area is 0)."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        k = x0 * y1 - x1 * y0
        a, cx, cy = a + k, cx + (x0 + x1) * k, cy + (y0 + y1) * k
    if abs(a) < 1e-12:
        return sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
    return cx / (3 * a), cy / (3 * a)


def region_data(r, col: dict, theme: dict, mw: float, mh: float, G: dict | None, S, beats: bool, fxc=None) -> dict:
    """Scene entry of a region: points in map px (globe scenes: `ll` lat/lng per point and the points projected at
    the region's first frame), style colours of every owner (`changes`) and its box label. The theme region_style
    applies to every scene; a region's own style wins. The theme label_style or the region's own `box` decides
    whether its label gets a solid box."""
    style = r.style or theme["region_style"]
    boxed = r.box if r.box is not None else theme["label_style"] == "box"

    def paint(e: str) -> dict:
        c = col[e]
        out = {"entity": e, "colour": c}
        if style == "block":
            b = entities.block_colour(c, theme)
            L, C, h = legibility.rgb_oklch(legibility.hex_rgb(b))
            out.update(block=b, side=legibility.oklch_hex(max(0.0, L - theme["block_side_dl"]), C, h),
                       bevel=tint(b, theme["block_bevel_tint"]))
        if r.label and boxed:
            out["box"] = entities.box_colour(c, theme)
        return out

    def xy(pts: list[list[float]], f: int) -> tuple[list, list | None]:
        if not G:
            return [[round(x * mw, 2), round(y * mh, 2)] for x, y in pts], None
        ll = [[round(90 - y * 180, 5), round(x * 360 - 180, 5)] for x, y in pts]
        v = globe.camera_at(G, f, S.fps)
        return [list(globe.project(v, a, b, S.width, S.height) or (-1000.0, -1000.0)) for a, b in ll], ll

    pts, ll = xy(r.polygon, r.at_frame)
    out = {**paint(r.entity), "edge": r.edge or theme["region_edge"], "style": style, "points": pts,
           "at_frame": r.at_frame, "changes": [{**paint(c.entity), "at_frame": c.at_frame} for c in r.changes]}
    if r.rings:   # islands and holes: the template draws one even-odd path; the flag spans the bbox of every ring
        rings = [pts] + [xy(ring, r.at_frame)[0] for ring in r.rings]
        xs, ys = [p[0] for g in rings for p in g], [p[1] for g in rings for p in g]
        out.update(rings=rings, bbox=[min(xs), min(ys), max(xs), max(ys)])
    if ll:
        out["ll"] = ll
    if style == "block":
        out.update(edge_colour=(r.edge_colour or theme["block_edge_colour"]).lower(), dashed=r.dashed,
                   draw_s=theme["block_draw_s"] if r.draw_s is None else r.draw_s)
    if r.label:
        f = r.at_frame if r.label_at_frame is None else r.label_at_frame
        (lp,), lll = xy([list(r.label_at or _centroid(r.polygon))], f)
        out["label"] = {"text": r.label, "x": round(lp[0], 2), "y": round(lp[1], 2), "at_frame": f,
                        **({"ll": lll[0]} if lll else {})}
    if r.hatch and fxc is not None:
        out["hatch"] = fx.hatch_data(fxc.hatch, r.hatch)
    return out


def figure_text(f, v: float, theme: dict) -> str:
    """On-screen text of figure f showing value v: years as plain digits; numbers rounded half up to f.decimals with
    the theme separators (same rule as the template counter)."""
    if f.kind == "year":
        return f"{f.prefix}{int(v)}{f.suffix}"
    m = 10 ** f.decimals
    n = math.floor(abs(v) * m + 0.5)
    whole = f"{n // m:,}".replace(",", theme["number_group"])
    frac = f"{theme['number_decimal']}{n % m:0{f.decimals}d}" if f.decimals else ""
    return f"{f.prefix}{'-' if v < 0 and n else ''}{whole}{frac}{f.suffix}"


# ease-in curves exactly as vendor/js/gsap.min.js defines them (its Expo lands on 0 and 1: 2^(10(p-1)) p + p^6 (1-p))
_GSAP_IN = {"none": lambda p: p, "linear": lambda p: p, "power0": lambda p: p,
            "sine": lambda p: 1.0 if p == 1 else 1 - math.cos(p * math.pi / 2),
            "expo": lambda p: 2 ** (10 * (p - 1)) * p + p ** 6 * (1 - p),
            "circ": lambda p: 1 - math.sqrt(max(0.0, 1 - p * p))}
_GSAP_POWER = {"power1": 2, "quad": 2, "power2": 3, "cubic": 3, "power3": 4, "quart": 4, "power4": 5, "quint": 5,
               "strong": 5}


def gsap_ease(name: str):
    """Python twin of a GSAP 3 ease name ("power2.out", "sine.inOut", "back.out(1.7)", "none"): the ease-in curve of the
    family, mirrored for .out and joined at 0.5 for .inOut exactly as GSAP builds them; a bare family is .out."""
    fam, _, kind = name.strip().partition(".")
    kind, _, arg = kind.partition("(")
    fam, kind = fam.lower(), kind or "out"
    if fam in _GSAP_POWER:
        n = _GSAP_POWER[fam]
        def e_in(p):
            return p ** n
    elif fam == "back":
        s = float(arg.rstrip(")")) if arg else 1.70158
        def e_in(p):
            return p * p * ((s + 1) * p - s)
    elif fam in _GSAP_IN:
        e_in = _GSAP_IN[fam]
    else:
        raise EngineError(f"ease {name!r}: counter figures mirror the GSAP power, sine, expo, circ, back and none eases")
    if fam in ("none", "linear", "power0") or kind == "in":
        return e_in
    if kind == "out":
        return lambda p: 1 - e_in(1 - p)
    if kind == "inOut":
        return lambda p: e_in(2 * p) / 2 if p < 0.5 else 1 - e_in(2 * (1 - p)) / 2
    raise EngineError(f"ease {name!r}: unknown ease type {kind!r} (in, out or inOut)")


def counter_value(f, frame: int, fps: int, count_s: float, ease: str = "power2.out") -> float:
    """Value a counter figure shows at `frame` (relative to the segment start): the template tweens it from count_from
    to value with the profile's [motion] entrance ease (default power2.out, the cubic ease-out)."""
    p = min(1.0, max(0.0, (frame - f.at_frame) / fps / count_s))
    return f.count_from + (f.value - f.count_from) * gsap_ease(ease)(p)


def _accent(f, th) -> str | None:
    """theme figure_accent for a number figure (not a year) without an entity; None otherwise."""
    return th.figure_accent.lower() if th.figure_accent and f.kind != "year" and not f.entity else None


def title_place(sc, cam: dict, mw: float, mh: float, tl: Timeline, px: float) -> tuple[float, float] | None:
    """(left, top) in output px of a scene title above its title_entity's region (theme title_anchor "region"): the
    region's on-screen box at the title's at_frame (layout.camera_at), the title TITLE_GAP_EM above it, or below it
    when that would leave the safe area; horizontally centred on the region and kept inside the side margins at an
    estimated width. None when no region of the title entity exists."""
    from . import layout

    rings = [ring for r in sc.regions or [] if r.entity == sc.title_entity for ring in r.all_rings()]
    if not rings:
        return None
    st = layout.camera_at(cam, sc.title_at_frame or 0)
    xs = [x * mw * st["s"] + st["x"] for ring in rings for x, _ in ring]
    ys = [y * mh * st["s"] + st["y"] for ring in rings for _, y in ring]
    S, (sx, top_min, bottom_y) = tl.settings, units(tl).safe()   # side margin, safe top y, safe bottom y (output px)
    h, gap = px * 1.04, px * TITLE_GAP_EM
    top = min(ys) - gap - h
    if top < top_min:
        top = max(ys) + gap
    top = min(max(top, top_min), bottom_y - h)
    w = min(900.0 * S.width / 1080, 0.62 * px * len(sc.title))
    left = min(max((min(xs) + max(xs)) / 2, sx + w / 2), S.width - sx - w / 2)
    return round(left, 1), round(top, 1)


def figure_end_frame(f, fps: int, default_count_s: float, pop: str = "elastic") -> int:
    """Frame (relative to the segment start) at which a figure's entrance ends: a bounce lasts FIGURE_POP_S[pop] (theme
    figure_pop), a counter its count_s."""
    return f.at_frame + math.ceil((FIGURE_POP_S[pop] if f.motion == "bounce" else (f.count_s or default_count_s)) * fps)


def sfx_events(tl: Timeline) -> list[dict]:
    """Sound cue events of the HyperFrames scenes: one "counter_tick" per frame on which a counter's shown text
    changes, at the absolute frame."""
    th, fps, out = profiles.of(tl).render_values("hf"), tl.settings.fps, []
    for s in tl.segments:
        if s.engine_type != HYPERFRAMES:
            continue
        assert s.gsap_scene_config is not None
        cfg: GsapScene = s.gsap_scene_config
        for i, f in enumerate(cfg.figures or []):
            if f.motion != "counter":
                continue
            cs, ease = f.count_s or th["counter_s"], th["motion"]["entrance"]   # the template's MO.entrance
            prev = figure_text(f, f.count_from, th)
            for fr in range(f.at_frame + 1, figure_end_frame(f, fps, th["counter_s"]) + 1):
                txt = figure_text(f, counter_value(f, fr, fps, cs, ease), th)
                if txt != prev:
                    out.append({"kind": "counter_tick", "frame": s.start_frame + fr, "seg": s.seg_index, "figure": i})
                prev = txt
    return out


def route_end_frame(r, fps: int, default_draw_s: float) -> int:
    """Frame (relative to the segment start) at which a route's arrowhead reaches the last point."""
    return r.at_frame + math.ceil((r.draw_s or default_draw_s) * fps)


def script_files(scene: dict) -> list[Path]:
    """Scripts a scene adds to the template: hf/props.js (props), hf/parallax.js (layers), the Stage 4a/4b effect
    scripts, printstyle.js (profile [print]), and hf/globe.js / hf/scene3d.js (modules)."""
    print_js = config.TEMPLATE_DIR / "printstyle.js"
    return ([PROPS_JS] if scene.get("props") else []) + ([parallax.JS] if scene.get("layers") else []) + \
        ([FX_BACKDROP_JS] if scene.get("backdrop") else []) + \
        ([FX_TIMEZONE_JS] if scene.get("timezones") else []) + \
        ([FX_BUBBLE_JS] if scene.get("bubbles") else []) + \
        ([FX2_JS] if any(scene.get(k) for k in ("flag_characters", "cutaway", "insets", "particles")) else []) + \
        ([print_js] if scene.get("print") and print_js.is_file() else []) + \
        ([globe.JS] if scene.get("globe") else []) + ([SCENE3D_JS] if scene.get("cam3d") else [])


def _card_name(src: Path) -> str:
    """Build-dir name of a card image: its content hash, so two images with one basename never share a file."""
    return f"card_{blake3_files([src])[0][:32]}{src.suffix.lower()}"


def split_panels(seg: Segment, tl: Timeline) -> list[tuple[Segment, Timeline]]:
    """A split segment as two (segment, timeline) panels at the panel size: the top panel is the scene without its
    split, the bottom one split.second; both keep the segment's frames. A plain segment is one panel: itself."""
    sc = seg.gsap_scene_config
    if sc is None or sc.split is None:
        return [(seg, tl)]
    sp: SplitView = sc.split
    ph = (tl.settings.height - sp.divider_px) // 2
    if ph % 2:
        raise EngineError(f"split: frame height {tl.settings.height} minus divider {sp.divider_px} must give even panels")
    tlp = tl.model_copy(update={"settings": tl.settings.model_copy(update={"height": ph})})
    top = seg.model_copy(update={"gsap_scene_config": sc.model_copy(update={"split": None})})
    return [(top, tlp), (seg.model_copy(update={"gsap_scene_config": sp.second}), tlp)]
