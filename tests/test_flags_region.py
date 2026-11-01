"""Flag schema, assets, profile bounds and scene wiring."""
import json
import subprocess

import pytest
from pydantic import ValidationError

from video_engine import assets, config, hyperframes, profiles
from video_engine.timeline import Flag, Pin, Region
from video_engine.util import EngineError

SQ = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]


def test_flag_normalization_and_invalid_code():
    assert Flag.model_validate('GB').code == 'gb'
    with pytest.raises(EngineError, match='closest'):
        Flag.model_validate('zz')
    assert Pin(label='X', x=0, y=0, flag='FR').flag.code == 'fr'
    assert Region(entity='X', polygon=SQ, flag='th').flag.code == 'th'


@pytest.mark.parametrize('field,value', [('alpha', 1.1), ('reveal_s', 0.1), ('edge_px', 8), ('glow_px', 15)])
def test_flag_bounds(field, value):
    with pytest.raises(ValidationError):
        Flag.model_validate({'code': 'gb', field: value})


@pytest.mark.parametrize('value', [2.9, 7.1])
def test_pin_height_bounds(value):
    with pytest.raises(ValidationError):
        profiles.FlagsProfile(pin_height_pct=value)


def test_scene_copy_and_credits(project, tmp_path):
    def mutate(d):
        sc = d['segments'][2]['gsap_scene_config']
        sc['regions'] = [{'entity': 'Sekutu', 'polygon': SQ, 'flag': 'GB',
                          'changes': [{'entity': 'Indonesia', 'at_frame': 15, 'flag': 'fr'}]}]
        sc['pins'][0]['flag'] = 'gb'
    path, tl = project(mutate)
    credited, added = assets.ensure_credits(tl, path)
    credited, added_again = assets.ensure_credits(credited, path)
    assert {x['asset_id'] for x in added if x['asset_id'].startswith('flag_')} == {'flag_gb', 'flag_fr'}
    assert all(x['license'] == 'MIT' and x['author'] == 'lipis/flag-icons contributors'
               and x['source_url'] == 'https://github.com/lipis/flag-icons'
               for x in added if x['asset_id'].startswith('flag_'))
    assert not [x for x in added_again if x['asset_id'].startswith('flag_')]
    build, scene = hyperframes.build_dir(credited.segments[2], credited, path, tmp_path / 'hf')
    flag = scene['regions'][0]['flag']
    assert flag['mask'] == 'polygon'
    assert flag['href'].startswith('assets/') and len(flag['href'].split('/')[-1]) == 20
    assert (build / flag['href']).is_file()
    assert scene['pins'][0]['flag']['pin_height_pct'] * scene['height'] / 100 == 86.4
    assert scene['regions'][0]['changes'][0]['flag']['href'] != flag['href']
    assert 'flagclip' in (build / 'index.html').read_text()
    result = hyperframes.probe_dom(build, [1.0], 1080, 1920)
    assert result['times']


def test_unset_flag_scene_and_key(project):
    def mutate(d):
        d['segments'][2]['gsap_scene_config']['regions'] = [{'entity': 'Sekutu', 'polygon': SQ}]
    path, tl = project(mutate)
    seg = tl.segments[2]
    before = hyperframes.scene_data(seg, tl, path), hyperframes.keys(seg, tl, path)
    region = seg.gsap_scene_config.regions[0]
    region.flag = Flag.model_validate('gb')
    assert hyperframes.keys(seg, tl, path) != before[1]
    region.flag = None
    assert (hyperframes.scene_data(seg, tl, path), hyperframes.keys(seg, tl, path)) == before


@pytest.mark.parametrize('globe', [False, True])
def test_flag_dom_clip(project, tmp_path, globe):
    from PIL import Image

    def mutate(d):
        sc = d['segments'][2]['gsap_scene_config']
        sc['regions'] = [{'entity': 'Sekutu', 'polygon': SQ, 'flag': 'gb'}]
        if globe:
            sc['map_asset'] = 'assets/world-flag-test.jpg'
            sc['pins'] = []
            sc['globe'] = {'keys': [{'at_frame': 0, 'lat': 0, 'lng': 0, 'altitude': 3}]}
    path, tl = project(mutate)
    if globe:
        Image.new('RGB', (512, 256), 'blue').save(path / 'assets/world-flag-test.jpg')
    build, scene = hyperframes.build_dir(tl.segments[2], tl, path, tmp_path / 'hf')
    if globe:
        assert scene['regions'][0]['ll']
    script = hyperframes.PROBE_JS.read_text().replace(
        'return { rects, paints, lines };',
        '''const image = document.querySelector('image[clip-path]');
        rects.push({id:'flag-test', href:image?.getAttribute('href'), clip:image?.getAttribute('clip-path'),
                    polygon:!!document.querySelector('clipPath[id^=flagclip] polygon')});
        return { rects, paints, lines };''')
    probe = tmp_path / 'flag_probe.mjs'
    probe.write_text(script)
    env = config.hf_env()
    env.update(ENGINE_PUPPETEER=str(config.PUPPETEER), ENGINE_CHROME=str(config.CHROME))
    if globe:
        env['ENGINE_WEBGL'] = '1'
    with hyperframes.serve(build) as url:
        result = subprocess.run([str(config.NODE), str(probe), url + '/index.html', '-', '1', '1080', '1920'], env=env,
                                text=True, capture_output=True, check=True, timeout=30)
    dom = next(r for r in json.loads(result.stdout)['times'][0]['rects'] if r['id'] == 'flag-test')
    assert dom == {'id': 'flag-test', 'href': scene['regions'][0]['flag']['href'],
                   'clip': 'url(#flagclip0)', 'polygon': True}


def test_svg_aspect_reads_viewbox(tmp_path):
    (tmp_path / 'a.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 480"></svg>')
    (tmp_path / 'b.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"></svg>')
    (tmp_path / 'c.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    assert [hyperframes._svg_aspect(tmp_path / f'{n}.svg') for n in 'abc'] == [1.3333, 1.0, 1.3333]


TALL = [[0.45, 0.2], [0.55, 0.2], [0.55, 0.8], [0.45, 0.8]]


@pytest.mark.parametrize('fit', ['cover', 'contain', 'stretch'])
def test_flag_fit_rect_on_tall_region(project, tmp_path, fit):
    """Chrome draws an <image>-referenced SVG as `meet` whatever preserveAspectRatio says, so the template
    must size the image itself: cover spans the region's full height, contain its width, stretch the bbox."""
    def mutate(d):
        d['segments'][2]['gsap_scene_config']['regions'] = [
            {'entity': 'Sekutu', 'polygon': TALL, 'flag': {'code': 'th', 'fit': fit}}]
    path, tl = project(mutate)
    build, scene = hyperframes.build_dir(tl.segments[2], tl, path, tmp_path / 'hf')
    script = hyperframes.PROBE_JS.read_text().replace(
        'return { rects, paints, lines };',
        '''const im = document.querySelector('image[clip-path]'), pg = document.querySelector('clipPath[id^=flagclip] polygon');
        const b = pg.getBBox(), a = n => +im.getAttribute(n);
        rects.push({id:'fit-test', img:[a('x'), a('y'), a('width'), a('height')], box:[b.x, b.y, b.width, b.height],
                    par: im.getAttribute('preserveAspectRatio')});
        return { rects, paints, lines };''')
    probe = tmp_path / 'fit_probe.mjs'
    probe.write_text(script)
    env = config.hf_env()
    env.update(ENGINE_PUPPETEER=str(config.PUPPETEER), ENGINE_CHROME=str(config.CHROME))
    with hyperframes.serve(build) as url:
        result = subprocess.run([str(config.NODE), str(probe), url + '/index.html', '-', '1', '1080', '1920'], env=env,
                                text=True, capture_output=True, check=True, timeout=30)
    dom = next(r for r in json.loads(result.stdout)['times'][0]['rects'] if r['id'] == 'fit-test')
    (x, y, w, h), (bx, by, bw, bh) = dom['img'], dom['box']
    assert dom['par'] == 'none' and scene['regions'][0]['flag']['aspect'] == pytest.approx(4 / 3, abs=1e-3)
    assert abs((x + w / 2) - (bx + bw / 2)) < 1 and abs((y + h / 2) - (by + bh / 2)) < 1   # centred on the region
    if fit == 'stretch':
        assert (w, h) == pytest.approx((bw, bh), abs=1)
    else:
        assert w / h == pytest.approx(4 / 3, rel=1e-3)
        assert (h == pytest.approx(bh, abs=1) and w >= bw) if fit == 'cover' else (w == pytest.approx(bw, abs=1) and h <= bh)


# automatic fit (profile default `auto`) -----------------------------------------------------------------------------
MYANMAR = [[(92.2 + 180) / 360, (90 - 28.5) / 180], [(101.2 + 180) / 360, (90 - 28.5) / 180],
           [(101.2 + 180) / 360, (90 - 10.0) / 180], [(92.2 + 180) / 360, (90 - 10.0) / 180]]   # bbox lng/lat


def test_good_auto_fit_contains_only_when_cover_would_cut_too_much():
    assert hyperframes.flag_fit(4 / 3, 4 / 3, 0.35) == 'cover'
    assert hyperframes.flag_fit(1.0, 4 / 3, 0.35) == 'cover'          # loses 25 %: still recognisable
    assert hyperframes.flag_fit(0.5, 4 / 3, 0.35) == 'contain'        # a tall strip would be all that is left
    assert hyperframes.flag_fit(4.0, 4 / 3, 0.35) == 'contain'        # very wide (Chile on its side)
    assert hyperframes.flag_fit(0.0, 4 / 3, 0.35) == 'cover'          # degenerate bbox: keep the old default


def test_good_region_aspect_uses_cos_latitude_on_the_globe():
    r = hyperframes.region_aspect(MYANMAR, 1080, 1920, True)
    assert r == pytest.approx(9.0 * 0.9455 / 18.5, rel=0.01)       # cos(19.25 deg)
    assert hyperframes.region_aspect(SQ, 1000, 500, False) == pytest.approx(2.0)   # flat map px


@pytest.mark.parametrize('globe', [False, True])
def test_good_scene_resolves_auto_fit_per_region(project, globe):
    from PIL import Image
    wide = [[0.2, 0.45], [0.8, 0.45], [0.8, 0.6], [0.2, 0.6]]

    def mutate(d):
        sc = d['segments'][2]['gsap_scene_config']
        tall = MYANMAR if globe else TALL
        sc['regions'] = [{'entity': 'Sekutu', 'polygon': tall, 'flag': 'mm'},
                         {'entity': 'Indonesia', 'polygon': SQ, 'flag': 'gb',
                          'changes': [{'entity': 'Sekutu', 'at_frame': 15, 'flag': 'fr'}]},
                         {'entity': 'Sekutu', 'polygon': TALL, 'flag': {'code': 'th', 'fit': 'cover'}}]
        if globe:
            sc.update(map_asset='assets/world-flag-test.jpg', pins=[],
                      globe={'keys': [{'at_frame': 0, 'lat': 20, 'lng': 97, 'altitude': 2}]})
        else:
            sc['regions'].append({'entity': 'Indonesia', 'polygon': wide, 'flag': 'id'})
    path, tl = project(mutate)
    if globe:
        Image.new('RGB', (512, 256), 'blue').save(path / 'assets/world-flag-test.jpg')
    sc = hyperframes.scene_data(tl.segments[2], tl, path)
    fits = [r['flag']['fit'] for r in sc['regions']]
    assert fits[:3] == ['contain', 'cover', 'cover']                 # tall -> contain, square -> cover, explicit wins
    assert sc['regions'][1]['changes'][0]['flag']['fit'] == 'cover'
    assert 'auto_max_loss' not in sc['regions'][0]['flag']
    if not globe:
        assert fits[3] == 'contain'                                  # 0.6 x 0.15 of the map: too wide for cover


def test_good_geoverum_and_default_profiles_choose_fit_automatically():
    for name in ('default', 'geoverum'):
        assert profiles.load(name).data.flags.fit == 'auto'
