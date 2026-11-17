"""Profile-dependent timeline preparation, preserving public validation semantics."""
from __future__ import annotations
from video_engine.timeline.scene import CameraKey3D

def prepare(self):
    from video_engine import profiles as profiles
    from video_engine.core import units as units
    from video_engine.core.util import EngineError
    try:
        orient = units.orientation_of(self.settings.width, self.settings.height)
        profiles.check_orientation(profiles.load(self.profile), self.settings.width, self.settings.height)
        prof = profiles.load(self.profile, self.profile_overrides, orientation=orient)
        mz = prof.camera.max_zoom
    except EngineError as e:
        raise ValueError(str(e)) from e
    for s in self.segments[1:]:
        if s.transition_in == "page_turn" and not prof.transitions.page_turn:
            raise ValueError(f"segment {s.seg_index}: transition_in 'page_turn' is not allowed by the profile "
                             "([transitions] page_turn = false)")
    try:
        profiles.check_timeline_caption_style(self, prof)   # keys, types, geometry: the profile's own check
    except EngineError as e:
        raise ValueError(str(e)) from e
    for s in self.segments:
        sc = s.gsap_scene_config
        if sc is None:
            continue
        mode = sc.camera_mode or prof.camera.mode
        if sc.camera3d_preset is not None:
            from video_engine.layout import camera_full3d as camera_full3d
            p = sc.camera3d_preset
            try:
                keys = [CameraKey3D(**k) for k in camera_full3d.preset_keys(
                    p.name, s.n_frames, self.settings.fps, target=p.target, height=p.height, fov=p.fov, **p.options)]
            except ValueError as e:
                raise ValueError(f"segment {s.seg_index}: {e}") from e
            if sc.camera3d and sc.camera3d != keys:
                raise ValueError(f"segment {s.seg_index}: give camera3d keys or camera3d_preset, not both")
            sc.camera3d = keys
        if mode == "3d" and not sc.camera3d:
            raise ValueError(f"segment {s.seg_index}: camera mode '3d' needs gsap_scene_config.camera3d keys "
                             "or camera3d_preset")
        if sc.camera3d and mode != "3d":
            raise ValueError(f"segment {s.seg_index}: camera3d keys need camera mode '3d' (set "
                             "gsap_scene_config.camera_mode = \"3d\" or the profile [camera] mode)")
        if sc.camera3d and sc.camera3d[-1].at_frame >= s.n_frames:
            raise ValueError(f"segment {s.seg_index}: camera3d key at_frame {sc.camera3d[-1].at_frame} is past the "
                             f"segment ({s.n_frames} frames)")
    try:
        from video_engine.layout import framing as framing
        framing.apply(self)
    except (EngineError, ValueError) as e:
        raise ValueError(str(e)) from e
    for s in self.segments:
        for i, k in enumerate((s.gsap_scene_config.camera_path or []) if s.gsap_scene_config else []):
            if k.zoom > mz + 1e-9:
                raise ValueError(f"segment {s.seg_index} camera_path[{i}]: zoom {k.zoom:g} exceeds the profile "
                                 f"[camera] max_zoom {mz:g}")
    return self
