#!/usr/bin/env python3
"""reference_frames.json / .md: the frame-by-frame description of the reference video that drives the timeline.

Measured (tools/analyze_reference.py, every frame at 30 fps): cuts, camera scale and centre per shot (homographies
chained from the shot's first frame), zoom / pan / rotation velocity, 3D cues (vertical and horizontal keystone rate =
perspective tilt change, rotation, a second layer moving against the map = parallax), sampled region colours.
Read by eye from the 0.5 s contact sheets and the caption-band crops (out/ref/cap_*.png): caption wording and timing,
on-screen titles and labels, elements (props / icons) and region fills. Those are listed in ANNOT below with the
frame times they were read at.

usage: python3 tools/analyze_reference.py REF.mp4 && python3 tools/build_reference_frames.py REF.mp4 [OURS.mp4]
"""
import json, math, sys
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent.parent
REF = sys.argv[1]
CAM = json.load(open(HERE / "out/ref/frames_camera.json"))
FPS = CAM["fps"]
FR = CAM["frames"]

# hard cuts (frame of the first frame of the new shot) found by the analysis: inliers collapse + histogram jump
CUTS = [0, 495, 683, 1273, 1471, len(FR)]
TRANSITIONS = [
    {"at_f": 495, "t": 16.5, "kind": "tilt-zoom into the water surface, then cut to the cross-section diagram",
     "measured": "zoom -1.4 .. -4.9 ln/s over frames 491-501, keystone rate up to 1.8/s, blur falls to 210"},
    {"at_f": 683, "t": 22.767, "kind": "radial zoom blur out of the diagram (0.4 s), cut to satellite close-up",
     "measured": "blur 112 (sharpness drop) at the cut, zoom -0.4 ln/s before it"},
    {"at_f": 1273, "t": 42.433, "kind": "hard cut into a full-frame red grade (landlocked)",
     "measured": "hist change 1.70, 18 inliers"},
    {"at_f": 1471, "t": 49.033, "kind": "whip pan with motion blur (0.3 s) to the Eurasia view",
     "measured": "blur 41-68 for frames 1468-1479, zoom |53| ln/s spike"},
]

CAPTIONS = [  # [t0, t1, text] read from the caption band crops (centre y 1366 px of 1920, x centred)
    [0.2, 1.5, "Why is the Caspian Sea"], [1.6, 2.2, "actually"], [2.3, 3.2, "not a sea"], [3.4, 4.3, "but a lake"],
    [4.4, 4.7, "And not just"], [4.9, 5.4, "any lake"], [5.6, 6.2, "but the largest"], [6.2, 6.9, "lake on Earth"],
    [7.0, 7.9, "by surface area"], [8.1, 8.8, "It stretches for about"], [9.0, 10.3, "750 miles"],
    [10.4, 11.3, "and five countries"], [11.3, 12.4, "share its shores"], [12.5, 13.5, "Powerful storms here"],
    [13.6, 14.0, "bring waves"], [14.2, 14.7, "as tall as"], [14.8, 16.0, "a three-story building"],
    [16.1, 16.8, "and in places"], [16.9, 17.2, "it's over"], [17.4, 18.1, "three thousand"], [18.3, 18.9, "feet deep"],
    [19.1, 19.4, "which is enough"], [19.4, 19.7, "to cover the world's"], [19.7, 21.0, "tallest building"],
    [21.0, 22.6, "And here's what makes"], [22.7, 23.4, "it even more confusing"], [23.5, 24.1, "the water here"],
    [24.2, 25.1, "is salty"], [25.1, 25.8, "It seems to have"], [25.9, 26.4, "everything a sea"],
    [26.5, 27.0, "should have"], [27.1, 27.5, "except"], [27.6, 28.2, "one thing"], [28.3, 29.2, "a natural connection"],
    [29.4, 30.2, "to the ocean"], [30.3, 30.7, "If you look"], [30.8, 31.4, "at the Black Sea"],
    [31.5, 32.3, "it might seem cut off"], [32.4, 32.8, "too"], [32.8, 33.3, "But through"], [33.4, 33.9, "the Bosporus"],
    [34.0, 34.9, "and a series of seas"], [35.0, 35.7, "and straits"], [35.9, 36.2, "it reaches"],
    [36.3, 37.0, "the Mediterranean"], [37.1, 37.8, "then passes through"], [37.9, 38.8, "the Strait of Gibraltar"],
    [38.9, 40.2, "into the Atlantic Ocean"], [40.4, 40.8, "The Caspian"], [41.0, 41.4, "however"],
    [41.5, 42.3, "is completely"], [42.4, 43.1, "landlocked"], [43.2, 43.6, "which is why"],
    [43.7, 44.4, "it's geographically"], [44.6, 45.1, "classified"], [45.2, 46.0, "as a lake"], [46.1, 46.4, "But then"],
    [46.6, 47.4, "why is the water here"], [47.5, 48.3, "salty"], [48.4, 48.7, "You see"], [48.8, 49.3, "the Caspian"],
    [49.4, 49.9, "was once part"], [50.1, 51.0, "of a much larger body"], [51.1, 51.5, "of water"], [51.6, 51.8, "called"],
    [51.9, 52.8, "the Paratethys"], [53.0, 53.9, "Over millions of years"], [54.0, 54.8, "the land rose"],
    [55.0, 55.9, "water levels changed"], [56.1, 56.7, "and this huge"], [56.8, 57.4, "body of water"],
    [57.5, 57.8, "split"], [57.9, 59.0, "into separate basins"], [59.1, 60.1, "Some of the Caspian's salt"],
    [60.2, 60.7, "comes from that"], [60.9, 61.5, "ancient sea"], [61.6, 62.1, "while rivers"],
    [62.2, 63.4, "continue to bring in more"], [63.6, 64.4, "And to ancient people"], [64.7, 65.2, "this huge body"],
    [65.4, 66.1, "of salty water"], [66.2, 66.7, "really seemed"], [66.8, 67.6, "like a sea"], [67.7, 68.4, "The name stuck"],
    [68.5, 68.9, "which is why"], [69.0, 69.4, "we still call it"], [69.5, 70.2, "a sea today"], [70.3, 70.6, "even though"],
    [70.7, 71.5, "geographically"], [71.6, 72.7, "it's a giant lake"],
]
CAPTION_LOOK = {"font": "rounded bold sans (Roboto-like), white, soft dark drop shadow, no box",
                "cap_height_px": 34, "centre_y_px": 1366, "x": "centred", "chunk": "1-5 words, hard switch, no fade"}

TEXTS = [  # on-screen texts other than captions: wording, look, normalised position (x, y of the centre), timing
    {"t0": 0.8, "t1": 5.4, "text": "Caspian Sea", "look": "white bold sans, blue outer glow, inside the lake",
     "pos": [0.45, 0.49], "anim": "scale-in pop", "shot": 1},
    {"t0": 2.6, "t1": 3.4, "text": "Sea (struck through)", "look": "red strike line drawn across 'Sea'", "pos": [0.55, 0.49], "shot": 1},
    {"t0": 3.5, "t1": 5.4, "text": "Caspian Lake", "look": "'Lake' in yellow bold", "pos": [0.45, 0.49], "shot": 1},
    {"t0": 6.0, "t1": 8.4, "text": "LARGEST lake", "look": "yellow heavy caps 'LARGEST' + white 'lake', glow", "pos": [0.42, 0.29], "shot": 1},
    {"t0": 7.0, "t1": 8.4, "text": "by surface area", "look": "white small bold", "pos": [0.48, 0.33], "shot": 1},
    {"t0": 8.4, "t1": 8.9, "text": "LARGEST lake by surface", "look": "same, flies toward the camera (fly-through)", "pos": [0.5, 0.3], "shot": 1},
    {"t0": 9.0, "t1": 10.4, "text": "750 miles (1,200 km)", "look": "white bold rotated along a double arrow N-S", "pos": [0.47, 0.5], "shot": 1},
    {"t0": 10.5, "t1": 13.3, "text": "Russia / Kazakhstan / Turkmenistan / Iran / Azerbaijan", "look": "white bold country names with shadow",
     "pos": {"Russia": [0.2, 0.44], "Kazakhstan": [0.6, 0.27], "Turkmenistan": [0.7, 0.5], "Iran": [0.65, 0.62], "Azerbaijan": [0.35, 0.53]}, "shot": 1},
    {"t0": 14.6, "t1": 16.4, "text": "32 ft", "look": "white small, beside the building prop", "pos": [0.8, 0.52], "shot": 1},
    {"t0": 16.9, "t1": 22.6, "text": "counter 122 ft (37 m) -> 3,363 ft (1,025 m)", "look": "yellow bold ft + white (m), counts up 16.9-18.6",
     "pos": [0.62, 0.38], "shot": 2},
    {"t0": 19.3, "t1": 22.6, "text": "Burj Khalifa 2,717 ft", "look": "white bold + yellow figure", "pos": [0.18, 0.37], "shot": 2},
    {"t0": 24.0, "t1": 25.4, "text": "Salty...", "look": "white heavy, written letter by letter in the lake", "pos": [0.35, 0.47], "shot": 3},
    {"t0": 30.8, "t1": 32.8, "text": "Black Sea", "look": "white bold inside a glowing blue Black Sea, yellow ellipse ring", "pos": [0.5, 0.48], "shot": 3},
    {"t0": 33.0, "t1": 35.6, "text": "Black Sea (big)", "look": "huge cyan-white bold in the sea, flies past", "pos": [0.8, 0.22], "shot": 3},
    {"t0": 33.5, "t1": 35.6, "text": "Bosphorus", "look": "small white bold label", "pos": [0.6, 0.24], "shot": 3},
    {"t0": 35.5, "t1": 37.0, "text": "Mediterranean Sea", "look": "huge cyan bold, flies past at the bottom", "pos": [0.3, 0.93], "shot": 3},
    {"t0": 38.0, "t1": 40.8, "text": "Atlantic Ocean", "look": "cyan-white bold label, two lines", "pos": [0.2, 0.32], "shot": 3},
    {"t0": 45.0, "t1": 46.6, "text": "LAKE", "look": "yellow bold caps, typed on letter by letter", "pos": [0.5, 0.38], "shot": 4},
    {"t0": 51.2, "t1": 53.4, "text": "Paratethys", "look": "white italic serif-ish with a curved leader", "pos": [0.35, 0.35], "shot": 5},
    {"t0": 68.6, "t1": 69.0, "text": "Caspian Sea (giant)", "look": "huge white/blue glow title flying out past the camera", "pos": [0.5, 0.58], "shot": 5},
    {"t0": 69.0, "t1": 72.79, "text": "Caspian Sea", "look": "white bold blue glow over the lake", "pos": [0.5, 0.48], "shot": 5},
    {"t0": 71.4, "t1": 72.79, "text": "LAKE", "look": "yellow bold caps with a small arrow down to the lake", "pos": [0.55, 0.33], "shot": 5},
]

ELEMENTS = [  # props / icons / graphics (not text); position normalised
    {"t0": 0.7, "t1": 9.0, "what": "yellow glow outline around the Caspian", "pos": [0.45, 0.47], "shot": 1},
    {"t0": 6.0, "t1": 8.4, "what": "yellow light burst on the Caspian at world scale", "pos": [0.45, 0.4], "shot": 1},
    {"t0": 9.0, "t1": 10.4, "what": "white double-headed arrow N-S along the lake", "pos": [0.47, 0.5], "shot": 1},
    {"t0": 14.0, "t1": 16.4, "what": "cartoon breaking wave (blue/white) on the sea", "pos": [0.42, 0.55], "shot": 1},
    {"t0": 14.4, "t1": 16.4, "what": "red three-story building icon for scale", "pos": [0.72, 0.5], "shot": 1},
    {"t0": 16.5, "t1": 22.7, "what": "cross-section: sky, water layers, brown seabed; vertical depth line", "pos": [0.5, 0.6], "shot": 2},
    {"t0": 19.3, "t1": 22.7, "what": "white Burj Khalifa silhouette rising from the seabed", "pos": [0.33, 0.55], "shot": 2},
    {"t0": 23.3, "t1": 25.4, "what": "salt shaker pouring onto the lake", "pos": [0.62, 0.2], "shot": 3},
    {"t0": 26.0, "t1": 28.6, "what": "4 round icons: turtle/fish, waves, tap, salt (what a sea has)", "pos": [0.55, 0.38], "shot": 3},
    {"t0": 28.4, "t1": 30.2, "what": "red X marks + dashed white arrows to the oceans, red tint on Eurasia", "pos": [0.5, 0.45], "shot": 3},
    {"t0": 30.8, "t1": 32.6, "what": "yellow ellipse ring around the Black Sea", "pos": [0.5, 0.47], "shot": 3},
    {"t0": 34.0, "t1": 40.8, "what": "glowing cyan route line Black Sea -> Bosporus -> Mediterranean -> Gibraltar", "pos": [0.5, 0.5], "shot": 3},
    {"t0": 41.3, "t1": 43.6, "what": "golden padlock (open at 42, closed after)", "pos": [0.5, 0.47], "shot": "3-4"},
    {"t0": 42.43, "t1": 43.6, "what": "full-frame red grade, Caspian outlined in glowing red", "pos": [0.5, 0.5], "shot": 4},
    {"t0": 43.6, "t1": 46.6, "what": "Caspian with red glow outline on satellite", "pos": [0.5, 0.5], "shot": 4},
    {"t0": 46.8, "t1": 49.0, "what": "white question marks + salt badge icon over the lake", "pos": [0.45, 0.45], "shot": 4},
    {"t0": 51.3, "t1": 56.0, "what": "Paratethys extent in light blue, shrinking (water levels change)", "pos": [0.45, 0.4], "shot": 5},
    {"t0": 59.8, "t1": 64.0, "what": "white salt crystal cubes inside the lake", "pos": [0.5, 0.45], "shot": 5},
    {"t0": 61.6, "t1": 64.0, "what": "blue river lines (Volga, Ural ...) carrying salt cubes into the lake", "pos": [0.5, 0.3], "shot": 5},
    {"t0": 65.0, "t1": 68.4, "what": "ancient people with spears and boats on the west shore", "pos": [0.6, 0.4], "shot": 5},
]

REGION_FILLS = [  # what is filled, colour sampled at (t, x, y) from the reference frame (median of 15x15 px)
    {"t": 3.0, "name": "Caspian Sea", "fill": "bright blue, opaque-ish, yellow glow edge", "at": [0.45, 0.56], "shot": 1},
    {"t": 12.0, "name": "Russia", "fill": "flat red, opaque", "at": [0.1, 0.2], "shot": 1},
    {"t": 12.0, "name": "Kazakhstan", "fill": "flat brown", "at": [0.75, 0.3], "shot": 1},
    {"t": 12.0, "name": "Turkmenistan", "fill": "flat orange", "at": [0.72, 0.47], "shot": 1},
    {"t": 12.0, "name": "Iran", "fill": "flat green", "at": [0.62, 0.66], "shot": 1},
    {"t": 12.0, "name": "Azerbaijan", "fill": "flat green/teal", "at": [0.38, 0.52], "shot": 1},
    {"t": 24.0, "name": "Caspian Sea", "fill": "light blue semi-transparent", "at": [0.42, 0.55], "shot": 3},
    {"t": 29.0, "name": "Eurasia (no outlet)", "fill": "red semi-transparent wash", "at": [0.5, 0.42], "shot": 3},
    {"t": 31.0, "name": "Black Sea", "fill": "bright blue glow", "at": [0.5, 0.48], "shot": 3},
    {"t": 36.5, "name": "Mediterranean Sea", "fill": "bright cyan-blue glow", "at": [0.45, 0.5], "shot": 3},
    {"t": 42.8, "name": "whole frame", "fill": "red grade", "at": [0.2, 0.2], "shot": 4},
    {"t": 52.0, "name": "Paratethys", "fill": "pale blue semi-transparent", "at": [0.4, 0.43], "shot": 5},
    {"t": 61.0, "name": "Caspian Sea", "fill": "blue", "at": [0.5, 0.5], "shot": 5},
]


def sample(cap, t, x, y):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * FPS)))
    ok, fr = cap.read()
    if not ok:
        return None
    h, w = fr.shape[:2]
    px, py = int(x * w), int(y * h)
    p = fr[max(0, py - 7):py + 8, max(0, px - 7):px + 8].reshape(-1, 3)
    b, g, r = np.median(p, axis=0)
    return "#%02x%02x%02x" % (int(r), int(g), int(b))


def shot_of(f):
    for k in range(len(CUTS) - 1):
        if CUTS[k] <= f < CUTS[k + 1]:
            return k
    return len(CUTS) - 2


def camera_track(rows):
    """Chain per-frame velocities into scale and centre (fraction of the shot's first frame) per shot."""
    out = []
    for k in range(len(CUTS) - 1):
        s, cx, cy = 1.0, 0.5, 0.5
        for f in range(CUTS[k], CUTS[k + 1]):
            r = rows[f]
            if f > CUTS[k] and "zoom_ln_s" in r and r.get("inliers", 0) >= 40:
                s *= math.exp(r["zoom_ln_s"] / FPS)
                cx += r["pan_x_w_s"] / FPS / s
                cy += r["pan_y_w_s"] / FPS / s * (1080 / 1920) * (1920 / 1080)
            out.append((k, s, cx, cy))
    return out


def window(rows, f0, f1, key):
    v = [rows[f][key] for f in range(f0, f1) if key in rows[f] and rows[f].get("inliers", 0) >= 40]
    return round(float(np.mean(v)), 4) if v else None


def summarise(rows, track, label):
    samples = []
    times = sorted(set([i * 0.5 for i in range(int(len(rows) / FPS / 0.5) + 1)] + [c / FPS for c in CUTS[1:-1]]))
    for t in times:
        f = min(len(rows) - 1, int(round(t * FPS)))
        k, s, cx, cy = track[f]
        f0, f1 = max(CUTS[k], f - 7), min(CUTS[k + 1], f + 8)
        samples.append({"t": round(t, 3), "f": f, "shot": k + 1, "scale": round(s, 3), "centre": [round(cx, 3), round(cy, 3)],
                        "zoom_ln_s": window(rows, f0, f1, "zoom_ln_s"), "pan_x_w_s": window(rows, f0, f1, "pan_x_w_s"),
                        "pan_y_w_s": window(rows, f0, f1, "pan_y_w_s"), "rot_deg_s": window(rows, f0, f1, "rot_deg_s"),
                        "tilt_v_s": window(rows, f0, f1, "tilt_v_s"), "parallax_px_s": window(rows, f0, f1, "parallax_px_s"),
                        "sharpness": rows[f]["blur"]})
    shots = []
    for k in range(len(CUTS) - 1):
        fr = [rows[f] for f in range(CUTS[k] + 1, CUTS[k + 1]) if "zoom_ln_s" in rows[f] and rows[f].get("inliers", 0) >= 200]
        z = np.array([r["zoom_ln_s"] for r in fr]); px = np.array([r["pan_x_w_s"] for r in fr]); py = np.array([r["pan_y_w_s"] for r in fr])
        tv = np.array([r["tilt_v_s"] for r in fr]); th = np.array([r["tilt_h_s"] for r in fr]); ro = np.array([r["rot_deg_s"] for r in fr])
        pa = np.array([r.get("parallax_px_s", 0) for r in fr])
        speed = np.abs(z) + np.hypot(px, py)
        still = int((speed < 0.01).sum())
        shots.append({"shot": k + 1, "f0": CUTS[k], "f1": CUTS[k + 1], "t0": round(CUTS[k] / FPS, 3), "t1": round(CUTS[k + 1] / FPS, 3),
                      "dur_s": round((CUTS[k + 1] - CUTS[k]) / FPS, 3),
                      "scale_end": round(track[CUTS[k + 1] - 1][1], 3), "centre_end": [round(track[CUTS[k + 1] - 1][2], 3), round(track[CUTS[k + 1] - 1][3], 3)],
                      "zoom_ln_s": {"mean": round(float(z.mean()), 3), "p05": round(float(np.percentile(z, 5)), 3), "p95": round(float(np.percentile(z, 95)), 3)},
                      "pan_w_s_median": round(float(np.median(np.hypot(px, py))), 4),
                      "rotation_deg_s_median": round(float(np.median(np.abs(ro))), 3),
                      "rotation_deg_s_p95": round(float(np.percentile(np.abs(ro), 95)), 3),
                      "tilt_v_s_median": round(float(np.median(np.abs(tv))), 4),
                      "tilt_v_s_p95": round(float(np.percentile(np.abs(tv), 95)), 4),
                      "tilt_h_s_p95": round(float(np.percentile(np.abs(th), 95)), 4),
                      "parallax_px_s_median": round(float(np.median(pa)), 1),
                      "frames_still": still, "frames_measured": len(fr)})
    return {"label": label, "shots": shots, "samples": samples}


def main():
    track = camera_track(FR)
    cap = cv2.VideoCapture(REF)
    fills = [{**r, "colour": sample(cap, r["t"], *r["at"])} for r in REGION_FILLS]
    data = {"video": {"path": REF, "fps": FPS, "frames": len(FR), "size": [1080, 1920]},
            "method": __doc__.strip().splitlines()[2:8],
            "cuts_f": CUTS, "transitions": TRANSITIONS,
            "captions": [{"t0": a, "t1": b, "text": c} for a, b, c in CAPTIONS], "caption_look": CAPTION_LOOK,
            "texts": TEXTS, "elements": ELEMENTS, "region_fills": fills,
            "camera": summarise(FR, track, "reference")}
    (HERE / "reference_frames.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    L = ["# Reference frames: xGmyqt_Ild0.mp4 (72.79 s, 30 fps, 2182 frames)", "",
         "Data: `reference_frames.json` (built by `tools/analyze_reference.py` + `tools/build_reference_frames.py`).", "",
         "## Shots and camera (measured every frame)", "",
         "| shot | t0-t1 s | scale at end | zoom ln/s mean (p05..p95) | pan w/s median | rot deg/s median (p95) | tilt/s median, p95 (v), p95 (h) | parallax px/s | still frames |",
         "|---|---|---|---|---|---|---|---|---|"]
    for s in data["camera"]["shots"]:
        L.append(f"| {s['shot']} | {s['t0']}-{s['t1']} | {s['scale_end']} | {s['zoom_ln_s']['mean']} ({s['zoom_ln_s']['p05']}..{s['zoom_ln_s']['p95']}) | "
                 f"{s['pan_w_s_median']} | {s['rotation_deg_s_median']} ({s['rotation_deg_s_p95']}) | {s['tilt_v_s_median']}, {s['tilt_v_s_p95']}, {s['tilt_h_s_p95']} | {s['parallax_px_s_median']} | {s['frames_still']}/{s['frames_measured']} |")
    L += ["", "## Transitions", ""] + [f"- {x['t']} s: {x['kind']} ({x['measured']})" for x in TRANSITIONS]
    L += ["", "## Region fills (sampled colour)", ""] + [f"- {r['t']} s {r['name']}: {r['fill']} `{r['colour']}`" for r in fills]
    L += ["", "## Texts (non-caption)", ""] + [f"- {x['t0']}-{x['t1']} s: \"{x['text']}\" ({x['look']})" for x in TEXTS]
    L += ["", "## Elements", ""] + [f"- {x['t0']}-{x['t1']} s: {x['what']}" for x in ELEMENTS]
    L += ["", f"## Captions ({len(CAPTIONS)} chunks; {CAPTION_LOOK['font']}, centre y {CAPTION_LOOK['centre_y_px']} px)", ""]
    L += [f"- {a}-{b}: {c}" for a, b, c in CAPTIONS]
    (HERE / "reference_frames.md").write_text("\n".join(L) + "\n")
    print("written")


if __name__ == "__main__":
    main()
