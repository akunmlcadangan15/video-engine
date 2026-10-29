"""Camera motion of the reference at 10 fps: ORB + RANSAC similarity between consecutive frames.
Writes out/ref/motion.json: per step t, ln scale/s, pan x/y in frame widths/s (map moving under the camera inverted)."""
import json, subprocess, sys, math
import numpy as np, cv2
REF = sys.argv[1]; OUT = sys.argv[2]
W, H, FPS = 270, 480, 10
p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", REF, "-vf", f"fps={FPS},scale={W}:{H},format=gray",
                      "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
orb = cv2.ORB_create(1500); bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
prev = None; rows = []; i = 0
mask = np.full((H, W), 255, np.uint8); mask[int(H*0.62):int(H*0.75), :] = 0   # caption band
while True:
    b = p.stdout.read(W*H)
    if len(b) < W*H: break
    f = np.frombuffer(b, np.uint8).reshape(H, W)
    kp, de = orb.detectAndCompute(f, mask)
    if prev is not None and de is not None and prev[1] is not None and len(kp) > 20:
        m = bf.match(prev[1], de)
        if len(m) >= 12:
            a = np.float32([prev[0][x.queryIdx].pt for x in m]); c = np.float32([kp[x.trainIdx].pt for x in m])
            M, inl = cv2.estimateAffinePartial2D(a, c, method=cv2.RANSAC, ransacReprojThreshold=2.0)
            if M is not None:
                s = math.hypot(M[0, 0], M[1, 0]); rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))
                # centre displacement of the map content
                cx, cy = W/2, H/2
                nx = M[0, 0]*cx + M[0, 1]*cy + M[0, 2]; ny = M[1, 0]*cx + M[1, 1]*cy + M[1, 2]
                rows.append({"t": round(i/FPS, 2), "zoom_ln_s": round(math.log(s)*FPS, 3),
                             "pan_x_w_s": round(-(nx-cx)/W*FPS, 3), "pan_y_w_s": round(-(ny-cy)/W*FPS, 3),
                             "rot_deg": round(rot, 2), "inliers": int(inl.sum()), "matches": len(m)})
    prev = (kp, de); i += 1
json.dump(rows, open(OUT, "w"))
print(len(rows))
