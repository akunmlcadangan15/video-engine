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

ARC_STEPS = 64          # arc-length samples per leg
NEAR_FRAC = 1e-4        # three.js near plane as a fraction of the eye height (scene3d.js uses the same)
UP = (0.0, 1.0, 0.0)
CORNERS = (("top-left", 0.0, 0.0), ("top-right", 1.0, 0.0), ("bottom-left", 0.0, 1.0), ("bottom-right", 1.0, 1.0))
PROBE = [(x, y) for y in (0.0, 0.5, 1.0) for x in (0.0, 0.5, 1.0)]   # screen points for magnification and motion


# ------------------------------------------------------------------------------------------------ vector / quaternion
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


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
