# Minimal example project

A 3 s vertical map shot with one pin and a generated narration tone. Peta Asia Timur asli dari OpenStreetMap, bukan aset dummy.

```sh
./prepare.sh                 # unduh peta OSM -> assets/map.jpg, ffmpeg narasi -> assets/narration.wav, credits.txt
engine build .               # out/final.mp4 + out/build_report.json
engine sheet .               # out/sheet.png
engine deliver . --export out/package
```

`prepare.sh` butuh `curl`, `ffmpeg`, Python dengan Pillow, dan jaringan. Interpreter: `$PYTHON`, lalu `$VIRTUAL_ENV/bin/python` (venv aktif), lalu `python3`. `assets/map.jpg` adalah peta Asia Timur asli dari ubin OpenStreetMap (tile.openstreetmap.org, zoom 4, mencakup `map_bounds` [15, 70, 60, 155]); tidak ada fallback pola uji `testsrc2`/`smptehdbars` — unduhan gagal berarti skrip gagal eksplisit. Sebelum build, cek R38: YAVG peta `>= 35`; di bawah itu skrip gagal eksplisit. `assets/narration.wav` (sine 440 Hz, 3 s) tetap dummy lokal; ganti material asli untuk produksi.
