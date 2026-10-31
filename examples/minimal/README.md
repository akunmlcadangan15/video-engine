# Minimal example project

A 3 s vertical map shot with one pin and a generated narration tone. No network and no third-party asset.

```sh
./prepare.sh                 # ffmpeg writes assets/map.jpg, assets/narration.wav and credits.txt
engine build .               # out/final.mp4 + out/build_report.json
engine sheet .               # out/sheet.png
engine deliver . --export out/package
```

`prepare.sh` needs ffmpeg in `PATH`. Replace `assets/map.jpg` and `assets/narration.wav` with real
material to turn this into a production project.
