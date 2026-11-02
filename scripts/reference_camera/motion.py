"""Per-frame global camera motion of a reference video: similarity (zoom, rotation, pan), homography perspective and cut
scores. Needs opencv-python-headless (not an engine dependency). Usage: python -I motion.py VIDEO OUT.json"""
import json
import subprocess
import sys

import cv2
import numpy as np

video, out = sys.argv[1], sys.argv[2]
W, H = 304, 540
raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"scale={W}:{H}:flags=area,format=gray",
                      "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W)
fps = 30.0
mask = np.full((H, W), 255, np.uint8)
mask[int(0.66 * H):int(0.76 * H), :] = 0          # burned-in caption band (static text biases toward zero motion)

rows = []
for i in range(len(fr) - 1):
    a, b = fr[i], fr[i + 1]
    diff = float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())
    ha = cv2.calcHist([a], [0], None, [32], [0, 256]); hb = cv2.calcHist([b], [0], None, [32], [0, 256])
    hist = float(cv2.compareHist(ha, hb, cv2.HISTCMP_BHATTACHARYYA))
    row = {"f": i, "t": i / fps, "diff": diff, "hist": hist, "ok": False}
    p0 = cv2.goodFeaturesToTrack(a, maxCorners=600, qualityLevel=0.005, minDistance=6, mask=mask)
    if p0 is not None and len(p0) >= 12:
        p1, st, _ = cv2.calcOpticalFlowPyrLK(a, b, p0, None, winSize=(21, 21), maxLevel=3)
        g = st.ravel() == 1
        if g.sum() >= 12:
            A, inl = cv2.estimateAffinePartial2D(p0[g], p1[g], method=cv2.RANSAC, ransacReprojThreshold=1.0)
            if A is not None:
                s = float(np.hypot(A[0, 0], A[1, 0]))
                rot = float(np.degrees(np.arctan2(A[1, 0], A[0, 0])))
                # displacement of the frame centre under the transform, in frame widths
                c = np.array([W / 2, H / 2])
                dc = A[:, :2] @ c + A[:, 2] - c
                Hm, hin = cv2.findHomography(p0[g], p1[g], cv2.RANSAC, 1.0)
                persp = float(np.hypot(Hm[2, 0], Hm[2, 1]) * W) if Hm is not None else 0.0
                row.update(ok=True, n=int(g.sum()), inl=float(inl.mean()), s=s, rot=rot,
                           dx=float(dc[0] / W), dy=float(dc[1] / W), persp=persp)
    rows.append(row)
json.dump({"fps": fps, "W": W, "H": H, "n": len(fr), "rows": rows}, open(out, "w"))
print(len(fr), "frames")
