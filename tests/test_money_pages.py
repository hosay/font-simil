"""Proprietary-font pages (/similar-to/<proprietary>): the pages people search
for. Copy must be true for the font shown (license, metric compatibility),
every page has its own comparison note, and the recommended font links to
Google Fonts with a ready CSS snippet when it is there."""

import re
from pathlib import Path

import pytest

from fontmatch.web import helpers
from fontmatch.web.helpers import (
    METRIC_COMPATIBLE,
    PROPRIETARY_FONTS,
    PROPRIETARY_NOTES,
    PROPRIETARY_TO_OPEN_SOURCE,
    slugify,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fontmatch.service.app import create_app

    db = tmp_path_factory.mktemp("db") / "test.db"
    return create_app(db_path=db, fixture_dir=FIXTURES, testing=True).test_client()


def _html(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, path
    return resp.get_data(as_text=True)


def test_every_proprietary_font_has_metadata_and_a_unique_note():
    for name in PROPRIETARY_TO_OPEN_SOURCE:
        meta = PROPRIETARY_FONTS[name]
        assert meta["description"] and meta["vendor"] and meta["category"], name
        assert len(PROPRIETARY_NOTES[name]) > 40, name
    assert len(set(PROPRIETARY_NOTES.values())) == len(PROPRIETARY_NOTES)
    assert METRIC_COMPATIBLE <= set(PROPRIETARY_TO_OPEN_SOURCE)


def test_metric_compatible_claim_only_where_true(client):
    assert "metrically compatible" in _html(client, "/similar-to/arial")
    for slug in ("palatino", "optima"):  # Lora / Lato: similar look, different widths
        assert "metrically compatible" not in _html(client, f"/similar-to/{slug}")


def test_license_claim_matches_the_font(client, tmp_path, monkeypatch):
    # The FAQ used to say every alternative is under the SIL Open Font
    # License. It must name the font's own license (here: Apache, from the
    # google/fonts directory the family lives in).
    d = tmp_path / "apache" / "arimo"
    d.mkdir(parents=True)
    (d / "METADATA.pb").write_text('name: "Arimo"\n')
    monkeypatch.setattr(helpers, "GOOGLE_FONTS_REPOS", [tmp_path])
    helpers.google_fonts_name.cache_clear()
    helpers.google_fonts_dir.cache_clear()
    html = _html(client, "/similar-to/arial")
    helpers.google_fonts_name.cache_clear()
    helpers.google_fonts_dir.cache_clear()
    faq = html[html.find("Frequently Asked Questions") :]
    assert "released under the Apache 2.0" in faq
    assert "SIL Open Font License" not in faq


def test_note_is_on_the_page(client):
    html = _html(client, "/similar-to/palatino")
    assert PROPRIETARY_NOTES["Palatino"].split(";")[0][:60] in html.replace("&#39;", "'")


def test_related_alternatives_link_same_category(client):
    html = _html(client, "/similar-to/palatino")
    related = html[html.find('class="related-fonts"') :]
    assert 'href="/similar-to/garamond"' in related
    assert 'href="/similar-to/palatino"' not in related


def test_popular_page_links_every_proprietary_page(client):
    html = _html(client, "/popular")
    for name in PROPRIETARY_TO_OPEN_SOURCE:
        assert f'/similar-to/{slugify(name)}"' in html, name


@pytest.fixture
def lato_on_google_fonts(tmp_path, monkeypatch):
    from fontmatch.index.store import FontStore

    d = tmp_path / "ofl" / "lato"
    d.mkdir(parents=True)
    (d / "METADATA.pb").write_text('name: "Lato"\n')
    monkeypatch.setattr(helpers, "GOOGLE_FONTS_REPOS", [tmp_path])
    helpers.google_fonts_name.cache_clear()
    real = FontStore.google_fonts_source
    monkeypatch.setattr(
        FontStore,
        "google_fonts_source",
        lambda self, fam: "ofl/lato/Lato-Regular.ttf" if fam == "Lato" else real(self, fam),
    )
    yield
    helpers.google_fonts_name.cache_clear()


def test_recommended_font_links_to_google_fonts_with_css(client, lato_on_google_fonts):
    html = _html(client, "/similar-to/optima")
    rec = html[
        html.find('class="alt-recommendation"') : html.find("More open-source alternatives")
    ]
    assert 'href="https://fonts.google.com/specimen/Lato"' in rec
    assert "fonts.googleapis.com/css2?family=Lato" in html
    assert re.search(
        r"font-family: &#39;Lato&#39;, Optima, sans-serif|font-family: 'Lato', Optima, sans-serif",
        html,
    )


def test_match_cards_show_the_google_fonts_name():
    from fontmatch.web.helpers import enrich_matches

    class Store:
        def get_font_source(self, name):
            return "ofl/outfit/Outfit[wght].ttf"

        def google_fonts_source(self, family):
            return "ofl/outfit/Outfit[wght].ttf"

    m = {"name": "Outfit[wght].ttf", "family": "Outfit Thin", "distance": 0.1}
    orig = helpers.google_fonts_name
    try:
        helpers.google_fonts_name = lambda src: "Outfit"
        [out] = enrich_matches([m], store=Store())
    finally:
        helpers.google_fonts_name = orig
    assert out["display_family"] == "Outfit" and out["gf_family"] == "Outfit"


def test_corpus_page_does_not_call_a_font_a_stand_in_for_itself(client, tmp_path, monkeypatch):
    # /similar-to/<free font> for a variable font: the DB family is the
    # default instance name ("DM Sans 9pt"), the Google Fonts name differs.
    # The page must use one name, not present the font as its own stand-in.
    from fontmatch.index.store import FontStore

    d = tmp_path / "ofl" / "lato"
    d.mkdir(parents=True)
    (d / "METADATA.pb").write_text('name: "Lato Prime"\n')
    monkeypatch.setattr(helpers, "GOOGLE_FONTS_REPOS", [tmp_path])
    helpers.google_fonts_name.cache_clear()
    monkeypatch.setattr(
        FontStore,
        "google_fonts_source",
        lambda self, fam: "ofl/lato/Lato.ttf" if fam == "Lato" else None,
    )
    html = _html(client, "/similar-to/lato")
    helpers.google_fonts_name.cache_clear()
    assert "closest open-source equivalent" not in html
    assert "alternatives to <em>Lato Prime</em>" in html
