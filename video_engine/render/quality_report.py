"""Resolved renderer quality reporting."""
from __future__ import annotations
from video_engine.timeline import Timeline
from video_engine.profiles import of, aac_bitrate

def quality_report(tl: "Timeline") -> dict:
    """Resolved settings actually passed to capture and encode, not nullable profile defaults."""
    from video_engine.render import hyperframes as hyperframes, maps as maps
    r = of(tl).render
    q, crf, segment = hyperframes.encode_settings(tl)
    return {"width": tl.settings.width, "height": tl.settings.height, "fps": tl.settings.fps,
            "hf_quality": q, "hf_crf": crf, "segment_x264": segment, "final_x264": r.x264("final"),
            "final_pix_fmt": r.final_pix_fmt(), "map_jpeg_quality": r.map_jpeg_quality or maps.MAP_JPEG_QUALITY,
            "aac_bitrate": aac_bitrate(tl)}

