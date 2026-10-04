"""/admin/stats: password-protected usage dashboard."""

import base64
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

FIXTURES = Path(__file__).parent / "fixtures"
PASSWORD = "correct horse"


def _auth(user="dfadmin", password=PASSWORD):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(scope="module")
def base_app(tmp_path_factory):
    from fontmatch.service.app import create_app

    tmp = tmp_path_factory.mktemp("admin")
    return create_app(db_path=tmp / "test.db", fixture_dir=FIXTURES, testing=True)


@pytest.fixture
def app(base_app, tmp_path, monkeypatch):
    from fontmatch.mcp_server.usage import UsageLog

    usage = UsageLog(tmp_path / "usage.db")
    usage.record(tool="find_free_font_from_image", subject="u1", status="ok", latency_ms=900,
                 top_family="Inter", top_similarity=98, country="US", image_host="files.oai")
    usage.record(tool="find_free_font_from_image", subject="u2", status="no_text", latency_ms=400)
    usage.record(tool="find_free_alternatives", subject="u1", status="ok", latency_ms=50,
                 query="Gotham", top_family="Montserrat", top_similarity=80, country="MX")
    monkeypatch.setitem(base_app.config, "ADMIN_USER", "dfadmin")
    monkeypatch.setitem(base_app.config, "ADMIN_PASSWORD_HASH", generate_password_hash(PASSWORD))
    monkeypatch.setitem(base_app.config, "MCP_USAGE_DB", tmp_path / "usage.db")
    return base_app


class TestAuth:
    def test_disabled_without_password_hash(self, base_app, monkeypatch):
        monkeypatch.setitem(base_app.config, "ADMIN_PASSWORD_HASH", "")
        assert base_app.test_client().get("/admin/stats", headers=_auth()).status_code == 404

    def test_no_credentials_challenges(self, app):
        resp = app.test_client().get("/admin/stats")
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"].startswith("Basic")

    @pytest.mark.parametrize("user,password", [("dfadmin", "wrong"), ("admin", PASSWORD), ("", "")])
    def test_bad_credentials_rejected(self, app, user, password):
        resp = app.test_client().get("/admin/stats", headers=_auth(user, password))
        assert resp.status_code == 401
        assert "Inter" not in resp.get_data(as_text=True)

    def test_good_credentials(self, app):
        resp = app.test_client().get("/admin/stats", headers=_auth())
        assert resp.status_code == 200


class TestDashboard:
    def _html(self, app):
        return app.test_client().get("/admin/stats", headers=_auth()).get_data(as_text=True)

    def test_shows_mcp_usage(self, app):
        html = self._html(app)
        for text in ["find_free_font_from_image", "find_free_alternatives", "Inter", "Montserrat",
                     "Gotham", "no_text", "MX"]:
            assert text in html, text

    def test_counts(self, app):
        from fontmatch.web.admin import usage_stats

        stats = usage_stats(app.config["MCP_USAGE_DB"])
        assert stats["totals"]["today"]["calls"] == 3
        assert stats["totals"]["today"]["users"] == 2
        by_tool = {t["tool"]: t for t in stats["tools"]}
        assert by_tool["find_free_font_from_image"]["calls"] == 2
        assert by_tool["find_free_font_from_image"]["ok"] == 1
        assert len(stats["daily"]) == 30
        assert stats["daily"][-1]["calls"] == 3

    def test_missing_usage_db_renders_empty_state(self, app, tmp_path, monkeypatch):
        monkeypatch.setitem(app.config, "MCP_USAGE_DB", tmp_path / "nope.db")
        resp = app.test_client().get("/admin/stats", headers=_auth())
        assert resp.status_code == 200
        assert "No ChatGPT calls" in resp.get_data(as_text=True)
        assert not (tmp_path / "nope.db").exists()

    def test_private_headers_and_no_analytics(self, app):
        resp = app.test_client().get("/admin/stats", headers=_auth())
        assert "no-store" in resp.headers["Cache-Control"]
        assert "noindex" in resp.headers["X-Robots-Tag"]
        assert "clarity" not in resp.get_data(as_text=True).lower()

    def test_robots_disallows_admin(self, app):
        assert "Disallow: /admin/" in app.test_client().get("/robots.txt").get_data(as_text=True)


def test_unreadable_usage_db_is_reported_not_hidden(app, tmp_path, monkeypatch):
    import sqlite3

    bad = tmp_path / "bad.db"
    sqlite3.connect(bad).execute("CREATE TABLE other (x)").connection.commit()
    monkeypatch.setitem(app.config, "MCP_USAGE_DB", bad)
    html = app.test_client().get("/admin/stats", headers=_auth()).get_data(as_text=True)
    assert "Can't read the ChatGPT usage database" in html


def test_malformed_password_hash_is_401_not_500(app, monkeypatch):
    monkeypatch.setitem(app.config, "ADMIN_PASSWORD_HASH", "not-a-hash")
    assert app.test_client().get("/admin/stats", headers=_auth()).status_code == 401
