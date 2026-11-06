"""Infographic blocks and the local asset library: deterministic PNG output, declarative data, licence rows."""
import csv
import json

import pytest
from PIL import Image

from video_engine import blocks, cli, library
from video_engine.util import EngineError


def _rgba(path):
    im = Image.open(path)
    assert im.mode == "RGBA"
    return im


def test_good_chart_accepts_object_and_list_and_writes_a_transparent_png(tmp_path):
    a = blocks.render("chart", {"title": "Nilai", "bars": [{"label": "Satu", "value": 12},
                                                          {"label": "Dua", "value": -3, "display": "0"}]},
                      tmp_path / "a.png", width=540, height=360)
    b = blocks.render("chart", [{"label": "Satu", "value": 12}], tmp_path / "b.png")
    for p in (a, b):
        im = _rgba(p)
        assert im.getpixel((0, 0))[3] == 0
    assert _rgba(a).size == (540, 360)


def test_good_timeline_and_infographic_accept_a_list_and_a_json_file(tmp_path):
    data = tmp_path / "data.json"
    data.write_text(json.dumps([{"label": "A", "at": 0}, {"label": "B", "at": 1, "detail": "x"}]))
    assert _rgba(blocks.render("timeline", f"@{data}", tmp_path / "t.png")).size == (1080, 720)
    assert _rgba(blocks.render("infographic", [{"label": "A", "value": "42%"}], tmp_path / "i.png")).size == (1080, 720)


@pytest.mark.parametrize("kind,data,msg", [
    ("chart", {"bars": []}, "at least one bar"),
    ("chart", {"bars": [{"label": "A"}]}, "numeric value"),
    ("timeline", {"events": [{"label": "A", "at": 0}]}, "at least two events"),
    ("timeline", {"events": [{"label": "A", "at": 0}, {"at": 1}]}, "needs label and at"),
    ("infographic", {"items": [{"value": 1}]}, "needs label and value"),
    ("chart", 42, "object or array"),
], ids=["BAD-empty-chart", "BAD-chart-value", "BAD-short-timeline", "BAD-timeline-label",
        "BAD-infographic-label", "BAD-data-type"])
def test_bad_block_data_is_a_clean_engine_error(tmp_path, kind, data, msg):
    with pytest.raises(EngineError, match=msg):
        blocks.render(kind, data, tmp_path / "x.png")


def test_bad_block_unknown_kind_and_bad_json(tmp_path):
    with pytest.raises(EngineError, match="unknown block"):
        blocks.render("pie", [], tmp_path / "x.png")
    with pytest.raises(EngineError, match="not valid JSON"):
        blocks.render("chart", "not json", tmp_path / "x.png")
    assert cli.main(["block", "chart", "--data", "not json", "--out", str(tmp_path / "c.png")]) == 1


def _catalog(tmp_path):
    root = tmp_path / "open"
    (root / "icons").mkdir(parents=True)
    (root / "icons" / "peta.png").write_bytes(b"png-bytes")
    with (root / "catalog.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "name", "source", "licence", "files", "size", "attribution"])
        w.writerow(["icons", "Peta Ikon", "Open Asset Kit https://example.org/kit", "CC0", "1", "1 KB", "Tim"])
    library.clear_index()
    return root


def _stock(tmp_path):
    root = tmp_path / "stock"
    (root / "footage").mkdir(parents=True)
    (root / "photos").mkdir()
    (root / "footage" / "kota.mp4").write_bytes(b"video")
    (root / "photos" / "pasar.jpg").write_bytes(b"photo")
    (root / "index.csv").write_text("file,source_id,source_url,licence_credit,first_used_project,sha256\n"
                                    "footage/kota.mp4,es-1,https://stock.example/kota,Stock License | A,proj,0\n")
    (root / "photos" / "pasar.json").write_text(json.dumps({
        "descriptionurl": "https://commons.example/pasar",
        "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}, "Artist": {"value": "B"}}}))
    library.clear_index()
    return root


def test_good_search_filters_kinds_tokens_and_limit(tmp_path):
    open_root, stock_root = _catalog(tmp_path), _stock(tmp_path)
    rows = library.search("kota", open_assets_root=open_root, stock_roots=(stock_root,))
    assert [r.kind for r in rows] == ["broll"] and rows[0].source_url == "https://stock.example/kota"
    photos = library.search("pasar", kind="photos", open_assets_root=open_root, stock_roots=(stock_root,))
    assert photos and photos[0].license == "CC BY 4.0" and photos[0].author == "B"
    assert len(library.search("", limit=1, open_assets_root=open_root, stock_roots=(stock_root,))) == 1
    assert library.search("missing", open_assets_root=open_root, stock_roots=(stock_root,)) == []


def test_good_credit_row_copies_the_file_and_needs_a_source_for_licensed_assets(tmp_path):
    open_root, stock_root = _catalog(tmp_path), _stock(tmp_path)
    asset = library.credit_for_path(open_root / "icons" / "peta.png", open_assets_root=open_root,
                                    stock_roots=(stock_root,))
    assert asset is not None and asset.source_url == "https://example.org/kit" and asset.bytes > 0
    row = library.credit_row(asset, tmp_path / "proj")
    assert row["asset_id"] == "peta" and row["path"] == "assets/peta.png"
    assert (tmp_path / "proj" / "assets" / "peta.png").read_bytes() == b"png-bytes"
    assert library.credit_for_path(tmp_path / "elsewhere.png", open_assets_root=open_root,
                                   stock_roots=(stock_root,)) is None

    no_url = library.Asset(path=str(open_root / "icons" / "peta.png"), kind="icons", name="x", license="CC0")
    with pytest.raises(EngineError, match="no source URL"):
        library.credit_row(no_url, tmp_path / "proj2")


def test_good_cli_assets_search_and_copy(tmp_path, capsys):
    open_root, stock_root = _catalog(tmp_path), _stock(tmp_path)
    assert cli.main(["assets", "--query", "kota", "--open-assets", str(open_root),
                     "--stock-root", str(stock_root)]) == 0
    assert "kota.mp4" in capsys.readouterr().out
    assert cli.main(["assets", "--copy", str(stock_root / "footage" / "kota.mp4"),
                     "--project", str(tmp_path / "p"), "--open-assets", str(open_root),
                     "--stock-root", str(stock_root)]) == 0
    assert "https://stock.example/kota" in capsys.readouterr().out
    assert (tmp_path / "p" / "assets" / "kota.mp4").is_file()
