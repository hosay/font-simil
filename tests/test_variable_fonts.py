"""Named-instance helpers for variable fonts (fontTools-based, crash-safe)."""

from pathlib import Path

from fontmatch.fonts import load
from fontmatch.fonts.variable import axis_values, choose_instance, named_instances

FIX = Path(__file__).parent / "fixtures_variable"
ROBOTO = Path(__file__).parent / "fixtures" / "Roboto-Regular.ttf"


def test_static_font_has_no_instances():
    assert named_instances(load(ROBOTO).tt) == []
    assert choose_instance(load(ROBOTO).tt, ["Regular"]) is None


def test_josefin_instances_and_choice():
    tt = load(FIX / "JosefinSlab[wght].ttf").tt
    names = [n for n, _ in named_instances(tt)]
    assert names[0] == "Thin" and "Regular" in names and "Bold" in names
    assert choose_instance(tt, ["Regular"]) == ("Regular", {"wght": 400.0})
    assert choose_instance(tt, ["Nope"]) is None


def test_axis_values_in_fvar_order_with_defaults():
    tt = load(FIX / "JosefinSlab[wght].ttf").tt
    assert axis_values(tt, {"wght": 700.0}) == [700.0]
    assert axis_values(tt, {}) == [100.0]  # default
