"""Perspective tilt of a HyperFrames map camera (camera_path key `tilt`).

The template puts #cam (map, regions, routes, pins) and the map props layer into #tilt, a W x H box that turns about
the frame centre O = (W/2, H/2) by `perspective(P) rotateX(t)` with P = PERSPECTIVE x H. A point q of the flat
camera picture (frame px, what the 2D camera alone would show) is drawn at

    Y = q.y - O.y,  k = P / (P - Y sin t),  screen = O + k (q.x - O.x, Y cos t)

(the top half recedes, the bottom half comes closer). k is the local magnification of the picture at q.
"""
from __future__ import annotations

import math

PERSPECTIVE = 1.0   # CSS perspective distance as a multiple of the frame height
HORIZON = 0.05      # a screen point needs P cos t + v sin t >= HORIZON x P to unproject (else: at or past the horizon)


def perspective_px(H: int) -> float:
    return PERSPECTIVE * H


def project(qx: float, qy: float, t: float, W: int, H: int) -> tuple[float, float, float]:
    """(screen x, screen y, magnification k) of flat-picture point q under tilt t (deg)."""
    P, a = perspective_px(H), math.radians(t)
    Y = qy - H / 2
    k = P / (P - Y * math.sin(a))
    return W / 2 + k * (qx - W / 2), H / 2 + k * Y * math.cos(a), k


def unproject(sx: float, sy: float, t: float, W: int, H: int) -> tuple[float, float] | None:
    """Flat-picture point drawn at screen (sx, sy) under tilt t (deg); None at or beyond the horizon."""
    P, a = perspective_px(H), math.radians(t)
    v = sy - H / 2
    den = P * math.cos(a) + v * math.sin(a)
    if den < HORIZON * P:
        return None
    Y = v * P / den
    return W / 2 + (sx - W / 2) * (P - Y * math.sin(a)) / P, H / 2 + Y


def max_mag(t: float, W: int, H: int) -> float:
    """Largest magnification on screen under tilt t: at the bottom edge."""
    q = unproject(W / 2, H, t, W, H)
    return project(q[0], q[1], t, W, H)[2]


def has_tilt(cam: dict) -> bool:
    return "t" in cam["start"]


def corners_off_map(cam_state: dict, W: int, H: int, mw: float, mh: float) -> list[str]:
    """Screen corners of one camera state {x, y, s, t} that show no map (past the map edge or beyond the horizon)."""
    out = []
    for name, (sx, sy) in (("top-left", (0, 0)), ("top-right", (W, 0)), ("bottom-left", (0, H)), ("bottom-right", (W, H))):
        q = unproject(sx, sy, cam_state.get("t", 0.0), W, H)
        if q is None:
            out.append(name)
            continue
        mx, my = (q[0] - cam_state["x"]) / cam_state["s"], (q[1] - cam_state["y"]) / cam_state["s"]
        if not (-0.5 <= mx <= mw + 0.5 and -0.5 <= my <= mh + 0.5):
            out.append(name)
    return out


def uncovered(scene: dict) -> list[tuple[int, list[str]]]:
    """Frames of a tilted scene whose picture leaves a screen corner without map: (frame, corners)."""
    from .layout import camera_at
    cam = scene["camera"]
    if not has_tilt(cam):
        return []
    W, H = scene["width"], scene["height"]
    mw, mh = scene["map_size"]
    out = []
    for f in range(scene["n_frames"]):
        c = camera_at(cam, f)
        if c.get("t", 0) > 0 and (bad := corners_off_map(c, W, H, mw, mh)):
            out.append((f, bad))
    return out
