#!/usr/bin/env python3
"""Caspian v3: write master_timeline.json (5 segments on the reference cuts) from the shot plan below.

Maps: assets/caspian_eurasia.jpg (S1, S4, S5), caspian_basin.jpg (S2), caspian_westward.jpg (S3), all made by
tools/make_maps.py from the NASA Blue Marble 500 m tiles. Notes and map props are placed in frame px at a planned
camera frame: `--probe` plans the camera (layout) and stores the views in src/views.json, then rewrites the
timeline with every screen position resolved to its map point (notes do not move the camera).
Run: python3 tools/build_timeline_v3.py && <engine python> tools/build_timeline_v3.py --probe
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))
GEO = json.loads((HERE / "assets/geo.json").read_text())
VIEWS_P = HERE / "src/views.json"
VIEWS = json.loads(VIEWS_P.read_text()) if VIEWS_P.exists() else {}
FPS, TOTAL = 30, 2182
CUTS = [0, 495, 683, 1273, 1471, TOTAL]
W, H = 1080, 1920
WANT = {}                                   # (seg, frame) views the screen placements need

BMNG = "NASA Earth Observatory / Reto Stöckli, NASA Goddard Space Flight Center (Blue Marble: Next Generation)"
BM500 = ("https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-topography-bathymetry/"
         "july/world.topo.bathy.200407.3x21600x21600.{}.jpg")
TWEMOJI = ("https://github.com/jdecked/twemoji", "CC-BY-4.0",
           "Twemoji graphics by Twitter, Inc. and other contributors, CC BY 4.0 (rasterised from the owned SVG)")
KEN2 = "Kenney (kenney.nl)"
CREDITS = [
    {"asset_id": "bmng500_eurasia", "path": "assets/caspian_eurasia.jpg", "source_url": BM500.format("C1"),
     "license": "Public Domain", "author": BMNG, "watermark_free": True},
    {"asset_id": "bmng500_westward", "path": "assets/caspian_westward.jpg",
     "source_url": BM500.format("B1"), "license": "Public Domain", "author": BMNG,
     "watermark_free": True},
    {"asset_id": "bmng500_basin", "path": "assets/caspian_basin.jpg", "source_url": BM500.format("C1"),
     "license": "Public Domain", "author": BMNG, "watermark_free": True},
]
# Twemoji (CC BY 4.0) stand-ins for the Fluent Emoji (MIT) props of asset_plan.json: R14 at master 7dbd017 has no MIT
PROPS = {   # asset_id: (name, codepoint)
    "tw_cross": "274c", "tw_globe": "1f30f", "tw_ruler": "1f4cf", "tw_storm": "26c8", "tw_bolt": "26a1",
    "tw_wave": "1f30a", "tw_house": "1f3e0", "tw_salt": "1f9c2", "tw_turtle": "1f422", "tw_fish": "1f41f",
    "tw_tap": "1f6b0", "tw_noentry": "1f6ab", "tw_ship": "1f6a2", "tw_unlocked": "1f513", "tw_locked": "1f512",
    "tw_qwhite": "2754", "tw_qred": "2753", "tw_hourglass": "23f3", "tw_shell": "1f41a", "tw_ice": "1f9ca",
    "tw_canoe": "1f6f6",
}
PROP_FILE = {}
for k, cp in PROPS.items():
    PROP_FILE[k] = f"assets/props/{k}_{cp}.png"
    CREDITS.append({"asset_id": k, "path": PROP_FILE[k], "source_url": TWEMOJI[0], "license": TWEMOJI[1],
                    "author": TWEMOJI[2] + f", U+{cp.upper()}", "watermark_free": True})
GI = "https://game-icons.net/1x1/{}.html"
OTHER = [   # asset_id, path, source_url, licence, author
    ("kn_pirate_ship", "assets/props/kenney_pirate_ship_2.png", "https://kenney.nl/assets/pirate-pack", "CC0", KEN2),
    ("kn_unit_01", "assets/props/medievalUnit_01.png", "https://kenney.nl/assets/medieval-rts", "CC0", KEN2),
    ("kn_unit_05", "assets/props/medievalUnit_05.png", "https://kenney.nl/assets/medieval-rts", "CC0", KEN2),
    ("kn_unit_09", "assets/props/medievalUnit_09.png", "https://kenney.nl/assets/medieval-rts", "CC0", KEN2),
    ("k3_house", "assets/models/city-kit-suburban/building-type-a.glb", "https://kenney.nl/assets/city-kit-suburban",
     "CC0", KEN2),
    ("k3_skyscraper", "assets/models/city-kit-commercial/building-skyscraper-c.glb",
     "https://kenney.nl/assets/city-kit-commercial", "CC0", KEN2),
    ("k3_cargo", "assets/models/watercraft-kit/ship-cargo-a.glb", "https://kenney.nl/assets/watercraft-kit", "CC0",
     KEN2),
    ("k3_rowboat", "assets/models/watercraft-kit/boat-row-small.glb", "https://kenney.nl/assets/watercraft-kit",
     "CC0", KEN2),
    ("k3_pirate", "assets/models/pirate-kit/ship-small.glb", "https://kenney.nl/assets/pirate-kit", "CC0", KEN2),
    ("gi_spears", "assets/props/gi_spears.png", GI.format("lorc/spears"), "CC-BY-3.0",
     "Lorc (game-icons.net), CC BY 3.0 (rasterised from the owned SVG)"),
    ("gi_caveman", "assets/props/gi_caveman.png", GI.format("delapouite/caveman"), "CC-BY-3.0",
     "Delapouite (game-icons.net), CC BY 3.0 (rasterised from the owned SVG)"),
    ("gi_raft", "assets/props/gi_raft.png", GI.format("delapouite/raft"), "CC-BY-3.0",
     "Delapouite (game-icons.net), CC BY 3.0 (rasterised from the owned SVG)"),
    ("ph_pinnace", "assets/models/ship_pinnace/ship_pinnace_1k.gltf", "https://polyhaven.com/a/ship_pinnace", "CC0",
     "Poly Haven"),
]
for aid, path, url, lic, author in OTHER:
    PROP_FILE[aid] = path
    CREDITS.append({"asset_id": aid, "path": path, "source_url": url, "license": lic, "author": author,
                    "watermark_free": True})
PLACED = []                                  # (asset_id, abs t0 s, abs t1 s, where) for the report


def f(t_abs, seg):
    return round(t_abs * FPS) - CUTS[seg]


def P(m, key):
    return GEO[m]["points"][key]


def map_size(m):
    s = GEO[m]["spec"]
    a = s["height_km"] / s["width_km"]            # cover fit on 1080 x 1920
    return (W, W * a) if a >= H / W else (H / a, H)


def to_map(seg, m, frame, sx, sy):
    """Frame px at segment frame -> normalised map point under it (planned camera from views.json)."""
    WANT.setdefault(seg, set()).add(frame)
    v = VIEWS.get(str(seg), {}).get(str(frame))
    if not v:
        return [0.5, 0.5]
    mw, mh = v["map_size"]
    return [round(min(1, max(0, (sx - v["x"]) / (v["s"] * mw))), 4),
            round(min(1, max(0, (sy - v["y"]) / (v["s"] * mh))), 4)]


def note(seg, m, frame, text, sx, sy, arrow=None, rot=-6.0):
    n = {"text": text, "at": to_map(seg, m, frame, sx, sy), "rotation": rot}
    if arrow:
        n["arrow_to"] = to_map(seg, m, frame, *arrow)
    return n


def region(m, key, entity, **kw):
    return {"entity": entity, "polygon": GEO[m]["regions"][key], **kw}


def pin(m, key, label=""):
    x, y = P(m, key)
    return {"label": label or key.replace("_", " ").title(), "x": x, "y": y}   # marker-only pins: unique, unshown


def prop(seg, aid, t0, t1, *, screen=None, at=None, m=None, at_frame=None, width_px=120, depth=0.0, rot=0.0,
         enter=None, exit=None, idle=None, z=0, model=None, shadow=True):
    """t0, t1 absolute seconds. screen=(x, y) frame px; at=(sx, sy) frame px resolved to a map point at
    `at_frame` (segment frame, default the first shown frame)."""
    p = {"asset": PROP_FILE[aid], "asset_id": aid, "t_in": f(t0, seg), "t_out": f(t1, seg), "width_px": width_px,
         "rotation": rot, "z": z}
    if screen:
        p.update(anchor="screen", x=screen[0], y=screen[1])
    else:
        x, y = to_map(seg, m, at_frame if at_frame is not None else p["t_in"], *at)
        p.update(anchor="map", x=x, y=y, depth=depth)
    p["enter"] = enter or {"kind": "pop", "s": 0.3}
    p["exit"] = exit or {"kind": "fade", "s": 0.25}
    if idle:
        p["idle"] = idle
    if model:
        p["model"] = model
    if shadow:
        p["shadow"] = {"dx": 0, "dy": 8, "blur": 12, "alpha": 0.4}
    PLACED.append((aid, t0, t1, f"S{seg + 1} {'screen' if screen else 'map'}"))
    return p


def hf(seg, m, asset_id, scene, visual_type):
    return {"seg_index": seg, "engine_type": "HYPERFRAMES_GSAP", "start_frame": CUTS[seg], "end_frame": CUTS[seg + 1],
            "source_in_frame": 0, "overlays": [], "visual_type": visual_type,
            "gsap_scene_config": {"template": "map_pins", "map_region": m, "map_asset": f"assets/{m}.jpg",
                                  "map_asset_id": asset_id, **scene}}


BOB = {"kind": "bob", "amplitude": 6, "period_s": 1.6}
SWAY = {"kind": "rotate", "amplitude": 4, "period_s": 2.2}


def shot1():
    m, s = "caspian_eurasia", 0
    countries = [("RUS", "Russia"), ("KAZ", "Kazakhstan"), ("TKM", "Turkmenistan"), ("IRN", "Iran"),
                 ("AZE", "Azerbaijan")]
    regions = [{**region(m, "Caspian Sea", "Caspian Sea"), "edge": "white"}] + \
              [{**region(m, k, name), "at_frame": f(10.4, s)} for k, name in countries]
    pins = [pin(m, "caspian_c", "Caspian"), pin(m, "ladoga", ""), pin(m, "persian_gulf", ""),
            pin(m, "russia", "Russia"), pin(m, "kazakhstan", "Kazakhstan"), pin(m, "turkmenistan", "Turkmenistan"),
            pin(m, "iran", "Iran"), pin(m, "azerbaijan", "Azerbaijan"), pin(m, "south_basin", ""),
            pin(m, "balkhash", ""), pin(m, "van", "")]
    routes = [{"points": [[P(m, "caspian_n")[0], P(m, "caspian_n")[1] + 0.002],
                          [P(m, "caspian_s")[0], P(m, "caspian_s")[1] - 0.004]], "draw_s": 0.5}]
    # beats spaced for the layout timing gates (L10: settle 0.8 s + entries + 1.0 s hold before the next exit)
    B = [f(0.0, s), f(2.4, s), f(5.3, s), f(8.8, s), f(10.3, s), f(10.8, s), f(11.3, s), f(11.8, s), f(12.2, s),
         f(13.8, s)]
    beats = [
        {"at_frame": B[0], "region": 0, "markers": [0, 8], "notes": [note(s, m, 80, "Caspian Lake", 520, 600)]},
        {"at_frame": B[2], "markers": [1, 10]},
        {"at_frame": B[3], "region": 0, "routes": [0], "camera": False},
        {"at_frame": B[4], "region": 1, "routes": [0], "camera": False},
        {"at_frame": B[5], "region": 2, "routes": [0], "camera": False},
        {"at_frame": B[6], "region": 3, "camera": False},
        {"at_frame": B[7], "region": 5, "camera": False},
        {"at_frame": B[8], "region": 4},             # Iran: own camera fit keeps its fill off the caption band (L09)
        {"at_frame": B[9], "markers": [8], "hold": "read"},
    ]
    props = [
        prop(s, "tw_cross", 2.6, 3.6, screen=(800, 780), width_px=110, rot=-8),
        prop(s, "tw_globe", 5.8, 8.4, screen=(800, 420), width_px=150, idle=SWAY),
        prop(s, "tw_ruler", 9.0, 10.3, screen=(300, 860), width_px=130, rot=-20),
        prop(s, "tw_storm", 12.5, 14.0, screen=(560, 560), width_px=170, idle=BOB, z=2),
        prop(s, "tw_wave", 14.1, 16.5, screen=(330, 880), width_px=170, idle=BOB),
        prop(s, "k3_house", 14.4, 16.5, screen=(560, 900), width_px=180,
             model={"yaw": 30, "pitch": 20, "size_px": 512, "turntable_s": 6, "turn_deg": 60}),
    ]
    return hf(s, m, "bmng500_eurasia", {"regions": regions, "pins": pins, "routes": routes, "beats": beats,
                                        "props": props}, "photo")


def shot2():
    m, s = "caspian_basin", 1
    dx, dy = P(m, "south_basin")
    n = CUTS[s + 1] - CUTS[s]
    def key(t, z, tilt, move="glide", fx=0.0, fy=0.0):
        return {"at_frame": t, "focus": [round(dx + fx, 4), round(dy + fy, 4)], "zoom": z, "move": move,
                "tilt": tilt}
    props = [
        prop(s, "tw_ruler", 16.9, 18.7, screen=(860, 560), width_px=130, idle=SWAY),
        prop(s, "k3_skyscraper", 19.3, 22.7, screen=(300, 720), width_px=200, enter={"kind": "slide", "s": 0.8,
             "dir": "up", "distance_px": 300}, model={"yaw": 25, "pitch": 12, "size_px": 768}),
    ]
    return hf(s, m, "bmng500_basin", {
        "pins": [{**pin(m, "south_basin", "Burj Khalifa 2,717 ft"), "at_frame": f(19.5, s)}],
        "figures": [{"kind": "number", "value": 3363, "suffix": " ft", "motion": "counter", "count_from": 122,
                     "count_s": 1.7, "at_frame": f(16.9, s), "position": [0.5, 0.2]}],
        # M03: tilt 40 deg at zoom >= 2.1 with the focus 0.1 map heights south keeps map under every corner
        "camera_path": [key(0, 3.4, 0, fy=0.11), key(f(18.3, s), 2.8, 30, fy=0.1), key(f(20.5, s), 2.5, 40, fy=0.1),
                        key(n - 1, 2.1, 40, fy=0.1)],
        "props": props}, "diagram")


def shot3():
    m, s = "caspian_westward", 2
    regions = [{**region(m, "Caspian Sea", "Caspian Sea"), "edge": "white"},
               {**region(m, "Black Sea", "Black Sea"), "edge": "white"},
               {**region(m, "Mediterranean Sea", "Mediterranean"), "edge": "white"},
               {**region(m, "North Atlantic Ocean", "Atlantic"), "edge": "white"}]
    pins = [pin(m, "bosphorus", "Bosphorus"), pin(m, "gibraltar", "Gibraltar"), pin(m, "caspian_mid", ""),
            pin(m, "black_sea_c", ""), pin(m, "atlantic", "")]
    cm, bs = P(m, "caspian_mid"), P(m, "black_sea_c")
    routes = [{"points": [cm, bs], "draw_s": 0.6}]
    B = [0, f(26.0, s), f(28.2, s), f(30.2, s), f(33.2, s), f(36.4, s), f(37.4, s), f(38.8, s)]
    beats = [
        {"at_frame": B[0], "region": 0, "hold": "read", "notes": [note(s, m, 60, "Salty...", 560, 620)]},
        {"at_frame": B[1], "markers": [2, 3], "routes": [0]},
        {"at_frame": B[3], "region": 1, "markers": [3], "notes": [note(s, m, B[3] + 50, "Black Sea", 640, 560)]},
        {"at_frame": B[4], "pins": [0], "markers": [3]},
        {"at_frame": B[7], "pins": [1], "markers": [4]},   # Atlantic fill would sit under the caption band at zoom 1 (L09)
    ]
    ic = dict(enter={"kind": "pop", "s": 0.25}, width_px=120)
    props = [
        prop(s, "tw_salt", 23.3, 25.4, m=m, at=(800, 860), width_px=150, depth=0.6, rot=150,
             enter={"kind": "slide", "s": 0.4, "dir": "down", "distance_px": 120}, idle=SWAY, z=2),
        prop(s, "tw_fish", 26.1, 28.6, screen=(290, 480), width_px=80, enter={"kind": "pop", "s": 0.25}),
        prop(s, "tw_wave", 26.4, 28.6, screen=(420, 400), **ic),
        prop(s, "tw_salt", 27.2, 28.6, screen=(840, 400), **ic),
        prop(s, "tw_cross", 28.6, 30.2, m=m, at=(330, 640), at_frame=B[2] + 30, width_px=100, depth=0.3),
        prop(s, "tw_ship", 33.4, 35.7, screen=(740, 900), width_px=130, idle=BOB),
        prop(s, "ph_pinnace", 38.9, 40.8, screen=(780, 720), width_px=220,
             model={"yaw": 250, "pitch": 15, "size_px": 512, "turntable_s": 8, "turn_deg": 40}, idle=BOB),
    ]
    return hf(s, m, "bmng500_westward", {"regions": regions, "pins": pins, "routes": routes, "beats": beats,
                                         "props": props}, "photo")


RED = {"tint": {"color": "#b01c08", "amount": 0.55}, "gain": [1.05, 0.82, 0.78], "saturation": 1.1}


def shot4():
    m, s = "caspian_eurasia", 3
    regions = [{**region(m, "Caspian Sea", "Caspian Sea"), "edge": "white"}]
    pins = [pin(m, "south_basin", ""), pin(m, "caspian_mid", "")]
    B = [0, f(44.1, s), f(45.7, s)]
    beats = [
        {"at_frame": B[0], "region": 0},
        {"at_frame": B[1], "region": 0, "camera": False, "notes": [note(s, m, B[1] + 20, "LAKE", 760, 600)]},
        {"at_frame": B[2], "markers": [1], "hold": "read"},
    ]
    props = [
        prop(s, "tw_locked", 42.45, 43.7, screen=(540, 640), width_px=170, idle=SWAY),
        prop(s, "tw_qred", 47.0, 49.0, screen=(780, 680), width_px=100, idle=SWAY),
        prop(s, "tw_salt", 47.2, 49.0, screen=(600, 960), width_px=120),
    ]
    return {**hf(s, m, "bmng500_eurasia", {"regions": regions, "pins": pins, "beats": beats, "props": props},
                "photo"), "grade": RED}


def shot5():
    m, s = "caspian_eurasia", 4
    regions = [{**region(m, "Caspian Sea", "Caspian Sea"), "edge": "white"},
               {**region(m, "Paratethys", "Paratethys"), "edge": "white"},
               {**region(m, "Black Sea", "Black Sea"), "edge": "white"}]
    pins = [pin(m, "west_coast", ""), pin(m, "black_sea_c", "Black Sea"), pin(m, "ladoga", ""),
            pin(m, "persian_gulf", ""), pin(m, "caspian_mid", ""), pin(m, "aral", "")]
    ural = [pt for pt in GEO[m]["routes"]["ural"] if pt[1] >= P(m, "caspian_n")[1] - 0.012]
    routes = [{"points": GEO[m]["routes"]["volga"], "draw_s": 1.0}, {"points": ural, "draw_s": 0.5}]
    B = [0, f(50.0, s), f(53.4, s), f(56.0, s), f(57.5, s), f(59.1, s), f(61.8, s), f(64.6, s), f(67.6, s),
         f(70.6, s)]
    beats = [
        {"at_frame": B[0], "region": 0},
        {"at_frame": B[1], "region": 1, "notes": [note(s, m, B[1] + 40, "Paratethys", 420, 640)]},
        {"at_frame": B[2], "region": 2, "pins": [1]},
        {"at_frame": B[3], "region": 1, "pins": [1], "camera": False},
        {"at_frame": B[4], "region": 0},
        {"at_frame": B[5], "markers": [4], "hold": "read"},
        {"at_frame": B[6], "region": 0, "routes": [0]},
        {"at_frame": B[7], "markers": [0], "routes": [1]},
        {"at_frame": B[8], "region": 0, "hold": "read",
         "notes": [note(s, m, B[8] + 50, "LAKE", 760, 560, arrow=(640, 760))]},
    ]
    cube = dict(width_px=90)
    k3 = dict(enter={"kind": "pop", "s": 0.3})
    props = [
        prop(s, "tw_hourglass", 53.0, 54.4, screen=(820, 430), width_px=130, idle=SWAY),
        prop(s, "tw_shell", 60.2, 61.5, screen=(220, 430), width_px=120, idle=BOB),
        prop(s, "tw_salt", 60.4, 61.4, screen=(780, 600), **cube),
        prop(s, "gi_caveman", 64.0, 67.5, screen=(180, 620), width_px=110),
        prop(s, "k3_rowboat", 64.8, 67.5, screen=(720, 800), width_px=160, idle=BOB,
             model={"yaw": 200, "pitch": 30, "size_px": 512}),
    ]
    return hf(s, m, "bmng500_eurasia", {"regions": regions, "pins": pins, "routes": routes, "beats": beats,
                                        "props": props}, "photo")


def captions():
    ref = json.loads((HERE / "reference_frames.json").read_text())
    out = []
    for c in ref["captions"]:
        words = c["text"].split()
        a, b = round(c["t0"] * FPS), min(TOTAL - 1, round(c["t1"] * FPS))
        step = (b - a) / len(words)
        for i, w in enumerate(words):
            s0, s1 = a + round(i * step), a + round((i + 1) * step)
            out.append({"word": w, "start_frame": s0, "end_frame": max(s1, s0 + 1)})
    return out


def timeline():
    PLACED.clear()
    return {
        "project_id": "geoverum_caspian",
        "profile": "geoverum",
        "settings": {"width": W, "height": H, "fps": FPS, "topic": "Why the Caspian Sea is not a sea"},
        "credits_manifest": CREDITS,
        "audio_chain": {"auto_captions": False, "language": "en",
                        "music_brief": {"mood": "curious documentary", "energy": "medium", "bpm_min": 90,
                                        "bpm_max": 125, "duration_s": 73}},
        "segments": [shot1(), shot2(), shot3(), shot4(), shot5()],
        "captions": captions(),
    }


def write_credits():
    """Visual section of credits.txt = every v3 credit; map-data, NASA note and audio sections are kept."""
    path = HERE / "credits.txt"
    lines = path.read_text().splitlines()
    a = next(i for i, l in enumerate(lines) if l.startswith("Visual"))
    b = next(i for i, l in enumerate(lines) if l.startswith("Map data"))
    vis = ["Visual (maps: orthographic reprojection of the NASA Blue Marble 500 m tiles by tools/make_maps.py plus a "
           "tone grade; props: unmodified copies of owned PNG files, or owned SVG files rasterised to 512 px PNG; 3D models rendered as sprites by the engine)"]
    for c in CREDITS:
        vis.append(f"- {c['asset_id']} ({c['path']}) — {c['author']} — {c['source_url']} — {c['license']}")
    path.write_text("\n".join(lines[:a] + vis + [""] + lines[b:]) + "\n")


def write():
    tl = timeline()
    write_credits()
    (HERE / "master_timeline.json").write_text(json.dumps(tl, indent=2, ensure_ascii=False) + "\n")
    (HERE / "src/placed.json").write_text(json.dumps(PLACED, indent=1))
    return tl


def probe():
    """Plan every segment's camera and store the views the screen placements ask for."""
    from video_engine import hyperframes, timeline as T
    tl = T.load(HERE)
    views = {}
    for seg in tl.segments:
        want = WANT.get(seg.seg_index)
        if not want:
            continue
        sc = hyperframes.scene_data(seg, tl, HERE)
        from video_engine import layout
        views[str(seg.seg_index)] = {str(fr): {**{k: v for k, v in layout.camera_at(sc["camera"], fr).items()},
                                               "map_size": sc["map_size"]} for fr in sorted(want)}
    VIEWS_P.write_text(json.dumps(views, indent=1))
    return views


if __name__ == "__main__":
    (HERE / "src").mkdir(parents=True, exist_ok=True)
    write()
    if "--probe" in sys.argv:
        VIEWS.update(probe())
        write()
    print("master_timeline.json written;", len(PLACED), "props placed")
