"""Terms of Service page (/terms) and the operator named in the legal pages."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from fontmatch.service.app import create_app

    return create_app(db_path=tmp_path_factory.mktemp("terms") / "t.db", testing=True)


def _html(app, path):
    resp = app.test_client().get(path)
    assert resp.status_code == 200, path
    return resp.get_data(as_text=True)


def test_terms_page_names_the_company_and_key_protections(app):
    html = _html(app, "/terms")
    for text in [
        "Terms of Service",
        "Datacleave Ltd",
        "British Columbia",
        "as is",
        "Limitation of liability",
        "font licen",  # users must check each font's license themselves
        "trademarks",
        "Share links",
        "legal@dupefont.com",
        "Copyright Act",
        "Digital Millennium Copyright Act",
    ]:
        assert text.lower() in html.lower(), text


def test_terms_have_no_em_dashes_or_placeholders(app):
    html = _html(app, "/terms")
    body = html[html.find('<article class="policy">') :]
    assert "—" not in body and "–" not in body
    assert "[" not in body.split("</article>")[0]


def test_privacy_policy_names_the_operator(app):
    html = _html(app, "/privacy")
    assert "Datacleave Ltd" in html
    assert 'href="/terms"' in html


@pytest.mark.parametrize("path", ["/", "/identify", "/privacy", "/terms"])
def test_footer_links_terms(app, path):
    assert 'href="/terms"' in _html(app, path)


def test_terms_in_sitemap(app):
    assert "/terms" in _html(app, "/sitemap.txt")


def test_upload_and_share_mention_the_terms(app):
    html = _html(app, "/identify")
    form = html[html.find('id="image"') :]
    assert 'href="/terms"' in form


def test_privacy_states_where_data_is_processed(app):
    html = _html(app, "/privacy")
    assert "We run the Service from Canada" in html
    assert "We run the Service from the United States" not in html


def test_api_docs_link_terms(app):
    assert 'href="/terms"' in _html(app, "/api/docs")


def test_consent_checkbox_mentions_training(app, monkeypatch):
    import io

    from tests.test_web_image import FakeIdentifier, _png

    monkeypatch.setitem(app.config, "IMAGE_IDENTIFIER", FakeIdentifier())
    resp = app.test_client().post(
        "/identify-image",
        data={"image": (io.BytesIO(_png()), "s.png")},
        content_type="multipart/form-data",
    )
    assert "including training our matching models" in resp.get_data(as_text=True)


@pytest.mark.parametrize(
    "concession",
    [
        "except where the law allows",  # no invitations to reverse engineer
        "except through the API",  # no carve-out for competitors
        "if you credit DupeFont",  # no blanket licence to reuse results
        "without crediting",
        "you may also bring proceedings in the courts where you live",
        "rely on statutory limitation periods",
        "for example in Quebec",
        "put a notice on the site",
        "we will act promptly",
        "we will forward the notice",
        "The Service is free.",
        "We won't publish the image",
        "we use your content only",
        "other than through the API",
        "You may use the names of fonts",
        "in good faith for identification",
        "We use them only",
    ],
)
def test_terms_make_no_unneeded_concessions(app, concession):
    html = _html(app, "/terms").replace("&#39;", "'")
    assert concession.lower() not in html.lower()


def test_terms_ban_competitive_use_and_extraction(app):
    html = _html(app, "/terms").lower()
    assert "competes with dupefont" in html
    assert "machine-learning model" in html and "text and data mining rights" in html
    assert "further access to the service is unauthorized" in html
    assert "including through the api" in html
    assert "reverse engineer" in html


@pytest.mark.parametrize("path", ["/terms", "/privacy"])
def test_no_city_named(app, path):
    assert "Victoria" not in _html(app, path)
