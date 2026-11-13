"""Full 3D camera of a HyperFrames map scene (`gsap_scene_config.camera3d`, camera mode "3d").

The planar camera (camera3d.py, layout.camera_at) is a 2.5D state {x, y, s, tilt, roll}. This mode puts the map on a
plane in 3D and flies a perspective camera over it: dolly, orbit, crane, truck and free yaw / pitch / roll.

World (right-handed, three.js convention): the map is the plane z = 0, map point (u, v) (normalised, v down) at
world (u * mw, -v * mh, 0) in cover px (mw x mh = the map at cover scale), so north is +y and z points at the viewer.
A key gives `eye` and `target` as [u, v, h] with h the height above the map in map widths (world z = h * mw), a
`roll` about the view axis (deg, clockwise on screen) and a vertical `fov` (deg).

Sampler (pure, frame-indexed, no wall clock): `sample(keys, frame)`.
  - The camera stays on key i for `hold` frames after its at_frame, then travels to key i + 1 by its at_frame.
  - Progress p of a leg: `glide` = smoothstep (cubic ease in-out), `linear` = constant.
  - Position: Catmull-Rom spline through the key eyes (end tangents mirrored), reparameterised by arc length
    (ARC_STEPS samples per leg), so the eye covers equal distance per unit of p.
  - Orientation: the key quaternion (lookAt(eye, target, up = +y) then roll) and SLERP by p (shortest arc).
  - fov: linear in p.
`frames(keys, n)` evaluates every frame once; the scene carries those states, so the browser (hf/scene3d.js) sets the
three.js camera from the same numbers the gates use.

Projection (Python mirror of THREE.PerspectiveCamera): view = inverse(T(position) R(q)), perspective with vertical
fov and aspect W / H; NDC -> screen px x = (ndc.x + 1) / 2 W, y = (1 - ndc.y) / 2 H.

Gates (layer2): M02 sizes the map by the largest on-screen magnification (`need_w`), M03 checks every screen corner
ray of every frame hits the map in front of the camera (`uncovered`), R56 reads the screen motion plus speed,
acceleration and angular speed (`metrics`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .vecmath import add as _add, cross as _cross, length as _norm, mul as _mul, sub as _sub

ARC_STEPS = 64          # arc-length samples per leg
NEAR_FRAC = 1e-4        # three.js near plane as a fraction of the eye height (scene3d.js uses the same)
UP = (0.0, 1.0, 0.0)
CORNERS = (("top-left", 0.0, 0.0), ("top-right", 1.0, 0.0), ("bottom-left", 0.0, 1.0), ("bottom-right", 1.0, 1.0))
PROBE = [(x, y) for y in (0.0, 0.5, 1.0) for x in (0.0, 0.5, 1.0)]   # screen points for magnification and motion


# ------------------------------------------------------------------------------------------------ vector / quaternion
# (vector ops are shared via vecmath; _norm here is the scalar length, _unit raises on zero length)
def _unit(a):
    n = _norm(a)
    if n < 1e-12:
        raise ValueError("zero-length vector")
    return _mul(a, 1.0 / n)


def quat_from_basis(x, y, z) -> tuple[float, float, float, float]:
    """Quaternion (x, y, z, w) of the rotation whose columns are the unit axes x, y, z (three.js setFromRotationMatrix)."""
    m11, m12, m13 = x[0], y[0], z[0]
    m21, m22, m23 = x[1], y[1], z[1]
    m31, m32, m33 = x[2], y[2], z[2]
    tr = m11 + m22 + m33
    if tr > 0:
        s = 0.5 / math.sqrt(tr + 1.0)
        return ((m32 - m23) * s, (m13 - m31) * s, (m21 - m12) * s, 0.25 / s)
    if m11 > m22 and m11 > m33:
        s = 2.0 * math.sqrt(1.0 + m11 - m22 - m33)
        return (0.25 * s, (m12 + m21) / s, (m13 + m31) / s, (m32 - m23) / s)
    if m22 > m33:
        s = 2.0 * math.sqrt(1.0 + m22 - m11 - m33)
        return ((m12 + m21) / s, 0.25 * s, (m23 + m32) / s, (m13 - m31) / s)
    s = 2.0 * math.sqrt(1.0 + m33 - m11 - m22)
    return ((m13 + m31) / s, (m23 + m32) / s, 0.25 * s, (m21 - m12) / s)


def quat_rotate(q, v):
    """v rotated by the unit quaternion q."""
    x, y, z, w = q
    u = (x, y, z)
    t = _mul(_cross(u, v), 2.0)
    return _add(_add(v, _mul(t, w)), _cross(u, t))


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def slerp(a, b, t: float):
    """Shortest-arc spherical interpolation of unit quaternions."""
    d = sum(x * y for x, y in zip(a, b))
    if d < 0:
        b, d = tuple(-v for v in b), -d
    if d > 0.9995:
        q = tuple(x + (y - x) * t for x, y in zip(a, b))
        n = math.sqrt(sum(v * v for v in q))
        return tuple(v / n for v in q)
    th = math.acos(min(1.0, d))
    s = math.sin(th)
    wa, wb = math.sin((1 - t) * th) / s, math.sin(t * th) / s
    return tuple(wa * x + wb * y for x, y in zip(a, b))


def look_at(eye, target, roll_deg: float = 0.0, up=UP):
    """Camera quaternion: -z looks from eye to target, +y as close to `up` as possible, then `roll` (deg, clockwise
    on screen) about the view axis. ValueError when the view direction is parallel to `up`."""
    z = _unit(_sub(eye, target))            # three.js: the camera looks down its -z
    x = _cross(up, z)
    if _norm(x) < 1e-9:
        raise ValueError("view direction is parallel to north (+y); move the eye or the target")
    x = _unit(x)
    y = _cross(z, x)
    q = quat_from_basis(x, y, z)
    if roll_deg:
        a = math.radians(roll_deg) / 2      # the camera turns counter-clockwise, so the map turns clockwise on screen
        q = quat_mul(q, (0.0, 0.0, math.sin(a), math.cos(a)))
    return q


# ------------------------------------------------------------------------------------------------------- sampler
@dataclass(frozen=True)
class State:
    position: tuple[float, float, float]     # world (cover px)
    quaternion: tuple[float, float, float, float]
    fov: float                               # vertical, deg

    def as_list(self) -> list[float]:
        return [round(v, 6) for v in (*self.position, *self.quaternion)] + [round(self.fov, 6)]


def world(p, mw: float, mh: float) -> tuple[float, float, float]:
    """[u, v, h] (normalised map point, height in map widths) -> world cover px."""
    return (p[0] * mw, -p[1] * mh, (p[2] if len(p) > 2 else 0.0) * mw)


def _ease(move: str, p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p) if move == "glide" else p


def _catmull(p0, p1, p2, p3, t):
    t2, t3 = t * t, t * t * t
    return tuple(0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3)
                 for a, b, c, d in zip(p0, p1, p2, p3))


class Path3D:
    """The sampled camera of one segment: keys (dicts or CameraKey3D) in world units."""

    def __init__(self, keys, mw: float, mh: float):
        if not keys:
            raise ValueError("camera3d needs at least one key")
        self.keys = keys
        self.eyes = [world(k.eye, mw, mh) for k in keys]
        self.quats = [look_at(e, world(k.target, mw, mh), k.roll) for e, k in zip(self.eyes, keys)]
        self.fovs = [k.fov for k in keys]
        self.arcs = [self._arc(i) for i in range(len(keys) - 1)]

    def _ctrl(self, i):
        e = self.eyes
        p1, p2 = e[i], e[i + 1]
        p0 = e[i - 1] if i > 0 else _sub(_mul(p1, 2), p2)          # mirrored end tangents
        p3 = e[i + 2] if i + 2 < len(e) else _sub(_mul(p2, 2), p1)
        return p0, p1, p2, p3

    def _arc(self, i):
        c = self._ctrl(i)
        pts = [_catmull(*c, j / ARC_STEPS) for j in range(ARC_STEPS + 1)]
        acc = [0.0]
        for a, b in zip(pts, pts[1:]):
            acc.append(acc[-1] + _norm(_sub(b, a)))
        return acc

    def _pos(self, i, s):
        """Eye on leg i at arc-length fraction s."""
        acc = self.arcs[i]
        total = acc[-1]
        if total < 1e-9:
            return self.eyes[i]
        want = s * total
        j = max(0, min(ARC_STEPS - 1, next((k for k in range(1, ARC_STEPS + 1) if acc[k] >= want), ARC_STEPS) - 1))
        seg = acc[j + 1] - acc[j]
        t = (j + ((want - acc[j]) / seg if seg > 1e-12 else 0.0)) / ARC_STEPS
        return _catmull(*self._ctrl(i), t)

    def sample(self, frame: float) -> State:
        ks = self.keys
        if frame <= ks[0].at_frame or len(ks) == 1:
            return State(self.eyes[0], self.quats[0], self.fovs[0])
        for i in range(len(ks) - 1):
            a, b = ks[i], ks[i + 1]
            if frame <= b.at_frame:
                f0 = a.at_frame + a.hold
                if frame <= f0:
                    return State(self.eyes[i], self.quats[i], self.fovs[i])
                p = _ease(b.move, (frame - f0) / max(1e-9, b.at_frame - f0))
                return State(self._pos(i, p), slerp(self.quats[i], self.quats[i + 1], p),
                             self.fovs[i] + (self.fovs[i + 1] - self.fovs[i]) * p)
        return State(self.eyes[-1], self.quats[-1], self.fovs[-1])


def frames(keys, n: int, mw: float, mh: float) -> list[State]:
    path = Path3D(keys, mw, mh)
    return [path.sample(f) for f in range(n)]


# ---------------------------------------------------------------------------------------------------- projection
def project(st: State, p, W: int, H: int):
    """World point -> (screen x, screen y, view depth); depth <= 0 = behind the camera."""
    qi = (-st.quaternion[0], -st.quaternion[1], -st.quaternion[2], st.quaternion[3])
    v = quat_rotate(qi, _sub(p, st.position))          # camera space: looks down -z
    depth = -v[2]
    if depth <= 1e-9:
        return None, None, depth
    f = 1.0 / math.tan(math.radians(st.fov) / 2)
    ndc_x, ndc_y = f / (W / H) * v[0] / depth, f * v[1] / depth
    return (ndc_x + 1) / 2 * W, (1 - ndc_y) / 2 * H, depth


def ray_to_map(st: State, sx: float, sy: float, W: int, H: int):
    """Screen point -> world point where its ray meets the map plane z = 0 (None: the ray misses the plane ahead)."""
    f = 1.0 / math.tan(math.radians(st.fov) / 2)
    ndc_x, ndc_y = sx / W * 2 - 1, 1 - sy / H * 2
    d = quat_rotate(st.quaternion, (ndc_x * (W / H) / f, ndc_y / f, -1.0))
    if abs(d[2]) < 1e-12:
        return None
    t = -st.position[2] / d[2]
    if t <= 0:
        return None
    return _add(st.position, _mul(d, t))


def on_map(p, mw: float, mh: float) -> bool:
    return p is not None and -0.5 <= p[0] <= mw + 0.5 and -mh - 0.5 <= p[1] <= 0.5


def uncovered(states: list[State], W: int, H: int, mw: float, mh: float) -> list[tuple[int, list[str]]]:
    """Frames whose screen corners show no map (the ray misses the plane, hits it behind the eye or off the map)."""
    out = []
    for f, st in enumerate(states):
        bad = [name for name, x, y in CORNERS if not on_map(ray_to_map(st, x * W, y * H, W, H), mw, mh)]
        if bad:
            out.append((f, bad))
    return out


def magnification(st: State, W: int, H: int) -> float:
    """Largest screen px per cover px over the PROBE screen points that see the map."""
    best = 0.0
    for x, y in PROBE:
        p = ray_to_map(st, x * W, y * H, W, H)
        if p is None:
            continue
        a = project(st, p, W, H)
        if a[0] is None:
            continue
        for d in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
            b = project(st, _add(p, d), W, H)
            if b[0] is not None:
                best = max(best, math.hypot(b[0] - a[0], b[1] - a[1]))
    return best


def need_w(states: list[State], W: int, H: int, mw: float) -> float:
    """Widest on-screen map width the scene needs (M02, map derivative size): map width x the deepest magnification."""
    return mw * max([magnification(st, W, H) for st in states] or [1.0])


def metrics(states: list[State], W: int, H: int, fps: int) -> dict:
    """Screen motion of the map under the PROBE points (fixed map points per frame pair): total px, peak speed (px/s),
    peak acceleration (px/s^2) and peak angular speed of the camera (deg/s)."""
    total, speeds, ang = 0.0, [], []
    for a, b in zip(states, states[1:]):
        moves = []
        for x, y in PROBE:
            p = ray_to_map(a, x * W, y * H, W, H)
            if p is None:
                continue
            q = project(b, p, W, H)
            if q[0] is not None:
                moves.append(math.hypot(q[0] - x * W, q[1] - y * H))
        m = sum(moves) / len(moves) if moves else 0.0
        total += m
        speeds.append(m * fps)
        d = abs(sum(u * v for u, v in zip(a.quaternion, b.quaternion)))
        ang.append(math.degrees(2 * math.acos(min(1.0, d))) * fps)
    acc = [abs(v1 - v0) * fps for v0, v1 in zip(speeds, speeds[1:])]
    return {"motion_px": total, "peak_speed": max(speeds, default=0.0), "peak_accel": max(acc, default=0.0),
            "peak_ang_speed": max(ang, default=0.0)}


def motion(states: list[State], W: int, H: int, fps: int) -> list[tuple[float, float]]:
    """P01 speeds per frame: (|ln scale| / s, 2D screen travel in frame widths / s)."""
    if not states:
        return []
    ext = [*states, states[-1]]
    out: list[tuple[float, float]] = []
    for a, b in zip(ext, ext[1:]):
        moves = []
        for x, y in PROBE:
            p = ray_to_map(a, x * W, y * H, W, H)
            if p is None:
                continue
            q = project(b, p, W, H)
            if q[0] is not None:
                moves.append(math.hypot(q[0] - x * W, q[1] - y * H))
        m = sum(moves) / len(moves) if moves else 0.0
        za = max(1e-9, a.position[2] * math.tan(math.radians(a.fov) / 2))
        zb = max(1e-9, b.position[2] * math.tan(math.radians(b.fov) / 2))
        out.append((abs(math.log(za / zb)) * fps, m * fps / W))
    return out


# ------------------------------------------------------------------------------------------------------- presets
# Each preset is a function of time that gives the view as (distance to the target, tilt off the vertical, azimuth,
# roll, fov). `preset_keys` samples it every `step` frames into `camera3d` keys with move "linear", so the curve of
# the preset (not the per-leg glide) sets the timing. Azimuth 0 puts the eye south of the target, north up on screen;
# a positive azimuth moves the eye east.
def _ease_out_expo(x: float) -> float:
    return 1.0 if x >= 1.0 else 1.0 - 2.0 ** (-10.0 * x)


def _ease_in_expo(x: float) -> float:
    return (2.0 ** (10.0 * min(1.0, max(0.0, x))) - 1.0) / 1023.0


def _smooth(x: float) -> float:
    return _ease("glide", x)


def _push_in(t, T, o):
    """Dolly towards the target by `depth` of the start distance while the camera pitches `pitch` deg off the
    vertical; easeOutExpo."""
    p = _ease_out_expo(t / T)
    return o["height"] * (1.0 - o["depth"] * p), o["pitch"] * p, 0.0, 0.0, o["fov"]


def _orbit(t, T, o):
    """Arc of `yaw` deg about the target at a fixed distance and `tilt`, centred on the south view, with the roll
    going 0 -> `roll`; smoothstep."""
    p = _smooth(t / T)
    return o["height"], o["tilt"], o["yaw"] * (p - 0.5), o["roll"] * p, o["fov"]


def _crash_zoom(t, T, o):
    """Exponential dolly by `depth` in `push_s`, then a damped spring about the end distance (`overshoot` of the
    travel, `freq_hz`, `decay` per second)."""
    if t < o["push_s"]:
        p = _ease_in_expo(t / o["push_s"])
    else:
        tau = t - o["push_s"]
        p = 1.0 + o["overshoot"] * math.exp(-o["decay"] * tau) * math.sin(2.0 * math.pi * o["freq_hz"] * tau)
    return o["height"] * (1.0 - o["depth"] * p), 0.0, 0.0, 0.0, o["fov"]


def _vertigo(t, T, o):
    """Dolly zoom: the eye pulls back to `pull` times the start distance along a view tilted `tilt` deg while the fov
    narrows so that distance * tan(fov / 2) stays constant: the target keeps its size and the perspective flattens."""
    d = o["height"] * (1.0 + (o["pull"] - 1.0) * _smooth(t / T))
    return d, o["tilt"], 0.0, 0.0, 2.0 * math.degrees(math.atan(math.tan(math.radians(o["fov"]) / 2.0) * o["height"] / d))


PRESETS = {
    "push_in": (_push_in, {"depth": 0.35, "pitch": 3.0}),
    "orbit": (_orbit, {"yaw": 24.0, "roll": 1.5, "tilt": 35.0}),
    "crash_zoom": (_crash_zoom, {"depth": 0.55, "push_s": 0.4, "overshoot": 0.06, "freq_hz": 3.0, "decay": 7.0}),
    "vertigo": (_vertigo, {"pull": 1.6, "tilt": 40.0}),
}
FOV_RANGE = (10.0, 120.0)   # CameraKey3D.fov bounds


def preset_keys(name: str, n_frames: int, fps: int, *, target=(0.5, 0.5), height: float = 0.6, fov: float = 50.0,
                aspect: float = 1.0, step: int = 2, **opts) -> list[dict]:
    """`camera3d` keys of a named camera move over a segment of `n_frames`.

    target: normalised map point the camera looks at. height: start distance from the target in map widths.
    aspect: map width / map height at cover scale (turns a ground offset into v). step: frames between keys.
    opts: the preset's own parameters (PRESETS[name][1] lists them with their defaults).
    Keys start at frame 0, end before n_frames and are deterministic; runs of identical views keep only their ends.
    ValueError: unknown preset or option, fewer than 2 frames, or a view outside the key bounds."""
    if name not in PRESETS:
        raise ValueError(f"unknown camera3d preset '{name}' (known: {', '.join(PRESETS)})")
    fn, defaults = PRESETS[name]
    unknown = sorted(set(opts) - set(defaults))
    if unknown:
        raise ValueError(f"camera3d preset '{name}' has no option {', '.join(unknown)} (known: {', '.join(defaults)})")
    if n_frames < 2 or fps <= 0 or step < 1 or height <= 0 or aspect <= 0:
        raise ValueError("camera3d preset needs n_frames >= 2 and positive fps, step, height and aspect")
    o = {**defaults, **opts, "height": height, "fov": fov}
    last = n_frames - 1
    T = last / fps
    at = sorted({*range(0, last, step), last})
    keys = []
    for f in at:
        dist, tilt, az, roll, fv = fn(f / fps, T, o)
        if not (dist > 0 and 0 <= tilt < 90 and FOV_RANGE[0] <= fv <= FOV_RANGE[1]):
            raise ValueError(f"camera3d preset '{name}' leaves the camera bounds at frame {f} "
                             f"(distance {dist:g}, tilt {tilt:g}, fov {fv:g})")
        ground, a = dist * math.sin(math.radians(tilt)), math.radians(az)
        keys.append({"at_frame": f,
                     "eye": [round(target[0] + ground * math.sin(a), 6),
                             round(target[1] + ground * math.cos(a) * aspect, 6),
                             round(dist * math.cos(math.radians(tilt)), 6)],
                     "target": [target[0], target[1], 0.0], "roll": round(roll, 4), "fov": round(fv, 4),
                     "move": "linear", "hold": 0})

    def view(k):
        return k["eye"], k["roll"], k["fov"]
    return [k for i, k in enumerate(keys)
            if i in (0, len(keys) - 1) or not view(keys[i - 1]) == view(k) == view(keys[i + 1])]
