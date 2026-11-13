"""Layout of HyperFrames map scenes told in beats (gsap_scene_config.beats) under a profile [layout] table.

A beat lists what is on screen: one active region, labelled pins, unlabelled markers, routes, the title or the data
card in Z1, area labels. The planner derives from the beats and the profile: the camera_path (one settle key per beat
that fits its map content into Z2), the entry and exit frames of every element, the Z1 positions of the title and the
data card, the area-label positions, a per-frame pin-label track, and the trimmed routes. `apply_segment` writes the
derived frames and camera keys into the segment; `scene_layout` is the `layout` block of the scene data the template
reads. `frame_items` is the on-screen state of one frame; video_engine/gates/layout_gates.py checks it.

Coordinates are output px; profile lengths are px at 1080 x 1920 and scale with the output size."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .layout_camera import camera_at, _glide, _quart_in_out, EASES, PULSE_STEPS, PULSE_FLOOR, _LABEL_MOVE, label_move_frames, shrink_pulses, DRIFT_SHARE, DRIFT_MIN, bound_drift, cubic_bezier, head_box, _label_metrics, label_box, area_tracking, area_metrics, area_corners, note_corners, note_box, _frange, area_box, Item, frame_items, animating, scene_layout
from .layout_geometry import Box, FONT_FILES, VARIABLE, wt, Metrics, text_metrics, halo, text_box, inflate, overlap_area, inside, _r, _seg_hits_box, poly_edges, crosses_edge, point_in_poly, _pt_seg_dist, seg_box_dist, polyline_box_dist, clip_area, rot_corners, corners_box, quad_inside_poly, _segs_cross, quad_outside_poly, _closest
from .layout_models import _M, LayoutGrid, LayoutZones, LayoutSizes, LayoutBudget, LayoutTiming, LayoutLabels, LayoutFly, LayoutProfile, HEAD, HEAD_C, SHADOW, ENTRY_ORDER, EXIT_ORDER, Span, Label, Area, Note, Heading, Plan, _frames, _detail
from .layout_planner import _Planner

if TYPE_CHECKING:
    from .timeline import Segment, Timeline

__all__ = ['Box', 'FONT_FILES', 'VARIABLE', 'wt', 'Metrics', 'text_metrics', 'halo', 'text_box', 'inflate', 'overlap_area', 'inside', '_r', '_seg_hits_box', 'poly_edges', 'crosses_edge', 'point_in_poly', '_pt_seg_dist', 'seg_box_dist', 'polyline_box_dist', 'clip_area', 'rot_corners', 'corners_box', 'quad_inside_poly', '_segs_cross', 'quad_outside_poly', '_closest', '_M', 'LayoutGrid', 'LayoutZones', 'LayoutSizes', 'LayoutBudget', 'LayoutTiming', 'LayoutLabels', 'LayoutFly', 'LayoutProfile', 'HEAD', 'HEAD_C', 'SHADOW', 'ENTRY_ORDER', 'EXIT_ORDER', 'Span', 'Label', 'Area', 'Note', 'Heading', 'Plan', '_frames', '_detail', 'camera_at', '_glide', '_quart_in_out', 'EASES', 'PULSE_STEPS', 'PULSE_FLOOR', '_LABEL_MOVE', 'label_move_frames', 'shrink_pulses', 'DRIFT_SHARE', 'DRIFT_MIN', 'bound_drift', 'cubic_bezier', 'head_box', '_label_metrics', 'label_box', 'area_tracking', 'area_metrics', 'area_corners', 'note_corners', 'note_box', '_frange', 'area_box', 'Item', 'frame_items', 'animating', 'scene_layout', '_Planner', 'profile_layout', 'is_beats', '_plan_key', '_PLANS', 'plan', 'apply_segment', 'apply']

def profile_layout(tl: "Timeline") -> LayoutProfile | None:
    from . import profiles
    return profiles.of(tl).layout


def is_beats(seg: "Segment", tl: "Timeline") -> bool:
    from .timeline import HYPERFRAMES
    sc = seg.gsap_scene_config
    return seg.engine_type == HYPERFRAMES and sc is not None and bool(sc.beats) and profile_layout(tl) is not None


def _plan_key(seg: "Segment", tl: "Timeline", project_dir: Path) -> str:
    from . import profiles
    scene = seg.gsap_scene_config
    assert scene is not None
    sc = scene.model_dump(mode="json", exclude={"camera_path", "title_at_frame"})
    for k in ("pins", "regions", "routes", "figures", "cards"):
        for e in sc.get(k) or []:
            e.pop("at_frame", None)
    p = project_dir / scene.map_asset
    return json.dumps([sc, seg.n_frames, tl.settings.model_dump(mode="json"), profiles.of(tl).blake3,
                       str(Path(project_dir).resolve()), str(p.resolve()), p.stat().st_mtime_ns], sort_keys=True)


_PLANS: dict[str, Plan] = {}


def plan(seg: "Segment", tl: "Timeline", project_dir: Path) -> Plan:
    key = _plan_key(seg, tl, Path(project_dir))
    if key not in _PLANS:
        if len(_PLANS) > 16:
            _PLANS.clear()
        _PLANS[key] = _Planner(seg, tl, Path(project_dir)).run()
    return _PLANS[key]


def apply_segment(seg: "Segment", tl: "Timeline", project_dir: Path) -> Plan | None:
    """Writes the derived camera_path and element frames into a beats segment (idempotent). None = not a beats
    segment under a layout profile, or its map is missing."""
    sc = seg.gsap_scene_config
    if not is_beats(seg, tl) or sc is None:
        return None
    if not (Path(project_dir) / sc.map_asset).is_file():
        return None
    from .timeline import CameraKey
    p = plan(seg, tl, project_dir)
    sc.camera_path = [CameraKey(**{k: v for k, v in kk.items() if not (k == "move_s" and kk["move"] != "settle")})
                      for kk in p.keys]
    first = lambda key, default: (p.spans.get(key) or [Span(default, 0)])[0].t_in
    for i, pin in enumerate(sc.pins):
        pin.at_frame = min(seg.n_frames - 1, first(f"head:{i}", seg.n_frames - 1))
    for i, r in enumerate(sc.regions or []):
        r.at_frame = min((t for (t, _, _, _) in p.region_steps[i]), default=seg.n_frames - 1)
    for i, r in enumerate(sc.routes or []):
        r.at_frame = first(f"route:{i}", seg.n_frames - 1)
    if sc.title:
        if p.pr is not None:   # [print] places the title inside its beat's region (an area label), not in Z1
            ta = next((a for a in p.areas.values() if a.title), None)
            sc.title_at_frame = first(f"area:{ta.key}", 0) if ta else 0
        else:
            sc.title_at_frame = first("title:0", seg.n_frames - 1)
    for i, fg in enumerate(sc.figures or []):
        fg.at_frame = first(f"fig:{i}", seg.n_frames - 1)
    return p


def apply(tl: "Timeline", project_dir: Path) -> None:
    for s in tl.segments:
        apply_segment(s, tl, project_dir)
