#!/usr/bin/env python3
"""Per-shot diff of our render against reference_frames.json: camera (scale / centre / velocities / 3D cues / still
frames, both measured by tools/analyze_reference.py the same way), text timing (reference on-screen texts vs our
planned text entries) and caption timing. Writes out/compare/compare.json + compare.md.

usage: python3 tools/analyze_reference.py OURS.mp4 out/ours/frames_camera.json
       /workspace/engine-venv/bin/python tools/compare_to_reference.py REF.mp4
"""
import json, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))
import build_reference_frames as B          # noqa: E402  (reads out/ref/frames_camera.json + the annotations)

FPS = B.FPS
ours = json.load(open(HERE / "out/ours/frames_camera.json"))["frames"]
ref = B.FR
R = B.summarise(ref, B.camera_track(ref), "reference")
O = B.summarise(ours, B.camera_track(ours), "remake")


def speeds(rows, f0, f1):
    v = [abs(r["zoom_ln_s"]) + float(np.hypot(r["pan_x_w_s"], r["pan_y_w_s"]))
         for r in rows[f0 + 1:f1] if "zoom_ln_s" in r and r.get("inliers", 0) >= 40]
    return np.array(v)


shots = []
for k, (rs, os_) in enumerate(zip(R["shots"], O["shots"])):
    f0, f1 = B.CUTS[k], B.CUTS[k + 1]
    so = speeds(ours, f0, f1)
    sr = speeds(ref, f0, f1)
    shots.append({"shot": k + 1, "t0": rs["t0"], "t1": rs["t1"],
                  "scale_end": [rs["scale_end"], os_["scale_end"]],
                  "centre_end": [rs["centre_end"], os_["centre_end"]],
                  "zoom_ln_s_mean": [rs["zoom_ln_s"]["mean"], os_["zoom_ln_s"]["mean"]],
                  "pan_w_s_median": [rs["pan_w_s_median"], os_["pan_w_s_median"]],
                  "rot_deg_s_median": [rs["rotation_deg_s_median"], os_["rotation_deg_s_median"]],
                  "tilt_v_s_p95": [rs["tilt_v_s_p95"], os_["tilt_v_s_p95"]],
                  "parallax_px_s_median": [rs["parallax_px_s_median"], os_["parallax_px_s_median"]],
                  "speed_median": [round(float(np.median(sr)), 4) if len(sr) else None, round(float(np.median(so)), 4) if len(so) else None],
                  "frames_speed_lt_0.01": [int((sr < 0.01).sum()), int((so < 0.01).sum())],
                  "frames_speed_lt_0.002": [int((sr < 0.002).sum()), int((so < 0.002).sum())],
                  "frames_measured": [len(sr), len(so)]})

# our planned text entries (beats segments) from the layout plan
from video_engine import timeline, layout    # noqa: E402
tl = timeline.load(HERE / "master_timeline.json")
planned = []
for s in tl.segments:
    if not layout.is_beats(s, tl):
        continue
    p = layout.plan(s, tl, HERE)
    for key, sp in p.spans.items():
        if key.split(":")[0] in ("label", "note", "title", "area"):
            for x in sp:
                planned.append({"id": f"seg{s.seg_index}:{key}", "t_in": round(s.start_frame / FPS + (x.t_in + x.d_in) / FPS, 2)})
planned.sort(key=lambda r: r["t_in"])
data = {"shots": shots, "planned_text_entries": planned, "reference_texts": B.TEXTS,
        "samples": {"reference": R["samples"], "remake": O["samples"]}}
out = HERE / "out/compare"; out.mkdir(parents=True, exist_ok=True)
(out / "compare.json").write_text(json.dumps(data, indent=1))
L = ["| shot | scale end ref/ours | centre end ref/ours | zoom ln/s mean ref/ours | pan w/s med ref/ours | rot deg/s med ref/ours | tilt_v p95 ref/ours | parallax px/s ref/ours | speed med ref/ours | frames <0.01 ref/ours | frames <0.002 ref/ours |",
     "|---|---|---|---|---|---|---|---|---|---|---|"]
for s in shots:
    L.append("| {shot} ({t0}-{t1}) | {a[0]} / {a[1]} | {b[0]} / {b[1]} | {c[0]} / {c[1]} | {d[0]} / {d[1]} | {e[0]} / {e[1]} | {g[0]} / {g[1]} | {h[0]} / {h[1]} | {i[0]} / {i[1]} | {j[0]}/{n[0]} / {j[1]}/{n[1]} | {k[0]} / {k[1]} |".format(
        a=s["scale_end"], b=s["centre_end"], c=s["zoom_ln_s_mean"], d=s["pan_w_s_median"], e=s["rot_deg_s_median"],
        g=s["tilt_v_s_p95"], h=s["parallax_px_s_median"], i=s["speed_median"], j=s["frames_speed_lt_0.01"],
        k=s["frames_speed_lt_0.002"], n=s["frames_measured"], **s))
L += ["", "planned text entries (s): " + ", ".join(f"{r['id']}@{r['t_in']}" for r in planned)]
(out / "compare.md").write_text("\n".join(L) + "\n")
print("\n".join(L))
