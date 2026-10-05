"""Privacy policy page, consent-gated Clarity, self-hosted fonts, rating IP retention."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CLARITY_ID = "ys6l88q2n9"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from fontmatch.service.app import create_app

    tmp = tmp_path_factory.mktemp("privacy")
    return create_app(db_path=tmp / "test.db", fixture_dir=FIXTURES, testing=True)


def _html(app, path):
    return app.test_client().get(path).get_data(as_text=True)


class TestPrivacyPage:
    def test_page_renders_key_disclosures(self, app):
        resp = app.test_client().get("/privacy")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        for text in ["Privacy Policy", "privacy@dupefont.com", "Microsoft Clarity", "ChatGPT",
                     "30 days", "180 days", "12 months", 'id="analytics"']:
            assert text in html, text
        assert "[OPERATOR" not in html

    def test_operator_line_from_config(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "OPERATOR", "Example LLC, 1 Main St")
        assert "operated by Example LLC, 1 Main St" in _html(app, "/privacy")

    @pytest.mark.parametrize("path", ["/", "/popular", "/identify", "/privacy"])
    def test_footer_links_privacy(self, app, path):
        assert 'href="/privacy"' in _html(app, path)

    def test_in_sitemap(self, app):
        assert "/privacy" in _html(app, "/sitemap.txt")


class TestClarity:
    @pytest.mark.parametrize("path", ["/", "/popular", "/privacy", "/api/docs"])
    def test_loaded_on_regular_pages(self, app, path):
        html = _html(app, path)
        assert f'data-clarity="{CLARITY_ID}"' in html
        assert "data-cookie-settings" in html

    def test_never_on_upload_pages(self, app):
        assert "data-clarity" not in _html(app, "/identify")

    def test_loader_is_consent_gated(self, app):
        js = app.test_client().get("/static/analytics.js").get_data(as_text=True)
        assert "clarity.ms/tag/" in js
        assert "Europe" in js and "globalPrivacyControl" in js and "consentv2" in js

    def test_can_be_disabled(self, app, monkeypatch):
        monkeypatch.setitem(app.config, "CLARITY_PROJECT_ID", "")
        assert "data-clarity" not in _html(app, "/")


def test_no_google_fonts_requests(app):
    html = _html(app, "/")
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert app.test_client().get("/static/fonts/inter-latin.woff2").status_code == 200


def test_old_rating_ips_are_forgotten(tmp_path):
    from fontmatch.index.store import FontStore

    store = FontStore(tmp_path / "s.db")
    store.save_user_score("A", "B", 4, "1.2.3.4")
    store.save_report("A", "C", "1.2.3.4")
    store.save_user_score("A", "D", 5, "5.6.7.8")
    store.conn.execute(
        "UPDATE user_scores SET created_at = datetime('now', '-400 days') WHERE match_font = 'B'"
    )
    store.conn.execute("UPDATE match_reports SET created_at = datetime('now', '-400 days')")
    store.conn.commit()
    assert store.forget_old_rating_ips(days=365) == 2
    ips = {r[0]: r[1] for r in store.conn.execute("SELECT match_font, ip_address FROM user_scores")}
    assert ips == {"B": None, "D": "5.6.7.8"}
    assert store.get_average_score("A", "B")[1] == 1  # the rating itself is kept


def test_policy_covers_share_links_and_feedback_images(tmp_path):
    from fontmatch.service.app import create_app

    app = create_app(db_path=tmp_path / "p.db", testing=True)
    html = app.test_client().get("/privacy").get_data(as_text=True)
    for text in ["Share links", "Get a share link", "Was this right?", "Let Dupefont keep this image",
                 "Up to 2 years", "October 5, 2026"]:
        assert text in html, text
    assert "We don't use your uploads to train" not in html
