# Landscape example project

A 4 s landscape (16:9) still with a camera move and a drawn lower third, under the default profile. Peta Asia Timur asli dari OpenStreetMap, bukan aset dummy. Native path only, so it builds without the HyperFrames toolchain.

```sh
./prepare.sh                 # unduh peta OSM -> assets/still.jpg (16:9, 4800x2700), ffmpeg narasi -> assets/narration.wav, credits.txt; ./prepare.sh 1440p or ./prepare.sh 4k rewrites settings and the box
engine-build .               # out/final.mp4 + out/build_report.json
engine sheet .
```

`prepare.sh` butuh `curl`, `ffmpeg`, Python dengan Pillow, dan jaringan. Interpreter: `$PYTHON`, lalu `$VIRTUAL_ENV/bin/python` (venv aktif), lalu `python3`. `assets/still.jpg` adalah potongan tengah peta Asia Timur asli dari ubin OpenStreetMap (tile.openstreetmap.org, zoom 4, mencakup `map_bounds` [15, 70, 60, 155], dipotong ke 16:9); tidak ada fallback pola uji `testsrc2`/`smptehdbars` — unduhan gagal berarti skrip gagal eksplisit. Sebelum build, cek R38: YAVG peta `>= 35`; di bawah itu skrip gagal eksplisit. `assets/narration.wav` (sine 440 Hz, 4 s) tetap dummy lokal; ganti material asli untuk produksi.

Expected gate output: every technical gate passes; AS01 warns about the lower third's accent strip (default
anti-slop rule). The final file is H.264 level 4.0 at 1080p and 5.1 at 4K. Units and the landscape rules:
docs/PROFILES.md, Orientation.
