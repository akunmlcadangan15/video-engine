#!/bin/sh
# Contoh minimal — peta Asia Timur ASLI dari OpenStreetMap.
# Peta ASLI: ubin raster OpenStreetMap (tile.openstreetmap.org, zoom 4) untuk
# map_bounds [south 15, west 70, north 60, east 155]; ubin x=11..14, y=4..7
# mencakup lon 67.5..157.5 / lat 0..66.6.
# Narasi: sine 440 Hz 3 s (panjang = total timeline 90 frame), dummy lokal.
# TIDAK ada fallback testsrc2/smptehdbars: kalau unduhan OSM tidak berhasil,
# skrip GAGAL EKSPLISIT (exit 1) — peta uji tidak boleh diam-diam lolos sebagai
# peta asli.
# Cek R38 sebelum build: YAVG peta (ffmpeg signalstats, metadata YAVG) harus
# >= 35; di bawah itu skrip GAGAL EKSPLISIT (exit 1) — peta terlalu gelap,
# tidak boleh lanjut ke engine build.
set -eu
cd "$(dirname "$0")"
mkdir -p assets tiles

UA="video-engine-minimal/1.0 (+https://www.openstreetmap.org/copyright)"
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
img.resize((2600, 1800)).save("assets/map.jpg", quality=92)
PY
test -s assets/map.jpg || { echo "assets/map.jpg tidak terbentuk dari ubin OSM" >&2; exit 1; }

YAVG=$(ffmpeg -v info -i assets/map.jpg -vf "signalstats,metadata=print" -f null - 2>&1 | sed -n 's/.*YAVG=\([0-9.]*\).*/\1/p' | head -n 1)
test -n "$YAVG" || { echo "cek R38 gagal: YAVG tidak terbaca dari assets/map.jpg (ffmpeg signalstats)" >&2; exit 1; }
awk -v v="$YAVG" 'BEGIN { exit !(v >= 35) }' || { echo "cek R38 gagal: YAVG=$YAVG di bawah minimum 35 — peta terlalu gelap, tidak boleh lanjut build; ganti peta yang lebih terang lalu jalankan ulang ./prepare.sh" >&2; exit 1; }

ffmpeg -v error -y -f lavfi -i "sine=frequency=440:sample_rate=48000" -t 3 -af "volume=-26dB" assets/narration.wav

python3 - <<'PY'
import os
url = "https://tile.openstreetmap.org/4/11/4.png — © OpenStreetMap contributors (ODbL)"
old = open("credits.txt").read() if os.path.exists("credits.txt") else ""
old = old.replace("https://example.org/example-map", url)
if url not in old:
    old = url + "\n\n" + old
open("credits.txt", "w").write(old)
PY

echo "assets ready: engine build $(pwd)"
