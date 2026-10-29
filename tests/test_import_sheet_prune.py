"""import-shots, sheet, cache --prune: good and bad cases."""
import csv
import json
import shutil

import pytest
from PIL import Image

from conftest import ff
from video_engine import cache_prune, importer, sheet, timeline
from video_engine.util import EngineError

COLS = ["shot", "type", "file", "title", "source_url", "author", "licence", "on_screen_s"]


@pytest.fixture
def src(tmp_path, assets):
    """A source edit: 3 stills + 1 map, shot CSV, manifest with one extra credited file, credits, voiceover."""
    s = tmp_path / "src"
    (s / "media").mkdir(parents=True)
    for n in ("a.jpg", "b.jpg", "c.jpg", "alt.jpg"):
        shutil.copyfile(assets / "still.jpg", s / "media" / n)
    shutil.copyfile(assets / "map.jpg", s / "media" / "city_map.jpg")
    ff("-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000", "-t", "4", s / "vo.wav")
    rows = [("1", "map", "city_map.jpg", "Kota", "https://ex.org/map", "A", "PD", "0-1"),
            ("2", "source", "a.jpg", "", "https://ex.org/a", "B", "CC BY 4.0", "1-2"),
            ("3", "source", "b.jpg", "", "https://ex.org/b", "", "own composite of PD", "2-3"),
            ("4", "source", "c.jpg", "", "https://ex.org/c", "", "PD", "3-4")]
    with (s / "shots.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(COLS); w.writerows(rows)
    with (s / "manifest.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["file", "source_url", "licence", "author"])
        w.writerow(["alt.jpg", "https://ex.org/alt", "Public domain", "C"])
        w.writerow(["nourl.jpg", "", "PD", ""])
    (s / "credits.txt").write_text("https://ex.org/map\n")
    return s


def run(src, dst, **kw):
    return importer.import_shots(src / "shots.csv", dst, [src / "media"], credits_txt=src / "credits.txt",
                                 voiceover=src / "vo.wav", manifest=src / "manifest.csv", **kw)


def test_good_import_builds_loadable_project(src, tmp_path):
    dst = tmp_path / "proj"
    r = run(src, dst, substitute={"c.jpg": "alt.jpg"}, exclude={"3"}, settings={"topic": "Uji"})
    assert r["segments"] == 3 and r["frames"] == 120 and r["excluded"] == ["3"]
    tl = timeline.load(dst)
    segs = tl.segments
    assert segs[0].gsap_scene_config and segs[0].gsap_scene_config.map_asset == "assets/city_map.jpg"
    assert [(s.start_frame, s.end_frame) for s in segs] == [(0, 30), (30, 90), (90, 120)]  # shot 3 merged into 2
    cred = {c.asset_id: c for c in tl.credits_manifest}
    assert cred["alt"].source_url == "https://ex.org/alt"           # substitute credit from manifest
    assert "c" not in cred and "b" not in cred
    assert (dst / "assets" / "alt.jpg").is_file() and (dst / "credits.txt").is_file()
    assert importer.norm_licence_text("own composite of PD") == "Own"
    urls = {c.source_url for c in tl.credits_manifest}
    assert urls <= {"https://ex.org/map", "https://ex.org/a", "https://ex.org/alt"}  # only URLs from inputs


def test_bad_substitute_without_manifest_row(src, tmp_path):
    with pytest.raises(EngineError, match="no manifest row"):
        run(src, tmp_path / "p", substitute={"c.jpg": "unknown.jpg"})


def test_bad_substitute_manifest_row_without_url_never_invents(src, tmp_path):
    with pytest.raises(EngineError, match="no source_url"):
        run(src, tmp_path / "p", substitute={"c.jpg": "nourl.jpg"})


def test_bad_unknown_and_first_exclude(src, tmp_path):
    with pytest.raises(EngineError, match="unknown shots"):
        run(src, tmp_path / "p", exclude={"9"})
    with pytest.raises(EngineError, match="first shot"):
        run(src, tmp_path / "q", exclude={"1"})


def test_bad_refuses_overwrite_without_force(src, tmp_path):
    dst = tmp_path / "p"
    run(src, dst)
    with pytest.raises(EngineError, match="--force"):
        run(src, dst)
    run(src, dst, force=True)


def test_good_sheet_tiles(project, tmp_path):
    d, tl = project()
    video = tmp_path / "final.mp4"
    S = tl.settings
    ff("-f", "lavfi", "-i", f"testsrc2=size={S.width}x{S.height}:rate={S.fps}", "-frames:v", str(tl.total_frames),
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", video)
    out = sheet.make(tl, video, tmp_path / "sheet.png", cols=2)
    w, h = S.width // sheet.TILE_DIV, S.height // sheet.TILE_DIV
    rows = -(-len(tl.segments) // 2)
    assert Image.open(out).size == (2 * (w + 6) + 6, rows * (h + 26 + 6) + 6)


def test_bad_sheet_missing_video(project, tmp_path):
    _, tl = project()
    with pytest.raises(EngineError, match="build first"):
        sheet.make(tl, tmp_path / "none.mp4", tmp_path / "s.png")


def _fill(root, keep):
    for k in cache_prune.KINDS:
        (root / k).mkdir(parents=True, exist_ok=True)
    (root / "segments" / "stale.mp4").write_bytes(b"x")
    (root / "words" / "stale.json").write_text("[]")
    (root / "other").mkdir()
    (root / "other" / "x.mp4").write_bytes(b"x")          # not a known kind dir: untouched
    for n in keep:
        kind = "words" if n.endswith(".json") else "maps" if n.endswith(".jpg") else "segments"
        (root / kind / n).write_bytes(b"k")


def test_good_prune_keeps_referenced_only(project, tmp_path):
    d, _ = project()
    keep, problems = cache_prune.referenced([d])
    assert not problems and any(n.endswith(".jpg") for n in keep) and any(n.endswith(".mp4") for n in keep)
    root = tmp_path / "c"
    _fill(root, keep)
    dry = cache_prune.prune([d], root, dry_run=True)
    assert sorted(dry["would_delete"]) == ["segments/stale.mp4", "words/stale.json"]
    assert (root / "segments" / "stale.mp4").exists()
    r = cache_prune.prune([d], root)
    assert sorted(r["deleted"]) == ["segments/stale.mp4", "words/stale.json"] and r["kept"] == len(
        [n for n in keep if (root / ("words" if n.endswith(".json") else "maps" if n.endswith(".jpg") else "segments") / n).exists()])
    assert (root / "other" / "x.mp4").exists()
    assert all((root / ("maps" if n.endswith(".jpg") else "words" if n.endswith(".json") else "segments") / n).exists() for n in keep)


def test_good_prune_key_tracks_timeline_change(project, tmp_path):
    d, _ = project()
    k1, _ = cache_prune.referenced([d])
    doc = json.loads((d / "master_timeline.json").read_text())
    doc["segments"][0].setdefault("camera_move", {"preset": "ken_burns_zoom_in"})["intensity"] = 1.7
    (d / "master_timeline.json").write_text(json.dumps(doc))
    k2, _ = cache_prune.referenced([d])
    assert k1 != k2


def test_bad_prune_unloadable_project_deletes_nothing(project, tmp_path):
    d, _ = project()
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "master_timeline.json").write_text("{not json")
    root = tmp_path / "c"
    _fill(root, set())
    r = cache_prune.prune([d, bad], root)
    assert r["problems"] and r["deleted"] == [] and (root / "segments" / "stale.mp4").exists()


def test_good_clear_removes_entry_dirs_only(tmp_path):
    from video_engine import cache
    root = tmp_path / "c"
    _fill(root, set())
    r = cache.clear(root)
    assert sorted(r["deleted"]) == ["segments/stale.mp4", "words/stale.json"] and r["skipped_leased"] == []
    assert not any((root / k).exists() for k in cache.KINDS) and (root / "other" / "x.mp4").exists()


def test_bad_clear_on_missing_root_is_noop(tmp_path):
    from video_engine import cache
    assert cache.clear(tmp_path / "absent") == {"deleted": [], "skipped_leased": [], "reclaimed_leases": []}
