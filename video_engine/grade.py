"""Colour grade: a full-frame grade of a segment's picture (captions are burned in later and stay ungraded).

A grade is lift / gamma / gain (per channel or one value), saturation, a tint, an optional 3D .cube LUT and a strength;
it is baked into one 33-point 3D LUT and applied by ffmpeg lut3d (tetrahedral) to the rendered segment file, per frame
range: the beat's grade, else the segment's, else profile [grade]. The graded file is cached (kind `segments`) under
the source file's content hash, the baked LUTs, their frame ranges and the encoder settings."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import cache, config, toolchain
from .util import EngineError, blake3_files, blake3_json, code_hash, run

if TYPE_CHECKING:
    from .timeline import Segment, Timeline

GRADE_VERSION = "grade-1"
N = 33                                      # baked LUT points per axis
LUMA = np.array([0.2126, 0.7152, 0.0722])   # Rec. 709
X264_THREADS = 2                            # = resources.X264_THREADS: x264 output depends on its thread count
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

Chan = Union[float, tuple[float, float, float]]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Tint(_Strict):
    color: str                               # "#rrggbb": hue and chroma move toward it at the same luminance (clipped)
    amount: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _c(self):
        if not HEX.match(self.color):
            raise ValueError(f"tint color {self.color!r} must be #rrggbb")
        if float(_rgb(self.color) @ LUMA) < 0.05:
            raise ValueError(f"tint color {self.color} is too dark to carry a luminance (Rec. 709 luma < 0.05)")
        return self


class Grade(_Strict):
    lift: Chan = 0.0                         # -0.5..0.5: raises (or lowers) the blacks, whites stay
    gamma: Chan = 1.0                        # 0.2..5: > 1 brightens the mid-tones
    gain: Chan = 1.0                         # 0..4: scales the whites
    saturation: float = Field(1.0, ge=0, le=3)
    tint: Optional[Tint] = None
    lut: Optional[str] = None                # .cube 3D LUT after the values above (segment / beat: project-relative)
    strength: float = Field(1.0, ge=0, le=1)  # mix of the graded picture over the ungraded one

    @model_validator(mode="after")
    def _r(self):
        for name, lo, hi in (("lift", -0.5, 0.5), ("gamma", 0.2, 5.0), ("gain", 0.0, 4.0)):
            for v in np.atleast_1d(getattr(self, name)):
                if not lo <= v <= hi:
                    raise ValueError(f"grade {name} {v} outside {lo}..{hi}")
        if self.lut is not None and not self.lut.lower().endswith(".cube"):
            raise ValueError(f"grade lut {self.lut!r} must be a .cube file")
        return self

    @property
    def identity(self) -> bool:
        return self.strength == 0 or (self == Grade(strength=self.strength))


def _rgb(hex_: str) -> np.ndarray:
    return np.array([int(hex_[k:k + 2], 16) for k in (1, 3, 5)]) / 255.0


def read_cube(path: Path) -> np.ndarray:
    """3D .cube LUT as an array [b, g, r, 3] mapped to a 0-1 domain."""
    size, lo, hi, rows = None, np.zeros(3), np.ones(3), []
    try:
        text = Path(path).read_text(errors="replace")
    except OSError as e:
        raise EngineError(f"grade lut '{path}': {e}") from None
    for line in text.splitlines():
        t = line.split("#", 1)[0].strip()
        if not t:
            continue
        w = t.split()
        if w[0] == "LUT_1D_SIZE":
            raise EngineError(f"grade lut '{path}': 1D LUTs are not supported (LUT_3D_SIZE needed)")
        if w[0] == "LUT_3D_SIZE":
            size = int(w[1])
        elif w[0] == "DOMAIN_MIN":
            lo = np.array(w[1:4], float)
        elif w[0] == "DOMAIN_MAX":
            hi = np.array(w[1:4], float)
        elif w[0][0] in "+-.0123456789":
            rows.append([float(v) for v in w[:3]])
    if not size or not 2 <= size <= 256:
        raise EngineError(f"grade lut '{path}': LUT_3D_SIZE missing or outside 2..256")
    if len(rows) != size ** 3:
        raise EngineError(f"grade lut '{path}': {len(rows)} entries, LUT_3D_SIZE {size} needs {size ** 3}")
    if np.any(hi <= lo):
        raise EngineError(f"grade lut '{path}': DOMAIN_MAX must exceed DOMAIN_MIN")
    return (np.array(rows).reshape(size, size, size, 3) - lo) / (hi - lo)


def sample(lut: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Trilinear lookup of RGB values x (..., 3) in 0-1 through lut [b, g, r, 3]."""
    n = lut.shape[0]
    p = np.clip(x, 0, 1) * (n - 1)
    i = np.minimum(p.astype(int), n - 2)
    f = p - i
    r, g, b = i[..., 0], i[..., 1], i[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0
    for db, wb in ((0, 1 - fb), (1, fb)):
        for dg, wg in ((0, 1 - fg), (1, fg)):
            for dr, wr in ((0, 1 - fr), (1, fr)):
                out = out + lut[b + db, g + dg, r + dr] * (wb * wg * wr)
    return out


def apply_values(g: Grade, x: np.ndarray, lut_root: Path) -> np.ndarray:
    """The grade of RGB values x (..., 3) in 0-1."""
    lift, gamma, gain = (np.broadcast_to(np.asarray(v, float), (3,)) for v in (g.lift, g.gamma, g.gain))
    y = np.clip(gain * (x + lift * (1 - x)), 0, 1) ** (1 / gamma)
    lum = (y @ LUMA)[..., None]
    y = np.clip(lum + g.saturation * (y - lum), 0, 1)
    if g.tint and g.tint.amount:
        c = _rgb(g.tint.color)
        toned = np.clip((y @ LUMA)[..., None] * c / float(c @ LUMA), 0, 1)
        y = y * (1 - g.tint.amount) + toned * g.tint.amount
    if g.lut:
        y = sample(read_cube(lut_root / g.lut), y)
    return np.clip(x * (1 - g.strength) + y * g.strength, 0, 1)


def cube_text(g: Grade, lut_root: Path) -> str:
    """The grade baked into an N-point 3D .cube (red fastest)."""
    v = np.linspace(0, 1, N)
    b, gg, r = np.meshgrid(v, v, v, indexing="ij")
    y = apply_values(g, np.stack([r, gg, b], -1), lut_root).reshape(-1, 3)
    return f"LUT_3D_SIZE {N}\n" + "".join(f"{a:.6f} {c:.6f} {d:.6f}\n" for a, c, d in y)


def ranges(seg: "Segment", tl: "Timeline", project_dir: Path) -> list[tuple[int, int, Grade, Path]]:
    """[(first frame, end frame exclusive, grade, LUT root)] of the non-identity grades of seg, adjacent equal ones
    merged: per beat its grade, else the segment's, else profile [grade]."""
    from . import profiles
    pg = profiles.of(tl).grade
    base = (seg.grade, Path(project_dir)) if seg.grade is not None else (pg, config.PROFILES_DIR)
    n = seg.n_frames
    sc = seg.gsap_scene_config
    beats = (sc.beats or []) if sc is not None else []
    parts = []
    if beats:
        if beats[0].at_frame > 0:
            parts.append((0, beats[0].at_frame, *base))
        for i, b in enumerate(beats):
            end = beats[i + 1].at_frame if i + 1 < len(beats) else n
            parts.append((b.at_frame, min(end, n), *((b.grade, Path(project_dir)) if b.grade is not None else base)))
    else:
        parts.append((0, n, *base))
    out = []
    for a, e, g, root in parts:
        if a >= e or g is None or g.identity:
            continue
        if out and out[-1][1] == a and out[-1][2] == g and out[-1][3] == root:
            out[-1] = (out[-1][0], e, g, root)
        else:
            out.append((a, e, g, root))
    return out


def luts(seg: "Segment", tl: "Timeline", project_dir: Path) -> list[Path]:
    """Every LUT file the grades of seg read."""
    return sorted({root / g.lut for _, _, g, root in ranges(seg, tl, project_dir) if g.lut})


def check(seg: "Segment", tl: "Timeline", project_dir: Path) -> list[str]:
    """Problems of seg's grades (unreadable LUTs); empty when it can be graded."""
    errs = []
    for p in luts(seg, tl, project_dir):
        try:
            read_cube(p)
        except EngineError as e:
            errs.append(str(e))
    return errs


def _key(seg: "Segment", rs: list, cubes: list[str], src: Path) -> str:
    return blake3_json({"v": GRADE_VERSION, "code": code_hash([__file__]), "src": blake3_files([src])[0], "n": seg.n_frames,
                        "ranges": [[a, e, blake3_json(c)] for (a, e, _, _), c in zip(rs, cubes)],
                        "x264": config.X264, "threads": X264_THREADS, "ffmpeg": toolchain.ffmpeg_version(config.FFMPEG)})


def key(seg: "Segment", tl: "Timeline", project_dir: Path, src: Path) -> str | None:
    """Cache key of the graded file of rendered segment file src; None when seg has no grade."""
    rs = ranges(seg, tl, project_dir)
    return _key(seg, rs, [cube_text(g, root) for _, _, g, root in rs], src) if rs else None


def apply(seg: "Segment", tl: "Timeline", project_dir: Path, src: Path, workdir: Path,
          use_cache: bool = True) -> tuple[Path, bool]:
    """(graded file, cache hit) of rendered segment file src; src itself when seg has no grade."""
    rs = ranges(seg, tl, project_dir)
    if not rs:
        return Path(src), False
    cubes = [cube_text(g, root) for _, _, g, root in rs]
    key = _key(seg, rs, cubes, src)
    if use_cache and (hit := cache.get_file("segments", key)):
        return hit, True
    wd = Path(workdir) / f"grade_{seg.seg_index}"
    wd.mkdir(parents=True, exist_ok=True)
    try:
        chain = []
        for i, ((a, e, _, _), c) in enumerate(zip(rs, cubes)):
            f = wd / f"g{i}.cube"
            f.write_text(c)
            on = "" if (a, e) == (0, seg.n_frames) else f":enable='between(n,{a},{e - 1})'"
            chain.append(f"lut3d=file={f}:interp=tetrahedral{on}")
        # chroma up by nearest and back down by area: an ungraded frame comes back within 1 code value (~49 dB)
        vf = ("scale=in_color_matrix=bt709:in_range=tv:flags=neighbor+accurate_rnd,format=gbrp16le,"
              + ",".join(chain) + ",scale=out_color_matrix=bt709:out_range=tv:flags=area+accurate_rnd,format=yuv420p")
        out = wd / "graded.mp4"
        run([config.FFMPEG, "-v", "error", "-y", "-i", src, "-an", "-vf", vf, *config.X264, "-threads",
             str(X264_THREADS), "-frames:v", str(seg.n_frames), "-movflags", "+faststart", out])
        if use_cache:
            return cache.put_file("segments", key, out), False
        keep = Path(workdir) / f"graded_{seg.seg_index}.mp4"
        shutil.move(out, keep)
        return keep, False
    finally:
        shutil.rmtree(wd, ignore_errors=True)
