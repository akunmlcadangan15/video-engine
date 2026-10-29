"""GOOD / BAD: [print] helpers (region edge ink per surface, text roles per surface, typeface names)."""
from video_engine import legibility as L, printstyle, profiles

PR = profiles.load("geoverum").print
SEKUTU = "#4B4D8B"


def _l(hex_):
    return L.rgb_oklch(L.hex_rgb(hex_))[0]


def test_good_region_edge_on_paper_is_dark_entity_ink():
    e = printstyle.edge_ink(SEKUTU, PR, "paper")
    assert abs(_l(e) - PR.region_edge_l) < 0.01
    assert abs(L.rgb_oklch(L.hex_rgb(e))[2] - L.rgb_oklch(L.hex_rgb(SEKUTU))[2]) < 6     # same hue


def test_good_region_edge_on_dark_imagery_is_light():
    e = printstyle.edge_ink(SEKUTU, PR, "dark")
    assert abs(_l(e) - PR.region_edge_l_dark) < 0.01 and _l(e) > 0.6


def test_bad_paper_edge_on_dark_imagery_would_vanish():
    """The paper edge (L ~ .4) on a night satellite map (L ~ .25) has almost no contrast: the dark surface needs its own."""
    dark_map = "#262C34"
    paper_edge = printstyle.edge_ink(SEKUTU, PR, "paper")
    dark_edge = printstyle.edge_ink(SEKUTU, PR, "dark")
    lc = lambda fg: abs(float(L.apca_lc(L.apca_y(L.hex_rgb(fg)), L.apca_y(L.hex_rgb(dark_map)))))
    assert lc(paper_edge) < 15 < 45 < lc(dark_edge)


def test_good_roles_paper_garamond_dark_inter():
    assert printstyle.roles("paper", PR)["label"][0] != printstyle.roles("dark", PR)["label"][0]
    assert printstyle.roles("dark", PR)["label"][0] == "inter"


def test_good_and_bad_typeface_of_css_family():
    assert printstyle.typeface("'EB Garamond', serif") == printstyle.typeface("EB Garamond")
    assert printstyle.typeface("Kalam") != printstyle.typeface("EB Garamond")
