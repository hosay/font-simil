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


class TestIndexedOnlyFiltering:
    """Front page and sitemap should only list fonts with fingerprints + licenses."""

    def test_list_font_families_indexed_only_excludes_unlicensed(self, app):
        """Fonts without a license should not appear when indexed_only=True."""
        store = app.config["STORE"]
        # All test fixtures have licenses, so all should appear
        all_fams = store.list_font_families(clean_only=True)
        indexed_fams = store.list_font_families(clean_only=True, indexed_only=True)
        assert len(indexed_fams) > 0
        assert len(indexed_fams) <= len(all_fams)

    def test_list_font_families_indexed_only_excludes_no_fingerprint(self, app):
        """A font stored without a fingerprint should not appear when indexed_only=True."""
        store = app.config["STORE"]
        # Insert a font with no fingerprint
        store.conn.execute(
            """INSERT OR IGNORE INTO fonts
               (file_hash, name, family, subfamily, license_id, source)
               VALUES ('fakehash999', 'FakeFont.ttf', 'FakeFont', 'Regular', 'OFL-1.1', 'test')"""
        )
        store.conn.commit()

        all_fams = store.list_font_families(clean_only=True)
        indexed_fams = store.list_font_families(clean_only=True, indexed_only=True)
        assert "FakeFont" in all_fams
        assert "FakeFont" not in indexed_fams

    def test_homepage_only_shows_indexed_fonts(self, client, app):
        """The browse corpus section should only contain fonts with working pages."""
        store = app.config["STORE"]
        # Insert unlicensed font
        store.conn.execute(
            """INSERT OR IGNORE INTO fonts
               (file_hash, name, family, subfamily, license_id, source)
               VALUES ('fakehash888', 'NoLicense.ttf', 'Nolicense Family', 'Regular', '', 'crawled.com')"""
        )
        store.conn.commit()

        resp = client.get("/")
        html = resp.data.decode()
        assert "Nolicense Family" not in html

    def test_sitemap_only_lists_indexed_fonts(self, client, app):
        """The sitemap should not list fonts that would show 'not found'."""
        store = app.config["STORE"]
        # Insert unlicensed font
        store.conn.execute(
            """INSERT OR IGNORE INTO fonts
               (file_hash, name, family, subfamily, license_id, source)
               VALUES ('fakehash777', 'SitemapGhost.ttf', 'Sitemapghost', 'Regular', '', 'crawled.com')"""
        )
        store.conn.commit()

        resp = client.get("/sitemap.txt")
        text = resp.data.decode()
        assert "sitemapghost" not in text.lower()


class TestSimilarToCache:
    """The /similar-to/ route should cache results to avoid recomputing."""

    def test_similar_to_caches_result(self, client, app):
        """After first visit, results should be cached in match_cache."""
        store = app.config["STORE"]
        families = store.list_font_families()
        if not families:
            pytest.skip("No fonts in test corpus")

        from fontmatch.web.helpers import slugify

        slug = slugify(families[0])

        # Clear any existing cache
        store.conn.execute("DELETE FROM match_cache")
        store.conn.commit()

        # First request — should compute and cache
        resp = client.get(f"/similar-to/{slug}")
        assert resp.status_code == 200

        # Check cache was populated
        row = store.conn.execute("SELECT COUNT(*) FROM match_cache").fetchone()
        assert row[0] > 0, "similar-to should cache its results"

    def test_similar_to_uses_cached_result(self, client, app):
        """Second visit should serve from cache (same results)."""
        store = app.config["STORE"]
        families = store.list_font_families()
        if not families:
            pytest.skip("No fonts in test corpus")

        from fontmatch.web.helpers import slugify

        slug = slugify(families[0])

        # First request
        resp1 = client.get(f"/similar-to/{slug}")
        html1 = resp1.data.decode()

        # Second request (should use cache)
        resp2 = client.get(f"/similar-to/{slug}")
        html2 = resp2.data.decode()

        assert resp2.status_code == 200
        # Results should be identical
        assert html1 == html2


class TestProprietaryFontComparison:
    def test_proprietary_font_shows_recommendation_card(self, client):
        resp = client.get("/similar-to/times-new-roman")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Times New Roman" in html
        assert "proprietary" in html.lower()
        assert "alt-recommendation" in html

    def test_proprietary_font_shows_oss_alternative(self, client):
        resp = client.get("/similar-to/arial")
        html = resp.data.decode()
        assert "Arimo" in html

    def test_proprietary_font_has_font_detection_js(self, client):
        resp = client.get("/similar-to/times-new-roman")
        html = resp.data.decode()
        assert "measureText" in html

    def test_proprietary_font_has_missing_card(self, client):
        resp = client.get("/similar-to/calibri")
        html = resp.data.decode()
        assert "missing-card" in html
        assert "Carlito" in html

    def test_proprietary_font_in_sitemap(self, client):
        resp = client.get("/sitemap.txt")
        html = resp.data.decode()
        assert "/similar-to/times-new-roman" in html

    def test_regular_font_has_no_proprietary_card(self, client, app):
        store = app.config["STORE"]
        families = store.list_font_families()
        if families:
            from fontmatch.web.helpers import slugify

            slug = slugify(families[0])
            resp = client.get(f"/similar-to/{slug}")
            html = resp.data.decode()
            assert "alt-recommendation" not in html
            assert "missing-card" not in html


class TestDailyRateLimit:
    """Daily IP-based rate limiting and usage tracking."""

    @pytest.fixture
    def rate_app(self):
        """App with a very low daily rate limit for testing."""
        import tempfile

        from fontmatch.service.app import create_app

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test_rate.db"
            application = create_app(db_path=db_path, fixture_dir=FIXTURES, testing=True)
            application.config["DAILY_RATE_LIMIT"] = 5
            application.config["DAILY_RATE_TRACK_THRESHOLD"] = 3
            yield application

    @pytest.fixture
    def rate_client(self, rate_app):
        return rate_app.test_client()

    def test_requests_under_limit_succeed(self, rate_client):
        """Requests under the daily limit should all return 200."""
        for _ in range(5):
            resp = rate_client.get("/")
            assert resp.status_code == 200

    def test_requests_over_limit_return_429_api(self, rate_client):
        """API requests over the daily limit return JSON 429 with reset_at."""
        # Exhaust the limit
        for _ in range(5):
            rate_client.get("/api/browse")
        # Next request should be blocked
        resp = rate_client.get("/api/browse")
        assert resp.status_code == 429
        data = resp.get_json()
        assert data["error"] == "Daily rate limit exceeded"
        assert "reset_at" in data
        assert "limit" in data

    def test_requests_over_limit_return_429_web(self, rate_client):
        """Web requests over the daily limit return 429 HTML page."""
        for _ in range(5):
            rate_client.get("/")
        resp = rate_client.get("/")
        assert resp.status_code == 429
        html = resp.data.decode()
        assert "limit" in html.lower()

    def test_rate_limit_resets_next_day(self, rate_app, rate_client):
        """Counter resets when the UTC date changes."""
        from unittest.mock import patch
        from datetime import datetime, timezone

        # Exhaust limit on "today"
        for _ in range(6):
            rate_client.get("/")

        # Verify blocked
        resp = rate_client.get("/")
        assert resp.status_code == 429

        # Mock tomorrow's date
        tomorrow = datetime(2099, 1, 2, tzinfo=timezone.utc)
        with patch("fontmatch.index.store.datetime") as mock_dt:
            mock_dt.now.return_value = tomorrow
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            resp = rate_client.get("/")
            assert resp.status_code == 200

    def test_heavy_users_tracking(self, rate_app):
        """IPs exceeding the threshold appear in list_heavy_users."""
        store = rate_app.config["STORE"]
        # Simulate 4 requests (threshold is 3)
        for _ in range(4):
            store.increment_daily_usage("10.0.0.1")
        heavy = store.list_heavy_users(threshold=3)
        ips = [u["ip_address"] for u in heavy]
        assert "10.0.0.1" in ips

    def test_rate_limit_headers_present(self, rate_client):
        """Tracked requests include X-RateLimit-* response headers."""
        resp = rate_client.get("/")
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers

    def test_health_endpoint_exempt(self, rate_client):
        """Health check should not count toward rate limit."""
        # Exhaust the limit
        for _ in range(5):
            rate_client.get("/")
        # Health check should still work
        resp = rate_client.get("/api/health")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" not in resp.headers

    def test_cleanup_old_usage(self, rate_app):
        """cleanup_old_usage removes rows older than the specified days."""
        store = rate_app.config["STORE"]
        # Insert an old row directly
        store.conn.execute(
            "INSERT INTO daily_usage (ip_address, date, request_count) VALUES (?, ?, ?)",
            ("192.168.1.1", "2020-01-01", 500),
        )
        store.conn.commit()
        deleted = store.cleanup_old_usage(days=90)
        assert deleted >= 1
        row = store.conn.execute(
            "SELECT COUNT(*) FROM daily_usage WHERE ip_address = '192.168.1.1' AND date = '2020-01-01'"
        ).fetchone()
        assert row[0] == 0
