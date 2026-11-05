#!/bin/sh
# Generate the binary assets of this example (no network): a 16:9 still and a 4 s narration tone.
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
mkdir -p assets
ffmpeg -v error -y -f lavfi -i "testsrc2=size=4800x2700:rate=1" -frames:v 1 assets/still.jpg
ffmpeg -v error -y -f lavfi -i "sine=frequency=440:sample_rate=48000" -t 4 -af "volume=-26dB" assets/narration.wav
printf '%s\n' "https://example.org/example-still" > credits.txt
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
