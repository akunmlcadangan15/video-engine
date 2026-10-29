#!/usr/bin/env python3
"""Write master_timeline.json from the shot plan below and assets/geo.json (made by tools/make_maps.py).

Segment boundaries copy the reference shot map (reference_shots.json); beat frames copy its text and camera beats.
Run: python3 tools/build_timeline.py
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
GEO = json.loads((HERE / "assets/geo.json").read_text())
FPS = 30
TOTAL = 2182                                   # reference: 2182 frames at 30 fps
CUTS = [0, 495, 683, 1273, 1471, TOTAL]        # 16.500, 22.767, 42.433, 49.033 s

BM_JULY = ("https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-topography-bathymetry/"
           "july/world.topo.bathy.200407.3x21600x10800.jpg")
BM_JULY_C1 = ("https://assets.science.nasa.gov/content/dam/science/esd/eo/images/bmng/bmng-topography-bathymetry/"
              "july/world.topo.bathy.200407.3x21600x21600.C1.jpg")
NE_GRAY = "https://www.naturalearthdata.com/downloads/10m-raster-data/10m-gray-earth/"
BMNG = "NASA Earth Observatory / Reto Stöckli, NASA Goddard Space Flight Center (Blue Marble: Next Generation)"
CREDITS = [
    {"asset_id": "bmng_globe", "path": "assets/caspian_globe.jpg", "source_url": BM_JULY, "license": "Public Domain",
     "author": BMNG, "watermark_free": True},
    {"asset_id": "bmng_deep", "path": "assets/caspian_deep.jpg", "source_url": BM_JULY_C1, "license": "Public Domain",
     "author": BMNG, "watermark_free": True},
    {"asset_id": "bmng_straits", "path": "assets/caspian_straits.jpg", "source_url": BM_JULY, "license": "Public Domain",
     "author": BMNG, "watermark_free": True},
    {"asset_id": "ne_gray_relief", "path": "assets/caspian_relief.jpg", "source_url": NE_GRAY,
     "license": "Public Domain", "author": "Natural Earth (naturalearthdata.com)", "watermark_free": True},
    {"asset_id": "bmng_paratethys", "path": "assets/caspian_paratethys.jpg", "source_url": BM_JULY,
     "license": "Public Domain", "author": BMNG, "watermark_free": True},
]


def f(t_abs, seg):
    """Absolute reference second -> frame relative to the start of segment `seg`."""
    return round(t_abs * FPS) - CUTS[seg]


def P(m, key):
    return GEO[m]["points"][key]


def region(m, key, entity):
    return {"entity": entity, "polygon": GEO[m]["regions"][key]}


def pin(m, key, label=""):
    x, y = P(m, key)
    return {"label": label, "x": x, "y": y}


def near(m, key, dx=0.0, dy=0.0):
    x, y = P(m, key)
    return [round(min(1, max(0, x + dx)), 4), round(min(1, max(0, y + dy)), 4)]


def note(m, text, at_key, dx, dy, arrow_key=None, rot=-6.0):
    n = {"text": text, "at": near(m, at_key, dx, dy), "rotation": rot}
    if arrow_key:
        n["arrow_to"] = near(m, arrow_key)
    return n


def note_px(m, text, at_key, sx, sy, zoom, arrow_key=None, rot=-6.0):
    """Note centred (sx, sy) output px from a map point, for a view at `zoom` over the cover fit."""
    return note(m, text, at_key, sx / (1080 * zoom), sy / (1920 * zoom), arrow_key, rot)


def hf(seg, m, asset_id, scene, visual_type):
    return {"seg_index": seg, "engine_type": "HYPERFRAMES_GSAP", "start_frame": CUTS[seg], "end_frame": CUTS[seg + 1],
            "source_in_frame": 0, "overlays": [], "visual_type": visual_type,
            "gsap_scene_config": {"template": "map_pins", "map_region": m, "map_asset": f"assets/{m}.jpg",
                                  "map_asset_id": asset_id, **scene}}


def shot1():
    m = "caspian_globe"
    s = 0
    # reference blocking colours: Russia red, Azerbaijan grey-brown, Turkmenistan orange, Iran green, Kazakhstan brown;
    # entity order fixes the palette pick (mauve, indigo, ochre, green, brown); each water body is its own entity (profile colours)
    countries = [("RUS", "Russia"), ("KAZ", "Kazakhstan"), ("AZE", "Azerbaijan"), ("TKM", "Turkmenistan"), ("IRN", "Iran")]
    regions = [{**region(m, "Caspian Sea", "Caspian Sea"), "edge": "white"}] + \
              [{**region(m, k, name), "at_frame": f(12.0, s)} for k, name in countries]
    pins = [pin(m, "caspian_c", "Caspian"), pin(m, "caspian_n", "LARGEST"), pin(m, "aral", "Aral"),
            pin(m, "balkhash", "Balkhash"), pin(m, "ladoga", "Ladoga"), pin(m, "van", "Van"),
            pin(m, "russia", "Russia"), pin(m, "kazakhstan", "Kazakhstan"), pin(m, "azerbaijan", "Azerbaijan"),
            pin(m, "iran", "Iran"), pin(m, "turkmenistan", "Turkmenistan"), pin(m, "south_basin", "32 ft"),
            pin(m, "caspian_n", "5 countries")]
    routes = [{"points": [near(m, "caspian_n", 0, 0.004), near(m, "caspian_s", 0, -0.006)], "draw_s": 0.5}]
    lake = note_px(m, "Caspian Lake", "caspian_c", -150, 160, 1.7, None, -6)
    miles = note_px(m, "750 miles", "caspian_c", 190, 0, 1.7, None, -5)
    ft = note_px(m, "32 ft", "south_basin", 150, 0, 2.4, None, -5)
    beats = [   # reference: Caspian Sea -> Lake 0.8-5.4, LARGEST lake 6.0, 750 miles 9.0, countries 10.5, 32 ft 14.6
        {"at_frame": 0, "region": 0, "notes": [lake]},
        {"at_frame": f(4.1, s), "pins": [1], "markers": [4]},
        {"at_frame": f(8.0, s), "region": 0, "routes": [0], "notes": [miles]},
        {"at_frame": f(12.0, s), "markers": [11], "notes": [ft]},
    ]
    return hf(s, m, "bmng_globe", {"regions": regions,
                                   "pins": pins, "routes": routes, "beats": beats}, "photo")


def shot2():
    m = "caspian_deep"
    s = 1
    dx, dy = P(m, "south_basin")
    key = lambda t, z, move="settle": {"at_frame": t, "focus": [round(dx + 330 / (1080 * z), 4),
                                                              round(dy + 120 / (1920 * z), 4)], "zoom": z,
                                       "move": move, **({"move_s": 0.8} if move == "settle" else {})}
    n = CUTS[s + 1] - CUTS[s]
    return hf(s, m, "bmng_deep", {
        "pins": [{**pin(m, "south_basin", "Burj Khalifa 2,717 ft"), "at_frame": f(19.5, s)}],
        "figures": [{"kind": "number", "value": 3363, "suffix": " ft", "motion": "counter", "count_from": 122,
                     "count_s": 1.7, "at_frame": f(17.0, s), "position": [0.5, 0.2]}],
        "camera_path": [
            {"at_frame": 0, "focus": [0.5, 0.42], "zoom": 1.15, "move": "glide"},
            key(f(17.6, s), 1.8), key(f(19.3, s), 2.2), key(f(21.0, s), 2.6), key(n - 1, 2.9, "glide"),
        ]}, "diagram")


def shot3():
    m = "caspian_straits"
    s = 2
    W = "white"
    regions = [{**region(m, "Caspian Sea", "Caspian Sea"), "edge": W},
               {**region(m, "Black Sea", "Black Sea"), "edge": W},
               {**region(m, "Mediterranean Sea", "Mediterranean"), "edge": W},
               {**region(m, "North Atlantic Ocean", "Atlantic"), "edge": W}]
    pins = [pin(m, "bosphorus", "Bosphorus"), pin(m, "gibraltar", "Gibraltar"),
            pin(m, "caspian_mid", "No way to the ocean"), pin(m, "persian_gulf", "Persian Gulf"),
            pin(m, "black_sea_lbl", "Black"), pin(m, "arctic_ob", "Arctic"), pin(m, "caspian_mid", "Caspian"),
            pin(m, "med_c", "Mediterranean")]
    routes = [{"points": GEO[m]["routes"]["sea_east"], "draw_s": 0.6},
              {"points": GEO[m]["routes"]["sea_west"], "draw_s": 0.6}]
    beats = [
        {"at_frame": 0, "markers": [6], "notes": [note_px(m, "Salty...", "caspian_mid", -40, 150, 2.96, None, -6)]},
        {"at_frame": f(26.8, s), "region": 0},
        {"at_frame": f(29.8, s), "region": 1, "notes": [note_px(m, "Black Sea", "black_sea_c", 0, 0, 2.0, None, -5)]},
        {"at_frame": f(32.8, s), "pins": [0]},
        {"at_frame": f(35.75, s), "region": 2, "notes": [note_px(m, "Mediterranean Sea", "med_c", 0, 0, 1.6, None, -5)]},
        {"at_frame": f(38.8, s), "region": 3, "pins": [1]},
    ]
    return hf(s, m, "bmng_straits", {"regions": regions, "pins": pins, "beats": beats}, "photo")


def shot4():
    m = "caspian_relief"
    s = 3
    regions = [region(m, "Caspian Sea", "Caspian Sea")]
    # reference: red-graded relief, LAKE typed on 45.0-46.6 above the lake. The marker pin (no label) frames the lake.
    # LAKE is a note on the paper beside the lake: the title/area-label halo failed R64 counters during camera pulses
    beats = [
        {"at_frame": 0, "markers": [0]},
        {"at_frame": f(TITLE4, s), "region": 0, "markers": [0],
         "notes": [note_px(m, "LAKE", "south_basin", NOTE4[0], NOTE4[1], 2.96, None, -5)]},
    ]
    return hf(s, m, "ne_gray_relief", {"regions": regions, "pins": [pin(m, "south_basin", "Caspian")],
                                       "beats": beats}, "map")


NOTE4 = (290, -190)
TITLE4 = 44.4


def shot5():
    m = "caspian_paratethys"
    s = 4
    W = "white"
    regions = [{**region(m, "Caspian Sea", "Caspian Sea"), "edge": W},
               {**region(m, "Paratethys", "Paratethys"), "edge": W},
               {**region(m, "Black Sea", "Black Sea"), "edge": W}]
    pins = [pin(m, "west_coast", "Caspian Sea"), pin(m, "black_sea_c", "Black Sea")]
    ural = [pt for pt in GEO[m]["routes"]["ural"] if pt[1] >= P(m, "caspian_n")[1] - 0.012]
    routes = [{"points": GEO[m]["routes"]["volga"], "draw_s": 1.0},
              {"points": ural, "draw_s": 0.5}]
    beats = [
        {"at_frame": 0, "region": 0},
        {"at_frame": f(50.0, s), "region": 1,
         "notes": [note_px(m, "Paratethys", "black_sea_c", 0, -120, 1.2, None, -6)]},
        {"at_frame": f(53.2, s), "region": 2, "pins": [1]},
        {"at_frame": f(56.3, s), "region": 1},
        {"at_frame": f(58.0, s), "region": 0},
        {"at_frame": f(60.6, s), "region": 0, "routes": [0], "camera": False},
        {"at_frame": f(62.2, s), "region": 0, "routes": [1], "camera": False},
        {"at_frame": f(63.9, s), "markers": [0]},
        {"at_frame": f(66.8, s), "markers": [0],
         "notes": [note_px(m, "Caspian Sea", "west_coast", 120, -150, 2.96, None, -5)]},
        {"at_frame": f(69.9, s), "markers": [0], "camera": False,
         "notes": [note_px(m, "LAKE", "west_coast", 150, 140, 2.96, "west_coast", -6)]},
    ]
    return hf(s, m, "bmng_paratethys", {"regions": regions, "pins": pins,
                                        "routes": routes, "beats": beats}, "photo")


def captions():
    """Caption words from the reference's on-screen caption chunks (reference_frames.json): each chunk's words share
    its [t0, t1) evenly."""
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


def main():
    tl = {
        "project_id": "geoverum_caspian",
        "profile": "geoverum",
        "settings": {"width": 1080, "height": 1920, "fps": FPS, "topic": "Why the Caspian Sea is not a sea"},
        "credits_manifest": CREDITS,
        "audio_chain": {"auto_captions": False, "language": "en",
                        "music_brief": {"mood": "curious documentary", "energy": "medium", "bpm_min": 90,
                                        "bpm_max": 125, "duration_s": 73}},
        "segments": [shot1(), shot2(), shot3(), shot4(), shot5()],
        "captions": captions(),
        "caption_style": {"max_words": 5},
    }
    (HERE / "master_timeline.json").write_text(json.dumps(tl, indent=2, ensure_ascii=False) + "\n")
    print("master_timeline.json written")


if __name__ == "__main__":
    main()
