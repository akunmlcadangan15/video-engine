"""Split camera speed into moves (peak bounded by minima) and fit each move's progress curve to standard eases.
Needs scipy (not an engine dependency).
Usage: python -I moves.py motion.json [t0 t1]"""
import json
import math
import sys

import numpy as np

d = json.load(open(sys.argv[1]))
fps, W, H, rows = d["fps"], d["W"], d["H"], d["rows"]
t0, t1 = (float(sys.argv[2]), float(sys.argv[3])) if len(sys.argv) > 3 else (0, 1e9)
n = len(rows)
zk = math.hypot(W, H) / 2 / W
ok = np.array([r["ok"] and r["hist"] < 0.25 for r in rows])
z = np.array([math.log(r["s"]) * fps if r["ok"] else 0 for r in rows])
px = np.array([r["dx"] * fps if r["ok"] else 0 for r in rows]); py = np.array([r["dy"] * fps if r["ok"] else 0 for r in rows])
sp = np.hypot(px, py) + np.abs(z) * zk
sp[~ok] = 0
# light smoothing (3 frames) for segmentation only
ss = np.convolve(sp, np.ones(3) / 3, mode="same")

EASES = {
    "linear": lambda t: t,
    "sine.inOut": lambda t: (1 - np.cos(np.pi * t)) / 2,
    "power2.inOut": lambda t: np.where(t < .5, 2 * t * t, 1 - (-2 * t + 2) ** 2 / 2),
    "power3.inOut": lambda t: np.where(t < .5, 4 * t ** 3, 1 - (-2 * t + 2) ** 3 / 2),
    "power4.inOut": lambda t: np.where(t < .5, 8 * t ** 4, 1 - (-2 * t + 2) ** 4 / 2),
    "power2.out": lambda t: 1 - (1 - t) ** 2,
    "power3.out": lambda t: 1 - (1 - t) ** 3,
    "power4.out": lambda t: 1 - (1 - t) ** 4,
    "expo.out": lambda t: 1 - 2 ** (-10 * t),
    "power2.in": lambda t: t ** 2,
    "engine glide": lambda t: 0.35 * t + 0.65 * (1 - np.cos(np.pi * t)) / 2,
}

# moves: prominent peaks of the 9-frame smoothed speed; a move spans from the minimum before its peak to the minimum after
from scipy.signal import find_peaks
sm = np.convolve(sp, np.hanning(9) / np.hanning(9).sum(), mode="same")
pk, pr = find_peaks(sm, prominence=0.06, height=0.08)
moves = []
for j, p in enumerate(pk):
    if not (t0 * fps <= p <= t1 * fps):
        continue
    lo = pk[j - 1] if j else 0
    hi = pk[j + 1] if j + 1 < len(pk) else n - 1
    a = lo + int(np.argmin(sm[lo:p + 1])); b = p + int(np.argmin(sm[p:hi + 1]))
    while a < p and not ok[a]:
        a += 1
    moves.append((a, p, b))

print("move | t start-peak-end | dur s | rise/fall s | peak fw/s | min/peak at ends | zoom x | pan fw (dir) | best ease (rmse) | 2nd")
for a, p, b in moves:
    if b - a < 6:
        continue
    p = a + int(np.argmax(sm[a:b + 1]))
    seg = sp[a:b + 1]
    prog = np.cumsum(seg); prog = (prog - prog[0]) / max(prog[-1] - prog[0], 1e-9)
    tt = np.linspace(0, 1, len(prog))
    fits = sorted((float(np.sqrt(np.mean((f(tt) - prog) ** 2))), k) for k, f in EASES.items())
    zx = math.exp(np.sum(z[a:b + 1]) / fps)
    cx, cy = -np.sum(px[a:b + 1]) / fps, -np.sum(py[a:b + 1]) / fps
    ang = math.degrees(math.atan2(-cy, cx))
    dname = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"][int(((ang + 22.5) % 360) // 45)] if math.hypot(cx, cy) > 0.03 else "-"
    print(f"{a / fps:5.2f}-{p / fps:5.2f}-{b / fps:5.2f} | {(b - a) / fps:4.2f} | {(p - a) / fps:4.2f}/{(b - p) / fps:4.2f} | "
          f"{sp[p]:5.2f} | {seg[0] / sp[p]:.2f}/{seg[-1] / sp[p]:.2f} | {zx:5.2f} | {math.hypot(cx, cy):4.2f} ({dname}) | "
          f"{fits[0][1]} ({fits[0][0]:.3f}) | {fits[1][1]} ({fits[1][0]:.3f})")
