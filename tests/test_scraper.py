"""Phase 8 — Web font scraper tests (no live network)."""

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


class TestCSSParser:
    def test_extracts_font_face_urls(self):
        from fontmatch.scrape.css_parser import extract_font_urls

        css = (FIXTURES / "sample.css").read_text()
        urls = extract_font_urls(css, base_url="https://example.com/styles/main.css")

        # Should extract font URLs, resolving relative to CSS URL
        assert len(urls) >= 2
        # Check that woff2 is preferred
        woff2_urls = [u for u in urls if u["url"].endswith(".woff2")]
        assert len(woff2_urls) >= 2

    def test_resolves_relative_urls(self):
        from fontmatch.scrape.css_parser import extract_font_urls

        css = "@font-face { src: url('fonts/test.woff2') format('woff2'); }"
        urls = extract_font_urls(css, base_url="https://example.com/css/style.css")
        assert urls[0]["url"] == "https://example.com/css/fonts/test.woff2"

    def test_resolves_absolute_path_urls(self):
        from fontmatch.scrape.css_parser import extract_font_urls

        css = "@font-face { src: url('/static/font.woff2') format('woff2'); }"
        urls = extract_font_urls(css, base_url="https://example.com/css/style.css")
        assert urls[0]["url"] == "https://example.com/static/font.woff2"

    def test_prefers_woff2_over_other_formats(self):
        from fontmatch.scrape.css_parser import extract_font_urls

        css = """@font-face {
            src: url('f.woff') format('woff'),
                 url('f.woff2') format('woff2'),
                 url('f.ttf') format('truetype');
        }"""
        urls = extract_font_urls(css, base_url="https://example.com/s.css")
        # Should return only the woff2 for this face
        assert len(urls) == 1
        assert urls[0]["url"].endswith(".woff2")

    def test_handles_empty_css(self):
        from fontmatch.scrape.css_parser import extract_font_urls

        urls = extract_font_urls("", base_url="https://example.com/s.css")
        assert urls == []


class TestDedup:
    def test_deduplicates_by_hash(self):
        from fontmatch.scrape.dedup import dedup_fonts

        font_bytes = (FIXTURES / "Cousine-Regular.ttf").read_bytes()
        # Same bytes, different "URLs"
        entries = [
            {"url": "https://a.com/font.ttf", "data": font_bytes},
            {"url": "https://b.com/font.ttf", "data": font_bytes},
        ]
        result = dedup_fonts(entries)
        assert len(result) == 1


class TestSiteList:
    def test_load_majestic_million_csv(self):
        """Test that we can parse a Majestic Million CSV fragment."""
        from fontmatch.scrape.sites import parse_site_list

        csv_content = (
            "GlobalRank,TldRank,Domain,TLD,RefSubNets,RefIPs,IDN_Domain,IDN_TLD,PrevGlobalRank,PrevTldRank,PrevRefSubNets,PrevRefIPs\n"
            "1,1,google.com,com,486790,2667404,google.com,com,1,1,487598,2672763\n"
            "2,2,facebook.com,com,486549,2381487,facebook.com,com,2,2,484979,2379498\n"
            "3,3,youtube.com,com,405948,2096926,youtube.com,com,3,3,405576,2095925\n"
        )
        domains = parse_site_list(csv_content, limit=2)
        assert domains == ["google.com", "facebook.com"]
