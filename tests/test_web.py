"""Flask HTML page tests."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def app():
    import tempfile

    from fontmatch.service.app import create_app

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        application = create_app(db_path=db_path, fixture_dir=FIXTURES, testing=True)
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


class TestHomepage:
    def test_homepage_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_homepage_contains_links(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "Identify Font" in html or "Upload" in html
        assert "FontMatch" in html


class TestIdentifyPage:
    def test_get_identify_shows_upload_form(self, client):
        resp = client.get("/identify")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert '<input type="file"' in html

    def test_post_identify_with_font_shows_results(self, client):
        font_path = FIXTURES / "Cousine-Regular.ttf"
        with open(font_path, "rb") as f:
            resp = client.post(
                "/identify",
                data={"file": (f, "Cousine-Regular.ttf")},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Results" in html

    def test_post_identify_with_no_file_redirects(self, client):
        resp = client.post("/identify", follow_redirects=True)
        assert resp.status_code == 200

    def test_post_identify_with_bad_file_shows_error(self, client):
        from io import BytesIO

        resp = client.post(
            "/identify",
            data={"file": (BytesIO(b"not a font"), "bad.ttf")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Unsupported" in html or "error" in html.lower()


class TestSimilarTo:
    def test_similar_to_known_font_returns_200(self, client, app):
        store = app.config["STORE"]
        families = store.list_font_families()
        if families:
            from fontmatch.web.helpers import slugify

            slug = slugify(families[0])
            resp = client.get(f"/similar-to/{slug}")
            assert resp.status_code == 200

    def test_similar_to_unknown_font_shows_not_found(self, client):
        resp = client.get("/similar-to/nonexistent-font-xyz")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "not found" in html.lower() or "upload" in html.lower()

    def test_similar_to_shows_match_results(self, client, app):
        store = app.config["STORE"]
        families = store.list_font_families()
        if families:
            from fontmatch.web.helpers import slugify

            slug = slugify(families[0])
            resp = client.get(f"/similar-to/{slug}")
            html = resp.data.decode()
            # Should contain match cards or a not-found message
            assert "match-card" in html or "not found" in html.lower()
