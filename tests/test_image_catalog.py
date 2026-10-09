"""Candidate catalog helpers."""
import pytest

from fontmatch.image.catalog import gf_category, resolve_source


def test_gf_category_parses_metadata(tmp_path):
    (tmp_path / "METADATA.pb").write_text('name: "X"\ndesigner: "Y"\ncategory: "HANDWRITING"\n')
    assert gf_category(tmp_path) == "handwriting"


def test_gf_category_missing(tmp_path):
    assert gf_category(tmp_path) is None


def test_resolve_source_relative_then_by_filename(tmp_path):
    root = tmp_path / "gf"
    (root / "ofl" / "abel").mkdir(parents=True)
    font = root / "ofl" / "abel" / "Abel-Regular.ttf"
    font.write_bytes(b"x")
    assert resolve_source("ofl/abel/Abel-Regular.ttf", [root], {}) == font
    assert resolve_source("Abel-Regular.ttf", [root], {"Abel-Regular.ttf": font}) == font
    assert resolve_source("Nope.ttf", [root], {}) is None


def test_catalog_json_roundtrip(tmp_path):
    from fontmatch.image.catalog import CatalogEntry, load_catalog_json, save_catalog_json

    entries = [
        CatalogEntry(
            name="A.ttf", family="A", base_family="a", subfamily="Regular",
            path=tmp_path / "A.ttf", license_id="OFL-1.1", category="sans", is_italic=False,
        )
    ]  # fmt: skip
    save_catalog_json(entries, tmp_path / "catalog.json")
    assert load_catalog_json(tmp_path / "catalog.json") == entries


def test_family_group_merges_siblings_only():
    from fontmatch.image.catalog import family_group

    assert family_group("Alegreya Sans SC") == family_group("Alegreya Sans") == "alegreya sans"
    assert family_group("Playfair Display") == "playfair"
    assert family_group("DM Sans 9pt") == "dm sans"
    assert family_group("Roboto Flex") == "roboto"
    assert family_group("Fira Sans Condensed") == "fira sans"
    assert family_group("Bitter Thin") == "bitter"
    # Different designs stay apart.
    assert family_group("Roboto Serif") != family_group("Roboto")
    assert family_group("Noto Serif") != family_group("Noto Sans")


@pytest.mark.parametrize(
    "family,text",
    [("libre barcode 39 text", False), ("wavefont", False), ("linefont", False),
     ("micro 5 charted", False), ("yarndings 12 charted", False), ("redacted script", False),
     ("flow circular", False), ("noto color emoji", False), ("noto sans symbols 2", False),
     ("noto music", False), ("zilla slab highlight", False),
     ("micro 5", True), ("silkscreen", True), ("zilla slab", True), ("six caps", True),
     ("noto sans", True), ("rubik glitch", True), ("jersey 10", True)],
)  # fmt: skip
def test_is_text_family(family, text):
    from fontmatch.image.catalog import is_text_family

    assert is_text_family(family) is text


def test_matcher_never_offers_non_text_families():
    import numpy as np

    from fontmatch.image.rank import ImageMatcher

    class Atlas:
        faces = [{"key": "A", "space": 0.3}, {"key": "B", "space": 0.3}, {"key": "C", "space": 0.3}]
        meta = np.zeros((3, 0))

    m = ImageMatcher(Atlas(), {"A": "arimo", "B": "libre barcode 39", "C": "wavefont"})
    assert list(m.faces) == [0]


@pytest.mark.parametrize("family", ["jsmath cmex10", "noto sans signwriting", "noto sans math"])
def test_math_and_signwriting_are_not_text(family):
    from fontmatch.image.catalog import is_text_family

    assert not is_text_family(family)
