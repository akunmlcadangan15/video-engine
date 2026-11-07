#!/usr/bin/env python3
"""profile-builder.py — konversi style-sheet rea-style (template.toml + breakdown.json opsional)
menjadi blok konfigurasi profil channel di profiles/<channel>.toml.

Contoh:
  python tools/rea-style/profile-builder.py --style tools/rea-style/template.toml --out profiles/<channel>.toml --dry-run
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tomllib


def build_profile_patch(style: dict, breakdown: dict | None = None) -> dict:
    patch: dict = {}
    if desc := style.get("description"):
        patch["description"] = desc

    timing = style.get("timing") or {}
    shot_med = float(timing.get("shot_median_s") or 0.0)
    if breakdown and not shot_med:
        shot_med = float((breakdown.get("pacing") or {}).get("median_shot_s") or 0.0)
    min_seg = float(timing.get("min_seg_s") or (max(1.0, shot_med * 0.5) if shot_med > 0 else 0.0))
    max_seg = float(timing.get("max_seg_s") or (max(6.0, shot_med * 3.0) if shot_med > 0 else 0.0))
    if min_seg > 0 and max_seg > 0:
        patch["draft"] = {
            "min_seg_s": round(min_seg, 2),
            "max_seg_s": round(max_seg, 2),
            "map_path": True,
        }

    cap = style.get("caption") or {}
    cs: dict = {}
    if cap.get("font"):
        cs["font"] = str(cap["font"])
    if cap.get("bold") is not None:
        cs["bold"] = bool(cap["bold"])
    if cap.get("primary_color"):
        cs["primary"] = str(cap["primary_color"]).lstrip("#").upper()
    if cap.get("highlight_colour"):
        cs["highlight_colour"] = str(cap["highlight_colour"]).lstrip("#").upper()
    if cap.get("emphasis_colour"):
        cs["emphasis_colour"] = str(cap["emphasis_colour"]).lstrip("#").upper()
    if cap.get("size") is not None:
        cs["size"] = int(cap["size"])
    if cap.get("outline_px") is not None:
        cs["outline"] = int(cap["outline_px"])
    if cap.get("soft_shadow") is not None:
        cs["soft_shadow"] = bool(cap["soft_shadow"])
    if cap.get("max_words") is not None:
        cs["max_words"] = int(cap["max_words"])
    if cap.get("active_word_highlight") is not None:
        cs["active_word_highlight"] = bool(cap["active_word_highlight"])
    pos = (cap.get("position") or "").strip().lower()
    if pos == "bottom":
        cs["fixed_y"] = int(cap.get("fixed_y") or 1417)
        cs["max_bottom_y"] = int(cap.get("max_bottom_y") or 1480)
    elif pos == "center":
        cs["fixed_y"] = int(cap.get("fixed_y") or 960)
    if cs:
        patch["caption_style"] = cs

    aud = style.get("audio") or {}
    audio_cfg: dict = {
        "target_lufs": float(aud.get("target_lufs", -14.0)),
        "true_peak": float(aud.get("true_peak", -1.5)),
    }
    if aud.get("lra") is not None:
        audio_cfg["lra"] = float(aud["lra"])
    if aud.get("tolerance_lu") is not None:
        audio_cfg["tolerance_lu"] = float(aud["tolerance_lu"])
    if aud.get("music_source") in ("library", "epidemic") or aud.get("require_source") == "epidemic":
        audio_cfg["require_source"] = "epidemic"
    patch["audio"] = audio_cfg

    th = style.get("theme") or {}
    cam = style.get("camera") or {}
    theme_cfg: dict = {}
    for k in ("route_colour", "pin_colour", "label_box_colour", "region_fill_opacity", "region_edge",
              "region_edge_px", "region_glow_px"):
        if k in th:
            theme_cfg[k] = th[k]
    if "drift_per_s" in cam:
        theme_cfg["camera_drift_per_s"] = float(cam["drift_per_s"])
    if theme_cfg:
        patch["theme"] = theme_cfg

    cam_cfg: dict = {}
    for k in ("max_zoom", "pulse_zoom", "pulse_s", "pulse_gap_s", "bank_roll", "bank_tilt"):
        if k in cam:
            cam_cfg[k] = cam[k]
    if cam_cfg:
        patch["camera"] = cam_cfg

    return patch


def format_toml_patch(patch: dict) -> str:
    lines: list[str] = []
    if "description" in patch:
        lines.append(f"description = {json.dumps(patch['description'])}")
    for section in ("caption_style", "audio", "draft", "theme", "camera"):
        if section not in patch:
            continue
        lines.append(f"\n[{section}]")
        for k, v in patch[section].items():
            lines.append(f"{k} = {json.dumps(v)}")
    return "\n".join(lines) + "\n"


def merge_toml_text(existing_text: str, patch: dict) -> str:
    """Merge patched sections into an existing profile TOML without erasing `include` or unrelated tables."""
    import re
    out = existing_text
    if "description" in patch:
        desc_line = f"description = {json.dumps(patch['description'])}"
        if re.search(r"(?m)^description\s*=", out):
            out = re.sub(r"(?m)^description\s*=.*$", lambda _m: desc_line, out, count=1)
        else:
            out = desc_line + "\n" + out
    for section in ("caption_style", "audio", "draft", "theme", "camera"):
        if section not in patch:
            continue
        sec_body = "\n".join(f"{k} = {json.dumps(v)}" for k, v in patch[section].items())
        pattern = re.compile(rf"(?ms)(^\[{re.escape(section)}\]\s*\n)(.*?)(?=^\[|\Z)")
        if pattern.search(out):
            def _update_sec(m):
                header, body = m.group(1), m.group(2)
                for k, v in patch[section].items():
                    kv_line = f"{k} = {json.dumps(v)}"
                    if re.search(rf"(?m)^{re.escape(k)}\s*=", body):
                        body = re.sub(rf"(?m)^{re.escape(k)}\s*=.*$", lambda _m: kv_line, body, count=1)
                    else:
                        body = body.rstrip("\n") + "\n" + kv_line + "\n"
                return header + body
            out = pattern.sub(_update_sec, out, count=1)
        else:
            out = out.rstrip("\n") + f"\n\n[{section}]\n{sec_body}\n"
    return out.rstrip("\n") + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Bangun/perbarui profil channel dari style-sheet rea-style.")
    p.add_argument("--style", required=True, help="style-sheet rea-style (template.toml)")
    p.add_argument("--breakdown", default=None, help="opsional: breakdown.json hasil rea-video")
    p.add_argument("--out", required=True, help="target profil di profiles/")
    p.add_argument("--dry-run", action="store_true", help="tampilkan rencana tanpa menulis file")
    args = p.parse_args()

    style_path = pathlib.Path(args.style)
    if not style_path.exists():
        print(f"style-sheet tidak ada: {args.style}", file=sys.stderr)
        return 1

    style = tomllib.loads(style_path.read_text(encoding="utf-8"))
    breakdown = None
    if args.breakdown:
        bp = pathlib.Path(args.breakdown)
        if bp.is_file():
            breakdown = json.loads(bp.read_text(encoding="utf-8"))

    patch = build_profile_patch(style, breakdown)
    rendered = format_toml_patch(patch)
    print(f"style-sheet : {args.style}")
    print(f"target profil: {args.out}")
    if args.dry_run:
        print("dry-run: belum ada perubahan ditulis.")
        print(rendered)
        return 0

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_file():
        rendered = merge_toml_text(out_path.read_text(encoding="utf-8"), patch)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
