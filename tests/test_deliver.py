"""Delivery naming, part splitting, build check and upload package."""
import json

import pytest

from video_engine import deliver
from video_engine.util import EngineError


def test_good_names(project):
    _, tl = project()
    n = deliver.names(tl)
    assert n["root"] == "07 - 2026-10-28 - Uji Coba"
    assert n["final_dir"].endswith("/05 Final") and n["assets_dir"].endswith("/03 Aset")
    assert n["final"] == "07 - Uji Coba - final.mp4" and n["join"] == "07 - JOIN.txt" and n["credits"] == "07 - credits.txt"


def test_bad_names_missing_settings(project):
    _, tl = project(lambda d: d["settings"].pop("topic"))
    with pytest.raises(EngineError, match="topic"):
        deliver.names(tl)


def test_good_topic_cleaned(project):
    _, tl = project(lambda d: d["settings"].update(topic='Uji/Coba: "x"  y'))
    assert "/" not in deliver.names(tl)["final"] and ":" not in deliver.names(tl)["final"]


@pytest.mark.parametrize("size,parts", [(10, 1), (95, 1), (96, 2), (300, 4)])
def test_good_split_ranges(size, parts):
    r = deliver.split_ranges(size, 95)
    assert len(r) == parts and r[0][0] == 0 and r[-1][1] == size
    assert all(b - a <= 95 for a, b in r) and all(r[i][1] == r[i + 1][0] for i in range(len(r) - 1))


def test_bad_split_empty():
    with pytest.raises(EngineError):
        deliver.split_ranges(0)


def test_good_plan_parts_and_join(project, tmp_path):
    p, tl = project()
    final = tmp_path / "final.mp4"
    final.write_bytes(b"x" * 250)
    pl = deliver.plan(tl, p, final, part_max=100)
    parts = [f for f in pl.files if f["range"]]
    assert len(parts) == 3 and parts[0]["name"].endswith(".part1") and pl.dry_run
    assert "cat " in pl.join_txt and "copy /b" in pl.join_txt
    assert any(f["name"] == "07 - credits.txt" for f in pl.files)


def test_bad_plan_no_final(project, tmp_path):
    p, tl = project()
    with pytest.raises(EngineError, match="not found"):
        deliver.plan(tl, p, tmp_path / "missing.mp4")


# Audit 2026-10-25 item 11: a file of exactly the part limit gave one ".part0" while JOIN.txt named another file.
# Rule: size <= limit -> one file under the final name, no parts; size > limit -> ".part1".."partN", each <= limit,
# and JOIN.txt names exactly those parts in order.
def test_good_plan_exactly_at_limit_is_one_unsplit_file(project, tmp_path):
    p, tl = project()
    final = tmp_path / "final.mp4"
    final.write_bytes(b"x" * 100)
    pl = deliver.plan(tl, p, final, part_max=100)
    vids = [f for f in pl.files if f["source"] == str(final)]
    assert [(f["name"], f["bytes"], f["range"]) for f in vids] == [("07 - Uji Coba - final.mp4", 100, None)]
    assert ".part" not in pl.join_txt and "No join needed" in pl.join_txt


@pytest.mark.parametrize("size", [101, 200, 201])
def test_bad_plan_one_byte_over_limit_splits_and_join_names_every_part(project, tmp_path, size):
    p, tl = project()
    final = tmp_path / "final.mp4"
    final.write_bytes(b"x" * size)
    pl = deliver.plan(tl, p, final, part_max=100)
    parts = [f for f in pl.files if f["range"]]
    names = [f["name"] for f in parts]
    assert names == [f"07 - Uji Coba - final.mp4.part{i + 1}" for i in range(len(parts))] and len(parts) >= 2
    assert all(f["bytes"] <= 100 for f in parts) and sum(f["bytes"] for f in parts) == size
    assert pl.join_txt.count(".part") == 2 * len(parts)            # every part once in the cat and once in copy /b
    assert " ".join(f'"{n}"' for n in names) in pl.join_txt and ".part0" not in pl.join_txt


def test_good_default_limit_boundary_100_000_000():
    assert deliver.PART_MAX_BYTES == 100_000_000
    assert deliver.split_ranges(100_000_000) == [(0, 100_000_000)]
    assert deliver.split_ranges(100_000_001) == [(0, 50_000_001), (50_000_001, 100_000_001)]


def _built(tmp_path):
    import json
    from video_engine import manifest
    d = tmp_path / "out"
    d.mkdir()
    final = d / "final.mp4"
    final.write_bytes(b"published")
    rep = {"ok": True, "build_id": "b1", "final_sha256": manifest.sha256(final, fresh=True)}
    (d / "build_report.json").write_text(json.dumps(rep))
    (d / manifest.MANIFEST).write_text(json.dumps({"build_id": "b1"}))
    return d, final, rep


def test_good_check_build_accepts_the_last_published_build(tmp_path):
    _, final, rep = _built(tmp_path)
    assert deliver.check_build(final)["build_id"] == rep["build_id"]


def test_bad_check_build_rejects_stale_or_failed_output(tmp_path):
    d, final, rep = _built(tmp_path)
    (d / "build_report.failed.json").write_text("{}")
    with pytest.raises(EngineError, match="last build failed"):
        deliver.check_build(final)
    (d / "build_report.failed.json").unlink()
    (d / "build_manifest.json").write_text('{"build_id": "later"}')
    with pytest.raises(EngineError, match="stale"):
        deliver.check_build(final)
    (d / "build_manifest.json").write_text('{"build_id": "b1"}')
    final.write_bytes(b"changed")
    with pytest.raises(EngineError, match="differs"):
        deliver.check_build(final)
    final.unlink()
    with pytest.raises(EngineError, match="not found"):
        deliver.check_build(final)


def _captioned(tl, words):
    from video_engine.timeline import CaptionWord
    return tl.model_copy(update={"captions": [CaptionWord(word=w, start_frame=a, end_frame=b) for w, a, b in words]})


def test_good_srt_groups_adjacent_words_and_uses_millisecond_timestamps(project):
    _, tl = project()
    tl = _captioned(tl, [("Halo", 0, 10), ("dunia", 10, 20), ("ini", 40, 50)])
    text = deliver.srt(tl)
    assert "1\n00:00:00,000 --> 00:00:00,667\nHalo dunia\n" in text
    assert "2\n00:00:01,333 --> 00:00:01,667\nini\n" in text


def test_good_metadata_has_youtube_and_reels(project):
    _, tl = project()
    tl = _captioned(tl, [("Sejarah", 0, 10), ("pelayaran", 10, 20)])
    md = deliver.metadata(tl)
    assert md["title"] == "Uji Coba" and md["youtube"]["shorts"] is True
    assert md["reels"]["hashtags"][0] == "#Shorts" and "Sejarah" in md["description"]


def test_good_export_writes_package_with_srt_thumbnail_and_metadata(project, tmp_path):
    from conftest import ff
    from PIL import Image
    p, tl = project()
    tl = _captioned(tl, [("Halo", 0, 10), ("dunia", 10, 20)])
    S = tl.settings
    final = tmp_path / "final.mp4"
    ff("-f", "lavfi", "-i", f"testsrc2=size={S.width}x{S.height}:rate={S.fps}", "-frames:v", str(tl.total_frames),
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", final)
    r = deliver.export_package(tl, p, final, tmp_path / "pkg", part_max=400_000, verify=False)
    root = tmp_path / "pkg"
    assert r["srt_cues"] == 1 and len([f for f in r["files"] if ".part" in f]) >= 2
    final_dir = root / "07 - 2026-10-28 - Uji Coba" / "05 Final"
    assert (final_dir / "07 - Uji Coba - final.srt").read_text().startswith("1\n")
    md = json.loads((final_dir / "07 - Uji Coba - metadata.json").read_text())
    assert md["youtube"]["title"] == "Uji Coba"
    assert Image.open(final_dir / "07 - Uji Coba - thumbnail.jpg").size == (S.width, S.height)
    assert (root / "07 - 2026-10-28 - Uji Coba" / "03 Aset" / "07 - credits.txt").is_file()
    # the byte-split parts join back to the published file
    parts = sorted(final_dir.glob("*.part*"), key=lambda x: int(x.name.rsplit("part", 1)[1]))
    assert b"".join(x.read_bytes() for x in parts) == final.read_bytes()
