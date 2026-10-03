"""Candidate catalog helpers."""

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
