import json

import pytest
from test_asset_resolver import roots as _roots

from video_engine import assets
from video_engine.util import EngineError


@pytest.fixture(name="roots")
def asset_roots(tmp_path, monkeypatch):
    return _roots.__wrapped__(tmp_path, monkeypatch)


def test_add_dry_run_dedup_and_validation(roots):
    source = roots / 'input.jpg'
    source.write_bytes(b'picture')
    args = {'licence': 'CC0', 'source': 'Example', 'source_url': 'https://example.com/a'}
    result = assets.register(source, **args, dry_run=True)
    assert not (roots / 'stock' / 'index.csv').exists()
    assert not (roots / 'stock' / 'image').exists()
    assert result['row']['sha256']
    assets.register(source, **args)
    with pytest.raises(EngineError, match='duplicate'):
        assets.register(source, **args)
    with pytest.raises(EngineError, match='invalid'):
        assets.register(source, licence='CC0', source='YouTube', source_url='https://youtube.com/a')
    with pytest.raises(EngineError, match='invalid'):
        assets.register(source, licence='CC0', source='Example')


def test_add_project_credit(roots, project):
    p, _ = project()
    source = roots / 'input.jpg'
    source.write_bytes(b'unique')
    result = assets.register(source, licence='CC0', source='Example', source_url='https://example.com/a',
                             dest='project', project=p, asset_id='registered')
    timeline = json.loads((p / 'master_timeline.json').read_text())
    assert any(c['asset_id'] == 'registered' for c in timeline['credits_manifest'])
    assert 'https://example.com/a' in (p / 'credits.txt').read_text()
    assert (p / result['row']['file']).read_bytes() == b'unique'


def test_cli_add_dry_run(roots, capsys):
    from video_engine.cli import main

    source = roots / 'cli.jpg'
    source.write_bytes(b'cli')
    assert main(['assets', 'add', str(source), '--licence', 'CC0', '--source', 'Example',
                 '--source-url', 'https://example.com/a', '--dry-run', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['dry_run'] is True
    assert not (roots / 'stock' / 'index.csv').exists()
