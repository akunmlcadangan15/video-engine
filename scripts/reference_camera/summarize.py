"""Shots and camera statistics from motion.json. Usage: python -I summarize.py motion.json [plot.png]"""
import json
import math
import sys

import numpy as np

d = json.load(open(sys.argv[1]))
fps, W, H, rows = d["fps"], d["W"], d["H"], d["rows"]
n = len(rows)
diff = np.array([r["diff"] for r in rows]); hist = np.array([r["hist"] for r in rows])
ok = np.array([r["ok"] for r in rows]); inl = np.array([r.get("inl", 0) for r in rows])
zk = math.hypot(W, H) / 2 / W   # zoom -> corner travel in frame widths
z = np.array([math.log(r["s"]) * fps if r["ok"] else np.nan for r in rows])       # ln scale / s (+ = zoom in)
px = np.array([r["dx"] * fps if r["ok"] else np.nan for r in rows])              # frame widths / s (content motion)
py = np.array([r["dy"] * fps if r["ok"] else np.nan for r in rows])
rot = np.array([r["rot"] * fps if r["ok"] else np.nan for r in rows])            # deg / s
persp = np.array([r.get("persp", 0) for r in rows])
pan = np.hypot(px, py)
speed = pan + np.abs(z) * zk

med = np.median(diff)
cut = (hist > 0.25) | ((diff > max(12, 6 * med)) & ((inl < 0.5) | ~ok))
cuts = [i + 1 for i in range(n) if cut[i] and (i == 0 or not cut[i - 1])]
bounds = [0] + cuts + [n + 1]
print(f"frames {n + 1}, cuts {len(cuts)} at s: {[round(c / fps, 2) for c in cuts]}")
print(f"median frame diff {med:.2f}")

still = (np.abs(z) < 0.05) & (pan < 0.02)
valid = ok & ~cut
print(f"moving share (P01 thresholds: |zoom| < 0.05 ln/s and pan < 0.02 fw/s = still): "
      f"{1 - still[valid].mean():.0%} of {valid.sum()} measured frames")
print(f"screen speed (fw/s) p50 {np.nanpercentile(speed[valid], 50):.3f} p90 {np.nanpercentile(speed[valid], 90):.3f} "
      f"max {np.nanmax(speed[valid]):.3f}; zoom ln/s p50 {np.nanpercentile(np.abs(z[valid]), 50):.3f} "
      f"p90 {np.nanpercentile(np.abs(z[valid]), 90):.3f}; pan fw/s p50 {np.nanpercentile(pan[valid], 50):.3f} "
      f"p90 {np.nanpercentile(pan[valid], 90):.3f}")
# longest still run
runs, r0 = [], None
for i in range(n):
    if valid[i] and still[i]:
        r0 = i if r0 is None else r0
    elif r0 is not None:
        runs.append((i - r0, r0)); r0 = None
runs.sort(reverse=True)
print("longest holds (s @ t):", [(round(a / fps, 2), round(b / fps, 2)) for a, b in runs[:5]])

print("\nshot | t0-t1 s | dur | frames ok | zoom total ln (x) | zoom ln/s mean/peak | pan total fw | pan fw/s mean/peak | "
      "dir | rot deg | persp | moving | peak at | start/end speed vs mean")
for k, (a, b) in enumerate(zip(bounds, bounds[1:])):
    idx = [i for i in range(a, min(b - 1, n)) if valid[i]]
    dur = (b - a) / fps
    if len(idx) < 3:
        print(f"{k:3d} | {a / fps:6.2f}-{(b - 1) / fps:6.2f} | {dur:4.1f} | {len(idx)} (unmeasured)")
        continue
    zi, pi, si = z[idx], pan[idx], speed[idx]
    ztot = np.nansum(zi) / fps
    ptot_x, ptot_y = np.nansum(px[idx]) / fps, np.nansum(py[idx]) / fps
    ang = math.degrees(math.atan2(-ptot_y, -ptot_x))   # camera direction = opposite of content motion
    dirs = {0: "E", 45: "NE", 90: "N", 135: "NW", 180: "W", -180: "W", -135: "SW", -90: "S", -45: "SE"}
    dname = dirs[min(dirs, key=lambda q: abs(q - ang))] if math.hypot(ptot_x, ptot_y) > 0.05 else "-"
    pk = int(np.nanargmax(si)); m = np.nanmean(si)
    q = max(1, len(si) // 8)
    se = (np.nanmean(si[:q]) / m if m > 1e-6 else 0, np.nanmean(si[-q:]) / m if m > 1e-6 else 0)
    print(f"{k:3d} | {a / fps:6.2f}-{(b - 1) / fps:6.2f} | {dur:4.1f} | {len(idx):3d}/{b - a - 1:3d} | "
          f"{ztot:+.2f} ({math.exp(ztot):.2f}x) | {np.nanmean(zi):+.3f}/{np.nanmax(np.abs(zi)):.3f} | "
          f"{math.hypot(ptot_x, ptot_y):.2f} | {np.nanmean(pi):.3f}/{np.nanmax(pi):.3f} | {dname:>2} | "
          f"{np.nansum(rot[idx]) / fps:+.1f} | {np.nanmax(persp[idx]):.3f} | {1 - still[idx].mean():.0%} | "
          f"{pk / max(1, len(si) - 1):.0%} | {se[0]:.2f}/{se[1]:.2f}")

if len(sys.argv) > 2:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t = np.arange(n) / fps
    fig, ax = plt.subplots(3, 1, figsize=(16, 9), sharex=True)
    ax[0].plot(t, z, lw=0.8); ax[0].set_ylabel("zoom ln/s (+in)"); ax[0].axhline(0, c="k", lw=0.5)
    ax[1].plot(t, px, lw=0.8, label="x"); ax[1].plot(t, py, lw=0.8, label="y"); ax[1].legend(loc="upper right")
    ax[1].set_ylabel("content pan fw/s"); ax[1].axhline(0, c="k", lw=0.5)
    ax[2].plot(t, speed, lw=0.8, c="m"); ax[2].set_ylabel("screen speed fw/s"); ax[2].set_xlabel("s")
    for a in ax:
        for c in cuts:
            a.axvline(c / fps, c="r", lw=0.6, alpha=0.6)
        a.grid(alpha=0.3)
    ax[0].set_ylim(-1.5, 1.5); ax[1].set_ylim(-1.5, 1.5); ax[2].set_ylim(0, 2)
    fig.tight_layout(); fig.savefig(sys.argv[2], dpi=80)
