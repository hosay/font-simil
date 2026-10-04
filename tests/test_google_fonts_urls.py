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
