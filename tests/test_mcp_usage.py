"""MCP usage tracking: one row per tool call, never breaks a call."""

import sqlite3

import anyio
import pytest

from tests.test_mcp_server import MATCH, FakeBackend, _fake_fetch


def _rows(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM mcp_calls ORDER BY id")]
    conn.close()
    return rows


def _call(server, name, args, subject="user-1", extra_meta=None):
    from mcp import Client

    meta = {"openai/subject": subject, **(extra_meta or {})}

    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args, meta=meta)

    return anyio.run(go)


IMAGE = {"download_url": "https://files.example.com/x.png", "file_id": "file_1"}


@pytest.fixture
def usage(tmp_path):
    from fontmatch.mcp_server.usage import UsageLog

    return UsageLog(tmp_path / "usage.db")


@pytest.fixture
def server(usage):
    from fontmatch.mcp_server.server import create_server

    return create_server(fetch=_fake_fetch, backend=FakeBackend(), usage=usage)


class TestUsageLog:
    def test_record_and_read_back(self, usage):
        usage.record(tool="find_free_alternatives", subject="abc", status="ok", latency_ms=12)
        (row,) = _rows(usage.path)
        assert row["tool"] == "find_free_alternatives"
        assert row["status"] == "ok"
        assert row["subject_hash"] and row["subject_hash"] != "abc"
        assert len(row["subject_hash"]) == 16
        assert row["ts"]

    def test_same_subject_same_hash(self, usage):
        usage.record(tool="t", subject="abc", status="ok")
        usage.record(tool="t", subject="abc", status="ok")
        usage.record(tool="t", subject="xyz", status="ok")
        hashes = [r["subject_hash"] for r in _rows(usage.path)]
        assert hashes[0] == hashes[1] != hashes[2]

    def test_never_raises(self, tmp_path):
        from fontmatch.mcp_server.usage import UsageLog

        log = UsageLog(tmp_path / "usage.db")
        log.conn.close()  # simulate a broken DB
        log.record(tool="t", subject="s", status="ok")  # must not raise

    def test_unwritable_location_does_not_raise(self, tmp_path):
        from fontmatch.mcp_server.usage import UsageLog

        blocker = tmp_path / "file"
        blocker.write_text("x")
        log = UsageLog(blocker / "sub" / "usage.db")
        log.record(tool="t", subject="s", status="ok")

    def test_prune_drops_old_rows(self, usage):
        usage.record(tool="t", subject="s", status="ok")
        usage.conn.execute("UPDATE mcp_calls SET ts = datetime('now', '-400 days')")
        usage.conn.commit()
        usage.record(tool="t", subject="s", status="ok")
        assert usage.prune(days=180) == 1
        assert len(_rows(usage.path)) == 1


class TestServerRecordsCalls:
    def test_alternatives_ok_row(self, server, usage):
        r = _call(server, "find_free_alternatives", {"font_name": "Helvetica"})
        assert not r.is_error
        (row,) = _rows(usage.path)
        assert row["tool"] == "find_free_alternatives"
        assert row["status"] == "ok"
        assert row["query"] == "Helvetica"
        assert row["top_family"] == MATCH["family"]
        assert row["top_similarity"] == MATCH["score"]
        assert row["latency_ms"] >= 0

    def test_image_ok_row_has_no_text_hint(self, server, usage):
        r = _call(
            server,
            "find_free_font_from_image",
            {"image": IMAGE, "text_hint": "SECRET HINT"},
            extra_meta={"openai/locale": "en-US", "openai/userLocation": {"country": "US"}},
        )
        assert not r.is_error
        (row,) = _rows(usage.path)
        assert row["status"] == "ok"
        assert row["image_host"] == "files.example.com"
        assert row["image_bytes"] > 0
        assert row["transcript_source"] == "hint"
        assert row["locale"] == "en-US"
        assert row["country"] == "US"
        assert "SECRET HINT" not in repr(row)

    def test_failures_are_recorded_with_status(self, server, usage):
        _call(server, "find_free_alternatives", {"font_name": "Nope"})
        _call(
            server,
            "find_free_font_from_image",
            {"image": {**IMAGE, "download_url": "https://bad/x"}},
        )
        _call(server, "find_free_font_from_image", {"image": IMAGE, "text_hint": "notext"})
        statuses = [(r["tool"], r["status"]) for r in _rows(usage.path)]
        assert statuses == [
            ("find_free_alternatives", "not_found"),
            ("find_free_font_from_image", "fetch_error"),
            ("find_free_font_from_image", "no_text"),
        ]

    def test_rate_limited_calls_are_recorded(self, usage, monkeypatch):
        import fontmatch.mcp_server.server as srv

        monkeypatch.setattr(srv, "PER_SUBJECT_PER_MINUTE", 1)
        server = srv.create_server(fetch=_fake_fetch, backend=FakeBackend(), usage=usage)
        _call(server, "find_free_alternatives", {"font_name": "Helvetica"})
        r = _call(server, "find_free_alternatives", {"font_name": "Helvetica"})
        assert r.is_error
        assert [row["status"] for row in _rows(usage.path)] == ["ok", "rate_limited"]

    def test_broken_usage_log_does_not_break_tools(self, usage):
        from fontmatch.mcp_server.server import create_server

        usage.conn.close()
        server = create_server(fetch=_fake_fetch, backend=FakeBackend(), usage=usage)
        assert not _call(server, "find_free_alternatives", {"font_name": "Helvetica"}).is_error

    def test_no_usage_log_by_default(self, tmp_path, monkeypatch):
        """create_server() without usage= must not write anywhere (tests run on prod)."""
        from fontmatch.mcp_server.server import create_server

        monkeypatch.chdir(tmp_path)
        server = create_server(fetch=_fake_fetch, backend=FakeBackend())
        assert not _call(server, "find_free_alternatives", {"font_name": "Helvetica"}).is_error
        assert list(tmp_path.iterdir()) == []


class TestResultsWidget:
    """Font samples reach ChatGPT through an Apps SDK UI component: plain MCP
    image content isn't rendered by ChatGPT."""

    def _resources(self, server):
        from mcp import Client

        async def go():
            async with Client(server) as client:
                listed = (await client.list_resources()).resources
                read = await client.read_resource(listed[0].uri)
                return listed, read

        return anyio.run(go)

    def test_widget_resource_is_an_mcp_app_with_csp_for_samples(self, server):
        from fontmatch.mcp_server.server import LEGACY_WIDGET_URIS, SITE_URL, WIDGET_URI

        listed, read = self._resources(server)
        assert [str(r.uri) for r in listed] == [WIDGET_URI, *LEGACY_WIDGET_URIS]
        content = read.contents[0]
        assert content.mime_type == "text/html;profile=mcp-app"
        meta = content.meta
        assert SITE_URL in meta["ui"]["csp"]["resourceDomains"]
        assert SITE_URL in meta["openai/widgetCSP"]["resource_domains"]
        assert "dupefont/sampleImages" in content.text

    def test_both_tools_point_at_the_widget(self, server):
        from fontmatch.mcp_server.server import WIDGET_URI
        from tests.test_mcp_server import _list_tools

        for tool in _list_tools(server):
            assert tool.meta["ui"]["resourceUri"] == WIDGET_URI
            assert tool.meta["openai/outputTemplate"] == WIDGET_URI
        image_tool = next(t for t in _list_tools(server) if t.name == "find_free_font_from_image")
        assert image_tool.meta["openai/fileParams"] == ["image"]
        assert "$ref" not in str(image_tool.input_schema)

    def test_sample_images_travel_in_result_meta_not_structured_content(self, server):
        """ChatGPT shows the model everything in structuredContent and it embeds
        image URLs as broken markdown images. Samples are widget-only, so they
        ride in ``_meta`` (window.openai.toolResponseMetadata)."""
        r = _call(server, "find_free_alternatives", {"font_name": "Helvetica"})
        match = r.structured_content["matches"][0]
        assert "sample_image_url" not in match
        assert "font-sample/" not in str(r.structured_content)
        assert "font-sample/" not in r.content[0].text
        samples = r.meta["dupefont/sampleImages"]
        assert len(samples) == len(r.structured_content["matches"])
        assert samples[0] == "https://dupefont.com/font-sample/Tinos-Regular.ttf.png?style=Regular"

    def test_image_tool_carries_samples_in_meta_too(self, server):
        r = _call(server, "find_free_font_from_image", {"image": IMAGE, "text_hint": "Hello"})
        assert "font-sample/" not in str(r.structured_content)
        assert r.meta["dupefont/sampleImages"] == [
            "https://dupefont.com/font-sample/Tinos-Regular.ttf.png?style=Regular"
        ]

    def test_match_without_a_file_name_keeps_the_sample_list_aligned(self, usage):
        from fontmatch.mcp_server.server import create_server

        class Backend(FakeBackend):
            def similar(self, name):
                return {
                    "query": name,
                    "matched_font": "Tinos",
                    "matches": [MATCH | {"name": None}, MATCH],
                }

        server = create_server(fetch=_fake_fetch, backend=Backend(), usage=usage)
        r = _call(server, "find_free_alternatives", {"font_name": "Helvetica"})
        assert len(r.structured_content["matches"]) == 2
        assert r.meta["dupefont/sampleImages"] == [
            None,
            "https://dupefont.com/font-sample/Tinos-Regular.ttf.png?style=Regular",
        ]

    def test_error_result_has_no_sample_meta(self, server):
        r = _call(server, "find_free_font_from_image", {"image": IMAGE, "text_hint": "notext"})
        assert r.is_error
        assert "dupefont/sampleImages" not in (r.meta or {})

    def test_output_schema_has_no_sample_image_field(self, server):
        from tests.test_mcp_server import _list_tools

        tool = next(t for t in _list_tools(server) if t.name == "find_free_alternatives")
        assert "sample_image_url" not in str(tool.output_schema)

    def test_previous_widget_uri_still_serves_the_current_widget(self, server):
        """ChatGPT asks for the URI it cached per tool for a while after a bump."""
        from mcp import Client

        from fontmatch.mcp_server.server import LEGACY_WIDGET_URIS, WIDGET_HTML, WIDGET_URI
        from tests.test_mcp_server import _run

        async def go():
            async with Client(server) as client:
                return [
                    (await client.read_resource(uri)).contents[0].text
                    for uri in (WIDGET_URI, *LEGACY_WIDGET_URIS)
                ]

        assert LEGACY_WIDGET_URIS
        assert all(text == WIDGET_HTML for text in _run(go))

        from fontmatch.mcp_server.server import WIDGET_HTML

        assert "dupefont/sampleImages" in WIDGET_HTML
        assert "toolResponseMetadata" in WIDGET_HTML
        assert "sample_image_url" not in WIDGET_HTML


def test_odd_request_meta_does_not_fail_the_call(usage, monkeypatch):
    import fontmatch.mcp_server.server as srv

    def broken(ctx):
        raise TypeError("weird meta")

    monkeypatch.setattr(srv, "_meta", broken)
    server = srv.create_server(fetch=_fake_fetch, backend=FakeBackend(), usage=usage)
    assert not _call(server, "find_free_alternatives", {"font_name": "Helvetica"}).is_error
    assert _rows(usage.path)[0]["status"] == "ok"
