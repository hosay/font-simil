"""GET /api/similar-to: free alternatives to a font by name (used by the MCP tool)."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fontmatch.service.app import create_app

    db = tmp_path_factory.mktemp("db") / "test.db"
    return create_app(db_path=db, fixture_dir=FIXTURES, testing=True).test_client()


def test_known_corpus_font(client):
    resp = client.get("/api/similar-to?font=Roboto")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["matched_font"] == "Roboto"
    assert body["proprietary"] is None
    assert body["matches"]
    m = body["matches"][0]
    for key in ("family", "license_id", "score", "similar_url", "google_fonts_url"):
        assert key in m
    assert all(x["family"].lower() != "roboto" for x in body["matches"])


def test_proprietary_name_maps_to_oss(client):
    resp = client.get("/api/similar-to?font=Arial")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["proprietary"]["name"] == "Arial"
    assert body["query"] == "Arial"


def test_unknown_font_404(client):
    resp = client.get("/api/similar-to?font=nonexistent-font-xyz")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


@pytest.mark.parametrize("q", ["", "x" * 101, "a\x00b"])
def test_invalid_query_400(client, q):
    resp = client.get("/api/similar-to", query_string={"font": q})
    assert resp.status_code == 400
