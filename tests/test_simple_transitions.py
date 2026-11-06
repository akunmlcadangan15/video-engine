"""Simple transitions (crossfade; page turn and map morph switched off per profile) and profile [render] quality."""
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from video_engine import config, hyperframes, maps, profiles, transitions
from video_engine.gates import layer1, style

from test_style_gates import CUTS, layout as tl_layout, levels

FPS = 30


def _profile(monkeypatch, name="geoverum", **tables):
    prof = profiles.load(name)
    data = prof.data.model_copy(update=tables)
    monkeypatch.setattr(profiles, "of", lambda _tl: profiles.Profile(name, data, "x"))
    return data


def _morph(d):
    d["segments"][2]["gsap_scene_config"]["morph"] = {"asset": "assets/paper.jpg", "at_frame": 20}


# good ---------------------------------------------------------------------------------------------------------------
def test_good_geoverum_cuts_type_changes_and_states_one_map(project):
    _, tl = project(tl_layout(("map", 10, None), ("photo", 10, None), ("map", 10, None), ("map", 10, None),
                              ("archive", 10, None), profile="geoverum"))
    T = profiles.of(tl).transitions
    assert (T.kind, T.same_type, T.fade_s, T.state_s, T.page_turn, T.map_morph) == \
           ("cut", {"map": "state"}, 0.5, 0.35, False, False)
    ws = transitions.windows(tl)
    assert [w.kind for w in ws] == ["state"]                       # only the same-map boundary is not a cut
    assert [(w.f0, w.f1) for w in ws] == [(895, 905)]


def test_good_fade_frames_dissolve_monotonically_from_out_to_in():
    a, b = np.full((8, 8, 3), 0, np.uint8), np.full((8, 8, 3), 240, np.uint8)
    fr = transitions.fade_frames([a] * 6, a, b, [b] * 6)
    v = [float(f.mean()) for f in fr]
    assert len(fr) == 12 and 0 < v[0] < 20 and 220 < v[-1] < 240
    assert all(y > x for x, y in zip(v, v[1:])) and abs(v[5] + v[6] - 240) < 2   # symmetric about the boundary


def test_good_fade_frames_hold_the_outer_frames_across_the_boundary():
    """Before the boundary the incoming shot is its first frame; from it the outgoing shot is its last frame."""
    rng = np.random.default_rng(3)
    tail = [rng.integers(0, 255, (4, 4, 3), np.uint8) for _ in range(2)]
    head = [rng.integers(0, 255, (4, 4, 3), np.uint8) for _ in range(2)]
    last = tail[-1]
    fr = transitions.fade_frames(tail, last, head[0], head)
    w = [k * k * (3 - 2 * k) for k in (np.arange(1, 5) / 5)]
    exp = [tail[0] * (1 - w[0]) + head[0] * w[0], tail[1] * (1 - w[1]) + head[0] * w[1],
           last * (1 - w[2]) + head[0] * w[2], last * (1 - w[3]) + head[1] * w[3]]
    for f, e in zip(fr, exp):
        assert np.abs(f.astype(float) - e).max() <= 1


def test_good_explicit_crossfade_is_named_in_v01(project, monkeypatch):
    def cross(d):
        tl_layout(("photo", 10, None), ("photo", 10, None), profile="geoverum")(d)
        d["segments"][1]["transition_in"] = "crossfade"
    _, tl = project(cross)
    fs = [f for f in style.v01_visual_type(tl) if f.level != "pass"]
    assert fs and "crossfade" in fs[0].message
    assert levels(style.c01_cuts(tl, CUTS), "C01")[0] == "pass"


def test_good_map_morph_allowed_by_default_profile(project):
    d, _ = project()
    Image.new("RGB", (750, 800), (200, 30, 30)).save(d / "assets" / "paper.jpg")
    p, tl = project(_morph)
    assert not [f for f in layer1.t02_integrity(tl, p) if f.level == "fail"]


def test_good_render_defaults_are_the_engine_constants():
    r = profiles.load().render
    assert r.x264("segment") == config.X264_SEGMENT and r.x264("final") == config.X264


def test_good_geoverum_render_quality_values():
    r = profiles.load("geoverum").render
    fin, seg = r.x264("final"), r.x264("segment")
    pick = lambda a, k: a[a.index(k) + 1]
    assert (pick(fin, "-preset"), pick(fin, "-crf"), pick(fin, "-maxrate"), pick(fin, "-bufsize")) == \
           ("medium", "16", "40000k", "80000k")
    assert (pick(seg, "-preset"), pick(seg, "-crf")) == ("medium", "8")
    assert "-tune" not in fin and "-tune" not in seg
    assert (pick(fin, "-pix_fmt"), pick(fin, "-profile:v"), pick(seg, "-pix_fmt")) == ("yuv420p10le", "high10", "yuv420p")
    assert (r.hf_quality, r.hf_crf, r.map_jpeg_quality) == ("high", 8, 95)


def test_good_hf_encode_settings_and_keys_follow_profile_render(project, monkeypatch):
    p, tl = project()
    assert hyperframes.encode_settings(tl) == (hyperframes.HF_QUALITY, hyperframes.HF_CRF, config.X264_SEGMENT)
    k0 = hyperframes.keys(tl.segments[2], tl, p)
    prof = profiles.load()
    _profile(monkeypatch, "default", render=prof.render.model_copy(update={"hf_quality": "high", "hf_crf": 8}))
    assert hyperframes.encode_settings(tl)[:2] == ("high", 8)
    assert hyperframes.keys(tl.segments[2], tl, p) != k0


def test_good_map_jpeg_quality_reaches_the_scene_and_the_map_key(project, monkeypatch, tmp_path):
    p, tl = project()
    sc0 = hyperframes.scene_data(tl.segments[2], tl, p)
    assert "map_q" not in sc0 and maps._q(sc0) == maps.MAP_JPEG_QUALITY
    prof = profiles.load()
    _profile(monkeypatch, "default", render=prof.render.model_copy(update={"map_jpeg_quality": 97}))
    sc = hyperframes.scene_data(tl.segments[2], tl, p)
    assert maps._q(sc) == 97
    src = p / sc["map"]
    assert maps.map_key(src, 1000, 97) != maps.map_key(src, 1000)


# bad ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw", [dict(kind="page_turn"), dict(by_type={"map": "page_turn"}),
                                dict(same_type={"archive": "page_turn"})])
def test_bad_page_turn_off_rejects_any_page_turn(kw):
    with pytest.raises(ValidationError, match="page_turn = false"):
        profiles.TransitionsProfile(page_turn=False, **kw)


@pytest.mark.parametrize("v", [0.05, 1.5])
def test_bad_fade_length_out_of_range(v):
    with pytest.raises(ValidationError):
        profiles.TransitionsProfile(fade_s=v)


def test_bad_map_morph_off_fails_t02(project):
    d, _ = project()
    Image.new("RGB", (750, 800), (200, 30, 30)).save(d / "assets" / "paper.jpg")
    p, tl = project(lambda doc: (_morph(doc), doc.update(profile="geoverum")))
    fs = [f for f in layer1.t02_integrity(tl, p) if f.level == "fail"]
    assert fs and "map_morph = false" in fs[0].message and fs[0].seg == 2


@pytest.mark.parametrize("kw", [dict(final_crf=60), dict(final_preset="ludicrous"), dict(final_tune="film2"),
                                dict(final_maxrate_kbps=10), dict(map_jpeg_quality=101), dict(hf_quality="ultra")])
def test_bad_render_values_rejected(kw):
    with pytest.raises(ValidationError):
        profiles.RenderProfile(**kw)


def test_good_map_to_map_with_different_assets_is_a_hard_cut(project):
    def _pair(d, same=True):
        tl_layout(("map", 10, None), ("map", 10, None), profile="geoverum")(d)
        if not same:
            d["segments"][1]["gsap_scene_config"]["map_asset"] = "assets/map2.jpg"
    _, tl = project(_pair)
    assert transitions.kind_at(tl, 1) == "state" and transitions.windows(tl)[0].kind == "state"
    _, tl2 = project(lambda d: _pair(d, same=False))
    assert transitions.kind_at(tl2, 1) == "cut" and transitions.windows(tl2) == []


def test_bad_explicit_state_needs_the_same_base_map(project):
    from video_engine.util import EngineError
    def _bad_state(d):
        tl_layout(("map", 10, None), ("map", 10, None), profile="geoverum")(d)
        d["segments"][1]["gsap_scene_config"]["map_asset"] = "assets/map2.jpg"
        d["segments"][1]["transition_in"] = "state"
    _, tl = project(_bad_state)
    with pytest.raises(EngineError, match="state"):
        transitions.kind_at(tl, 1)
