import csv

import pytest

from video_engine import assets, config
from video_engine.util import EngineError


@pytest.fixture
def roots(tmp_path, monkeypatch):
    for name in ('open', 'vendor', 'stock', 'download', 'cache', 'project', 'stock2'):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(config, 'OPEN_ASSETS', tmp_path / 'open')
    monkeypatch.setattr(config, 'VENDOR', tmp_path / 'vendor')
    monkeypatch.setattr(config, 'STOCK_DIRS', (tmp_path / 'stock',))
    monkeypatch.setattr(config, 'DOWNLOAD_DIR', tmp_path / 'download')
    monkeypatch.setattr(config, 'CACHE_DIR', tmp_path / 'cache')
    assets.clear_index()
    return tmp_path


def registry(root, rows):
    with (root / 'index.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['file', 'licence', 'source', 'source_url', 'author'])
        writer.writeheader()
        writer.writerows(rows)


def row(path, **extra):
    return {'file': path, 'licence': 'CC0', 'source': 'test', 'source_url': 'https://example.com/a', **extra}


def put(root, name, data=b'content'):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_priority_ambiguity_hash_and_download_visibility(roots, monkeypatch):
    for name in ('project', 'open', 'stock', 'download', 'stock2'):
        put(roots / name, 'assets/picture.jpg', name.encode())
        registry(roots / name, [row('assets/picture.jpg')])
    a = assets.resolve('assets/picture.jpg', project=roots / 'project')
    assert a.tier == 'project' and a.sha256 == assets.resolve('project:assets/picture.jpg', project=roots / 'project').sha256
    assert assets.resolve('open:assets/picture.jpg').tier == 'open'
    monkeypatch.setattr(config, 'STOCK_DIRS', (roots / 'stock', roots / 'stock2'))
    with pytest.raises(EngineError, match='ambiguous'):
        assets.resolve('download:assets/picture.jpg')
    put(roots / 'download', 'raw.jpg')
    with pytest.raises(EngineError, match='not found'):
        assets.resolve('download:raw.jpg')
    with pytest.raises(EngineError, match='not found'):
        assets.resolve('open:missing.jpg')


def test_licence_and_epidemic_audio(roots):
    put(roots / 'stock', 'x.mp3')
    registry(roots / 'stock', [row('x.mp3', source='Other')])
    with pytest.raises(EngineError, match='Epidemic'):
        assets.resolve('download:x.mp3', kind='music')
    registry(roots / 'stock', [row('x.mp3', source='Epidemic Sound', licence='Epidemic Sound licence')])
    assert assets.resolve('download:x.mp3', kind='music').kind == 'music'
    put(roots / 'open', 'bad.jpg')
    registry(roots / 'open', [row('bad.jpg', licence='', source_url='')])
    with pytest.raises(EngineError, match='registry row'):
        assets.resolve('open:bad.jpg')


def test_flag_icon_and_find_rank(roots):
    put(roots / 'open', '2d/flags/flag-icons/flags/4x3/id.svg', b'<svg/>')
    put(roots / 'open', '2d/map-icons/maki/icons/city.svg', b'<svg/>')
    assert assets.flag_for('id').endswith('/id.svg')
    assert assets.resolve(assets.flag_for('id')).licence == 'MIT'
    assert 'maki/icons/city.svg' in assets.icon_for('city')
    with pytest.raises(EngineError, match='unknown flag'):
        assets.flag_for('iz')
    put(roots / 'project', 'city.jpg')
    registry(roots / 'project', [row('city.jpg')])
    matches = assets.find('city', project=roots / 'project')
    assert matches[0].tier == 'project'
    assert list((roots / 'cache' / 'asset_index').glob('*.json'))


def test_find_invalidates_registry_cache(roots):
    put(roots / 'stock', 'map.jpg')
    registry(roots / 'stock', [row('map.jpg', licence='')])
    assert assets.find('map') == []
    registry(roots / 'stock', [row('map.jpg', licence='CC0')])
    assert assets.find('map')[0].ref == 'download:map.jpg'
