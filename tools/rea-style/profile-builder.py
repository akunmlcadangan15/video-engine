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
    if shot_med > 0:
        patch["draft"] = {
            "min_seg_s": round(max(1.0, shot_med * 0.5), 2),
            "max_seg_s": round(max(6.0, shot_med * 3.0), 2),
            "map_path": True,
        }

    cap = style.get("caption") or {}
    cs: dict = {}
    if cap.get("outline_px") is not None:
        cs["outline"] = int(cap["outline_px"])
    pos = (cap.get("position") or "").strip().lower()
    if pos == "bottom":
        cs["fixed_y"] = 1417
        cs["max_bottom_y"] = 1480
    elif pos == "center":
        cs["fixed_y"] = 960
    if cs:
        patch["caption_style"] = cs

    aud = style.get("audio") or {}
    audio_cfg: dict = {"target_lufs": -14.0, "true_peak": -1.5}
    if aud.get("music_source") == "library":
        audio_cfg["prefer_source"] = "epidemic"
    patch["audio"] = audio_cfg
    return patch


def format_toml_patch(patch: dict) -> str:
    lines: list[str] = []
    if "description" in patch:
        lines.append(f"description = {json.dumps(patch['description'])}")
    for section in ("caption_style", "audio", "draft"):
        if section not in patch:
            continue
        lines.append(f"\n[{section}]")
        for k, v in patch[section].items():
            lines.append(f"{k} = {json.dumps(v)}")
    return "\n".join(lines) + "\n"


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
    out_path.write_text(rendered, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
