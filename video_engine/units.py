"""Reference canvas and scale: one place that says how profile px become output px.

Profile and gate lengths are written in px at a *reference canvas*: 1080x1920 for portrait (Shorts) and 1920x1080 for
landscape. The orientation comes from the frame (`width > height` is landscape; square counts as portrait), so a
timeline needs no new field.

    u = units(tl)          # or units(settings) / Units.of(W, H)
    u.x(v), u.y(v)         # a reference px along x / y -> output px: v * W / ref_w, v * H / ref_h
    u.k                    # uniform scale W / ref_w (== H / ref_h for an exact 9:16 or 16:9 frame)
    u.safe()               # R63 safe area in output px: (sx, top, bottom)

`x` and `y` evaluate with the same operands and order as the legacy `v * W / 1080`, `v * H / 1920`, so portrait
numbers stay bit-identical. A frame whose aspect differs from its reference by more than `ASPECT_TOL` is scaled
non-uniformly (as before) and gets the advisory finding U01.
"""
from __future__ import annotations

from dataclasses import dataclass

PORTRAIT, LANDSCAPE = "portrait", "landscape"
ORIENTATIONS = (PORTRAIT, LANDSCAPE)
PORTRAIT_REF = (1080, 1920)
LANDSCAPE_REF = (1920, 1080)
REF = {PORTRAIT: PORTRAIT_REF, LANDSCAPE: LANDSCAPE_REF}
# R63 safe area (sides, top, bottom) in px at the reference canvas. Landscape: 5 % of each side (title safe).
SAFE = {PORTRAIT: (60, 100, 100), LANDSCAPE: (96, 54, 54)}
ASPECT_TOL = 0.01


def orientation_of(width: int, height: int) -> str:
    return LANDSCAPE if width > height else PORTRAIT


def orientation(obj) -> str:
    """Orientation of a timeline, settings object or (W, H) pair."""
    W, H = _size(obj)
    return orientation_of(W, H)


def ref_canvas(obj) -> tuple[int, int]:
    return REF[orientation(obj)]


def _size(obj) -> tuple[int, int]:
    if isinstance(obj, tuple):
        return int(obj[0]), int(obj[1])
    s = getattr(obj, "settings", obj)
    return int(s.width), int(s.height)


@dataclass(frozen=True)
class Units:
    W: int
    H: int
    orientation: str
    ref_w: int
    ref_h: int

    @classmethod
    def of(cls, W: int, H: int) -> "Units":
        o = orientation_of(W, H)
        rw, rh = REF[o]
        return cls(int(W), int(H), o, rw, rh)

    @property
    def landscape(self) -> bool:
        return self.orientation == LANDSCAPE

    @property
    def kx(self) -> float:
        return self.W / self.ref_w

    @property
    def ky(self) -> float:
        return self.H / self.ref_h

    @property
    def k(self) -> float:
        """Uniform scale for lengths (short side / 1080 for an exact 9:16 or 16:9 frame)."""
        return self.W / self.ref_w

    def x(self, v: float) -> float:
        return v * self.W / self.ref_w

    def y(self, v: float) -> float:
        return v * self.H / self.ref_h

    def px(self, v: float) -> float:
        """A length (font size, stroke, gap) at the reference canvas -> output px, by the uniform scale."""
        return v * self.W / self.ref_w

    def box(self, b) -> tuple[float, float, float, float]:
        """[x, y, w, h] at the reference canvas -> output px."""
        return (self.x(b[0]), self.y(b[1]), self.x(b[2]), self.y(b[3]))

    def safe_ref(self) -> tuple[int, int, int]:
        return SAFE[self.orientation]

    def safe(self) -> tuple[float, float, float]:
        """(sx, top, bottom) of the R63 safe area in output px; x in [sx, W - sx], y in [top, bottom]."""
        sx, st, sb = SAFE[self.orientation]
        return sx * self.W / self.ref_w, st * self.H / self.ref_h, self.H - sb * self.H / self.ref_h

    def aspect_error(self) -> float:
        """Relative difference between the frame aspect and the reference aspect (0 for exact 9:16 / 16:9)."""
        return abs((self.W / self.H) / (self.ref_w / self.ref_h) - 1.0)

    def uniform(self) -> bool:
        return self.aspect_error() <= ASPECT_TOL

    def ref_str(self) -> str:
        return f"{self.ref_w}x{self.ref_h}"

    def scene(self) -> dict:
        """What a HyperFrames scene needs so JS never recomputes W/1080 (window.SCENE.k / .ref / .safe)."""
        sx, st, sb = self.safe()
        return {"k": self.k, "kx": self.kx, "ky": self.ky, "orientation": self.orientation,
                "ref": [self.ref_w, self.ref_h], "safe": {"x": sx, "top": st, "bottom": sb}}


def units(obj) -> Units:
    """Units of a timeline, a settings object, or a (W, H) pair."""
    W, H = _size(obj)
    return Units.of(W, H)


def scale_height(W: int, H: int) -> int:
    """The portrait-equivalent output height whose `/ 1920` is the length scale (for code that scales by H / 1920):
    H for portrait, H x 1920 / 1080 for landscape (1080 -> 1920, 1440 -> 2560, 2160 -> 3840)."""
    return int(H) if W <= H else round(H * PORTRAIT_REF[1] / LANDSCAPE_REF[1])


# Named output formats (CLI `--format`): (width, height). Shorts stay portrait; landscape is a normal YouTube video.
FORMATS = {
    "short": (1080, 1920),
    "short-4k": (2160, 3840),
    "landscape-1080p": (1920, 1080),
    "landscape-1440p": (2560, 1440),
    "landscape-4k": (3840, 2160),
}
