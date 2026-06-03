"""Flask JSON API tests."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def app():
    """Create a test app with a small corpus pre-loaded."""
    import tempfile

    from fontmatch.service.app import create_app

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        application = create_app(db_path=db_path, fixture_dir=FIXTURES, testing=True)
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "font_count" in data


class TestIdentify:
    def test_identify_with_fixture_font(self, client):
        font_path = FIXTURES / "Cousine-Regular.ttf"
        with open(font_path, "rb") as f:
            resp = client.post(
                "/api/identify",
                data={"file": (f, "Cousine-Regular.ttf")},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "matches" in data
        assert len(data["matches"]) > 0
        match = data["matches"][0]
        assert "distance" in match
        assert "metric_distance" in match
        assert "perceptual_distance" in match
        assert "family" in match

    def test_identify_cached_second_time(self, client):
        font_path = FIXTURES / "Cousine-Regular.ttf"
        with open(font_path, "rb") as f:
            resp1 = client.post(
                "/api/identify",
                data={"file": (f, "Cousine-Regular.ttf")},
                content_type="multipart/form-data",
            )
        with open(font_path, "rb") as f:
            resp2 = client.post(
                "/api/identify",
                data={"file": (f, "Cousine-Regular.ttf")},
                content_type="multipart/form-data",
            )
        assert resp1.get_json()["matches"] == resp2.get_json()["matches"]

    def test_identify_corrupt_file_returns_400(self, client):
        from io import BytesIO

        resp = client.post(
            "/api/identify",
            data={"file": (BytesIO(b"not a font"), "bad.ttf")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data

    def test_identify_no_file_returns_400(self, client):
        resp = client.post("/api/identify")
        assert resp.status_code == 400

    def test_identify_increments_request_counter(self, app, client):
        store = app.config["STORE"]
        before = store.request_count("/api/identify")
        font_path = FIXTURES / "Cousine-Regular.ttf"
        with open(font_path, "rb") as f:
            client.post(
                "/api/identify",
                data={"file": (f, "Cousine-Regular.ttf")},
                content_type="multipart/form-data",
            )
        after = store.request_count("/api/identify")
        assert after == before + 1


class TestFontDetail:
    def test_get_font_by_id(self, client):
        resp = client.get("/api/fonts/1")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.get_json()
            assert "family" in data

    def test_get_nonexistent_font_returns_404(self, client):
        resp = client.get("/api/fonts/999999")
        assert resp.status_code == 404


class TestFontFile:
    def test_serve_known_font_file(self, client):
        resp = client.get("/api/font-file/Cousine-Regular.ttf")
        assert resp.status_code == 200
        assert resp.content_type in ("font/ttf", "application/octet-stream")
        assert len(resp.data) > 100

    def test_serve_unknown_font_returns_404(self, client):
        resp = client.get("/api/font-file/nonexistent.ttf")
        assert resp.status_code == 404

    def test_concurrent_font_file_requests(self, app):
        """Multiple simultaneous font-file requests must not cause 500 errors.

        Regression test: a shared SQLite connection without locking caused
        'cannot start a transaction within a transaction' under concurrency.
        """
        import concurrent.futures

        def fetch_font():
            with app.test_client() as c:
                return c.get("/api/font-file/Cousine-Regular.ttf").status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(fetch_font) for _ in range(20)]
            results = [f.result() for f in futures]

        assert all(code == 200 for code in results), (
            f"Expected all 200s, got: {results}"
        )
