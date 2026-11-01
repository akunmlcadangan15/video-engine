import csv
import json

from video_engine import assets, cli


def test_backfill_preserves_rows_and_never_infers_licence(tmp_path):
    root = tmp_path / "stock"
    footage = root / "footage"
    footage.mkdir(parents=True)
    (footage / "px_old.mp4").write_bytes(b"old")
    (footage / "px_new.mp4").write_bytes(b"new")
    (footage / "commons.jpg").write_bytes(b"image")
    (footage / "commons.json").write_text(json.dumps({
        "descriptionurl": "https://example.com/file",
        "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}, "Artist": {"value": "Author"}},
    }))
    index = root / "index.csv"
    index.write_text("file,source_id,custom\nfootage/px_old.mp4,old,keep\n")
    original = index.read_bytes()
    preview = assets.backfill(root, dry_run=True)
    assert index.read_bytes() == original
    assert not (root / "index.csv.lock").exists()
    assert preview["missing_licence"] == ["footage/px_new.mp4"]
    assert len(preview["added"]) == 2
    assert preview["added"][0]["licence_credit"] == "CC BY 4.0 | Author"
    assert preview["added"][1]["source_id"] == "pexels"
    assert assets.backfill(root)["added"] == preview["added"]
    with index.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["custom"] == "keep"
    assert assets.backfill(root, dry_run=True)["added"] == []


def test_cli_backfill_uses_configured_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(assets.config, "STOCK_DIRS", (tmp_path,))
    assert cli.main(["assets", "backfill", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["root"] == str(tmp_path)
    assert list(tmp_path.iterdir()) == []
