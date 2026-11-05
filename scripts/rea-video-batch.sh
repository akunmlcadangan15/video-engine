#!/bin/sh
# rea-video-batch.sh - bedah banyak video satu per satu
# Pakai: bash scripts/rea-video-batch.sh <folder-video> <folder-output> [--vlm]
set -eu
SRC=$1
OUT=$2
shift 2
[ -d "$SRC" ] || { echo "folder tidak ada: $SRC" >&2; exit 2; }
command -v rea-video >/dev/null 2>&1 || { echo "'rea-video' tidak ada di PATH" >&2; exit 127; }
mkdir -p "$OUT"
count=0
for f in "$SRC"/*.mp4 "$SRC"/*.mov "$SRC"/*.mkv; do
  [ -f "$f" ] || continue
  name=$(basename "$f" | sed 's/\.[^.]*$//')
  echo "== $name =="
  rea-video decompose "$f" -o "$OUT/$name" "$@" || echo "  gagal: $f" >&2
  rea-video summary "$OUT/$name/breakdown.json" > "$OUT/$name/summary.txt" 2>/dev/null || true
  count=$((count+1))
done
echo "selesai: $count video -> $OUT"
echo "Lanjut: agregasi breakdown.json di $OUT menjadi style di profiles/ (lihat docs/REA-VIDEO.md)"
