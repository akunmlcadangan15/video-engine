"""CLI wiring for the auxiliary tools: clean errors from sheet/import-shots, plus preview, audio and doctor."""
import csv
import shutil

from conftest import ff
from video_engine import cli, doctor


def test_bad_sheet_cols_zero_is_a_clean_cli_error(project, tmp_path, capsys):
    p, _ = project()
    assert cli.main(["sheet", str(p), "--cols", "0", "--video", str(tmp_path / "none.mp4")]) == 1
    err = capsys.readouterr().err
    assert "at least 1" in err and "Traceback" not in err


def test_bad_import_substitute_without_equals_is_a_clean_cli_error(tmp_path, assets, capsys):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copyfile(assets / "still.jpg", src / "a.jpg")
    ff("-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000", "-t", "2", src / "vo.wav")
    with (src / "shots.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["shot", "type", "file", "title", "source_url", "author", "licence", "on_screen_s"])
        w.writerow(["1", "source", "a.jpg", "", "https://ex.org/a", "A", "PD", "0-2"])
    (src / "credits.txt").write_text("https://ex.org/a\n")
    rc = cli.main(["import-shots", str(src / "shots.csv"), str(tmp_path / "p"), "--search", str(src),
                   "--credits", str(src / "credits.txt"), "--voiceover", str(src / "vo.wav"),
                   "--substitute", "broken"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "expects OLD=NEW" in err and "Traceback" not in err


def test_good_cli_preview_and_audio(project, tmp_path, capsys):
    p, tl = project()
    png = tmp_path / "frame.png"
    assert cli.main(["preview", str(p), "--seg", "0", "--time", "0.5", "--out", str(png)]) == 0
    assert png.is_file() and png.stat().st_size > 0
    doc = p.joinpath("master_timeline.json")
    import json
    d = json.loads(doc.read_text())
    d["captions"] = [{"word": "Halo", "start_frame": 0, "end_frame": 10}]
    doc.write_text(json.dumps(d))
    assert cli.main(["audio", str(p)]) == 0
    assert "Halo" in capsys.readouterr().out


def test_bad_preview_all_with_a_png_out_needs_sheet(project, tmp_path, capsys):
    p, _ = project()
    assert cli.main(["preview", str(p), "--all", "--out", str(tmp_path / "x.png")]) == 1
    assert "needs --sheet" in capsys.readouterr().err
    assert cli.main(["preview", str(p), "--all", "--seg", "0"]) == 1
    assert "drop --seg/--time" in capsys.readouterr().err


def test_bad_build_of_a_locked_project_is_refused(project, capsys):
    from video_engine import batch
    p, _ = project()
    with batch.ProjectLock(p):
        assert cli.main(["build", str(p)]) == 1
    assert "already locked by another build or batch" in capsys.readouterr().err
    assert not (p / "out" / "final.mp4").exists()


def test_good_cli_doctor(monkeypatch):
    monkeypatch.setattr(doctor, "check", lambda: [doctor.Check("node", True, "v1", "v1")])
    assert cli.main(["doctor"]) == 0


def test_good_cli_deliver_exports_package(project, tmp_path):
    import json
    from conftest import ff
    from video_engine import manifest
    p, tl = project()
    S = tl.settings
    out = p / "out"
    out.mkdir()
    final = out / "final.mp4"
    ff("-f", "lavfi", "-i", f"testsrc2=size={S.width}x{S.height}:rate={S.fps}", "-frames:v", str(tl.total_frames),
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", final)
    rep = {"ok": True, "build_id": "b1", "final_sha256": manifest.sha256(final, fresh=True)}
    (out / "build_report.json").write_text(json.dumps(rep))
    (out / manifest.MANIFEST).write_text(json.dumps({"build_id": "b1"}))
    assert cli.main(["deliver", str(p), "--export", str(tmp_path / "pkg")]) == 0
    final_dir = tmp_path / "pkg" / "07 - 2026-10-28 - Uji Coba" / "05 Final"
    assert (final_dir / "07 - Uji Coba - final.mp4").is_file()
    assert (final_dir / "07 - Uji Coba - final.srt").is_file()
    assert (final_dir / "07 - Uji Coba - metadata.json").is_file()
