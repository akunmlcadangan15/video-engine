#!/bin/sh
# Contoh landscape — peta Asia Timur ASLI dari OpenStreetMap.
# Peta ASLI: ubin raster OpenStreetMap (tile.openstreetmap.org, zoom 4) untuk
# map_bounds [south 15, west 70, north 60, east 155]; ubin x=11..14, y=4..7
# mencakup lon 67.5..157.5 / lat 0..66.6 (sama dengan examples/minimal).
# Still: potongan peta OSM 16:9 (tengah) diperbesar ke 4800x2700 sebagai assets/still.jpg untuk kamera ken_burns 16:9.
# Narasi: sine 440 Hz 4 s (panjang = total timeline 120 frame), dummy lokal.
# TIDAK ada fallback testsrc2/smptehdbars: kalau unduhan OSM tidak berhasil,
# skrip GAGAL EKSPLISIT (exit 1) — peta uji tidak boleh diam-diam lolos sebagai
# peta asli.
# Cek R38 sebelum build: YAVG peta (ffmpeg signalstats, metadata YAVG) harus
# >= 35; di bawah itu skrip GAGAL EKSPLISIT (exit 1) — peta terlalu gelap,
# tidak boleh lanjut ke engine build.
# Usage: ./prepare.sh [1080p|1440p|4k]   (default 1080p; rewrites settings.width/height in master_timeline.json)
set -eu
cd "$(dirname "$0")"
size="${1:-1080p}"
case "$size" in
  1080p) w=1920; h=1080 ;;
  1440p) w=2560; h=1440 ;;
  4k)    w=3840; h=2160 ;;
  *) echo "size must be 1080p, 1440p or 4k" >&2; exit 2 ;;
esac
mkdir -p assets tiles

UA="video-engine-landscape/1.0 (+https://www.openstreetmap.org/copyright)"
Z=4; X0=11; Y0=4; NX=4; NY=4
x="$X0"
while [ "$x" -lt $((X0 + NX)) ]; do
  y="$Y0"
  while [ "$y" -lt $((Y0 + NY)) ]; do
    f="tiles/${x}_${y}.png"
    curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 15 -A "$UA" \
      "https://tile.openstreetmap.org/${Z}/${x}/${y}.png" -o "$f" \
      || { echo "gagal unduh ubin OSM z=${Z}/${x}/${y} (https://tile.openstreetmap.org/${Z}/${x}/${y}.png) — periksa jaringan, lalu jalankan ulang ./prepare.sh" >&2; exit 1; }
    test -s "$f" || { echo "ubin OSM kosong: $f" >&2; exit 1; }
    y=$((y + 1))
  done
  x=$((x + 1))
done

python3 - <<'PY'
from PIL import Image
Z, X0, Y0, NX, NY = 4, 11, 4, 4, 4
rows = [
    [Image.open(f"tiles/{X0 + i}_{Y0 + j}.png").convert("RGB") for i in range(NX)]
    for j in range(NY)
]
w, h = rows[0][0].size
img = Image.new("RGB", (w * NX, h * NY))
for j, row in enumerate(rows):
    for i, t in enumerate(row):
        img.paste(t, (i * w, j * h))
tsw, tsh = img.size
# Potong tengah ke 16:9 agar tidak distorsi, lalu perbesar ke 4800x2700 (still landscape)
target_ratio = 16 / 9
cur_ratio = tsw / tsh
if cur_ratio > target_ratio:
    new_w = int(tsh * target_ratio)
    x0 = (tsw - new_w) // 2
    img = img.crop((x0, 0, x0 + new_w, tsh))
else:
    new_h = int(tsw / target_ratio)
    y0 = (tsh - new_h) // 2
    img = img.crop((0, y0, tsw, y0 + new_h))
img.resize((4800, 2700)).save("assets/still.jpg", quality=92)
PY
test -s assets/still.jpg || { echo "assets/still.jpg tidak terbentuk dari ubin OSM" >&2; exit 1; }

YAVG=$(ffmpeg -v info -i assets/still.jpg -vf "signalstats,metadata=print" -f null - 2>&1 | sed -n 's/.*YAVG=\([0-9.]*\).*/\1/p' | head -n 1)
test -n "$YAVG" || { echo "cek R38 gagal: YAVG tidak terbaca dari assets/still.jpg (ffmpeg signalstats)" >&2; exit 1; }
awk -v v="$YAVG" 'BEGIN { exit !(v >= 35) }' || { echo "cek R38 gagal: YAVG=$YAVG di bawah minimum 35 — peta terlalu gelap, tidak boleh lanjut build; ganti peta yang lebih terang lalu jalankan ulang ./prepare.sh" >&2; exit 1; }

ffmpeg -v error -y -f lavfi -i "sine=frequency=440:sample_rate=48000" -t 4 -af "volume=-26dB" assets/narration.wav

python3 - <<'PY'
import os
url = "https://tile.openstreetmap.org/4/11/4.png — © OpenStreetMap contributors (ODbL)"
old = open("credits.txt").read() if os.path.exists("credits.txt") else ""
old = old.replace("https://example.org/example-still", url)
if url not in old:
    old = url + "\n\n" + old
open("credits.txt", "w").write(old)
PY

python3 - "$w" "$h" <<'PY'
import json, sys
p = "master_timeline.json"
d = json.load(open(p))
d["settings"]["width"], d["settings"]["height"] = int(sys.argv[1]), int(sys.argv[2])
k = int(sys.argv[1]) / 1920
BASE = {"card": [96, 820, 760, 140]}   # overlay boxes at 1920x1080
for s in d["segments"]:
    for o in s.get("overlays", []):
        o["box"] = [round(v * k) for v in BASE.get(o["id"], o["box"])]
json.dump(d, open(p, "w"), indent=2)
PY
echo "assets ready ($w x $h): engine-build $(pwd)"
