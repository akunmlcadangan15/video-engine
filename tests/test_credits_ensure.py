import pytest
from test_asset_resolver import registry, row
from test_asset_resolver import roots as _roots

from video_engine import assets
from video_engine.gates import layer1
from video_engine.util import EngineError


@pytest.fixture(name="roots")
def asset_roots(tmp_path, monkeypatch):
    return _roots.__wrapped__(tmp_path, monkeypatch)


def test_ensure_credits_copies_timeline_and_passes_gates(roots, project):
    p, tl = project(lambda d: d['credits_manifest'].pop(0))
    ref = tl.segments[0].source
    registry(p, [row(ref)])
    updated, added = assets.ensure_credits(tl, p)
    assert len(added) == 1 and tl.credit_for(path=ref) is None
    token = layer1.EXTRA_CREDIT_URLS.set(tuple(c['source_url'] for c in added))
    try:
        assert all(f.level == 'pass' for f in layer1.r07_credits(updated, p))
        assert all(f.level == 'pass' for f in layer1.r14_licence(updated))
    finally:
        layer1.EXTRA_CREDIT_URLS.reset(token)


def test_ensure_credits_rejects_missing_metadata(roots, project):
    p, tl = project(lambda d: d['credits_manifest'].pop(0))
    with pytest.raises(EngineError, match='registry row'):
        assets.ensure_credits(tl, p)


def test_resolve_timeline_paths_keeps_old_and_resolves_prefixed(roots, project):
    p, tl = project()
    original = tl.segments[0].source
    assert assets.resolve_timeline_paths(tl, p).segments[0].source == original
    external = roots / 'stock' / 'copy.jpg'
    external.write_bytes((p / original).read_bytes())
    registry(roots / 'stock', [row('copy.jpg')])
    tl.segments[0].source_image = 'download:copy.jpg'
    tl.credits_manifest[0].path = 'download:copy.jpg'
    converted = assets.resolve_timeline_paths(tl, p)
    assert converted.segments[0].source == str(external)
    assert converted.credits_manifest[0].path == str(external)
    assert tl.segments[0].source == 'download:copy.jpg'


def test_resolve_timeline_paths_resolves_both_split_panels(roots, project):
    """A split segment's second panel is rendered from its own map: its prefixed reference is resolved like the top
    panel's (before the fix only the top panel was rewritten and the render looked for '<project>/download:...')."""
    from test_components import _split, tl_n
    n = tl_n(project)
    p, tl = project(_split(n))
    external = roots / 'stock' / 'copy.jpg'
    external.write_bytes((p / 'assets' / 'map.jpg').read_bytes())
    registry(roots / 'stock', [row('copy.jpg')])
    for panel in tl.segments[2].gsap_scene_config.panels():
        panel.map_asset = 'download:copy.jpg'
    converted = assets.resolve_timeline_paths(tl, p)
    assert [sc.map_asset for sc in converted.segments[2].gsap_scene_config.panels()] == [str(external)] * 2
    assert all(sc.map_asset == 'download:copy.jpg' for sc in tl.segments[2].gsap_scene_config.panels())
