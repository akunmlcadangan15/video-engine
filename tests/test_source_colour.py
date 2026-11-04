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


def _hdr_clip(path, size, trc, trc_code, extra=()):
    """HDR clip whose colour tags survive ffmpeg 9: the encoder flags (-color_trc) are dropped by ffmpeg 9's
    muxer, so the tags are written with the h264_metadata bitstream filter instead (as in
    test_good_untagged_hd_decodes_as_bt709) and verified with ffprobe before the caller tests anything."""
    src = _clip(path, size, extra=extra)
    tagged = path.with_name(path.stem + "_hdr.mp4")
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-i", str(src), "-c", "copy", "-bsf:v",
                    f"h264_metadata=matrix_coefficients=11:colour_primaries=9:transfer_characteristics={trc_code}",
                    str(tagged)], check=True)
    got = subprocess.run([config.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=color_transfer", "-of", "csv=p=0", str(tagged)],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert got == trc, f"ffmpeg did not write transfer tag {trc}: ffprobe reads {got!r}"
    return tagged


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
    # what a BT.601-scale decode gives (the old bug): ffmpeg 9's swscale alone now matches the BT.709 reference
    # on untagged HD, so force the 601 matrix explicitly instead of relying on its default choice
    assert np.abs(_rgb(src, "in_color_matrix=bt601:in_range=limited") - want).max() > 10


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


@pytest.mark.parametrize("trc, code, name", [("smpte2084", 16, "PQ"), ("arib-std-b67", 18, "HLG")])
def test_bad_hdr_source_is_refused(tmp_path, trc, code, name):
    src = _hdr_clip(tmp_path / "hdr.mp4", "1280x720", trc, code)
    with pytest.raises(EngineError, match=f"HDR source \\({name}"):
        decode_colour(src)


def test_bad_hdr_footage_fails_m01(project, tmp_path):
    from video_engine.gates import layer2
    (tmp_path / "proj" / "assets").mkdir(parents=True)
    hdr = _hdr_clip(tmp_path / "proj" / "assets" / "hdr.mp4", "1080x1920", "smpte2084", 16, extra=("-t", "3"))
    p, tl = project(lambda d: d["segments"][1].update(source_video=f"assets/{hdr.name}"))
    fs = layer2.m01_source_length(tl, p)
    assert [(f.gate, f.level, f.seg) for f in fs if "HDR" in f.message] == [("M01", "fail", 1)]
