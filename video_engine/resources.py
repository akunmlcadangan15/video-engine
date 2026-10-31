"""Shared worker budget and per-stage resource measurement.

Budget: one number `workers` (env VIDEO_ENGINE_WORKERS > profile `[resources] workers` > auto) caps every
parallel consumer of a build at once:
  auto workers   = clamp(min(CPU count, total RAM GB // RAM_PER_WORKER_GB), 2, 64)
  native_workers = max(1, workers // 4)   native segments rendered in parallel, X264_THREADS (2) x264 threads each
  hf_workers     = max(1, workers // 4)   HyperFrames Chrome capture workers of one HF segment render
  hf_segments    = 2 when 2 * hf_workers * 2 <= workers, else 1: HF segments rendered at once at most; a build
                   uses hf_parallel(native segment count)
  mux_threads    = clamp(workers - 2, 1, 6)  x264 threads of the composite pass (runs alone)
  native_workers * X264_THREADS + hf_workers * 2 <= workers whenever workers >= 4, and with the HF segments a
  build renders at once: min(natives, native_workers) * X264_THREADS + hf_parallel * hf_workers * 2 <= workers.
The x264 thread counts are part of the encoded bytes: X264_THREADS stays 2 and mux_threads is 6 for every budget
>= 8, so builds at the default budget of an 8-CPU machine keep their bitstreams.

Measurement (Monitor): a sampler thread reads /proc every SAMPLE_S for the engine process and all its descendants;
each Timer stage gets wall time, CPU seconds (user+sys of the engine and of its children, from /proc and
getrusage) and peak resident memory, split by kind:
  engine       the Python process itself (includes the in-process Rust core)
  ffmpeg       ffmpeg / ffprobe children
  hyperframes  node, Chrome and their children
  other        anything else
"""
from __future__ import annotations

import os
import resource
import threading
import time
from dataclasses import asdict, dataclass

RAM_PER_WORKER_GB = 1.8
X264_THREADS = 2
SAMPLE_S = 0.2
CLK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")


@dataclass(frozen=True)
class Budget:
    workers: int
    source: str            # env | profile | auto
    cpus: int
    ram_gb: float
    native_workers: int
    x264_threads: int
    hf_workers: int
    mux_threads: int
    hf_segments: int = 1

    def hf_parallel(self, natives: int) -> int:
        """HF segments to render at once next to `natives` native segments: hf_segments when the natives' x264
        threads and hf_segments renders of hf_workers Chrome workers (2 workers each) fit the budget, else 1."""
        nat = min(natives, self.native_workers) * self.x264_threads
        if nat + self.hf_segments * self.hf_workers * 2 <= self.workers:
            return self.hf_segments
        return 1

    def whisper_threads(self) -> int:
        """Whisper CPU threads: the same worker budget (at most 8, whisper's own practical cap)."""
        return max(1, min(8, self.workers))

    def report(self) -> dict:
        return asdict(self)


def ram_gb() -> float:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    return 8.0


def auto_workers(cpus: int, gb: float) -> int:
    return max(2, min(64, cpus, int(gb // RAM_PER_WORKER_GB)))


def plan(workers: int, source: str = "explicit", cpus: int | None = None, gb: float | None = None) -> Budget:
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    cpus = cpus or os.cpu_count() or 2
    gb = ram_gb() if gb is None else gb
    hf_env = os.environ.get("VIDEO_ENGINE_HF_WORKERS")
    hf = max(1, int(hf_env)) if hf_env else max(1, workers // 4)
    return Budget(workers, source, cpus, gb, max(1, workers // 4), X264_THREADS, hf, max(1, min(6, workers - 2)),
                  2 if 2 * hf * 2 <= workers else 1)


def budget(tl=None) -> Budget:
    """The build's budget: VIDEO_ENGINE_WORKERS, else the profile's [resources] workers (0 = auto), else auto."""
    cpus, gb = os.cpu_count() or 2, ram_gb()
    env = os.environ.get("VIDEO_ENGINE_WORKERS")
    if env:
        return plan(max(1, int(env)), "env", cpus, gb)
    if tl is not None:
        from . import profiles
        w = profiles.of(tl).data.resources.workers
        if w:
            return plan(w, "profile", cpus, gb)
    return plan(auto_workers(cpus, gb), "auto", cpus, gb)


# ------------------------------------------------------------------------------------------------ measurement

def _kind(comm: str) -> str:
    c = comm.lower()
    if c.startswith(("ffmpeg", "ffprobe")):
        return "ffmpeg"
    if c.startswith(("node", "chrome", "headless_shell", "chromium")) or "chrome" in c:
        return "hyperframes"
    return "other"


def _children() -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                s = f.read()
        except OSError:
            continue
        ppid = int(s[s.rfind(")") + 2:].split()[1])
        kids.setdefault(ppid, []).append(int(d))
    return kids


def _stat(pid: int) -> tuple[str, float, int] | None:
    """(comm, cpu seconds, rss bytes) of a live process."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            s = f.read()
    except OSError:
        return None
    comm = s[s.find("(") + 1:s.rfind(")")]
    rest = s[s.rfind(")") + 2:].split()
    return comm, (int(rest[11]) + int(rest[12])) / CLK, int(rest[21]) * PAGE


class Monitor:
    """Samples the engine process tree; begin(stage) / end(stage) -> stage row."""

    def __init__(self):
        self.me = os.getpid()
        self._lock = threading.Lock()
        self._open: dict[str, dict] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="resource-monitor", daemon=True)
        self._thread.start()

    def _sample(self):
        kids = _children()
        rss = {"engine": 0, "ffmpeg": 0, "hyperframes": 0, "other": 0}
        cpu: dict[int, tuple[str, float]] = {}
        todo, root = [self.me], True
        kind_of = {self.me: "engine"}
        while todo:
            pid = todo.pop()
            st = _stat(pid)
            if st is None:
                continue
            comm, c, r = st
            k = "engine" if root else (kind_of.get(pid) or _kind(comm))
            root = False
            rss[k] += r
            if pid != self.me:
                cpu[pid] = (k, c)
            for ch in kids.get(pid, []):
                cs = _stat(ch)
                kind_of[ch] = "hyperframes" if k == "hyperframes" else _kind(cs[0] if cs else "")
                todo.append(ch)
        with self._lock:
            for row in self._open.values():
                for k, v in rss.items():
                    row["peak"][k] = max(row["peak"][k], v)
                row["peak_total"] = max(row["peak_total"], sum(rss.values()))
                if row.get("child_base") is None:
                    row["child_base"] = {pid: c for pid, (_, c) in cpu.items()}
                for pid, (k, c) in cpu.items():
                    base = row["child_base"].get(pid, 0.0)
                    row["child_cpu"][pid] = (k, max(0.0, c - base))

    def _loop(self):
        while not self._stop.wait(SAMPLE_S):
            try:
                self._sample()
            except Exception:   # a vanished process mid-read; the next sample covers it
                pass

    def begin(self, name: str):
        ru_s, ru_c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
        with self._lock:
            self._open[name] = {"t": time.perf_counter(), "self": ru_s.ru_utime + ru_s.ru_stime,
                                "children": ru_c.ru_utime + ru_c.ru_stime, "child_cpu": {}, "child_base": None,
                                "peak": {"engine": 0, "ffmpeg": 0, "hyperframes": 0, "other": 0}, "peak_total": 0}
        self._sample()

    def end(self, name: str) -> dict:
        self._sample()
        ru_s, ru_c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
        with self._lock:
            row = self._open.pop(name)
        mb = 1024 * 1024
        cpu_self = ru_s.ru_utime + ru_s.ru_stime - row["self"]
        cpu_children = ru_c.ru_utime + ru_c.ru_stime - row["children"]
        by_kind_cpu = {"ffmpeg": 0.0, "hyperframes": 0.0, "other": 0.0}
        for k, c in row["child_cpu"].values():
            by_kind_cpu[k if k in by_kind_cpu else "other"] += c
        return {"stage": name, "wall_s": round(time.perf_counter() - row["t"], 3),
                "cpu_s": round(cpu_self + cpu_children, 3), "cpu_engine_s": round(cpu_self, 3),
                "cpu_children_s": round(cpu_children, 3),
                "cpu_sampled_s": {k: round(v, 2) for k, v in by_kind_cpu.items() if v},
                "peak_rss_mb": round(row["peak_total"] / mb, 1),
                "peak_rss_mb_by_kind": {k: round(v / mb, 1) for k, v in row["peak"].items() if v}}

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
