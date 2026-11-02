"""Source colour at decode (util.decode_colour): untagged HD footage is BT.709, untagged SD BT.601, tags win, full
range is kept, HDR is refused (M01) and RGB sources take no matrix."""
import subprocess

import numpy as np
import pytest

from video_engine import config, native
from video_engine.util import EngineError, decode_colour

RED = "0xE03020"


def _clip(path, size, matrix="bt709", tags=(), extra=()):
    """2 frames of RED encoded with `matrix` (limited range); `tags` = encoder colour tag args (none = untagged)."""
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={RED}:s={size}:r=30",
                    "-frames:v", "2", "-vf", f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p",
                    "-c:v", "libx264", "-crf", "0", *tags, *extra, str(path)], check=True)
    return path


def _rgb(path, opts):
    vf = f"scale=64:36{(':' + opts) if opts else ''},format=rgb24"
    out = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(path), "-vf", vf, "-frames:v", "1",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(36, 64, 3)[18, 32].astype(int)


def test_good_untagged_hd_decodes_as_bt709(tmp_path):
    """Untagged HD decodes like the same stream tagged BT.709 (ffmpeg's tag-aware decode is the reference)."""
    src = _clip(tmp_path / "hd.mp4", "1280x720")
    tagged = tmp_path / "tagged.mp4"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-i", str(src), "-c", "copy", "-bsf:v",
                    "h264_metadata=matrix_coefficients=1:colour_primaries=1:transfer_characteristics=1", str(tagged)],
                   check=True)
    assert decode_colour(src) == "in_color_matrix=bt709:in_range=limited"
    want = _rgb(tagged, "")
    assert np.abs(_rgb(src, decode_colour(src)) - want).max() <= 1
    assert np.abs(_rgb(src, "") - want).max() > 10      # what swscale alone made of it (BT.601): the old bug


def test_good_untagged_sd_is_bt601_and_tags_win(tmp_path):
    assert decode_colour(_clip(tmp_path / "sd.mp4", "640x360", "bt601")) == "in_color_matrix=bt601:in_range=limited"
    tagged = _clip(tmp_path / "t.mp4", "1280x720", "bt601", ("-colorspace", "smpte170m"))
    assert decode_colour(tagged) == "in_color_matrix=bt601:in_range=limited"


def test_good_full_range_is_kept(tmp_path):
    src = _clip(tmp_path / "pc.mp4", "1280x720", tags=("-colorspace", "bt709", "-color_range", "pc"))
    assert decode_colour(src).endswith("in_range=full")


def test_good_rgb_source_takes_no_matrix(tmp_path):
    src = tmp_path / "rgb.mkv"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={RED}:s=64x36:r=30",
                    "-frames:v", "2", "-c:v", "ffv1", "-pix_fmt", "bgr0", str(src)], check=True)
    assert decode_colour(src) == "" and native._cs(src) == ""


@pytest.mark.parametrize("trc, name", [("smpte2084", "PQ"), ("arib-std-b67", "HLG")])
def test_bad_hdr_source_is_refused(tmp_path, trc, name):
    src = _clip(tmp_path / "hdr.mp4", "1280x720", tags=("-colorspace", "bt2020nc", "-color_primaries", "bt2020",
                                                        "-color_trc", trc))
    with pytest.raises(EngineError, match=f"HDR source \\({name}"):
        decode_colour(src)


def test_bad_hdr_footage_fails_m01(project, tmp_path):
    from video_engine.gates import layer2
    (tmp_path / "proj" / "assets").mkdir(parents=True)
    _clip(tmp_path / "proj" / "assets" / "hdr.mp4", "1080x1920",
          tags=("-colorspace", "bt2020nc", "-color_trc", "smpte2084"), extra=("-t", "3"))
    p, tl = project(lambda d: d["segments"][1].update(source_video="assets/hdr.mp4"))
    fs = layer2.m01_source_length(tl, p)
    assert [(f.gate, f.level, f.seg) for f in fs if "HDR" in f.message] == [("M01", "fail", 1)]
