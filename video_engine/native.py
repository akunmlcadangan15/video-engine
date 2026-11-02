"""Native path: cut/trim (stream copy when the source stream has exactly the engine segment format, streams.py; else
libx264), camera moves on stills and footage through the Rust
sub-pixel warp, exact integer frame counts by construction, BLAKE3 segment cache."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import hypoverum_core_rs as core

from . import cache, config, overlays, profiles, resources, streams, toolchain
from .timeline import Segment, Timeline
from .util import EngineError, blake3_files, blake3_json, code_hash, decode_colour, run, video_info

NATIVE_VERSION = "native-1"
COPY_FORMAT = "copy-edit-list"   # stream-copy segment layout (render_copy); part of every copy-mode key
IMAGE_MAX_SIDE = 8192
CODE = (Path(__file__), Path(overlays.__file__))   # engine sources of a native segment's pixels (cache key)
BT709_VF = config.BT709_VF   # raw RGB frames -> BT.709 yuv420p


@dataclass
class NativeJob:
    seg: Segment
    src: Path
    mode: str                 # copy | encode | camera
    src_w: int
    src_h: int
    start_win: tuple | None = None
    end_win: tuple | None = None
    motion_px: float = 0.0
    key: str = ""
    encode_key: str = ""       # the encode-mode key when this job plans a copy that may fall back


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as im:
        return im.size


def source_size(path: Path, still: bool) -> tuple[int, int]:
    if still:
        return image_size(path)
    vi = video_info(path)
    return vi["width"], vi["height"]


def camera_endpoints(seg: Segment, tl: Timeline, sw: int, sh: int):
    cm = seg.camera_move
    W, H = tl.settings.width, tl.settings.height
    return core.camera_plan(cm.preset, cm.intensity, float(sw), float(sh), float(W), float(H), cm.focus_box)


def _cs(src: Path) -> str:
    """':in_color_matrix=..:in_range=..' of a video source for a scale filter (util.decode_colour), '' for RGB."""
    o = decode_colour(src)
    return f":{o}" if o else ""


def _keyframe_at(src: Path, t: float, fps: float) -> bool:
    out = run([config.FFPROBE, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries",
               "frame=pts_time", "-of", "csv=p=0", "-read_intervals", f"{max(0.0, t - 2)}%{t + 2}", str(src)])
    return any(abs(float(x.strip().rstrip(",")) - t) < 0.5 / fps for x in out.split() if x.strip())


def seg_x264(tl: Timeline | None) -> list[str]:
    """Segment (intermediate) encoder arguments of the timeline's profile ([render]); config.X264_SEGMENT without one."""
    return profiles.of(tl).render.x264("segment") if tl is not None else config.X264_SEGMENT


def copy_eligible(src: Path, W: int, H: int, fps: int, x264: list[str] | None = None) -> bool:
    """True when the first video stream of `src` has exactly the engine segment format (streams.engine_format of the
    segment encoder `x264`), every streams.VIDEO_KEYS parameter, time base included."""
    return not streams.mismatch(streams.params(src, "v:0"), streams.engine_format(W, H, fps, x264))


def plan(seg: Segment, tl: Timeline, project_dir: Path) -> NativeJob:
    src = (project_dir / seg.source).resolve()
    if not src.exists():
        raise EngineError(f"segment {seg.seg_index}: source {seg.source} not found")
    S = tl.settings
    still = seg.is_still
    sw, sh = source_size(src, still)
    job = NativeJob(seg=seg, src=src, mode="encode", src_w=sw, src_h=sh)
    if seg.camera_move is not None or still:
        if seg.camera_move is None:
            raise EngineError(f"segment {seg.seg_index}: still image needs a camera_move")
        job.mode = "camera"
        job.start_win, job.end_win, job.motion_px = camera_endpoints(seg, tl, sw, sh)
    elif copy_eligible(src, S.width, S.height, S.fps, seg_x264(tl)) and _keyframe_at(src, seg.source_in_frame / S.fps, S.fps):
        job.mode = "copy"
    if overlays.drawn(seg) and job.mode == "copy":
        job.mode = "encode"   # drawn overlays need a re-encode
    src_hash = blake3_files([src])[0]
    job.key = _key(job, tl, job.mode, src_hash)
    if job.mode == "copy":
        job.encode_key = _key(job, tl, "encode", src_hash)
    return job


def _key(job: NativeJob, tl: Timeline, mode: str, src_hash: str) -> str:
    """Cache key of the artifact this job would write in `mode` (the fallback from copy to encode included)."""
    seg, S = job.seg, tl.settings
    return blake3_json({
        "v": NATIVE_VERSION, "src": src_hash, "mode": mode, "in": seg.source_in_frame, "n": seg.n_frames,
        "cam": seg.camera_move.model_dump() if seg.camera_move else None, "W": S.width, "H": S.height, "fps": S.fps,
        "x264": seg_x264(tl), "threads": resources.X264_THREADS, "ffmpeg": toolchain.ffmpeg_version(config.FFMPEG),
        "code": code_hash(CODE), "algo": core.ALGO_VERSION, **({"copy": COPY_FORMAT} if mode == "copy" else {}),
        **({"ov": ov} if (ov := overlays.key_part(seg, profiles.of(tl).print)) else {}),
        **({"profile": pv} if (pv := profiles.of(tl).render_values("native")) else {}),
    })


def _overlay_args(seg: Segment, tl: Timeline, base_filter: str, png_dir: Path) -> tuple[list, list]:
    """(extra inputs, filter/map args) for input 0 filtered by base_filter, plus the drawn overlays of seg."""
    if not overlays.drawn(seg):
        return [], (["-vf", base_filter] if base_filter else [])
    inputs, chain = overlays.ffmpeg_parts(seg, tl, png_dir, first_input=1)
    return inputs, ["-filter_complex", f"[0:v]{base_filter or 'null'}[vbase];{chain}", "-map", "[vout]"]


def _encoder(out: Path, W: int, H: int, fps: int, n: int, threads: int, seg: Segment | None = None,
             tl: Timeline | None = None) -> subprocess.Popen:
    inputs, filt = (_overlay_args(seg, tl, BT709_VF, out.parent / "ov")
                    if seg is not None else ([], ["-vf", BT709_VF]))
    cmd = [config.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", *inputs, *filt, *seg_x264(tl), "-threads", str(threads), "-frames:v", str(n), "-an",
           "-movflags", "+faststart", str(out)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)


def _work_scale(job: NativeJob, W: int) -> float:
    """Decode scale so the most zoomed-in window still has >= output resolution (never upscale at decode)."""
    min_w = min(job.start_win[2], job.end_win[2])
    return min(1.0, max(W / min_w, 1e-3) * 1.02)


def _decode_cmd(job: NativeJob, ws: int, hs: int, fps: int) -> list:
    if job.seg.is_still:
        return [config.FFMPEG, "-v", "error", "-i", str(job.src), "-frames:v", "1",
                "-vf", f"scale={ws}:{hs}:flags=lanczos,format=rgb24", "-f", "rawvideo", "-"]
    t_in = job.seg.source_in_frame / fps
    return [config.FFMPEG, "-v", "error", "-ss", f"{t_in:.6f}", "-i", str(job.src), "-an",
            "-vf", f"fps={fps},scale={ws}:{hs}:flags=bicubic{_cs(job.src)},format=rgb24", "-frames:v", str(job.seg.n_frames),
            "-f", "rawvideo", "-"]


def _decoder_stderr(dec: subprocess.Popen) -> str:
    """Last 800 chars of a finished/stalled decoder's stderr (waits up to 5 s, then kills)."""
    try:
        dec.wait(timeout=5)
    except subprocess.TimeoutExpired:
        dec.kill()
        dec.wait()
    err = dec.stderr.read().decode(errors="replace").strip() if dec.stderr else ""
    return err[-800:] or f"decoder exit code {dec.returncode}, no stderr"


def render_camera(job: NativeJob, tl: Timeline, out: Path, threads: int) -> None:
    S = tl.settings
    W, H, n = S.width, S.height, job.seg.n_frames
    s = _work_scale(job, W)
    ws, hs = max(2, round(job.src_w * s)), max(2, round(job.src_h * s))
    fx, fy = ws / job.src_w, hs / job.src_h
    sc = lambda w: (w[0] * fx, w[1] * fy, w[2] * fx, w[3] * fy)
    wins = core.camera_windows(sc(job.start_win), sc(job.end_win), n)
    fsz = ws * hs * 3
    dec = subprocess.Popen(_decode_cmd(job, ws, hs, S.fps), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    enc = _encoder(out, W, H, S.fps, n, threads, job.seg, tl)
    try:
        frame = None
        for i in range(n):
            if i == 0 or not job.seg.is_still:
                buf = dec.stdout.read(fsz)
                if len(buf) == fsz:
                    frame = buf
                elif frame is None:
                    raise EngineError(f"segment {job.seg.seg_index}: could not decode {job.src.name}: "
                                      f"{_decoder_stderr(dec)}")
                # short source: hold the last frame (pre-flight M01 flags this before render)
            enc.stdin.write(core.warp_frame(frame, ws, hs, wins[i], W, H))
        enc.stdin.close()
        if enc.wait() != 0:
            raise EngineError(f"encoder failed: {enc.stderr.read().decode()[-800:]}")
    finally:
        dec.kill()
        dec.wait()
        if enc.poll() is None:
            enc.kill()


def render_encode(job: NativeJob, tl: Timeline, out: Path, threads: int) -> None:
    S = tl.settings
    t_in = job.seg.source_in_frame / S.fps
    vf = (f"fps={S.fps},scale={S.width}:{S.height}:force_original_aspect_ratio=increase:flags=bicubic{_cs(job.src)}"
          f":out_color_matrix=bt709:out_range=limited,format=yuv420p,"
          f"crop={S.width}:{S.height},setsar=1,tpad=stop_mode=clone:stop=-1")
    inputs, filt = _overlay_args(job.seg, tl, vf, out.parent / "ov")
    run([config.FFMPEG, "-v", "error", "-y", "-ss", f"{t_in:.6f}", "-i", job.src, *inputs, "-an", *filt,
         *seg_x264(tl), "-threads", str(threads), "-frames:v", str(job.seg.n_frames), "-movflags", "+faststart", out])


def render_copy(job: NativeJob, tl: Timeline, out: Path) -> None:
    """Packets of the source from the keyframe at source_in; the mp4 edit list keeps the first frame at 0."""
    t_in = job.seg.source_in_frame / tl.settings.fps
    run([config.FFMPEG, "-v", "error", "-y", "-ss", f"{t_in:.6f}", "-i", job.src, "-map", "0:v:0", "-an", "-c", "copy",
         "-frames:v", str(job.seg.n_frames), out])


def _copy_ok(out: Path, job: NativeJob, tl: Timeline) -> bool:
    """A copied segment is kept when it still has the engine format, n frames from 0 and lasts n / fps."""
    S = tl.settings
    if not copy_eligible(out, S.width, S.height, S.fps, seg_x264(tl)):
        return False
    try:
        streams.verify(out, job.seg.n_frames, S.fps, "copy")
    except EngineError:
        return False
    return True


def render(job: NativeJob, tl: Timeline, workdir: Path, threads: int = 3, use_cache: bool = True) -> tuple[Path, bool, str]:
    """Returns (segment_path, cache_hit, mode_used). Output always has exactly seg.n_frames frames."""
    if use_cache and (hit := cache.get_file("segments", job.key)):
        return hit, True, job.mode
    out = workdir / "seg" / f"seg{job.seg.seg_index:03d}_native.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    mode = job.mode
    if mode == "copy":
        render_copy(job, tl, out)
        if not _copy_ok(out, job, tl):
            mode = "encode"   # not frame exact at this keyframe, or the copy changed a parameter: re-encode
            if use_cache and job.encode_key and (hit := cache.get_file("segments", job.encode_key)):
                return hit, True, mode
    key = job.encode_key if (job.mode == "copy" and mode == "encode") else job.key
    if mode == "encode":
        render_encode(job, tl, out, threads)
    elif mode == "camera":
        render_camera(job, tl, out, threads)
    streams.verify(out, job.seg.n_frames, tl.settings.fps, f"segment {job.seg.seg_index}")
    return (cache.put_file("segments", key, out) if use_cache else out), False, mode


def preview_frame(job: NativeJob, tl: Timeline, frame_idx: int, out_png: Path) -> Path:
    """One output frame of a native segment without rendering the segment (outside any queue)."""
    from PIL import Image
    S = tl.settings
    n = job.seg.n_frames
    i = max(0, min(n - 1, frame_idx))
    if job.mode != "camera":
        t = (job.seg.source_in_frame + i) / S.fps
        vf = f"scale={S.width}:{S.height}:force_original_aspect_ratio=increase{_cs(job.src)},crop={S.width}:{S.height}"
        cmd = [config.FFMPEG, "-v", "error", "-ss", f"{t:.6f}", "-i", str(job.src), "-frames:v", "1", "-vf",
               vf + ",format=rgb24", "-f", "rawvideo", "-"]
        buf = subprocess.run(cmd, capture_output=True, check=True).stdout[: S.width * S.height * 3]
        rgb = overlays.composite_rgb(buf, S.width, S.height, job.seg, i, pr=profiles.of(tl).print)
        Image.frombytes("RGB", (S.width, S.height), rgb).save(out_png)
        return out_png
    s = _work_scale(job, S.width)
    ws, hs = max(2, round(job.src_w * s)), max(2, round(job.src_h * s))
    fx, fy = ws / job.src_w, hs / job.src_h
    sc = lambda w: (w[0] * fx, w[1] * fy, w[2] * fx, w[3] * fy)
    win = core.camera_windows(sc(job.start_win), sc(job.end_win), n)[i]
    if job.seg.is_still:
        cmd = _decode_cmd(job, ws, hs, S.fps)
    else:
        t = (job.seg.source_in_frame + i) / S.fps
        cmd = [config.FFMPEG, "-v", "error", "-ss", f"{t:.6f}", "-i", str(job.src), "-frames:v", "1",
               "-vf", f"scale={ws}:{hs}{_cs(job.src)},format=rgb24", "-f", "rawvideo", "-"]
    buf = subprocess.run(cmd, capture_output=True, check=True).stdout[: ws * hs * 3]
    rgb = overlays.composite_rgb(bytes(core.warp_frame(buf, ws, hs, win, S.width, S.height)), S.width, S.height, job.seg, i,
                                 pr=profiles.of(tl).print)
    Image.frombytes("RGB", (S.width, S.height), rgb).save(out_png)
    return out_png


def snapshot_rgb(job: NativeJob, tl: Timeline, frame_idx: int, ow: int, oh: int) -> bytes:
    """One output frame of a native segment as RGB24 bytes at ow x oh (gate snapshots; no full-size decode)."""
    S = tl.settings
    i = max(0, min(job.seg.n_frames - 1, frame_idx))
    if job.mode != "camera":
        t = (job.seg.source_in_frame + i) / S.fps
        vf = f"scale={ow}:{oh}:force_original_aspect_ratio=increase{_cs(job.src)},crop={ow}:{oh},format=rgb24"
        cmd = [config.FFMPEG, "-v", "error", "-ss", f"{t:.6f}", "-i", str(job.src), "-frames:v", "1", "-vf", vf,
               "-f", "rawvideo", "-"]
        buf = subprocess.run(cmd, capture_output=True, check=True).stdout[: ow * oh * 3]
        return overlays.composite_rgb(buf, ow, oh, job.seg, i, ow / S.width, profiles.of(tl).print)
    win = core.camera_windows(job.start_win, job.end_win, job.seg.n_frames)[i]
    s = min(1.0, ow / max(1.0, win[2]) * 1.02)
    ws, hs = max(2, round(job.src_w * s)), max(2, round(job.src_h * s))
    fx, fy = ws / job.src_w, hs / job.src_h
    if job.seg.is_still:
        cmd = [config.FFMPEG, "-v", "error", "-i", str(job.src), "-frames:v", "1",
               "-vf", f"scale={ws}:{hs}:flags=bilinear,format=rgb24", "-f", "rawvideo", "-"]
    else:
        t = (job.seg.source_in_frame + i) / S.fps
        cmd = [config.FFMPEG, "-v", "error", "-ss", f"{t:.6f}", "-i", str(job.src), "-frames:v", "1",
               "-vf", f"scale={ws}:{hs}{_cs(job.src)},format=rgb24", "-f", "rawvideo", "-"]
    buf = subprocess.run(cmd, capture_output=True, check=True).stdout[: ws * hs * 3]
    rgb = bytes(core.warp_frame(buf, ws, hs, (win[0] * fx, win[1] * fy, win[2] * fx, win[3] * fy), ow, oh))
    return overlays.composite_rgb(rgb, ow, oh, job.seg, i, ow / S.width, profiles.of(tl).print)
