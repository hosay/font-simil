"""Product review fixes: Google Fonts data for fonts ingested outside the
google/fonts tree, display names on image results, share-page CTA, and
per-page social titles."""

from pathlib import Path

import pytest

from fontmatch.web import helpers

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def gf_repo(tmp_path, monkeypatch):
    for lic, slug, name in [
        ("ofl", "montserrat", "Montserrat"),
        ("apache", "arimo", "Arimo"),
        ("ofl", "sourcesans3", "Source Sans 3"),
        ("ofl", "montserratalternates", "Montserrat Alternates"),
    ]:
        d = tmp_path / lic / slug
        d.mkdir(parents=True)
        (d / "METADATA.pb").write_text(f'name: "{name}"\n')
    monkeypatch.setattr(helpers, "GOOGLE_FONTS_REPOS", [tmp_path])
    helpers.google_fonts_name.cache_clear()
    helpers.google_fonts_dir.cache_clear()
    yield tmp_path
    helpers.google_fonts_name.cache_clear()
    helpers.google_fonts_dir.cache_clear()


class NoGFStore:
    """A family whose files came from outside google/fonts (license unknown)."""

    def google_fonts_source(self, family):
        return None

    def get_font_source(self, name):
        return name


def test_family_found_by_its_google_fonts_directory(gf_repo):
    assert helpers.google_fonts_dir("Montserrat") == "ofl/montserrat/METADATA.pb"
    assert helpers.google_fonts_dir("Source Sans 3") == "ofl/sourcesans3/METADATA.pb"
    assert helpers.google_fonts_dir("Montserrat Thin") is None  # name must match
    assert helpers.google_fonts_dir("../etc") is None


def test_match_gets_link_license_and_css_from_the_directory(gf_repo):
    m = {"name": "Arimo-Regular.ttf", "family": "Arimo", "distance": 0.1}
    m["license_id"] = "unknown"
    [out] = helpers.enrich_matches([m], store=NoGFStore())
    assert out["google_fonts_url"] == "https://fonts.google.com/specimen/Arimo"
    assert out["gf_family"] == "Arimo"
    assert out["license_label"] == "Apache 2.0"


def test_image_result_names_use_google_fonts_names(gf_repo):
    from fontmatch.web.api import _add_image_urls

    class Store(NoGFStore):
        def get_font_family(self, name):
            return "Montserrat Alternates ExLight"

        def google_fonts_source(self, family):
            return "ofl/montserratalternates/MontserratAlternates-ExtraLight.ttf"

    result = {"matches": [{"name": "MontserratAlternates-ExtraLight.ttf",
                           "family": "Montserrat Alternates ExLight", "style": "default"}]}  # fmt: skip
    [m] = _add_image_urls(result, Store())["matches"]
    assert m["display_family"] == "Montserrat Alternates"
    assert m["family"] == "Montserrat Alternates ExLight"  # API field unchanged


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fontmatch.service.app import create_app

    db = tmp_path_factory.mktemp("db") / "test.db"
    return create_app(db_path=db, fixture_dir=FIXTURES, testing=True).test_client()


def test_twitter_title_follows_the_page(client):
    html = client.get("/similar-to/optima").get_data(as_text=True)
    assert '<meta name="twitter:title" content="Free Alternative to Optima' in html


def test_proprietary_preview_text_is_neutral(client):
    html = client.get("/similar-to/optima").get_data(as_text=True)
    assert "is not installed on your device" not in html
