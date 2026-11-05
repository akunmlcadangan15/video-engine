# Landscape example project

A 4 s landscape (16:9) still with a camera move and a drawn lower third, under the default profile. No network and
no third-party asset. Native path only, so it builds without the HyperFrames toolchain.

```sh
./prepare.sh                 # 1080p (default); ./prepare.sh 1440p or ./prepare.sh 4k rewrites settings and the box
engine-build .               # out/final.mp4 + out/build_report.json
engine sheet .
```

Expected gate output: every technical gate passes; AS01 warns about the lower third's accent strip (default
anti-slop rule). The final file is H.264 level 4.0 at 1080p and 5.1 at 4K. Units and the landscape rules:
docs/PROFILES.md, Orientation.
