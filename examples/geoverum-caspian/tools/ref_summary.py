"""Summarise out/ref/motion.json into 0.5 s windows and compute audio onsets (spectral flux, >4 kHz band)."""
import json, subprocess, numpy as np
rows = json.load(open("out/ref/motion.json"))
win = {}
for r in rows:
    k = int(r["t"] // 0.5)
    w = win.setdefault(k, {"z": 0.0, "x": 0.0, "y": 0.0, "n": 0, "inl": []})
    if r["inliers"] >= 15:
        w["z"] += r["zoom_ln_s"] / 10; w["x"] += r["pan_x_w_s"] / 10; w["y"] += r["pan_y_w_s"] / 10
    w["n"] += 1; w["inl"].append(r["inliers"])
out = []
cum = 0.0
for k in sorted(win):
    w = win[k]; cum += w["z"]
    out.append({"t0": k*0.5, "dz_ln": round(w["z"], 3), "cum_ln": round(cum, 2), "dx_w": round(w["x"], 3),
                "dy_w": round(w["y"], 3), "min_inliers": min(w["inl"])})
json.dump(out, open("out/ref/motion_05s.json", "w"), indent=0)
# audio
sr = 22050
pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "/workspace/style-lab/geoverum/xGmyqt_Ild0.mp4", "-ac", "1",
                      "-ar", str(sr), "-f", "f32le", "-"], capture_output=True).stdout
a = np.frombuffer(pcm, np.float32)
hop, n = 256, 1024
fr = np.lib.stride_tricks.sliding_window_view(a, n)[::hop] * np.hanning(n)
S = np.abs(np.fft.rfft(fr, axis=1))
freqs = np.fft.rfftfreq(n, 1/sr)
hi = S[:, freqs > 4000]
flux = np.maximum(np.diff(np.log1p(hi*10), axis=0), 0).sum(1)
t = (np.arange(len(flux)) + 1) * hop / sr
thr = np.median(flux) + 3.5*np.std(flux)
peaks = [i for i in range(1, len(flux)-1) if flux[i] > thr and flux[i] >= flux[i-1] and flux[i] >= flux[i+1]]
on = []
for i in peaks:
    if not on or t[i] - on[-1][0] > 0.15:
        on.append((round(float(t[i]), 2), round(float(flux[i]/thr), 2)))
rms = np.sqrt((a[: len(a)//(sr//10)*(sr//10)].reshape(-1, sr//10)**2).mean(1))
db = 20*np.log10(rms + 1e-9)
json.dump({"hf_onsets_t_strength": on, "rms_db_per_100ms": [round(float(x), 1) for x in db]}, open("out/ref/audio.json", "w"))
print(len(on), "onsets")
for o in out: print(o)
