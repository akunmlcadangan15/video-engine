"""`engine draft` map scenes: the advanced HyperFrames fields a model may give a map segment (regions with flags, routes,
borders, figures, flag characters, pin flags; beats, globe, props and morph where the profile exposes them) reach
gsap_scene_config unchanged, and anything the renderer cannot execute is rejected with the field's location instead
of being dropped (video_engine/draft_scene.py)."""
from __future__ import annotations

import json

import pytest

from video_engine import draft, timeline
from video_engine.ai import base

from test_draft import client, good_plan, no_sleep, proj, run  # noqa: F401  (fixtures)

PH_POLY = [[0.55, 0.40], [0.62, 0.38], [0.64, 0.50], [0.57, 0.55]]


def map_seg(**extra):
    """Segment 1 of good_plan as a map segment on the fixture map, with `extra` keys."""
    return {"start_frame": 48, "end_frame": 105, "kind": "map", "asset": "map", "narration": "Brigadir Mallaby tewas",
            "why": "lokasi", "map_camera": "zoom_in",
            "map_pins": [{"label": "Surabaya", "x": 0.4, "y": 0.6, "at_s": 0.3, "flag": "id"}], **extra}


def plan(**extra):
    p = good_plan()
    p["segments"][1] = map_seg(**extra)
    p["credits"][1] = {"asset": "map", "credit_line": 3, "watermark_free": True}
    return p


ADVANCED = dict(
    map_regions=[{"entity": "Indonesia", "polygon": PH_POLY, "flag": "id", "at_s": 0.2, "edge": "glow",
                  "label": "INDONESIA"}],
    map_routes=[{"points": [[0.2, 0.7], [0.5, 0.5], [0.7, 0.3]], "at_s": 0.5, "draw_s": 1.0}],
    map_borders=[{"points": [[0.1, 0.1], [0.3, 0.1]], "at_s": 0.0, "dashed": True}],
    map_figures=[{"kind": "number", "value": 17508, "motion": "counter", "suffix": " pulau", "at_s": 0.4,
                  "count_s": 1.0, "position": [0.5, 0.25]}],
    map_flag_characters=[{"flag": "id", "xy": [0.2, 0.8], "at_s": 0.6, "eyes": "look_at:0"}],
)


def _scene(tl):
    return tl.segments[1].gsap_scene_config


# good ---------------------------------------------------------------------------------------------------------------
def test_good_advanced_fields_reach_the_scene(proj):
    inp = draft.collect(proj)
    tl, notes, _ = draft.assemble(inp, plan(**ADVANCED))
    sc = _scene(tl)
    fps = tl.settings.fps
    assert sc.pins[0].flag is not None and sc.pins[0].flag.code == "id"
    assert len(sc.regions) == 1 and sc.regions[0].flag.code == "id" and sc.regions[0].polygon == PH_POLY
    assert sc.regions[0].at_frame == round(0.2 * fps) and sc.regions[0].edge == "glow"
    assert sc.routes[0].points[1] == [0.5, 0.5] and sc.routes[0].at_frame == round(0.5 * fps)
    assert sc.borders[0].dashed and sc.figures[0].motion == "counter" and sc.figures[0].value == 17508
    assert sc.figures[0].at_frame == round(0.4 * fps) and sc.figures[0].suffix == " pulau"
    assert sc.flag_characters[0].eyes == "look_at:0" and sc.flag_characters[0].at_frame == round(0.6 * fps)


def test_good_round_trip_through_saved_timeline(proj, tmp_path):
    inp = draft.collect(proj)
    tl, _, _ = draft.assemble(inp, plan(**ADVANCED))
    timeline.save(tl, tmp_path / "t.json")
    sc = _scene(timeline.load(tmp_path / "t.json"))
    assert sc.regions[0].flag.code == "id" and sc.pins[0].flag.code == "id" and sc.routes and sc.figures


def test_good_old_minimal_draft_unchanged(proj):
    """A draft without the new keys assembles to the same scene as before (no new fields appear)."""
    inp = draft.collect(proj)
    p = plan()
    del p["segments"][1]["map_pins"][0]["flag"]
    sc = _scene(draft.assemble(inp, p)[0])
    d = sc.model_dump(exclude_none=True)
    assert set(d) == {"template", "map_asset", "map_asset_id", "pins", "cards", "camera_preset", "camera_intensity"}
    assert "flag" not in d["pins"][0]


def test_good_schema_exposes_scene_fields_and_validates_before_assembly(proj):
    inp = draft.collect(proj)
    props = draft.schema(inp)["properties"]["segments"]["items"]["properties"]
    for k in ADVANCED:
        assert k in props, k
    assert "flag" in props["map_pins"]["items"]["properties"]
    assert not base.schema_errors(plan(**ADVANCED), draft.schema(inp))


def test_good_draft_loop_keeps_regions_end_to_end(proj):
    """Mocked provider: the saved master_timeline.draft.json carries the region, route and counter."""
    r = run(proj, client({"m38": [plan(**ADVANCED)]}))
    assert r.ok, r.report
    sc = _scene(timeline.load(r.timeline_path))
    assert sc.regions and sc.routes and sc.figures and sc.pins[0].flag


# bad ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("extra, where", [
    (dict(map_regions=[{"entity": "X", "polygon": [[0.1, 0.1], [1.4, 0.2], [0.3, 0.4]]}]), "map_regions[0]"),
    (dict(map_routes=[{"points": [[0.2, 0.2], [0.2, 0.2]]}]), "map_routes[0]"),
    (dict(map_figures=[{"kind": "year", "value": 1945.5}]), "map_figures[0]"),
    (dict(map_regions=[{"entity": "X", "polygon": PH_POLY, "at_s": 99}]), "map_regions[0].at_s"),
    (dict(map_regions=[{"entity": "X", "polygon": PH_POLY, "flag": "zz"}]), "map_regions[0].flag"),
    (dict(map_routes=[{"points": [[0.2, 0.2], [0.4, 0.4]], "colour": "#ff0000"}]), "map_routes[0].colour"),
    (dict(map_lasers=[{"x": 1}]), "map_lasers"),
    (dict(map_flag_characters=[{"flag": "id", "xy": [0.2, 0.8], "eyes": "look_at:4"}]), "flag_characters"),
], ids=["region-point", "route-same", "year-decimal", "late-at_s", "bad-flag", "unknown-key", "unknown-field",
        "look_at-range"])
def test_bad_invalid_scene_data_is_rejected_with_location(proj, extra, where):
    inp = draft.collect(proj)
    with pytest.raises(ValueError) as e:
        draft.assemble(inp, plan(**extra))
    assert "segment 1" in str(e.value) and where in str(e.value), str(e.value)


def test_bad_pin_flag_unknown_key_rejected(proj):
    inp = draft.collect(proj)
    p = plan()
    p["segments"][1]["map_pins"][0]["glow"] = True
    with pytest.raises(ValueError, match=r"segment 1: map_pins\[0\]\.glow"):
        draft.assemble(inp, p)


def test_bad_scene_fields_on_a_non_map_segment(proj):
    inp = draft.collect(proj)
    p = good_plan()
    p["segments"][0]["map_routes"] = ADVANCED["map_routes"]
    with pytest.raises(ValueError, match=r"segment 0: map_routes .*map segment"):
        draft.assemble(inp, p)


def test_bad_profile_gated_fields_rejected_when_not_exposed(proj):
    """Default profile: no globe, props, morph or beats; the schema does not offer them and assembly names the key."""
    inp = draft.collect(proj)
    props = draft.schema(inp)["properties"]["segments"]["items"]["properties"]
    assert not {"map_globe", "map_props", "map_morph", "map_beats"} & set(props)
    with pytest.raises(ValueError, match=r"segment 1: map_globe is not enabled"):
        draft.assemble(inp, plan(map_globe={"keys": [{"at_s": 0, "lat": 0, "lng": 110, "altitude": 2}]}))


def test_bad_structural_errors_reported_before_assembly(proj):
    """schema_errors run first: a wrong type gets its JSON path in the repair prompt."""
    r = run(proj, client({"m38": [plan(map_routes=[{"points": "north"}])] * 3}), max_repairs=1)
    rep = json.loads(r.report_path.read_text())
    errs = [e for rnd in rep["rounds"] for e in rnd["errors"]]
    assert not r.ok and any("map_routes[0].points" in e and "expected array" in e for e in errs), errs


def test_good_spec_targets_exist_in_the_scene_model():
    """Adapter drift guard: every draft key maps onto a real GsapScene field."""
    from video_engine import draft_scene
    for key, spec in draft_scene.FIELDS.items():
        assert spec.target in timeline.GsapScene.model_fields, key


# profile opt-ins (geoverum: globe, props, beats; morph stays off with its [transitions] map_morph = false) -----------
def test_good_geoverum_exposes_beats_globe_props_not_morph(proj):
    inp = draft.collect(proj, profile="geoverum")
    props = draft.schema(inp)["properties"]["segments"]["items"]["properties"]
    assert {"map_beats", "map_globe", "map_props"} <= set(props) and "map_morph" not in props
    assert "map_beats" in draft.prompt(inp) and "map_morph" not in draft.prompt(inp)


def test_good_geoverum_beats_reach_the_scene(proj):
    inp = draft.collect(proj, profile="geoverum")
    tl, _, _ = draft.assemble(inp, plan(map_regions=ADVANCED["map_regions"], map_routes=ADVANCED["map_routes"],
                                        map_beats=[{"at_s": 0, "pins": [0], "region": 0},
                                                   {"at_s": 0.8, "routes": [0], "goal": "arah gerak"}]))
    b = _scene(tl).beats
    assert [x.at_frame for x in b] == [0, round(0.8 * tl.settings.fps)] and b[0].region == 0 and b[1].routes == [0]


def test_bad_geoverum_beats_index_and_map_path(proj):
    inp = draft.collect(proj, profile="geoverum")
    with pytest.raises(ValueError, match=r"segment 1: gsap_scene_config: .*routes index 3"):
        draft.assemble(inp, plan(map_beats=[{"at_s": 0, "routes": [3]}]))
    with pytest.raises(ValueError, match=r"segment 1: map_beats: beats derive the camera"):
        draft.assemble(inp, plan(map_beats=[{"at_s": 0}], map_path=[{"at_s": 0, "x": 0.5, "y": 0.5},
                                                                  {"at_s": 1, "x": 0.4, "y": 0.4}]))


def test_bad_globe_needs_a_world_texture(proj):
    inp = draft.collect(proj, profile="geoverum")
    m = next(a for a in inp.assets if a.id == "map")
    assert abs(m.width / m.height - 2) > 0.02                      # the fixture map is a regional crop
    with pytest.raises(ValueError, match=r"segment 1: map_globe: .*2:1"):
        draft.assemble(inp, plan(map_globe={"keys": [{"at_s": 0, "lat": -2, "lng": 118, "altitude": 2}]}))


def test_good_sidecar_map_bounds_allow_lat_lng_pins(proj):
    (proj / "assets" / "map.json").write_text(json.dumps({"map_bounds": [-11, 95, 6, 141]}))
    inp = draft.collect(proj)
    p = plan()
    p["segments"][1]["map_pins"] = [{"label": "Jakarta", "lat": -6.2, "lng": 106.8, "flag": "ID"}]
    sc = _scene(draft.assemble(inp, p)[0])
    assert sc.map_bounds == [-11, 95, 6, 141] and sc.pins[0].lat == -6.2 and sc.pins[0].flag.code == "id"


def test_bad_lat_lng_pin_without_map_bounds(proj):
    inp = draft.collect(proj)
    p = plan()
    p["segments"][1]["map_pins"] = [{"label": "Jakarta", "lat": -6.2, "lng": 106.8}]
    with pytest.raises(ValueError, match=r"segment 1: map_pins\[0\]: lat/lng need the map's bounds"):
        draft.assemble(inp, p)
