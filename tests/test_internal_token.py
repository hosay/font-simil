"""Calls from the MCP service carry X-Internal-Token and skip per-IP limits
(every MCP request comes from 127.0.0.1); the MCP service rate-limits per user."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fontmatch.service.app import create_app

    monkeypatch.setenv("FONTMATCH_INTERNAL_TOKEN", "s3cret-token-value")
    monkeypatch.setenv("DAILY_RATE_LIMIT", "2")
    app = create_app(db_path=tmp_path / "t.db", testing=True)
    return app.test_client()


def test_without_token_daily_limit_applies(client):
    codes = [client.get("/api/similar-to?font=Nope").status_code for _ in range(3)]
    assert codes[-1] == 429


def test_with_token_daily_limit_skipped(client):
    headers = {"X-Internal-Token": "s3cret-token-value"}
    codes = [
        client.get("/api/similar-to?font=Nope", headers=headers).status_code for _ in range(4)
    ]
    assert 429 not in codes


def test_wrong_token_is_not_exempt(client):
    headers = {"X-Internal-Token": "wrong"}
    codes = [
        client.get("/api/similar-to?font=Nope", headers=headers).status_code for _ in range(3)
    ]
    assert codes[-1] == 429


def test_token_from_proxied_request_is_not_exempt(client):
    """Only direct local calls count; anything that came through nginx has X-Forwarded-For."""
    headers = {"X-Internal-Token": "s3cret-token-value", "X-Forwarded-For": "203.0.113.9"}
    codes = [
        client.get("/api/similar-to?font=Nope", headers=headers).status_code for _ in range(3)
    ]
    assert codes[-1] == 429


def test_no_token_configured_means_no_exemption(tmp_path, monkeypatch):
    from fontmatch.service.app import create_app

    monkeypatch.delenv("FONTMATCH_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("DAILY_RATE_LIMIT", "1")
    c = create_app(db_path=tmp_path / "t.db", testing=True).test_client()
    codes = [
        c.get("/api/similar-to?font=Nope", headers={"X-Internal-Token": ""}).status_code
        for _ in range(2)
    ]
    assert codes[-1] == 429
