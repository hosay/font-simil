"""Endpoints OpenAI's plugin-directory submission requires: supportURL and domain verification."""

import pytest

from fontmatch.service.app import create_app


def _client(tmp_path):
    return create_app(db_path=tmp_path / "t.db", testing=True).test_client()


@pytest.fixture
def client(tmp_path):
    return _client(tmp_path)


class TestSupportPage:
    """submission requires a public HTTPS supportURL; a bounce or 404 is a cheap rejection."""

    def test_is_public(self, client):
        assert client.get("/support").status_code == 200

    def test_shows_a_contact_address(self, client):
        assert "support@dupefont.com" in client.get("/support").data.decode()

    def test_address_is_configurable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DUPEFONT_SUPPORT_EMAIL", "help@example.com")
        assert "help@example.com" in _client(tmp_path).get("/support").data.decode()

    def test_is_reachable_from_every_page(self, client):
        assert 'href="/support"' in client.get("/").data.decode()

    def test_is_listed_in_the_sitemap(self, client):
        assert "/support" in client.get("/sitemap.txt").data.decode()


class TestAppsChallenge:
    """Domain verification: OpenAI fetches a challenge token as plain text."""

    def test_serves_the_token_verbatim(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DUPEFONT_APPS_CHALLENGE", "tok-abc123")
        resp = _client(tmp_path).get("/.well-known/openai-apps-challenge")
        assert resp.status_code == 200
        assert resp.data.decode().strip() == "tok-abc123"
        assert resp.headers["Content-Type"].startswith("text/plain")

    def test_404s_when_no_token_is_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DUPEFONT_APPS_CHALLENGE", raising=False)
        assert _client(tmp_path).get("/.well-known/openai-apps-challenge").status_code == 404

    def test_is_not_indexed(self, client):
        """A verification token has no business in search results."""
        assert "/.well-known/" not in client.get("/sitemap.txt").data.decode()
