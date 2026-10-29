#!/usr/bin/env python3
"""Frame-by-frame camera analysis of the reference video at its native fps (30 fps, every frame).

For each consecutive frame pair: ORB features + RANSAC homography (full projective) on a 540x960 grayscale copy.
Derived per frame: zoom velocity (ln scale / s), pan velocity (frame widths / s, in screen space), rotation (deg/s),
perspective terms (tilt cue: how differently the top and bottom of the frame scale), inlier counts, and a
parallax cue (the median residual motion of the matches that disagree with the dominant homography, i.e. a second
layer moving differently: text, props or a second map plane). Cuts = frames whose pair has too few inliers and a large
histogram change. Per shot the homographies are chained to give the camera scale and centre relative to the shot's
first frame. Output: out/ref/frames_camera.json (per frame) - summarised into reference_frames.json by
tools/build_reference_frames.py.

usage: python3 tools/analyze_reference.py REF.mp4
"""
import json, math, sys
from pathlib import Path
import cv2
import numpy as np

W, H = 540, 960
ref = sys.argv[1]
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "out" / "ref" / "frames_camera.json"
cap = cv2.VideoCapture(ref)
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
orb = cv2.ORB_create(2000, fastThreshold=10)
bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
prev = None
rows = []
i = 0


def hist(g):
    h = cv2.calcHist([g], [0], None, [32], [0, 256]).ravel()
    return h / max(1, h.sum())


def local_scale(Hm, x, y):
    """Area scale of the homography at (x, y): sqrt|det J|."""
    a, b, c, d, e, f, g, h, _ = (Hm / Hm[2, 2]).ravel()
    w = g * x + h * y + 1
    u, v = (a * x + b * y + c) / w, (d * x + e * y + f) / w
    J = np.array([[a - g * u, b - h * u], [d - g * v, e - h * v]]) / w
    return math.sqrt(abs(np.linalg.det(J)))


while True:
    ok, fr = cap.read()
    if not ok:
        break
    small = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    kp, des = orb.detectAndCompute(g, None)
    row = {"f": i, "t": round(i / fps, 4), "sat": round(float(hsv[..., 1].mean()) / 255, 3),
           "blur": round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1)}
    if prev is not None:
        pg, pkp, pdes = prev
        row["hist_d"] = round(float(np.abs(hist(g) - hist(pg)).sum()), 3)
        m = bf.match(pdes, des) if pdes is not None and des is not None and len(pdes) > 8 and len(des) > 8 else []
        row["matches"] = len(m)
        if len(m) >= 12:
            p0 = np.float32([pkp[x.queryIdx].pt for x in m])
            p1 = np.float32([kp[x.trainIdx].pt for x in m])
            Hm, inl = cv2.findHomography(p0, p1, cv2.RANSAC, 2.0)
            if Hm is not None:
                inl = inl.ravel().astype(bool)
                row["inliers"] = int(inl.sum())
                Hn = Hm / Hm[2, 2]
                s = local_scale(Hn, W / 2, H / 2)
                c = Hn @ np.array([W / 2, H / 2, 1.0]); c = c[:2] / c[2]
                row["zoom_ln_s"] = round(math.log(max(s, 1e-6)) * fps, 4)
                # screen motion of the centre point (map moves -> camera pans the opposite way)
                row["pan_x_w_s"] = round(float(-(c[0] - W / 2) / W * fps), 4)
                row["pan_y_w_s"] = round(float(-(c[1] - H / 2) / W * fps), 4)
                row["rot_deg_s"] = round(math.degrees(math.atan2(Hn[1, 0], Hn[0, 0])) * fps, 3)
                st, sb = local_scale(Hn, W / 2, H * 0.15), local_scale(Hn, W / 2, H * 0.85)
                sl, sr = local_scale(Hn, W * 0.15, H / 2), local_scale(Hn, W * 0.85, H / 2)
                row["tilt_v_s"] = round(math.log(st / sb) * fps, 4)    # >0: top grows faster than bottom
                row["tilt_h_s"] = round(math.log(sl / sr) * fps, 4)
                row["persp"] = [round(float(Hn[2, 0]) * W, 6), round(float(Hn[2, 1]) * H, 6)]
                out_idx = ~inl
                if out_idx.sum() >= 8:
                    d = p1[out_idx] - p0[out_idx]
                    proj = cv2.perspectiveTransform(p0[out_idx].reshape(-1, 1, 2), Hm).reshape(-1, 2)
                    res = p1[out_idx] - proj
                    row["layer2_px_s"] = [round(float(np.median(d[:, 0])) * fps / W * 1080, 1),
                                          round(float(np.median(d[:, 1])) * fps / W * 1080, 1)]
                    row["layer2_n"] = int(out_idx.sum())
                    row["parallax_px_s"] = round(float(np.median(np.hypot(res[:, 0], res[:, 1]))) * fps / W * 1080, 1)
    rows.append(row)
    prev = (g, kp, des)
    i += 1
    if i % 300 == 0:
        print(i, flush=True)
json.dump({"fps": fps, "size_analysed": [W, H], "frames": rows}, open(out, "w"))
print("wrote", out, len(rows))
