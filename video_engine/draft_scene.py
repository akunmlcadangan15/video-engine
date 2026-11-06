"""Draft map scene adapter: the advanced HyperFrames fields a model may give a `kind map` draft segment, their JSON
schema and their conversion to gsap_scene_config (video_engine/timeline.py GsapScene).

One table (FIELDS) drives both the schema the model receives and the conversion, so the two cannot drift; the
targets are checked against GsapScene by tests/test_draft_scene.py. Draft conventions: times are seconds from the
segment start (`at_s`, `out_s`, ...) and become frames; map points are normalised [x, y] of the map image the model
saw; assets are draft asset ids. Anything the renderer cannot execute (unknown key, value out of range, a key the
profile does not expose, a field on a non-map segment) raises ValueError naming `segment N: <key>[i].<field>`, so
the repair prompt points at it; nothing is dropped or turned into a plain pin or card.

Always on: regions (with flags), routes, borders, figures (counters), flag characters, pin flags. Opt-in per profile
`[draft] scene = [...]`: "globe", "props", "morph", "beats" (beats also need a profile [layout])."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import ValidationError

from .timeline import (Backdrop, Beat, Border, Cutaway, Flag, FlagCharacter, Figure, Flow, Geoid, Globe, GsapScene,
                       Inset, MapMorph, Particle, Prop, Region, Route, Thermometer, sec_to_frame)
from .util import EngineError

GATED = ("globe", "props", "morph", "beats")
PROP_ASSET_EXT = (".png", ".webp")

s, n, i, b = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
PT = {"type": "array", "items": n}                       # [x, y] 0..1
PTS = {"type": "array", "items": PT}
INTS = {"type": "array", "items": i}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


def _arr(item: dict) -> dict:
    return {"type": "array", "items": item}


# ------------------------------------------------------------------------------------------------------- converters
@dataclass
class Ctx:
    seg: int                 # segment index (error locations)
    n: int                   # segment length (frames)
    fps: int
    assets: dict             # draft asset id -> AssetInfo
    map_asset: object        # AssetInfo of the segment's map
    used: list               # asset ids the timeline credits (props and morph textures are added)


def _err(path: str, msg) -> ValueError:
    return ValueError(f"{path}: {msg}")


def _keys(path: str, d, allowed) -> dict:
    if not isinstance(d, dict):
        raise _err(path, f"expected an object, got {type(d).__name__}")
    for k in d:
        if k not in allowed:
            raise _err(f"{path}.{k}", f"unknown field (allowed: {', '.join(allowed)})")
    return d


def _frame(c: Ctx, path: str, v, end: bool = False) -> int:
    """Seconds from the segment start -> frame; inside the segment (end=True: up to its exclusive end)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _err(path, f"expected seconds, got {v!r}")
    f = sec_to_frame(float(v), c.fps)
    hi = c.n if end else c.n - 1
    if not 0 <= f <= hi:
        raise _err(path, f"{v} s is outside the segment (0-{hi / c.fps:.2f} s)")
    return f


def _flag(path: str, v) -> dict:
    """ISO alpha-2 code (or {code, variant}) -> Flag input; unknown codes fail with the closest matches."""
    try:
        return Flag.model_validate(v).model_dump(exclude_none=True)
    except (ValidationError, EngineError, ValueError) as e:
        raise _err(path, _msg(e)) from None


def _msg(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'value'}: {x['msg']}" for x in e.errors())
    return str(e)


def _model(cls, d: dict, path: str) -> dict:
    """Validate one converted item against its timeline model so the error carries the draft location."""
    try:
        cls.model_validate(d)
    except (ValidationError, EngineError, ValueError) as e:
        raise _err(path, _msg(e)) from None
    return d


def _copy(src: dict, keys) -> dict:
    return {k: src[k] for k in keys if k in src}


def _asset(c: Ctx, path: str, aid, kind: str = "image"):
    a = c.assets.get(aid)
    if a is None:
        raise _err(path, f"unknown asset {aid!r}")
    if a.kind != kind:
        raise _err(path, f"{aid} is a {a.kind}, needs an {kind}")
    c.used.append(a.id)
    return a


def _region(c, path, r):
    _keys(path, r, REGION_KEYS)
    d = _copy(r, ("entity", "polygon", "rings", "edge", "style", "edge_colour", "dashed", "draw_s", "label", "label_at", "box"))
    if "flag" in r:
        d["flag"] = _flag(f"{path}.flag", r["flag"])
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    if "label_at_s" in r:
        d["label_at_frame"] = _frame(c, f"{path}.label_at_s", r["label_at_s"])
    chg = []
    for k, x in enumerate(r.get("changes") or []):
        p = f"{path}.changes[{k}]"
        _keys(p, x, ("at_s", "entity", "flag"))
        chg.append({"at_frame": _frame(c, f"{p}.at_s", x.get("at_s")), "entity": x.get("entity"),
                    **({"flag": _flag(f"{p}.flag", x["flag"])} if "flag" in x else {})})
    if chg:
        d["changes"] = chg
    return _model(Region, d, path)


def _route(c, path, r):
    _keys(path, r, ("points", "at_s", "draw_s", "to_pin"))
    d = _copy(r, ("points", "draw_s", "to_pin"))
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    return _model(Route, d, path)


def _border(c, path, r):
    _keys(path, r, ("points", "at_s", "draw_s", "colour", "dashed", "closed"))
    d = _copy(r, ("points", "draw_s", "colour", "dashed", "closed"))
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    return _model(Border, d, path)


def _figure(c, path, r):
    _keys(path, r, FIGURE_KEYS)
    d = _copy(r, ("kind", "value", "decimals", "prefix", "suffix", "motion", "count_from", "count_s", "position",
                  "entity", "box"))
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    return _model(Figure, d, path)


def _flag_character(c, path, r):
    _keys(path, r, ("flag", "xy", "height_pct", "eyes", "at_s", "out_s", "wave"))
    d = {"flag": _flag(f"{path}.flag", r.get("flag")), **_copy(r, ("xy", "height_pct", "eyes", "wave"))}
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    if "out_s" in r:
        d["out_frame"] = _frame(c, f"{path}.out_s", r["out_s"], end=True)
    return _model(FlagCharacter, d, path)


def _flow(c, path, r):
    _keys(path, r, ("paths", "colour", "width_px", "speed_px", "label", "at_s", "out_s", "arrows"))
    d = _copy(r, ("paths", "colour", "width_px", "speed_px", "label", "arrows"))
    d["seed"] = int(path.rsplit("[", 1)[-1].rstrip("]") or 0)
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    if "out_s" in r:
        d["out_frame"] = _frame(c, f"{path}.out_s", r["out_s"], end=True)
    return _model(Flow, d, path)


def _thermo(c, path, r):
    _keys(path, r, ("value", "min", "max", "unit", "decimals", "label", "note", "at_s", "out_s", "position",
                    "height_pct"))
    d = _copy(r, ("value", "min", "max", "unit", "decimals", "label", "note", "position", "height_pct"))
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    if "out_s" in r:
        d["out_frame"] = _frame(c, f"{path}.out_s", r["out_s"], end=True)
    return _model(Thermometer, d, path)


def _beat(c, path, r):
    _keys(path, r, ("at_s", "region", "pins", "markers", "routes", "title", "card", "camera", "heading", "hold", "goal"))
    d = {"at_frame": _frame(c, f"{path}.at_s", r.get("at_s")),
         **_copy(r, ("region", "pins", "markers", "routes", "title", "card", "camera", "heading", "hold", "goal"))}
    return _model(Beat, d, path)


def _prop(c, path, r):
    _keys(path, r, PROP_KEYS)
    a = _asset(c, f"{path}.asset", r.get("asset"))
    if not a.path.lower().endswith(PROP_ASSET_EXT):
        raise _err(f"{path}.asset", f"{a.id} is {a.path}; a prop needs a PNG or WebP with alpha")
    d = {"asset": a.path, "asset_id": a.id, **_copy(r, ("anchor", "x", "y", "lat", "lng", "width_px", "width_map",
                                                         "rotation", "opacity", "depth", "z", "path", "path_s",
                                                         "heading"))}
    if "at_s" in r:
        d["t_in"] = _frame(c, f"{path}.at_s", r["at_s"])
    if "out_s" in r:
        d["t_out"] = _frame(c, f"{path}.out_s", r["out_s"], end=True)
    return _model(Prop, d, path)


def _globe(c, path, g):
    _keys(path, g, ("keys",))
    m = c.map_asset
    if abs(m.width / m.height - 2.0) > 0.02:
        raise _err(path, f"the map asset {m.id} is {m.width}x{m.height}; a globe needs a whole-world 2:1 "
                         "equirectangular texture")
    keys = []
    for k, x in enumerate(g.get("keys") or []):
        p = f"{path}.keys[{k}]"
        _keys(p, x, ("at_s", "lat", "lng", "altitude", "tilt", "hold_s"))
        keys.append({"at_frame": _frame(c, f"{p}.at_s", x.get("at_s")), **_copy(x, ("lat", "lng", "altitude", "tilt")),
                     **({"hold": round(float(x["hold_s"]) * c.fps)} if "hold_s" in x else {})})
    return _model(Globe, {"keys": keys}, path)


def _morph(c, path, m):
    _keys(path, m, ("asset", "at_s", "s", "back_at_s", "sepia", "paper", "grain"))
    a = _asset(c, f"{path}.asset", m.get("asset"))
    base = c.map_asset
    if abs(a.width / a.height - base.width / base.height) > 0.02 * base.width / base.height:
        raise _err(f"{path}.asset", f"{a.id} ({a.width}x{a.height}) must cover the map's extent "
                                    f"({base.width}x{base.height}, same aspect ratio)")
    d = {"asset": a.path, "asset_id": a.id, **_copy(m, ("s", "sepia", "paper", "grain"))}
    if "at_s" in m:
        d["at_frame"] = _frame(c, f"{path}.at_s", m["at_s"])
    if "back_at_s" in m:
        d["back_at_frame"] = _frame(c, f"{path}.back_at_s", m["back_at_s"])
    return _model(MapMorph, d, path)




def _cutaway(c, path, r):
    _keys(path, r, ("layers", "xy", "width_pct", "at_s", "reveal_s"))
    d = _copy(r, ("layers", "xy", "width_pct", "reveal_s"))
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    return _model(Cutaway, d, path)


def _inset(c, path, r):
    _keys(path, r, ("asset", "anchor", "size_pct", "at_s", "out_s"))
    a = _asset(c, f"{path}.asset", r.get("asset"))
    d = {"asset": a.path, **_copy(r, ("anchor", "size_pct"))}
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    if "out_s" in r:
        d["out_frame"] = _frame(c, f"{path}.out_s", r["out_s"], end=True)
    return _model(Inset, d, path)


def _geoid(c, path, r):
    _keys(path, r, ("exaggeration", "colour_ramp", "rotate_deg_s", "at_s"))
    d = _copy(r, ("exaggeration", "colour_ramp", "rotate_deg_s"))
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    return _model(Geoid, d, path)


def _backdrop(c, path, r):
    _keys(path, r, ("grid", "stars"))
    return _model(Backdrop, _copy(r, ("grid", "stars")), path)


def _particle(c, path, r):
    _keys(path, r, ("kind", "xy", "at_s", "duration_s", "seed", "scale", "colours"))
    d = _copy(r, ("kind", "xy", "duration_s", "seed", "scale", "colours"))
    if "at_s" in r:
        d["at_frame"] = _frame(c, f"{path}.at_s", r["at_s"])
    return _model(Particle, d, path)

# ------------------------------------------------------------------------------------------------------------ table
REGION_KEYS = ("entity", "polygon", "rings", "flag", "at_s", "edge", "style", "edge_colour", "dashed", "draw_s", "label",
               "label_at", "label_at_s", "box", "changes")
FIGURE_KEYS = ("kind", "value", "decimals", "prefix", "suffix", "motion", "count_from", "count_s", "at_s", "position",
               "entity", "box")
PROP_KEYS = ("asset", "anchor", "x", "y", "lat", "lng", "width_px", "width_map", "rotation", "opacity", "at_s", "out_s",
             "depth", "z", "path", "path_s", "heading")
FLAG = s   # ISO 3166-1 alpha-2, lower or upper case


@dataclass(frozen=True)
class Spec:
    target: str                          # GsapScene field
    schema: dict                         # JSON schema of the draft value
    convert: Callable                    # (ctx, path, item) -> model input dict
    many: bool = True                    # list of items (False: one object)
    gate: str | None = None              # profile [draft] scene entry that exposes it (None = always)


FIELDS: dict[str, Spec] = {
    "map_regions": Spec("regions", _arr(_obj({
        "entity": s, "polygon": PTS, "rings": _arr(PTS), "flag": FLAG, "at_s": n, "edge": {"type": "string", "enum": ["white", "glow"]},
        "style": {"type": "string", "enum": ["tint", "block"]}, "edge_colour": s, "dashed": b, "draw_s": n,
        "label": s, "label_at": PT, "label_at_s": n, "box": b,
        "changes": _arr(_obj({"at_s": n, "entity": s, "flag": FLAG}, ["at_s", "entity"]))}, ["entity", "polygon"])),
        _region),
    "map_routes": Spec("routes", _arr(_obj({"points": PTS, "at_s": n, "draw_s": n, "to_pin": i}, ["points"])), _route),
    "map_borders": Spec("borders", _arr(_obj({"points": PTS, "at_s": n, "draw_s": n, "colour": s, "dashed": b,
                                              "closed": b}, ["points"])), _border),
    "map_figures": Spec("figures", _arr(_obj({
        "kind": {"type": "string", "enum": ["year", "number"]}, "value": n, "decimals": i, "prefix": s, "suffix": s,
        "motion": {"type": "string", "enum": ["bounce", "counter"]}, "count_from": n, "count_s": n, "at_s": n,
        "position": PT, "entity": s, "box": b}, ["kind", "value"])), _figure),
    "map_flag_characters": Spec("flag_characters", _arr(_obj({
        "flag": FLAG, "xy": PT, "height_pct": n, "eyes": s, "at_s": n, "out_s": n, "wave": b}, ["flag", "xy"])),
        _flag_character),
    "map_flows": Spec("flows", _arr(_obj({"paths": _arr(PTS), "colour": s, "width_px": n, "speed_px": n, "label": s,
                                          "at_s": n, "out_s": n, "arrows": b}, ["paths"])), _flow),
    "map_thermometers": Spec("thermometers", _arr(_obj({
        "value": n, "min": n, "max": n, "unit": s, "decimals": i, "label": s, "note": s, "at_s": n, "out_s": n,
        "position": PT, "height_pct": n}, ["value"])), _thermo),
    "map_beats": Spec("beats", _arr(_obj({
        "at_s": n, "region": i, "pins": INTS, "markers": INTS, "routes": INTS, "title": b, "card": INTS, "camera": b,
        "heading": s, "hold": {"type": "string", "enum": ["read"]}, "goal": s}, ["at_s"])), _beat, gate="beats"),
    "map_props": Spec("props", _arr(_obj({
        "asset": s, "anchor": {"type": "string", "enum": ["map", "screen"]}, "x": n, "y": n, "lat": n, "lng": n,
        "width_px": n, "width_map": n, "rotation": n, "opacity": n, "at_s": n, "out_s": n, "depth": n, "z": i,
        "path": PTS, "path_s": n, "heading": b}, ["asset"])), _prop, gate="props"),
    "map_globe": Spec("globe", _obj({"keys": _arr(_obj({"at_s": n, "lat": n, "lng": n, "altitude": n, "tilt": n,
                                                        "hold_s": n}, ["at_s", "lat", "lng", "altitude"]))},
                                    ["keys"]), _globe, many=False, gate="globe"),
    "map_morph": Spec("morph", _obj({"asset": s, "at_s": n, "s": n, "back_at_s": n, "sepia": n, "paper": n,
                                     "grain": n}, ["asset"]), _morph, many=False, gate="morph"),
    "map_cutaway": Spec("cutaway", _obj({
        "layers": _arr(_obj({"label": s, "colour": s, "thickness": n}, ["label", "colour", "thickness"])),
        "xy": PT, "width_pct": n, "at_s": n, "reveal_s": n}, ["layers"]), _cutaway, many=False, gate="cross_section"),
    "map_insets": Spec("insets", _arr(_obj({
        "asset": s, "anchor": _obj({"pin": i, "xy": PT}, []), "size_pct": n, "at_s": n, "out_s": n},
        ["asset", "anchor"])), _inset, gate="historical"),
    "map_geoid": Spec("geoid", _obj({"exaggeration": n, "colour_ramp": _arr(s), "rotate_deg_s": n, "at_s": n}, []),
                      _geoid, many=False, gate="terrain3d"),
    "map_backdrop": Spec("backdrop", _obj({
        "grid": _obj({"colour": s, "bg": s, "cell_pct": n, "line_px": n, "alpha": n, "graticule": b}, []),
        "stars": _obj({"count": i, "seed": i, "twinkle": n, "parallax": n}, [])}, []),
        _backdrop, many=False, gate="blind_spot"),
    "map_particles": Spec("particles", _arr(_obj({
        "kind": {"type": "string", "enum": ["fireworks", "fire"]}, "xy": PT, "at_s": n, "duration_s": n,
        "seed": i, "scale": n, "colours": _arr(s)}, ["kind", "xy"])), _particle, gate="data_viz"),
}


def enabled(dp, layout_on: bool, morph_on: bool = True) -> set[str]:
    """Draft keys a profile exposes: the always-on fields plus its `[draft] scene` entries (beats need [layout], morph needs map_morph)."""
    on = set(dp.scene) - ({"beats"} if not layout_on else set()) - ({"morph"} if not morph_on else set())
    return {k for k, sp in FIELDS.items() if sp.gate is None or sp.gate in on}


def schema_props(dp, layout_on: bool, morph_on: bool = True) -> dict:
    return {k: FIELDS[k].schema for k in FIELDS if k in enabled(dp, layout_on, morph_on)}


def convert(seg: dict, idx: int, *, n: int, fps: int, assets: dict, map_asset, used: list, dp, layout_on: bool,
            scene: dict, morph_on: bool = True) -> dict:
    """The FIELDS keys of draft segment `seg` -> gsap_scene_config fields, merged into `scene`, then the whole scene
    validated (cross-field rules: indices, globe exclusions) with the segment location."""
    c, on = Ctx(idx, n, fps, assets, map_asset, used), enabled(dp, layout_on, morph_on)
    for k, sp in FIELDS.items():
        if k not in seg:
            continue
        path = f"segment {idx}: {k}"
        if k not in on:
            why = " and a profile [layout]" if sp.gate == "beats" else ""
            raise ValueError(f"{path} is not enabled: profile [draft] scene must list \"{sp.gate}\"{why}; "
                             "remove the key")
        v = seg[k]
        if sp.many:
            if not isinstance(v, list):
                raise _err(path, f"expected an array, got {type(v).__name__}")
            scene[sp.target] = [sp.convert(c, f"{path}[{j}]", x) for j, x in enumerate(v)] or None
        else:
            scene[sp.target] = sp.convert(c, path, v)
    if scene.get("beats") and scene.get("camera_path"):
        raise _err(f"segment {idx}: map_beats", "beats derive the camera; drop map_path")
    if scene.get("globe"):
        scene.pop("map_bounds", None)
        scene.pop("map_projection", None)
    elif not scene.get("map_bounds"):
        for j, p in enumerate(scene.get("pins") or []):
            if p.get("x") is None and p.get("lat") is not None:
                raise _err(f"segment {idx}: map_pins[{j}]", f"lat/lng need the map's bounds ({map_asset.id} has no "
                                                           "sidecar map_bounds); give x, y on the map image instead")
    try:
        GsapScene.model_validate(scene)
    except (ValidationError, EngineError, ValueError) as e:
        raise _err(f"segment {idx}: gsap_scene_config", _msg(e)) from None
    return {k: v for k, v in scene.items() if v is not None}


PROMPT = """
   Map scene elements (optional, kind map only; times in seconds from the segment start, points [x, y] 0..1 on the map
   image you saw; give each element only when the narration needs it, timed to the words that name it):
   map_pins[].flag = ISO alpha-2 code (flag in the pin); map_regions = country/area highlight {entity, polygon (>= 3
   points tracing its outline; rings = further islands and holes, one flag over all), flag (ISO code: the flag
   fills the polygon), at_s, edge white|glow, style tint|block,
   label, changes (later owner)}; map_routes = self-drawing arrow {points start -> end, at_s, draw_s}; map_borders =
   self-drawing line {points, dashed for sea borders}; map_figures = big number or year {kind, value, motion counter
   (counts up) | bounce, prefix, suffix (unit), at_s, count_s, position [x, y] of the frame}; map_flag_characters =
   flag with eyes {flag, xy of the frame, eyes blink|look_left|look_right|look_at:<pin index>}; map_flows = moving
   streams (wind, current) {paths: [[[x, y], ...]] start -> end, colour, label (legend), at_s}; map_thermometers =
   {value, min, max, unit, label, note ("ilustratif" unless it is a sourced reading), at_s, position [x, y] of the
   frame}."""
PROMPT_GATED = {
    "beats": "map_beats = what is on screen per beat {at_s (first 0), region, pins, markers, routes (indices), title, "
             "card ([year figure] or [year, time]), camera, goal}; the layout derives camera and positions (no map_path)",
    "props": "map_props = cut-out image {asset (PNG/WebP id), anchor map|screen, x, y, width_map or width_px, at_s, "
             "out_s, path (map points it travels)}",
    "globe": "map_globe = 3D globe on a 2:1 world texture {keys [{at_s, lat, lng, altitude 0.005-12}]}; pins need "
             "lat/lng; no routes, borders, props, morph or beats on a globe",
    "morph": "map_morph = second map texture crossfaded over the map {asset (same aspect), at_s, back_at_s, sepia}",
}


def prompt(dp, layout_on: bool, morph_on: bool = True) -> str:
    on = enabled(dp, layout_on, morph_on)
    extra = [t for g, t in PROMPT_GATED.items() if any(FIELDS[k].gate == g for k in on)]
    return PROMPT + "".join(f"\n   {t}." for t in extra)
