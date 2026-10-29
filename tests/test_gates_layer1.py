"""Layer 1 gates: labelled GOOD and BAD cases."""
from video_engine.gates import layer1


def levels(fs, gate):
    return {f.level for f in fs if f.gate == gate}


# T02 ---------------------------------------------------------------------------------------------------------------
def test_T02_good(project):
    p, tl = project()
    assert levels(layer1.t02_integrity(tl, p), "T02") == {"pass"}


def test_T02_bad_gap(project):
    p, tl = project(lambda d: d["segments"][1].update(start_frame=61))
    fs = layer1.t02_integrity(tl, p)
    assert any("gap" in f.message for f in fs if f.level == "fail")


def test_T02_bad_overlap(project):
    p, tl = project(lambda d: d["segments"][1].update(start_frame=59))
    assert any("overlap" in f.message for f in layer1.t02_integrity(tl, p))


def test_T02_bad_missing_source(project):
    p, tl = project(lambda d: d["segments"][0].update(source_image="assets/nope.jpg"))
    assert any("not found" in f.message for f in layer1.t02_integrity(tl, p))


def test_T02_bad_first_not_zero_and_unknown_preset(project):
    def mut(d):
        d["segments"][0]["start_frame"] = 5
        d["segments"][0]["camera_move"]["preset"] = "spin"
    p, tl = project(mut)
    msgs = " ".join(f.message for f in layer1.t02_integrity(tl, p))
    assert "must start at 0" in msgs and "unknown camera preset" in msgs


# R07 ---------------------------------------------------------------------------------------------------------------
def test_R07_good(project):
    p, tl = project()
    assert levels(layer1.r07_credits(tl, p), "R07") == {"pass"}


def test_R07_bad_uncredited_source(project):
    p, tl = project(lambda d: d["credits_manifest"].pop(0))
    assert any("no credits_manifest entry" in f.message for f in layer1.r07_credits(tl, p))


def test_R07_bad_not_a_url(project):
    p, tl = project(lambda d: d["credits_manifest"][1].update(source_url="pexels clock video"))
    assert any("not an http(s) URL" in f.message for f in layer1.r07_credits(tl, p))


def test_R07_bad_url_missing_from_credits_txt(project):
    p, tl = project()
    (p / "credits.txt").write_text("only one line\n")
    assert sum(f.level == "fail" for f in layer1.r07_credits(tl, p)) == 3


def test_R07_bad_no_credits_txt(project):
    p, tl = project(credits_txt=False)
    assert any("credits.txt missing" in f.message for f in layer1.r07_credits(tl, p))


def test_R07_good_own_licence_needs_no_url(project):
    def mut(d):
        d["audio_chain"] = {"bgm_path": "assets/tone.wav", "bgm_asset_id": "tone"}
        d["credits_manifest"].append({"asset_id": "tone", "source_url": "own:generated", "license": "Own",
                                      "watermark_free": True})
    p, tl = project(mut)
    assert levels(layer1.r07_credits(tl, p), "R07") == {"pass"}


def test_R07_bad_bgm_uncredited(project):
    p, tl = project(lambda d: d.update(audio_chain={"bgm_path": "assets/tone.wav"}))
    assert any("bgm" in f.message for f in layer1.r07_credits(tl, p))


# R14 ---------------------------------------------------------------------------------------------------------------
def test_R14_good(project):
    _, tl = project()
    assert levels(layer1.r14_licence(tl), "R14") == {"pass"}


def test_R14_bad_noncommercial(project):
    _, tl = project(lambda d: d["credits_manifest"][0].update(license="CC BY-NC 4.0"))
    assert any("not in allowlist" in f.message for f in layer1.r14_licence(tl))


def test_R14_bad_watermark_not_declared(project):
    _, tl = project(lambda d: d["credits_manifest"][1].pop("watermark_free"))
    assert any("not declared" in f.message for f in layer1.r14_licence(tl))


def test_R14_bad_watermark_false(project):
    _, tl = project(lambda d: d["credits_manifest"][1].update(watermark_free=False))
    assert any("is false" in f.message for f in layer1.r14_licence(tl))


# R44 ---------------------------------------------------------------------------------------------------------------
def test_R44_good(project, W):
    _, tl = project()
    assert levels(layer1.r44_duplicates(tl, W(("Surabaya", 0, 10), ("menolak", 10, 20))), "R44") == {"pass"}


def test_R44_bad_repeated_word(project, W):
    _, tl = project()
    fs = layer1.r44_duplicates(tl, W(("Surabaya", 0, 10), ("surabaya,", 12, 20)))
    assert any("repeated" in f.message for f in fs)


def test_R44_bad_card_repeats_spoken_line(project, W):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["cards"] = [{"title": "Batas Ultimatum"}]
    _, tl = project(mut)
    fs = layer1.r44_duplicates(tl, W(("batas", 130, 140), ("ultimatum", 140, 150)))
    assert any("repeats the caption" in f.message for f in fs)


def test_R44_good_card_text_spoken_in_other_segment(project, W):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["cards"] = [{"title": "Batas Ultimatum"}]
    _, tl = project(mut)
    assert levels(layer1.r44_duplicates(tl, W(("batas", 10, 20), ("ultimatum", 20, 30))), "R44") == {"pass"}


def test_R44_bad_duplicate_overlay_text(project):
    def mut(d):
        d["segments"][2]["gsap_scene_config"]["pins"].append({"label": "titik a", "x": 0.6, "y": 0.6})
    _, tl = project(mut)
    assert any("duplicates" in f.message for f in layer1.r44_duplicates(tl, []))


# L22 ---------------------------------------------------------------------------------------------------------------
def test_L22_good(project, W):
    _, tl = project()
    assert levels(layer1.l22_captions(tl, W(("a", 0, 10), ("b", 10, 20))), "L22") == {"pass"}


def test_L22_bad_slot(project):
    _, tl = project(lambda d: d.update(caption_style={"slot": "middle-ish"}))
    assert any("slot" in f.message for f in layer1.l22_captions(tl, []))


def test_L22_bad_overlap_and_outside_timeline(project, W):
    _, tl = project()
    fs = layer1.l22_captions(tl, W(("a", 0, 20), ("b", 10, 30), ("c", 170, 200)))
    msgs = " ".join(f.message for f in fs)
    assert "before previous word ends" in msgs and "after timeline end" in msgs
