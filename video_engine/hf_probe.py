"""HyperFrames DOM probe HTTP server, cache, and persistent Puppeteer worker pool."""
from __future__ import annotations

import atexit
import collections
import contextlib
import functools
import http.server
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import cache, config, globe, toolchain
from .util import EngineError, blake3_files, blake3_json, code_hash, run

_INKS: dict[str, dict] = {}   # in-process memo: segment key -> scene inks (the key covers scene, files, template)

PROBE_JS = config.PKG / "hf" / "dom_probe.mjs"
PROBE_VERSION = "dom-2"   # bump when the cached probe result format or the probe call changes
SHOT_FIELDS = ("png", "bg", "mask", "line_bg", "line_mask")   # screenshot paths of one probe time item

_DEFAULT_RUN = run
_DEFAULT_PROBE_JS = PROBE_JS
_POOL_MAX_IDLE = 2
_CRASH_MARKERS = ("Target closed", "Session closed", "Protocol error", "Browser disconnected", "browser has disconnected")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class _HTTPServer(http.server.ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


@contextlib.contextmanager
def serve(directory: Path):
    """Static server on a dynamic port (bind port 0). Yields the base URL."""
    h = functools.partial(_QuietHandler, directory=str(directory))
    srv = _HTTPServer(("127.0.0.1", 0), h)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


class _WarmWorkerDead(Exception):
    """Raised when a reused idle worker died before receiving the new request."""


class _ProbeWorker:
    """One persistent Node + Puppeteer browser speaking NDJSON to `dom_probe.mjs --server`."""

    def __init__(self, probe_js: Path, env: dict[str, str]):
        self.alive = True
        self._stderr: collections.deque[str] = collections.deque(maxlen=40)
        self._out_q: queue.Queue[str | None] = queue.Queue()
        self.proc = subprocess.Popen(
            [str(config.NODE), str(probe_js), "--server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        threading.Thread(target=self._drain_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        try:
            ready_line = self._out_q.get(timeout=30)
        except queue.Empty as e:
            self.close()
            raise EngineError(f"dom_probe server timed out starting: {self._err_tail()}") from e
        if ready_line is None:
            self.close()
            raise EngineError(f"dom_probe server exited on startup: {self._err_tail()}")
        try:
            msg = json.loads(ready_line)
        except json.JSONDecodeError as e:
            self.close()
            raise EngineError(f"dom_probe server bad ready line {ready_line!r}: {self._err_tail()}") from e
        if not msg.get("ready"):
            self.close()
            raise EngineError(f"dom_probe server unexpected banner: {msg}")

    def _drain_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                self._out_q.put(line)
        finally:
            self._out_q.put(None)

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            s = line.rstrip()
            if s:
                self._stderr.append(s)

    def _err_tail(self) -> str:
        return " | ".join(self._stderr)

    def is_healthy(self) -> bool:
        return self.alive and self.proc.poll() is None

    def request(self, payload: dict, timeout: float, reused: bool = False) -> dict:
        if not self.is_healthy():
            self.alive = False
            raise _WarmWorkerDead("worker exited")
        line_in = json.dumps(payload, separators=(",", ":")) + "\n"
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(line_in)
            self.proc.stdin.flush()
        except OSError as e:
            self.close()
            if reused:
                raise _WarmWorkerDead(str(e)) from e
            raise EngineError(f"dom_probe write failed: {e} ({self._err_tail()})") from e
        try:
            raw = self._out_q.get(timeout=timeout)
        except queue.Empty as e:
            self.close()
            raise EngineError(f"dom_probe timed out after {timeout}s ({self._err_tail()})") from e
        if raw is None:
            self.close()
            if reused:
                raise _WarmWorkerDead("EOF from warm worker")
            raise EngineError(f"dom_probe exited unexpectedly ({self._err_tail()})")
        try:
            resp = json.loads(raw)
        except json.JSONDecodeError as e:
            self.close()
            raise EngineError(f"dom_probe returned invalid JSON: {raw[:200]!r}") from e
        if resp.get("id") != payload["id"]:
            self.close()
            raise EngineError(f"dom_probe protocol desync: expected id={payload['id']}, got {resp.get('id')}")
        if not resp.get("ok"):
            err = str(resp.get("error") or "dom_probe failed")
            if self.proc.poll() is not None or any(m in err for m in _CRASH_MARKERS):
                self.close()
            raise EngineError(err)
        return resp["result"]

    def close(self) -> None:
        self.alive = False
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    try:
                        self.proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass


class _ProbePool:
    """Thread-safe pool of warm `dom_probe.mjs --server` workers keyed by browser launch environment."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle: dict[tuple, collections.deque[_ProbeWorker]] = {}
        self._seq = 0

    def _next_id(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _pop(self, key: tuple) -> _ProbeWorker | None:
        with self._lock:
            q = self._idle.get(key)
            while q:
                w = q.popleft()
                if w.is_healthy():
                    return w
                w.close()
        return None

    def _put(self, key: tuple, w: _ProbeWorker) -> None:
        if not w.is_healthy():
            w.close()
            return
        with self._lock:
            q = self._idle.setdefault(key, collections.deque())
            if len(q) >= _POOL_MAX_IDLE:
                w.close()
                return
            q.append(w)

    def run_probe(self, probe_js: Path, env: dict[str, str], payload: dict, timeout: float) -> dict:
        key = (str(probe_js), tuple(sorted(env.items())))
        req = {**payload, "id": self._next_id()}
        w = self._pop(key)
        reused = w is not None
        if w is None:
            w = _ProbeWorker(probe_js, env)
        try:
            try:
                res = w.request(req, timeout=timeout, reused=reused)
            except _WarmWorkerDead:
                w = _ProbeWorker(probe_js, env)
                res = w.request(req, timeout=timeout, reused=False)
        except Exception:
            if not w.is_healthy():
                w.close()
            else:
                self._put(key, w)
            raise
        self._put(key, w)
        return res

    def close_all(self) -> None:
        with self._lock:
            workers = [w for q in self._idle.values() for w in q]
            self._idle.clear()
        for w in workers:
            w.close()


_POOL = _ProbePool()


def shutdown_probe_pool() -> None:
    """Terminate all idle pooled Puppeteer probe workers cleanly."""
    _POOL.close_all()


atexit.register(shutdown_probe_pool)


def probe_key(seg_dir: Path, times: list[float], width: int, height: int, snaps: bool, bg: str) -> str:
    """Cache key of a DOM probe: the content of every file of the built segment dir (paths relative to it: html,
    scene.js, assets, scripts; a globe or 3D camera script also switches the probe browser to WebGL), the probe times as passed to
    the probe (4 dp), the viewport, whether screenshots are taken and the bg mode, the toolchain identity (node,
    Chrome, npm tree, GSAP, template, dom_probe.mjs, ffmpeg) and the engine source that runs the probe (CODE)."""
    _hf = sys.modules.get("video_engine.hyperframes")
    code = getattr(_hf, "CODE", (Path(__file__),))
    files = sorted(p for p in Path(seg_dir).rglob("*") if p.is_file())
    return blake3_json({"v": PROBE_VERSION, "code": code_hash(code), "toolchain": toolchain.hf_identity(),
                        "dir": list(zip((str(p.relative_to(seg_dir)) for p in files), blake3_files(files))),
                        "times": [f"{t:.4f}" for t in times], "size": [int(width), int(height)],
                        "snaps": bool(snaps), "bg": bg})


def _probe_cached(name: str, snap_dir: Path | None) -> dict | None:
    """The cached probe result `name`, its screenshots copied into snap_dir; None when the JSON or any screenshot of
    the entry is missing (an entry LRU eviction broke counts as a miss)."""
    res = cache.get_json("dom", name)
    if res is None:
        return None
    shots = []
    for item in res["times"]:
        for f in SHOT_FIELDS:
            if item.get(f):
                if snap_dir is None or (hit := cache.get_file("dom", f"{name}.{item[f]}", "")) is None:
                    return None
                shots.append((f, item, hit))
    if snap_dir:
        snap_dir.mkdir(parents=True, exist_ok=True)
    for f, item, hit in shots:
        dst = snap_dir / item[f]
        shutil.copyfile(hit, dst)
        item[f] = str(dst)
    return res


def _probe_store(name: str, res: dict) -> None:
    """Store a probe result: every screenshot first (under <name>.<file name>), the JSON with bare file names last,
    so a present JSON means a complete entry."""
    out = json.loads(json.dumps(res))
    for item in out["times"]:
        for f in SHOT_FIELDS:
            if item.get(f):
                src = Path(item[f])
                cache.put_file("dom", f"{name}.{src.name}", src, "")
                item[f] = src.name
    cache.put_json("dom", name, out)


def _reusable_ink(ink_dir: Path | None, bg: str, snap_dir: Path | None) -> dict[str, dict]:
    """Reusable (clip, bg, mask) entries keyed by 4-dp time string from a prior `bgonly` probe."""
    if ink_dir is None or bg != "bg" or snap_dir is None or not (meta := ink_dir / "probe.json").is_file():
        return {}
    try:
        data = json.loads(meta.read_text())
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for item in data.get("times", []):
        clip, bg_p, mk_p = item.get("clip"), item.get("bg"), item.get("mask")
        if clip is None and bg_p is None and mk_p is None:
            out[f"{item['t']:.4f}"] = {"clip": None, "bg": None, "mask": None}
        elif clip and bg_p and mk_p and Path(bg_p).is_file() and Path(mk_p).is_file():
            out[f"{item['t']:.4f}"] = {"clip": dict(clip), "bg": str(bg_p), "mask": str(mk_p)}
    return out


def _apply_reused_ink(res: dict, reusable: dict[str, dict], snap_dir: Path) -> None:
    """Copy prior `bg`/`mask` screenshots and `clip` bounds onto matching time items returned by `dom_probe.mjs`."""
    for i, item in enumerate(res.get("times", [])):
        src = reusable.get(f"{item['t']:.4f}")
        if not src or not src["clip"]:
            continue
        bg_dst = snap_dir / f"bg_{i}.png"
        mk_dst = snap_dir / f"mask_{i}.png"
        shutil.copyfile(src["bg"], bg_dst)
        shutil.copyfile(src["mask"], mk_dst)
        item["bg"] = str(bg_dst)
        item["mask"] = str(mk_dst)
        item["clip"] = dict(src["clip"])


def probe_dom(seg_dir: Path, times: list[float], width: int, height: int, snap_dir: Path | None = None,
              bg: str = "", ref: str | None = None, timeout: int | None = None,
              reuse_ink: Path | None = None) -> dict:
    """DOM probe (hf/dom_probe.mjs); bg "bg" (needs snap_dir) adds the text-hidden and glyph-mask screenshots,
    "bgonly" takes only those. When `reuse_ink` holds a prior `bgonly` probe (`probe.json` + PNGs) for `bg="bg"`,
    matching timestamps skip re-capturing `bg`/`mask` in Chrome and reuse its PNGs before caching. Results are
    cached (kind "dom", probe_key; `ref` = the segment cache key of the probed segment, the entry name prefix
    `engine cache --prune` keeps entries by); a hit spawns no browser and copies the screenshots into snap_dir under
    the names the probe gives them. "cached" in the result: True on a hit."""
    name = f"{ref or 'adhoc'}.{probe_key(seg_dir, times, width, height, snap_dir is not None, bg)}"
    if (hit := _probe_cached(name, snap_dir)) is not None:
        hit["cached"] = True
        return hit
    env = config.hf_env()
    env["ENGINE_PUPPETEER"] = str(config.PUPPETEER)
    env["ENGINE_CHROME"] = str(config.CHROME)
    scene3d_name = "scene3d.js"
    if (seg_dir / globe.JS.name).exists() or (seg_dir / scene3d_name).exists():
        env["ENGINE_WEBGL"] = "1"     # globe and 3D camera scenes: SwiftShader WebGL in the probe browser
    if snap_dir:
        snap_dir.mkdir(parents=True, exist_ok=True)
    reusable = _reusable_ink(reuse_ink, bg, snap_dir)
    skip_ink = [f"{t:.4f}" for t in times if f"{t:.4f}" in reusable]
    t_limit = timeout if timeout is not None else (120 + (2 * len(times) if bg else 0))
    _hf = sys.modules.get("video_engine.hyperframes")
    cur_run = getattr(_hf, "run", _DEFAULT_RUN)
    cur_probe_js = getattr(_hf, "PROBE_JS", _DEFAULT_PROBE_JS)
    use_pool = (
        cur_run is _DEFAULT_RUN
        and Path(cur_probe_js) == _DEFAULT_PROBE_JS
        and os.environ.get("VIDEO_ENGINE_PROBE_POOL", "1") != "0"
    )
    with serve(seg_dir) as url:
        if use_pool:
            payload: dict = {
                "url": url + "/index.html",
                "outDir": str(snap_dir) if snap_dir else "-",
                "times": [float(f"{t:.4f}") for t in times],
                "width": int(width),
                "height": int(height),
                "mode": str(bg or ""),
            }
            if skip_ink:
                payload["skipInkTimes"] = skip_ink
            res = _POOL.run_probe(_DEFAULT_PROBE_JS, env, payload, timeout=t_limit)
        else:
            if skip_ink:
                env["ENGINE_SKIP_INK_TIMES"] = ",".join(skip_ink)
            out = cur_run([config.NODE, cur_probe_js, url + "/index.html", snap_dir or "-",
                           ",".join(f"{t:.4f}" for t in times), width, height, *([bg] if bg else [])],
                          env=env, timeout=t_limit)
            res = json.loads(out)
    if reusable and snap_dir is not None:
        _apply_reused_ink(res, reusable, snap_dir)
    _probe_store(name, res)
    res["cached"] = False
    return res


def probe_props(seg_dir: Path, times: list[float], width: int, height: int) -> dict:
    """Props probe (hf/props_probe.mjs): the #cam transform and every prop element's centre and size per time."""
    env = config.hf_env()
    env["ENGINE_PUPPETEER"] = str(config.PUPPETEER)
    env["ENGINE_CHROME"] = str(config.CHROME)
    _hf = sys.modules.get("video_engine.hyperframes")
    cur_run = getattr(_hf, "run", _DEFAULT_RUN)
    with serve(seg_dir) as url:
        out = cur_run([config.NODE, config.PKG / "hf" / "props_probe.mjs", url + "/index.html",
                       ",".join(f"{t:.6f}" for t in times), width, height], env=env, timeout=120)
    return json.loads(out)
