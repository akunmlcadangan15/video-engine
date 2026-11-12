#!/usr/bin/env python3
"""ffmpeg in front of HyperFrames' own libx264 encode.

HyperFrames builds that command itself (one thread per CPU, no deterministic flags). The same
screenshots then decode to different pictures when the CPU is busy, and the segment the engine
normalises afterwards follows them. A libx264 encode gets the same parameter set as
config.X264_REPEATABLE appended to its existing -x264-params. Every other invocation is passed
through unchanged, including -version.
"""
from __future__ import annotations

import os
import sys

# Keep in step with video_engine.config.X264_REPEATABLE. A test compares the two strings.
REPEATABLE = "lookahead-threads=1:sliced-threads=0:sync-lookahead=0:non-deterministic=0"


def _with_repeatable(args: list[str]) -> list[str]:
    """One libx264 thread plus the repeatable parameter set.

    HyperFrames passes `-threads` equal to the CPU count. That count is not a stable bitstream even with
    the repeatable parameters; one thread is.
    """
    encodes = any(args[i] == "-c:v" and i + 1 < len(args) and args[i + 1] == "libx264" for i in range(len(args)))
    if not encodes:
        return args
    out = list(args)
    if not any("non-deterministic=" in a for a in out):
        if "-x264-params" in out:
            i = out.index("-x264-params")
            if i + 1 < len(out):
                out[i + 1] = out[i + 1] + ":" + REPEATABLE
        else:
            out.extend(["-x264-params", REPEATABLE])
    last = None
    for i, a in enumerate(out):
        if a == "-threads" and i + 1 < len(out):
            last = i
    if last is None:
        out.extend(["-threads", "1"])
    else:
        out[last + 1] = "1"
    return out


def main() -> None:
    real = os.environ.get("VIDEO_ENGINE_FFMPEG_REAL") or "ffmpeg"
    os.execvp(real, [real, *_with_repeatable(sys.argv[1:])])


if __name__ == "__main__":
    main()
