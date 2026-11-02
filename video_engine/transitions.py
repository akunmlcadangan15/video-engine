"""Segment-boundary transitions (profile [transitions]; absent or all "cut" = hard cuts, no clip). A transition is a
short RGB clip drawn from the decoded segment files and laid over the concatenated segments by the final mux at its
first frame, under the captions and the watermark.

crossfade at boundary b: frames [b - n // 2, b - n // 2 + n), n = fade_s x fps. A dissolve from the outgoing to the
incoming shot (smoothstep weights (k + 1) / (n + 1)); segments do not overlap in time, so before b the incoming shot
is its first frame and from b the outgoing shot is its last frame (both segments keep their own timing).
state at boundary b: frames [b - n // 2, b - n // 2 + n), n = state_s x fps. A very short dissolve used only for
map -> map boundaries whose two segments share the same base map (a state change on one map, not a cut).
zoom_whip at boundary b: frames [b - whip_out_s, b + whip_in_s). The outgoing shot pushes in from 1 to whip_scale
(ease in) with a radial blur rising to whip_blur; the incoming shot settles from whip_scale to 1 (ease out cubic,
1 on the window's last frame) with the blur fading over its first whip_blur_in_s.
page_turn at boundary b: frames [b, b + page_s). The outgoing last frame lies over the incoming shot and folds away
from the bottom-right corner along a straight fold (power2.inOut); the folded flap shows the page back (lightened
mirror), the incoming shot is shaded along the fold with the shade fading as the page turns; the window's last frame
is the incoming frame.
Clip frame k lies over output frame first + k exactly (overlay_graph)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import hypoverum_core_rs as core

from . import config, profiles
from .timeline import HYPERFRAMES, Timeline
from .util import EngineError, decode_colour

TRANSITION_VERSION = "trans-1"   # bump when a transition clip's pixels change without a source-code change
BLUR_TAPS = 8          # zoomed copies averaged for the radial blur
PAGE_BACK = 0.72       # flap colour = PAGE_BACK x paper + (1 - PAGE_BACK) x mirrored page
PAGE_PAPER = (243, 236, 222)
PAGE_SHADE = 0.35      # incoming-shot darkening at the fold
PAGE_SHADE_PX = 48     # shade fall-off from the fold (px at 1080 wide)
PAGE_DIR = (-0.94, -0.34)   # fold travel: from the bottom-right corner towards the top-left


@dataclass
class Window:
    boundary: int     # first frame of the incoming segment
    seg: int          # incoming segment index
    kind: str         # cut | crossfade | state | zoom_whip | page_turn
    f0: int           # first frame drawn by the transition
    f1: int           # end frame (exclusive)


def _base_map(s) -> str | None:
    """Base map asset of a HyperFrames map segment (globe: its equirectangular texture), else None."""
    if s.engine_type != HYPERFRAMES or s.gsap_scene_config is None:
        return None
    return s.gsap_scene_config.map_asset


def same_base_map(a, b) -> bool:
    """Both sides are map segments built on the same base map asset."""
    ma, mb = _base_map(a), _base_map(b)
    return ma is not None and ma == mb


def kind_at(tl: Timeline, i: int) -> str:
    """Transition into segment i (i >= 1). A segment's explicit `transition_in` wins (page_turn still needs the
    profile's page_turn = true; state needs both sides on the same base map). Otherwise same_type[kind] when both
    sides share it, else by_type[incoming kind], else by_type[outgoing kind], else kind. A state rule that does not
    qualify (different maps) falls back to a hard cut."""
    T = profiles.of(tl).transitions
    a, b = tl.segments[i - 1], tl.segments[i]
    if b.transition_in is not None:
        k = b.transition_in
        if k == "page_turn" and not T.page_turn:
            raise EngineError(f"segment {b.seg_index}: transition_in 'page_turn' is off in this profile")
        if k == "state" and not same_base_map(a, b):
            raise EngineError(f"segment {b.seg_index}: transition_in 'state' needs two map segments on the same "
                              "map_asset")
        return k
    if a.kind == b.kind and a.kind in T.same_type:
        k = T.same_type[a.kind]
    else:
        k = T.by_type.get(b.kind) or T.by_type.get(a.kind) or T.kind
    if k == "state" and not same_base_map(a, b):
        return "cut"
    return k


def _frames(s: float, fps: int) -> int:
    return max(1, round(s * fps))


def windows(tl: Timeline) -> list[Window]:
    """Every non-cut boundary with its frame window (empty when the profile draws no transitions)."""
    T = profiles.of(tl).transitions
    explicit = any(s.transition_in not in (None, "cut") for s in tl.segments[1:])
    if not T.active() and not explicit:
        return []
    fps, out = tl.settings.fps, []
    for i in range(1, len(tl.segments)):
        k = kind_at(tl, i)
        b = tl.segments[i].start_frame
        if k in ("crossfade", "state"):
            n = _frames(T.fade_s if k == "crossfade" else T.state_s, fps)
            out.append(Window(b, i, k, b - n // 2, b - n // 2 + n))
        elif k == "zoom_whip":
            out.append(Window(b, i, k, b - _frames(T.whip_out_s, fps), b + _frames(T.whip_in_s, fps)))
        elif k == "page_turn":
            out.append(Window(b, i, k, b, b + _frames(T.page_s, fps)))
    return out


def fit_problems(tl: Timeline) -> list[tuple[int, str]]:
    """(segment, message) for every transition window that leaves its two segments or overlaps the next window."""
    out, ws = [], windows(tl)
    for w in ws:
        a, b = tl.segments[w.seg - 1], tl.segments[w.seg]
        if w.f0 < a.start_frame or w.f1 > b.end_frame:
            out.append((w.seg, f"{w.kind} frames {w.f0}-{w.f1} do not fit segments {a.seg_index} "
                               f"({a.start_frame}-{a.end_frame}) and {b.seg_index} ({b.start_frame}-{b.end_frame})"))
    for x, y in zip(ws, ws[1:]):
        if y.f0 < x.f1:
            out.append((y.seg, f"{y.kind} frames {y.f0}-{y.f1} overlap the {x.kind} frames {x.f0}-{x.f1}"))
    return out


# ------------------------------------------------------------------------------------------------ drawing

def _ease_in_cubic(t: float) -> float:
    return t ** 3


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _power2_in_out(t: float) -> float:
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def zoom(rgb: np.ndarray, scale: float) -> np.ndarray:
    """Frame pushed in by `scale` about its centre (bilinear, same size)."""
    H, W = rgb.shape[:2]
    if abs(scale - 1) < 1e-6:
        return rgb
    w, h = W / scale, H / scale
    win = ((W - w) / 2, (H - h) / 2, w, h)
    out = core.warp_frame(np.ascontiguousarray(rgb).tobytes(), W, H, win, W, H)
    return np.frombuffer(bytes(out), np.uint8).reshape(H, W, 3)


def radial_blur(rgb: np.ndarray, scale: float, blur: float) -> np.ndarray:
    """`rgb` pushed in by `scale` with a zoom blur: the mean of BLUR_TAPS copies at scale x (1 + blur x k / taps)."""
    if blur <= 1e-4:
        return zoom(rgb, scale)
    acc = np.zeros(rgb.shape, np.float32)
    for k in range(BLUR_TAPS):
        acc += zoom(rgb, scale * (1 + blur * k / (BLUR_TAPS - 1)))
    return np.clip(acc / BLUR_TAPS + 0.5, 0, 255).astype(np.uint8)


def whip_frames(tail: list[np.ndarray], head: list[np.ndarray], T: "profiles.TransitionsProfile", fps: int) -> list[np.ndarray]:
    """zoom_whip frames: the outgoing `tail` frames, then the incoming `head` frames."""
    out, n_out, n_in = [], len(tail), len(head)
    nb = round(T.whip_blur_in_s * fps)
    for i, fr in enumerate(tail):
        p = _ease_in_cubic((i + 1) / n_out)
        out.append(radial_blur(fr, 1 + (T.whip_scale - 1) * p, T.whip_blur * (i + 1) / n_out))
    for j, fr in enumerate(head):
        q = _ease_out_cubic((j + 1) / n_in)
        blur = T.whip_blur * (1 - j / nb) if j < nb else 0.0
        out.append(radial_blur(fr, T.whip_scale - (T.whip_scale - 1) * q, blur))
    return out


def _smoothstep(t: float) -> float:
    return t * t * (3 - 2 * t)


def fade_frames(tail: list[np.ndarray], a_last: np.ndarray, b_first: np.ndarray, head: list[np.ndarray]) -> list[np.ndarray]:
    """crossfade frames: the outgoing `tail` (frames before the boundary) then its last frame held, dissolved into the
    incoming first frame held, then its `head` (frames from the boundary)."""
    n = len(tail) + len(head)
    A = tail + [a_last] * len(head)
    B = [b_first] * len(tail) + head
    out = []
    for k in range(n):
        w = _smoothstep((k + 1) / (n + 1))
        out.append(np.clip(A[k].astype(np.float32) * (1 - w) + B[k].astype(np.float32) * w + 0.5, 0, 255).astype(np.uint8))
    return out


def page_frame(page: np.ndarray, under: np.ndarray, d: float, shade_amt: float = PAGE_SHADE) -> np.ndarray:
    """One page_turn frame: `page` folded along the line at distance d (px) from the bottom-right corner along
    PAGE_DIR, `under` showing where the page has turned away, darkened by up to `shade_amt` at the fold."""
    H, W = page.shape[:2]
    ux, uy = PAGE_DIR
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    s = (xx - (W - 1)) * ux + (yy - (H - 1)) * uy          # distance from the corner along the fold travel
    out = under.astype(np.float32)
    turned = s <= d
    gap = np.clip(d - s, 0, None)
    shade = 1 - shade_amt * np.exp(-gap / (PAGE_SHADE_PX * W / 1080))
    out *= np.where(turned, shade, 1.0)[..., None]
    rest = ~turned
    out[rest] = page[rest]
    rx = np.rint(xx - 2 * (s - d) * ux).astype(np.int64)  # flap: the turned part mirrored over the fold
    ry = np.rint(yy - 2 * (s - d) * uy).astype(np.int64)
    flap = rest & (rx >= 0) & (rx < W) & (ry >= 0) & (ry < H)
    back = page[ry[flap], rx[flap]].astype(np.float32)
    out[flap] = PAGE_BACK * np.array(PAGE_PAPER, np.float32) + (1 - PAGE_BACK) * back
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def page_frames(last: np.ndarray, head: list[np.ndarray]) -> list[np.ndarray]:
    """page_turn frames over the incoming `head`: the fold travels from the corner to the far corner (the last frame
    is the incoming frame untouched); the shade at the fold fades with the turn."""
    H, W = last.shape[:2]
    ux, uy = PAGE_DIR
    far = -(W - 1) * ux - (H - 1) * uy
    n = len(head)
    e = [_power2_in_out((j + 1) / n) for j in range(n)]
    return [page_frame(last, fr, far * e[j], PAGE_SHADE * (1 - e[j])) for j, fr in enumerate(head)]


# ------------------------------------------------------------------------------------------------ files

def decode(path: Path, first: int, count: int, W: int, H: int, threads: int = 2) -> list[np.ndarray]:
    """Frames [first, first + count) of a segment file as RGB arrays."""
    cmd = [config.FFMPEG, "-v", "error", "-threads", str(threads), "-i", str(path),
           "-vf", f"select=gte(n\\,{first}),scale={W}:{H}{(':' + o) if (o := decode_colour(path)) else ''}",
           "-frames:v", str(count), "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    buf = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(buf) // (W * H * 3)
    if n != count:
        raise EngineError(f"transition: {path.name} gave {n} frames from frame {first}, expected {count}")
    return [np.frombuffer(buf, np.uint8, W * H * 3, k * W * H * 3).reshape(H, W, 3) for k in range(n)]


def write_clip(frames: list[np.ndarray], out: Path, fps: int, threads: int = 2) -> Path:
    """Lossless clip (FFV1, RGB) of the frames. Bit-exact muxing (no random Matroska UIDs, no encoder tags): equal
    frames give an equal file, so the composite key (which hashes this file) is stable across builds."""
    H, W = frames[0].shape[:2]
    cmd = [config.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-c:v", "ffv1", *(["-threads", str(threads)] if threads else []),
           "-pix_fmt", "bgr0", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-map_metadata", "-1", str(out)]
    subprocess.run(cmd, input=b"".join(np.ascontiguousarray(f).tobytes() for f in frames), check=True,
                   capture_output=True)
    return out


def plan_key(tl: Timeline, seg_files: list[Path]) -> str:
    """Content key of every transition clip of this timeline and these segment files: cache lookup of the composite
    pass can happen before any clip is rendered (empty string when the profile draws no transition)."""
    from .manifest import sha256
    from .util import blake3_json, code_hash
    ws = windows(tl)
    if not ws:
        return ""
    S = tl.settings
    used = sorted({w.seg - 1 for w in ws} | {w.seg for w in ws})
    return blake3_json({"v": TRANSITION_VERSION, "code": code_hash([Path(__file__)]), "algo": core.ALGO_VERSION,
                        "windows": [[w.boundary, w.seg, w.kind, w.f0, w.f1] for w in ws],
                        "profile": profiles.of(tl).transitions.model_dump(mode="json"),
                        "segments": [sha256(seg_files[i]) for i in used],
                        "size": [S.width, S.height], "fps": S.fps})


def render(tl: Timeline, seg_files: list[Path], workdir: Path, threads: int = 2) -> list[tuple[Path, int]]:
    """(clip, first frame) of every transition, drawn from the segment files (one per segment, in order)."""
    if probs := fit_problems(tl):
        raise EngineError("transitions: " + "; ".join(m for _, m in probs))
    S, T = tl.settings, profiles.of(tl).transitions
    W, H, fps = S.width, S.height, S.fps
    out = []
    for w in windows(tl):
        a = tl.segments[w.seg - 1]
        if w.kind in ("crossfade", "state"):
            n_out, n_in = w.boundary - w.f0, w.f1 - w.boundary
            tail = decode(seg_files[w.seg - 1], a.n_frames - n_out, n_out, W, H, threads)
            a_last = decode(seg_files[w.seg - 1], a.n_frames - 1, 1, W, H, threads)[0]
            head = decode(seg_files[w.seg], 0, n_in, W, H, threads)
            frames = fade_frames(tail, a_last, head[0], head)
        elif w.kind == "zoom_whip":
            n_out = w.boundary - w.f0
            tail = decode(seg_files[w.seg - 1], a.n_frames - n_out, n_out, W, H, threads)
            head = decode(seg_files[w.seg], 0, w.f1 - w.boundary, W, H, threads)
            frames = whip_frames(tail, head, T, fps)
        else:
            last = decode(seg_files[w.seg - 1], a.n_frames - 1, 1, W, H, threads)[0]
            frames = page_frames(last, decode(seg_files[w.seg], 0, w.f1 - w.f0, W, H, threads))
        out.append((write_clip(frames, workdir / f"transition_{w.seg:03d}.mkv", fps, threads), w.f0))
    return out


def overlay_graph(clips: list[tuple[Path, int]], first_input: int, fps: int, src: str = "[0:v]") -> tuple[str, str]:
    """(filter chain, output label) laying every clip over `src` from its first frame (input indices from
    first_input, in clip order). Clip frame k gets timestamp f0 + k in a 1/fps time base, so it covers output frame
    f0 + k whatever the time bases of the clip file and of `src`. Empty chain when there are no clips.
    The RGB clip is converted on its own to BT.709 limited-range (config.OVERLAY_YUV_VF) and blended with
    overlay=format=yuv420: an auto-negotiated overlay converts the clip with a BT.601 matrix, so saturated colours
    jumped by ~15 levels for the length of every transition."""
    parts, cur = [], src
    for j, (_, f0) in enumerate(clips):
        parts.append(f"[{first_input + j}:v]settb=1/{fps},setpts=N+{f0},format=rgba,{config.OVERLAY_YUV_VF}[tr{j}];"
                     f"{cur}[tr{j}]overlay=eof_action=pass:repeatlast=0:format=yuv420[tv{j}]")
        cur = f"[tv{j}]"
    return ";".join(parts), cur
