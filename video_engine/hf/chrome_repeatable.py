#!/usr/bin/env python3
"""Chrome launcher wrapper for deterministic HyperFrames captures.

Injects CPU 2D rasterization (`--disable-gpu-rasterization`, `--num-raster-threads=1`) into every headless Chrome
launch triggered by HyperFrames (`HYPERFRAMES_BROWSER_PATH` / `PRODUCER_HEADLESS_SHELL_PATH`) while keeping ANGLE
SwiftShader active for WebGL scenes and models (`video_engine/hf/model_render.mjs` uses the same pair).
"""
from __future__ import annotations

import os
import sys

FLAGS = ("--disable-gpu-rasterization", "--num-raster-threads=1")


def _has_flag(args: list[str], flag: str) -> bool:
    name = flag.split("=", 1)[0]
    return any(a == name or a.startswith(name + "=") for a in args)


def _with_repeatable(args: list[str]) -> list[str]:
    if "--version" in args or "--help" in args or "-h" in args:
        return list(args)
    missing = [f for f in FLAGS if not _has_flag(args, f)]
    return [*missing, *args]


def main() -> None:
    real = os.environ.get("VIDEO_ENGINE_CHROME_REAL") or "chromium"
    os.execvp(real, [real, *_with_repeatable(sys.argv[1:])])


if __name__ == "__main__":
    main()
