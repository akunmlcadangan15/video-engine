"""Small shared helpers: subprocess, ffprobe, hashing, timing."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import hypoverum_core_rs as core

from . import config


class EngineError(RuntimeError):
    pass


def run(cmd: list, *, timeout: float | None = None, env: dict | None = None, cwd=None, quiet: bool = True) -> str:
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout)[-2000:]
        raise EngineError(f"command failed ({p.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    return p.stdout


@lru_cache(maxsize=256)
def _probe(path: str, mtime: float) -> dict:
    out = run([config.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    return json.loads(out)


def probe(path: str | Path) -> dict:
    p = Path(path)
    return _probe(str(p), p.stat().st_mtime)


def video_info(path: str | Path) -> dict:
    d = probe(path)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise EngineError(f"{path}: no video stream")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    dur = float(v.get("duration") or d["format"].get("duration") or 0)
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) in (90, 270):
        w, h = h, w
    return {"width": w, "height": h, "fps": fps, "duration": dur, "codec": v.get("codec_name"),
            "pix_fmt": v.get("pix_fmt"), "r_frame_rate": v.get("r_frame_rate")}


def count_frames(path: str | Path) -> int:
    """Exact decoded video frame count (packet count; cheap, exact for our CFR h264 outputs)."""
    out = run([config.FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
               "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)])
    return int(out.strip().split(",")[0])


def blake3_files(paths: list) -> list[str]:
    return core.blake3_files(paths)


def code_hash(files) -> str:
    """Content hash of the engine source files that shape a cached product (part of its cache key)."""
    return blake3_json(blake3_files([Path(f) for f in files]))


def blake3_json(obj) -> str:
    return core.blake3_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode())


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


@dataclass
class Timer:
    """Wall-time per stage; rows end up in timing.tsv. With a resources.Monitor, every stage also gets a
    resource row (wall, CPU, peak RSS by kind) in `resources`."""
    run_label: str = ""
    rows: list = field(default_factory=list)
    monitor: object = None
    resources: list = field(default_factory=list)

    @contextmanager
    def stage(self, name: str, note: str = ""):
        t0 = time.perf_counter()
        box = {"note": note}
        if self.monitor is not None:
            self.monitor.begin(name)
        try:
            yield box
        finally:
            self.rows.append((self.run_label, name, round(time.perf_counter() - t0, 3), box["note"]))
            if self.monitor is not None:
                self.resources.append(self.monitor.end(name))

    def add(self, name: str, seconds: float, note: str = ""):
        self.rows.append((self.run_label, name, round(seconds, 3), note))

    def write_tsv(self, path: Path, append: bool = True):
        new = not path.exists() or not append
        with open(path, "a" if append else "w") as f:
            if new:
                f.write("run\tstage\twall_s\tnote\n")
            for r in self.rows:
                f.write("\t".join(str(x) for x in r) + "\n")
