"""HyperFrames path: map/pin/card motion-graphics segments only (no captions in HTML). One HTML project per segment in
the build's hf/ directory, rendered cleanly by HyperFrames into the exact container format of the final mux."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import hypoverum_core_rs as core

from . import (
    assets,
    cache,
    camera3d,
    camera_full3d,
    components,
    config,
    entities,
    fx,
    globe,
    hfversion,
    legibility,
    parallax,
    printstyle,
    profiles,
    projection,
    props,
    resources,
    streams,
    toolchain,
)
from .hf_camera import (
    FIT_PAD_REF,
    HF_PRESETS,
    _FIT_SLACK,
    _fit_box,
    _monotone,
    _pin_xy,
    _pins_inside,
    _scaled_pulse,
    _state,
    camera_frames,
    camera_frames_sig,
    camera_plan,
    camera_states,
    card_frame,
    drift_base,
    drift_legs,
    fit_hold,
    fit_points,
    flow_legs,
    glide_speeds,
    leg_ease,
    motion_px,
    pin_frame,
    whip_blur,
)
from .hf_probe import (
    _INKS,
    PROBE_JS,
    PROBE_VERSION,
    SHOT_FIELDS,
    _QuietHandler,
    _probe_cached,
    _probe_store,
    probe_dom,
    probe_key,
    probe_props,
    serve,
    shutdown_probe_pool,
)
from .hf_scene import (
    _GSAP_IN,
    _GSAP_POWER,
    _SCENES,
    CARD_TOP,
    CARD_TOP_LANDSCAPE,
    FIGURE_POP_S,
    FX2_JS,
    FX_BACKDROP_JS,
    FX_BUBBLE_JS,
    FX_TIMEZONE_JS,
    HF_CRF,
    HF_FONTS,
    HF_QUALITY,
    HF_VERSION,
    INK_CACHE_MAX,
    PROPS_JS,
    SCENE3D_JS,
    SCENE_CACHE_MAX,
    TEMPLATE_SCALED,
    THEME_FONTS,
    TITLE_GAP_EM,
    TITLE_TOP,
    TITLE_TOP_LANDSCAPE,
    _accent,
    _bounded_put,
    _card_name,
    _centroid,
    _flag_data,
    _scene_key,
    _svg_aspect,
    clear_scene_cache,
    counter_value,
    figure_end_frame,
    figure_text,
    flag_fit,
    font_faces,
    font_files,
    font_roles,
    gsap_ease,
    landscape_theme,
    region_aspect,
    region_data,
    route_end_frame,
    scene_data,
    script_files,
    sfx_events,
    split_panels,
    tint,
    title_place,
)
from .maps import (
    _map_copy,
    _need_w,
    _q,
    deep_plan,
    deep_tiles,
    map_key,
    textures,
)
from .timeline import GsapScene, Segment, SplitView, Timeline
from .util import EngineError, blake3_files, blake3_json, code_hash, run

CODE = (
    Path(__file__),
    Path(__file__).parent / "hf_camera.py",
    Path(__file__).parent / "hf_scene.py",
    Path(__file__).parent / "hf_probe.py",
    Path(components.__file__),
    Path(__file__).parent / "hf" / "chrome_repeatable.py",
    Path(__file__).parent / "hf" / "ffmpeg_repeatable.py",
    Path(fx.__file__),
    Path(globe.__file__),
    Path(parallax.__file__),
    Path(props.__file__),
    Path(camera3d.__file__),
    Path(camera_full3d.__file__),
    Path(entities.__file__),
    Path(printstyle.__file__),
    Path(projection.__file__),
)   # writes the build dir (html, scene.js); legibility.py is keyed in the scene (legibility.code_hash)

__all__ = [
    "CARD_TOP", "CARD_TOP_LANDSCAPE", "CODE", "FIGURE_POP_S", "FIT_PAD_REF",
    "FX2_JS", "FX_BACKDROP_JS", "FX_BUBBLE_JS", "FX_TIMEZONE_JS", "HF_CRF",
    "HF_FONTS", "HF_PRESETS", "HF_QUALITY", "HF_VERSION", "INK_CACHE_MAX",
    "PROBE_JS", "PROBE_VERSION", "PROPS_JS", "SCENE3D_JS", "SCENE_CACHE_MAX",
    "SHOT_FIELDS", "TEMPLATE_SCALED", "THEME_FONTS", "TITLE_GAP_EM",
    "TITLE_TOP", "TITLE_TOP_LANDSCAPE", "_FIT_SLACK", "_GSAP_IN", "_GSAP_POWER",
    "_INKS", "_QuietHandler", "_SCENES", "_accent", "_bounded_put", "_card_name",
    "_centroid", "_fit_box", "_flag_data", "_inks", "_is_engine_format", "_key",
    "_monotone", "_pin_xy", "_pins_inside", "_probe_cached", "_probe_store",
    "_render_split", "_scaled_pulse", "_scene_key", "_state", "_svg_aspect",
    "build_dir", "cache_key", "camera_frames", "camera_frames_sig", "camera_plan",
    "camera_states", "card_frame", "clear_scene_cache", "counter_value", "deep_plan",
    "drift_base", "drift_legs", "encode_settings", "figure_end_frame",
    "figure_text", "fit_hold", "fit_points", "flag_fit", "flow_legs",
    "font_faces", "font_files", "font_roles", "glide_speeds", "gsap_ease",
    "keys", "landscape_theme", "leg_ease", "motion_px", "pin_frame",
    "probe_dom", "probe_key", "probe_props", "region_aspect", "region_data",
    "render", "route_end_frame", "scene_data", "script_files", "serve",
    "sfx_events", "shutdown_probe_pool", "split_panels", "tint", "title_place",
    "whip_blur",
]


def keys(seg: Segment, tl: Timeline, project_dir: Path) -> tuple[str, str]:
    """(segment cache key, map cache key) without writing files; equal to the keys `render` uses. A split segment's
    key covers both panels, the divider and the frame size."""
    assert seg.gsap_scene_config is not None
    sc: GsapScene = seg.gsap_scene_config
    if sc.split is not None:
        sp: SplitView = sc.split
        ks = [keys(s, t, project_dir) for s, t in split_panels(seg, tl)]
        return blake3_json({"split": [k[0] for k in ks], "divider": [sp.divider_px, sp.divider_colour],
                            "size": [tl.settings.width, tl.settings.height]}), ks[0][1]
    scene = scene_data(seg, tl, project_dir)
    mk = globe.tex_key(project_dir / scene["map"]) if "globe" in scene else map_key(project_dir / scene["map"], _need_w(scene), _q(scene))
    for c in scene["cards"]:
        if c["img"]:
            c["img"] = "assets/" + _card_name(project_dir / c["img"])
    return _key(scene, [config.TEXTURES_DIR / f for f in textures(scene)] + script_files(scene), mk, tl), mk


def build_dir(seg: Segment, tl: Timeline, project_dir: Path, out_dir: Path) -> tuple[Path, dict]:
    """Write the per-segment HyperFrames project. Returns (dir, scene)."""
    S = tl.settings
    scene = scene_data(seg, tl, project_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets").mkdir(parents=True)
    assert seg.gsap_scene_config is not None
    sc: GsapScene = seg.gsap_scene_config
    flags = [p.flag for p in sc.pins]
    flags += [f for r in sc.regions or [] for f in [r.flag, *(c.flag for c in r.changes)]]
    flags += [c.flag for c in sc.flag_characters or []]
    for flag in (f for f in flags if f):
        asset = assets.resolve(assets.flag_for(flag.code, flag.variant), project=project_dir, kind="flag")
        shutil.copyfile(asset.path, out_dir / "assets" / f"{asset.sha256[:16]}.svg")
    if "globe" in scene:
        for name, f in globe.files(seg, project_dir, scene["globe"]).items():
            shutil.copyfile(f, out_dir / "assets" / name)
        (out_dir / "assets" / "three").mkdir()
        for f in globe.THREE_FILES:
            shutil.copyfile(f, out_dir / "assets" / "three" / f.name)
    else:
        if "cam3d" in scene:
            (out_dir / "assets" / "three").mkdir()
            for f in globe.THREE_FILES:
                shutil.copyfile(f, out_dir / "assets" / "three" / f.name)
        mp = _map_copy(project_dir / scene["map"], _need_w(scene), _q(scene))
        shutil.copyfile(mp, out_dir / "assets" / "map.jpg")
        for name, f in deep_tiles(project_dir / scene["map"], scene).items():
            shutil.copyfile(f, out_dir / "assets" / name)
        if m := scene.get("morph"):
            src = project_dir / m["asset"]
            shutil.copyfile(_map_copy(src, _need_w(scene), _q(scene)), out_dir / m["img"])
            for name, f in deep_tiles(src, {"deep": m["deep"]}).items():
                shutil.copyfile(f, out_dir / "assets" / name)
    for c in scene["cards"]:
        if c["img"]:
            dst = out_dir / "assets" / _card_name(project_dir / c["img"])
            shutil.copyfile(project_dir / c["img"], dst)
            c["img"] = "assets/" + dst.name
    for name, src in (props.sources(seg, project_dir, S.fps).items() if scene.get("props") else ()):
        shutil.copyfile(src, out_dir / "assets" / name)
    for name, src in (parallax.sources(seg, project_dir).items() if scene.get("layers") else ()):
        shutil.copyfile(src, out_dir / "assets" / name)
    for name, src in (fx.sources(seg, project_dir).items() if scene.get("insets") else ()):
        shutil.copyfile(src, out_dir / "assets" / name)
    shutil.copyfile(config.GSAP_JS, out_dir / "assets" / "gsap.min.js")
    for name in textures(scene):
        shutil.copyfile(config.TEXTURES_DIR / name, out_dir / "assets" / name)
    for name in font_files(scene):
        shutil.copyfile(config.FONTS_DIR / name, out_dir / "assets" / name)
    if (styles := config.TEMPLATE_DIR / "styles.css").is_file():
        shutil.copyfile(styles, out_dir / "styles.css")
    dur = seg.n_frames / S.fps
    html = (config.TEMPLATE_DIR / "index.html").read_text()
    for k, v in {"__W__": S.width, "__H__": S.height, "__DUR__": f"{dur:.6f}",
                 "__MAP__": scene["globe"]["tex"] if "globe" in scene else "assets/map.jpg",
                 "__MW__": scene["map_size"][0], "__MH__": scene["map_size"][1], "__FONTFACES__": font_faces(scene)}.items():
        html = html.replace(k, str(v))
    for f in script_files(scene):
        shutil.copyfile(f, out_dir / f.name)
        mod = ' type="module"' if f in (globe.JS, SCENE3D_JS) else ""
        html = html.replace("</body>", f'<script{mod} src="{f.name}"></script>\n</body>', 1)
    (out_dir / "index.html").write_text(html)
    (out_dir / "scene.js").write_text("window.SCENE=" + json.dumps(scene, ensure_ascii=False) + ";\n")
    if "legibility" in scene:
        scene["inks"] = _inks(seg, tl, project_dir, out_dir, scene)
        (out_dir / "scene.js").write_text("window.SCENE=" + json.dumps(scene, ensure_ascii=False) + ";\n")
    return out_dir, scene


def _inks(seg: Segment, tl: Timeline, project_dir: Path, seg_dir: Path, scene: dict) -> dict:
    """Adaptive text styles of a legibility segment: probe the built (ink-less) segment at every element sample time
    with the text hidden and as a glyph mask, then `legibility.choose` per element."""
    key = keys(seg, tl, project_dir)[0]
    if key in _INKS:
        return _INKS[key]
    lg, S = scene["legibility"], tl.settings
    snap = seg_dir.parent / f"{seg_dir.name}_ink"
    try:
        probe = probe_dom(seg_dir, legibility.all_times(scene, lg), S.width, S.height, snap, bg="bgonly", ref=key)
        samples = legibility.collect(scene, lg, probe)
    finally:
        shutil.rmtree(snap, ignore_errors=True)
    choices = {e["id"]: legibility.choose(e, samples[e["id"]], lg, S.fps)
               for e in legibility.elements(scene) if samples.get(e["id"])}
    inks = legibility.scene_inks(choices)
    if lg.get("glow_colour") == "entity":     # light text glows in its entity colour only (none without one)
        for e in legibility.elements(scene):
            if e["id"] in inks:
                inks[e["id"]]["glow"] = e.get("colour")
    _bounded_put(_INKS, key, inks, INK_CACHE_MAX)
    return inks


def cache_key(seg: Segment, tl: Timeline, project_dir: Path, scene: dict | None = None) -> str:
    """Segment cache key (`scene` is accepted for call-site symmetry; the key is derived from the timeline)."""
    return keys(seg, tl, project_dir)[0]


def encode_settings(tl: Timeline) -> tuple[str, int, list[str]]:
    """(capture quality, HyperFrames CRF, normalising x264 args) of a timeline: profile [render], else HF_QUALITY,
    HF_CRF and config.X264_SEGMENT."""
    r = profiles.of(tl).render
    return r.hf_quality or HF_QUALITY, HF_CRF if r.hf_crf is None else r.hf_crf, r.x264("segment")


def _key(scene: dict, files: list[Path], mk: str, tl: Timeline) -> str:
    """Segment key: engine render version, capture/encode settings, the scene (every profile value that reaches the
    template, card and prop images by content-hash name), the map derivative key, content hashes of the textures,
    the scripts the scene adds (script_files) and the fonts copied into the build dir, the normalising x264 thread
    arguments (config.x264_threads), the toolchain identity (toolchain.hf_identity), and the engine source that
    writes the build dir (CODE)."""
    q, crf, x264 = encode_settings(tl)
    return blake3_json({"v": HF_VERSION, "code": code_hash(CODE), "enc": [q, crf], "x264": x264,
                        "x264_threads": config.x264_threads(resources.X264_THREADS), "algo": core.ALGO_VERSION,
                        "scene": scene, "map": mk, "files": blake3_files(files),
                        "fonts": blake3_files([config.FONTS_DIR / f for f in font_files(scene)]),
                        "toolchain": toolchain.hf_identity()})


def render(seg: Segment, tl: Timeline, project_dir: Path, workdir: Path, workers: int = 3,
           use_cache: bool = True) -> tuple[Path, bool]:
    key = keys(seg, tl, project_dir)[0]
    if use_cache and (hit := cache.get_file("segments", key)):
        return hit, True
    assert seg.gsap_scene_config is not None
    if seg.gsap_scene_config.split is not None:
        return _render_split(seg, tl, project_dir, workdir, workers, use_cache, key), False
    hf_dir = workdir / "hf" / f"seg{seg.seg_index:03d}"
    kfile = workdir / "hf" / f"seg{seg.seg_index:03d}.key"
    if (hf_dir / "index.html").is_file() and (hf_dir / "scene.js").is_file() and kfile.is_file() and kfile.read_text() == key:
        d = hf_dir
    else:
        d, _ = build_dir(seg, tl, project_dir, hf_dir)
        kfile.write_text(key)
    out = workdir / "seg" / f"seg{seg.seg_index:03d}_hf.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_suffix(".raw.mp4")
    q, crf, x264 = encode_settings(tl)
    run([config.NODE, hfversion.require().cli, "render", d, "--fps", str(tl.settings.fps), "--sdr", "-q", q, "--crf", str(crf),
         "--no-browser-gpu", "--workers", str(workers), "-o", raw], env=config.hf_env(), timeout=900)
    n = seg.n_frames
    if _is_engine_format(raw, tl, n):
        raw.replace(out)
    else:
        # normalise to the engine's exact BT.709 stream format and frame count so concat demuxer joins Native + HF seamlessly
        run([config.FFMPEG, "-v", "error", "-y", "-i", raw, "-an", "-vf",
             f"fps={tl.settings.fps},tpad=stop_mode=clone:stop=-1,setsar=1,{config.BT709_VF}", *x264,
             *config.x264_threads(resources.X264_THREADS), "-frames:v", str(n), "-movflags", "+faststart", out])
        raw.unlink(missing_ok=True)
    streams.verify(out, n, tl.settings.fps, f"segment {seg.seg_index} (HyperFrames)")
    return (cache.put_file("segments", key, out) if use_cache else out), False


def _render_split(seg: Segment, tl: Timeline, project_dir: Path, workdir: Path, workers: int, use_cache: bool,
                  key: str) -> Path:
    """Both panels rendered by HyperFrames (each its own scene, map and camera, same frames), stacked top / divider /
    bottom into the segment file; nothing animated outside HyperFrames."""
    assert seg.gsap_scene_config is not None
    assert seg.gsap_scene_config.split is not None
    sp: SplitView = seg.gsap_scene_config.split
    n, S = seg.n_frames, tl.settings
    files = []
    for k, (s, t) in enumerate(split_panels(seg, tl)):
        sub = workdir / "split" / f"seg{seg.seg_index:03d}_{k}"
        p, _ = render(s.model_copy(update={"seg_index": seg.seg_index}), t, project_dir, sub, workers, use_cache)
        files.append(p)
    out = workdir / "seg" / f"seg{seg.seg_index:03d}_hf.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    _, _, x264 = encode_settings(tl)
    run([config.FFMPEG, "-v", "error", "-y", "-i", files[0], "-i", files[1], "-f", "lavfi", "-i",
         f"color=c={sp.divider_colour}:s={S.width}x{sp.divider_px}:r={S.fps}", "-filter_complex",
         "[0:v][2:v][1:v]vstack=inputs=3,setsar=1[v]", "-map", "[v]", "-an", *x264,
         *config.x264_threads(resources.X264_THREADS), "-frames:v", str(n), out])
    streams.verify(out, n, S.fps, f"segment {seg.seg_index} (split screen)")
    return cache.put_file("segments", key, out) if use_cache else out


def _is_engine_format(p: Path, tl: Timeline, n: int) -> bool:
    """HyperFrames output usable directly as the segment file without normalising: matches the engine segment stream
    parameters (codec, pix_fmt, dimensions, frame rate, time_base, colour tags, SPS/PPS extradata) and has exactly
    n frames from 0 lasting n / fps (streams.verify)."""
    from . import native

    S = tl.settings
    _, _, x264 = encode_settings(tl)
    if not native.copy_eligible(p, S.width, S.height, S.fps, x264):
        return False
    try:
        streams.verify(p, n, S.fps, "HyperFrames output")
    except EngineError:
        return False
    return True
