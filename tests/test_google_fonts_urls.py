"""Google Fonts specimen links use the family's real name from the google/fonts
repo (METADATA.pb), not a guess that strips "weight-like" words: the guess
turned "Red Hat Text" into /specimen/Red+Hat and "Playfair Display" into
/specimen/Playfair (a different family). 160 of 2,690 families were wrong."""

from pathlib import Path

import pytest

from fontmatch.web import helpers
from fontmatch.web.helpers import google_fonts_url


@pytest.fixture
def gf_repo(tmp_path, monkeypatch):
    for lic, slug, name in [
        ("ofl", "redhattext", "Red Hat Text"),
        ("ofl", "playfairdisplay", "Playfair Display"),
        ("ofl", "dmsans", "DM Sans"),
    ]:
        d = tmp_path / lic / slug
        d.mkdir(parents=True)
        (d / "METADATA.pb").write_text(f'name: "{name}"\ndesigner: "x"\n')
    monkeypatch.setattr(helpers, "GOOGLE_FONTS_REPOS", [tmp_path])
    helpers.google_fonts_name.cache_clear()
    yield tmp_path
    helpers.google_fonts_name.cache_clear()


@pytest.mark.parametrize(
    "family,source,expected",
    [
        ("Red Hat Text", "redhattext/RedHatText[wght].ttf", "Red+Hat+Text"),
        ("Red Hat Text", "ofl/redhattext/RedHatText[wght].ttf", "Red+Hat+Text"),
        ("Playfair Display", "playfairdisplay/PlayfairDisplay[wght].ttf", "Playfair+Display"),
        ("DM Sans 9pt", "dmsans/DMSans[opsz,wght].ttf", "DM+Sans"),  # opsz instance name
    ],
)
def test_url_uses_the_repo_name(gf_repo, family, source, expected):
    assert google_fonts_url(family, source) == "https://fonts.google.com/specimen/" + expected


def test_without_metadata_falls_back_to_the_stripped_family(gf_repo):
    assert google_fonts_url("Alegreya Sans ExtraBold", "alegreyasans/X.ttf") == (
        "https://fonts.google.com/specimen/Alegreya+Sans"
    )
    assert google_fonts_url("Tinos") == "https://fonts.google.com/specimen/Tinos"


def test_paths_cannot_escape_the_repo(gf_repo):
    assert helpers.google_fonts_name("../../etc/passwd") is None
    assert helpers.google_fonts_name("/etc/passwd") is None


def test_store_returns_the_google_fonts_source(tmp_path):
    from fontmatch.index.store import FontStore

    store = FontStore(tmp_path / "t.db")
    with store._lock:
        store.conn.execute(
            "INSERT INTO fonts (name, family, subfamily, source, license_id, file_hash)"
            " VALUES ('a.ttf', 'Red Hat Text', 'Regular', 'redhattext/a.ttf', 'OFL-1.1', 'h1'),"
            " ('b.woff2', 'Zoho Puvi', 'Regular', 'zoho.com', '', 'h2')"
        )
    assert store.google_fonts_source("Red Hat Text") == "redhattext/a.ttf"
    assert store.google_fonts_source("Zoho Puvi") is None
    assert store.has_google_fonts_source("Red Hat Text")


REPO = Path(__file__).resolve().parent.parent / "google-fonts-repo"


@pytest.mark.skipif(not (REPO / "ofl" / "redhattext").is_dir(), reason="google-fonts-repo missing")
def test_real_repo_names():
    helpers.google_fonts_name.cache_clear()
    assert helpers.google_fonts_name("redhattext/RedHatText[wght].ttf") == "Red Hat Text"
    assert helpers.google_fonts_name("archivoblack/ArchivoBlack-Regular.ttf") == "Archivo Black"


# --- Families retired from fonts.google.com ----------------------------------
# google/fonts keeps directories for families Google Fonts no longer serves
# ("Big Shoulders Display SC" became "Big Shoulders"); their specimen and CSS
# URLs 404, so links go to the successor family or are dropped.


@pytest.fixture
def live(gf_repo, monkeypatch):
    for lic, slug, name in [
        ("ofl", "bigshouldersdisplaysc", "Big Shoulders Display SC"),
        ("ofl", "bigshoulders", "Big Shoulders"),
        ("ofl", "hindkochi", "Hind Kochi"),
    ]:
        d = gf_repo / lic / slug
        d.mkdir(parents=True)
        (d / "METADATA.pb").write_text(f'name: "{name}"\n')
    f = gf_repo / "live.txt"
    f.write_text("# comment\nRed Hat Text\nBig Shoulders\nTinos\nAlegreya Sans\n")
    monkeypatch.setattr(helpers, "GOOGLE_FONTS_LIVE_FILE", f)
    helpers.google_fonts_live_families.cache_clear()
    yield f
    helpers.google_fonts_live_families.cache_clear()


def test_retired_family_links_to_its_successor(live):
    src = "ofl/bigshouldersdisplaysc/BigShouldersDisplaySC[wght].ttf"
    assert google_fonts_url("Big Shoulders Display SC Bold", src) == (
        "https://fonts.google.com/specimen/Big+Shoulders"
    )
    assert helpers.google_fonts_live_name("Big Shoulders Display SC") == "Big Shoulders"


def test_retired_family_without_successor_has_no_link(live):
    assert google_fonts_url("Hind Kochi", "ofl/hindkochi/HindKochi-Regular.ttf") is None
    assert helpers.google_fonts_live_name("Hind Kochi") is None
    assert google_fonts_url("Some Unknown Font") is None


def test_live_families_unchanged(live):
    assert google_fonts_url("Red Hat Text", "redhattext/x.ttf").endswith("/Red+Hat+Text")
    assert google_fonts_url("Tinos") == "https://fonts.google.com/specimen/Tinos"


def test_missing_live_list_keeps_old_behaviour(gf_repo, monkeypatch, tmp_path):
    monkeypatch.setattr(helpers, "GOOGLE_FONTS_LIVE_FILE", tmp_path / "nope.txt")
    helpers.google_fonts_live_families.cache_clear()
    try:
        assert google_fonts_url("Hind Kochi") == "https://fonts.google.com/specimen/Hind+Kochi"
    finally:
        helpers.google_fonts_live_families.cache_clear()


def test_enrich_matches_uses_successor_for_link_and_css(live):
    class Store:
        def get_font_source(self, name):
            return "ofl/bigshouldersdisplaysc/BigShouldersDisplaySC[wght].ttf"

        def google_fonts_source(self, family):
            return self.get_font_source(family)

    m = {"name": "a.ttf", "family": "Big Shoulders Display SC Bold", "distance": 0.1,
         "license_id": "OFL-1.1"}  # fmt: skip
    helpers.enrich_matches([m], Store())
    assert m["display_family"] == "Big Shoulders Display SC"  # the font's real name
    assert m["google_fonts_url"] == "https://fonts.google.com/specimen/Big+Shoulders"
    # embed code only for the exact family: the successor has no small caps
    assert m["gf_css_family"] is None


def test_css_name_only_for_exact_live_family(live):
    assert helpers.google_fonts_css_name("Red Hat Text") == "Red Hat Text"
    assert helpers.google_fonts_css_name("Big Shoulders Display SC") is None
    assert helpers.google_fonts_css_name("Hind Kochi") is None
    assert helpers.google_fonts_css_name(None) is None


def test_shipped_live_list_is_sane():
    helpers.google_fonts_live_families.cache_clear()
    fams = helpers.google_fonts_live_families()
    assert fams is not None and len(fams) > 1500
    assert "Roboto" in fams and "Big Shoulders" in fams
    assert "Big Shoulders Display SC" not in fams
    for old, new in helpers.GOOGLE_FONTS_SUCCESSORS.items():
        assert new in fams, (old, new)
        assert old not in fams, old
