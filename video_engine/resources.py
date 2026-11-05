"""Shared worker budget and per-stage resource measurement.

Budget: one number `workers` (env VIDEO_ENGINE_WORKERS > profile `[resources] workers` > auto) caps every
parallel consumer of a build at once:
  auto workers   = clamp(min(CPU count, total RAM GB // ram_per_worker_gb(W, H)), 2, 64); per worker 1.8 GB at
                   1080x1920, scaled by pixel count above it (4K: 7.2 GB)
  native_workers = max(1, workers // 4)   native segments rendered in parallel, X264_THREADS (2) x264 threads each
  hf_workers     = max(1, workers // 4)   HyperFrames Chrome capture workers of one HF segment render
  hf_segments    = 2 when 2 * hf_workers * 2 <= workers, else 1: HF segments rendered at once at most; a build
                   uses hf_parallel(native segment count)
  mux_threads    = clamp(workers - 2, 1, 6)  x264 threads of the composite pass (runs alone)
  native_workers * X264_THREADS + hf_workers * 2 <= workers whenever workers >= 4, and with the HF segments a
  build renders at once: min(natives, native_workers) * X264_THREADS + hf_parallel * hf_workers * 2 <= workers.
The x264 thread counts are part of the encoded bytes: X264_THREADS stays 2 and mux_threads is 6 for every budget
>= 8, so builds at the default budget of an 8-CPU machine keep their bitstreams.

Measurement (Monitor): a sampler thread reads /proc every SAMPLE_S for the engine process and all its descendants
(where /proc is absent, e.g. macOS: one `ps -axo pid=,ppid=,rss=,time=,comm=` snapshot per tick, and `sysctl -n hw.memsize`
for the RAM size);
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
import subprocess
import threading
import time
from dataclasses import asdict, dataclass

RAM_PER_WORKER_GB = 1.8
X264_THREADS = 2
SAMPLE_S = 0.2
CLK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
HAVE_PROC = os.path.exists("/proc/self/stat")   # Linux: read /proc; otherwise (macOS) sysctl and one ps snapshot per tick
PS_ARGS = ("ps", "-axo", "pid=,ppid=,rss=,time=,comm=")


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


    def report(self) -> dict:
        return asdict(self)


def parse_memsize(text: str) -> float | None:
    """GB of `sysctl -n hw.memsize` output (bytes), same rounding as the /proc/meminfo path; None when unparsable."""
    try:
        n = int(text.strip())
    except ValueError:
        return None
    return round(n / 1024 / 1024 / 1024, 1) if n > 0 else None


def ram_gb() -> float:
    if not HAVE_PROC:
        for exe in ("sysctl", "/usr/sbin/sysctl"):
            try:
                out = subprocess.run([exe, "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            if (gb := parse_memsize(out)) is not None:
                return gb
        return 8.0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    return 8.0


def ram_per_worker_gb(W: int = 1080, H: int = 1920) -> float:
    """RAM one worker needs: RAM_PER_WORKER_GB at 1080x1920 (and 1920x1080), times the pixel-count ratio for larger
    frames (2560x1440: 3.2 GB, 3840x2160: 7.2 GB). Smaller frames keep the 1080p figure."""
    return RAM_PER_WORKER_GB * max(1.0, (W * H) / (1080 * 1920))


def auto_workers(cpus: int, gb: float, per_worker: float = RAM_PER_WORKER_GB) -> int:
    return max(2, min(64, cpus, int(gb // per_worker)))


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
    per = ram_per_worker_gb(tl.settings.width, tl.settings.height) if tl is not None else RAM_PER_WORKER_GB
    return plan(auto_workers(cpus, gb, per), "auto", cpus, gb)


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


def parse_cputime(text: str) -> float:
    """Seconds of a BSD ps `time` field: [[dd-]hh:]mm:ss[.ff] (0:01.23, 12:34.56, 1:02:03.00, 2-01:02:03)."""
    t = text.strip().replace(",", ".")
    days = 0
    if "-" in t:
        d, t = t.split("-", 1)
        days = int(d)
    sec = 0.0
    for part in t.split(":"):
        sec = sec * 60 + float(part)
    return days * 86400 + sec


def parse_ps(text: str) -> dict[int, tuple[int, str, float, int]]:
    """{pid: (ppid, comm, cpu seconds, rss bytes)} from `ps -axo pid=,ppid=,rss=,time=,comm=` output. rss is in KiB;
    comm is last and may be a full path with spaces (basename kept). Lines that do not parse are skipped."""
    out: dict[int, tuple[int, str, float, int]] = {}
    for line in text.splitlines():
        f = line.split(None, 4)
        if len(f) < 5:
            continue
        try:
            pid, ppid, rss_kib, cpu = int(f[0]), int(f[1]), int(f[2]), parse_cputime(f[3])
        except ValueError:
            continue
        comm = f[4].strip().rsplit("/", 1)[-1]
        out[pid] = (ppid, comm, cpu, rss_kib * 1024)
    return out


def _ps_snapshot() -> tuple[dict[int, list[int]], dict[int, tuple[str, float, int]]]:
    """(children by ppid, {pid: (comm, cpu seconds, rss bytes)}) of every process, from one ps call; empty when ps
    fails. The ps process itself is left out."""
    try:
        p = subprocess.Popen(PS_ARGS, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                             env={**os.environ, "LC_ALL": "C"})
        try:
            text, _ = p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            return {}, {}
    except (OSError, subprocess.SubprocessError):
        return {}, {}
    kids: dict[int, list[int]] = {}
    stats: dict[int, tuple[str, float, int]] = {}
    for pid, (ppid, comm, cpu, rss) in parse_ps(text).items():
        if pid == p.pid:
            continue
        kids.setdefault(ppid, []).append(pid)
        stats[pid] = (comm, cpu, rss)
    return kids, stats


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
        if HAVE_PROC:
            kids, stat = _children(), _stat
        else:
            kids, snap = _ps_snapshot()
            stat = snap.get
        rss = {"engine": 0, "ffmpeg": 0, "hyperframes": 0, "other": 0}
        cpu: dict[int, tuple[str, float]] = {}
        todo, root = [self.me], True
        kind_of = {self.me: "engine"}
        while todo:
            pid = todo.pop()
            st = stat(pid)
            if st is None:
                continue
            comm, c, r = st
            k = "engine" if root else (kind_of.get(pid) or _kind(comm))
            root = False
            rss[k] += r
            if pid != self.me:
                cpu[pid] = (k, c)
            for ch in kids.get(pid, []):
                cs = stat(ch)
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
