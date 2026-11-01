#!/bin/sh
# Generate the two binary assets of this example (no network): a map and a 3 s narration tone.
set -eu
cd "$(dirname "$0")"
mkdir -p assets
ffmpeg -v error -y -f lavfi -i "testsrc2=size=2700x2880:rate=1" -frames:v 1 assets/map.jpg
ffmpeg -v error -y -f lavfi -i "sine=frequency=440:sample_rate=48000" -t 3 -af "volume=-26dB" assets/narration.wav
printf '%s\n' "https://example.org/example-map" > credits.txt
echo "assets ready: engine build $(pwd)"
