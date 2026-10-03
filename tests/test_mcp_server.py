"""MCP server contract tests (in-process client, no network)."""

import anyio
import pytest

from fontmatch.image.fetch import FetchedImage, FetchError


def _png_bytes(size=(40, 20)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


MATCH = {
    "name": "Tinos-Regular.ttf",
    "family": "Tinos",
    "style": "Regular",
    "license_id": "OFL-1.1",
    "category": "serif",
    "score": 87,
    "similar_url": "/similar-to/tinos",
    "google_fonts_url": "https://fonts.google.com/specimen/Tinos",
}


class FakeBackend:
    def __init__(self):
        self.calls = []

    def identify_image(self, data, hint):
        from fontmatch.mcp_server.server import BackendError

        self.calls.append(("identify", len(data), hint))
        if hint == "notext":
            raise BackendError(422, "No readable text found in the image.")
        return {
            "transcript": hint or "OCR TEXT",
            "transcript_source": "hint" if hint else "ocr",
            "matches": [MATCH],
        }

    def similar(self, name):
        from fontmatch.mcp_server.server import BackendError

        self.calls.append(("similar", name))
        if name == "Nope":
            raise BackendError(404, "Font 'Nope' is not in our catalog")
        return {
            "query": name,
            "matched_font": "Arimo",
            "proprietary": {"name": name},
            "matches": [MATCH],
        }


def _fake_fetch(url):
    if "bad" in url:
        raise FetchError("Image host bad.example.com is not allowed")
    return FetchedImage(
        data=_png_bytes(), content_type="image/png", final_host="files.example.com"
    )


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def server(backend):
    from fontmatch.mcp_server.server import create_server

    return create_server(fetch=_fake_fetch, backend=backend)


def _run(coro_fn):
    return anyio.run(coro_fn)


def _list_tools(server):
    from mcp import Client

    async def go():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return _run(go)


def _call(server, name, args):
    from mcp import Client

    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return _run(go)


def _tool(server, name):
    return next(t for t in _list_tools(server) if t.name == name)


class TestImageToolContract:
    """Requirements from the OpenAI Apps SDK file-input docs."""

    def test_declares_file_param_meta(self, server):
        tool = _tool(server, "find_free_font_from_image")
        assert tool.meta["openai/fileParams"] == ["image"]

    def test_image_schema_has_all_four_string_props_and_required_ids(self, server):
        schema = _tool(server, "find_free_font_from_image").input_schema
        image = schema["properties"]["image"]
        if "$ref" in image:
            image = schema["$defs"][image["$ref"].split("/")[-1]]
        for prop in ("download_url", "file_id", "mime_type", "file_name"):
            assert image["properties"][prop]["type"] == "string"
        assert set(image["required"]) == {"download_url", "file_id"}
        assert "image" in schema["required"]

    def test_annotations_all_three_hints(self, server):
        ann = _tool(server, "find_free_font_from_image").annotations
        assert ann.read_only_hint is True
        assert ann.destructive_hint is False
        assert ann.open_world_hint is False


class TestImageToolCalls:
    def test_call_returns_matches_with_absolute_links(self, server, backend):
        result = _call(
            server,
            "find_free_font_from_image",
            {
                "image": {"download_url": "https://files.example.com/a.png", "file_id": "file_1"},
                "text_hint": "Harbor",
            },
        )
        assert not result.is_error
        body = result.structured_content
        assert body["transcript"] == "Harbor"
        m = body["matches"][0]
        assert m["family"] == "Tinos"
        assert m["similarity"] == 87
        assert m["dupefont_url"] == "https://dupefont.com/similar-to/tinos"
        assert m["google_fonts_url"] == "https://fonts.google.com/specimen/Tinos"
        assert "Tinos" in m["css"]
        assert backend.calls[0][0] == "identify" and backend.calls[0][2] == "Harbor"

    def test_backend_user_error_becomes_tool_error(self, server):
        result = _call(
            server,
            "find_free_font_from_image",
            {
                "image": {"download_url": "https://files.example.com/a.png", "file_id": "f"},
                "text_hint": "notext",
            },
        )
        assert result.is_error
        assert "No readable text" in result.content[0].text

    def test_fetch_error_is_tool_error_not_crash(self, server):
        result = _call(
            server,
            "find_free_font_from_image",
            {"image": {"download_url": "https://bad.example.com/a.png", "file_id": "file_1"}},
        )
        assert result.is_error
        assert "not allowed" in result.content[0].text


class TestAlternativesTool:
    def test_contract(self, server):
        tool = _tool(server, "find_free_alternatives")
        assert tool.annotations.read_only_hint is True
        assert tool.input_schema["required"] == ["font_name"]

    def test_call(self, server):
        result = _call(server, "find_free_alternatives", {"font_name": "Helvetica"})
        assert not result.is_error
        body = result.structured_content
        assert body["is_proprietary"] is True
        assert body["matches"][0]["family"] == "Tinos"

    def test_unknown_font(self, server):
        result = _call(server, "find_free_alternatives", {"font_name": "Nope"})
        assert result.is_error
        assert "not in our catalog" in result.content[0].text


class TestRateLimit:
    def test_per_subject_limit(self, backend, monkeypatch):
        import fontmatch.mcp_server.server as srv

        monkeypatch.setattr(srv, "PER_SUBJECT_PER_MINUTE", 2)
        server = srv.create_server(fetch=_fake_fetch, backend=backend)
        results = [
            _call(server, "find_free_alternatives", {"font_name": "Helvetica"}) for _ in range(3)
        ]
        assert [r.is_error for r in results] == [False, False, True]
        assert "Too many requests" in results[2].content[0].text


class TestHttpBackend:
    def _backend(self, handler):
        import httpx

        from fontmatch.mcp_server.server import HttpBackend

        return HttpBackend(
            "http://127.0.0.1:8087", token="s3cret", transport=httpx.MockTransport(handler)
        )

    def test_identify_posts_multipart_with_token(self):
        import httpx

        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["token"] = request.headers.get("x-internal-token")
            seen["body"] = request.content
            return httpx.Response(200, json={"matches": []})

        out = self._backend(handler).identify_image(b"PNGDATA", "Hello")
        assert out == {"matches": []}
        assert seen["path"] == "/api/identify-image"
        assert seen["token"] == "s3cret"
        assert b"PNGDATA" in seen["body"] and b"Hello" in seen["body"]

    def test_user_errors_are_mapped(self):
        import httpx

        from fontmatch.mcp_server.server import BackendError

        backend = self._backend(lambda r: httpx.Response(422, json={"error": "No readable text"}))
        with pytest.raises(BackendError) as exc:
            backend.identify_image(b"x", "")
        assert exc.value.status == 422 and "No readable text" in str(exc.value)

    def test_server_errors_are_generic(self):
        import httpx

        from fontmatch.mcp_server.server import BackendError

        backend = self._backend(lambda r: httpx.Response(500, text="Traceback..."))
        with pytest.raises(BackendError) as exc:
            backend.similar("Arial")
        assert "Traceback" not in str(exc.value)


def test_rate_limit_is_per_openai_subject(backend, monkeypatch):
    from mcp import Client

    import fontmatch.mcp_server.server as srv

    monkeypatch.setattr(srv, "PER_SUBJECT_PER_MINUTE", 1)
    server = srv.create_server(fetch=_fake_fetch, backend=backend)

    async def go():
        async with Client(server) as client:
            out = []
            for subject in ("user-a", "user-b", "user-a"):
                r = await client.call_tool(
                    "find_free_alternatives",
                    {"font_name": "Helvetica"},
                    meta={"openai/subject": subject},
                )
                out.append(r.is_error)
            return out

    assert _run(go) == [False, False, True]


def test_create_app_silences_httpx_url_logging(monkeypatch):
    """httpx logs full request URLs at INFO, which would put ChatGPT's signed
    download URLs in journald."""
    import logging

    from fontmatch.mcp_server.server import create_app

    monkeypatch.setenv("FONTMATCH_INTERNAL_TOKEN", "t")
    create_app()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


class TestPhase3Review:
    def test_image_param_is_inline_not_ref(self, server):
        schema = _tool(server, "find_free_font_from_image").input_schema
        assert "$ref" not in str(schema)
        image = schema["properties"]["image"]
        assert set(image["required"]) == {"download_url", "file_id"}

    def test_image_budget_separate_from_alternatives(self, backend, monkeypatch):
        import fontmatch.mcp_server.server as srv

        monkeypatch.setattr(srv, "IMAGE_PER_MINUTE", 1)
        server = srv.create_server(fetch=_fake_fetch, backend=backend)
        img = {"image": {"download_url": "https://files.example.com/a.png", "file_id": "f"}}
        assert not _call(server, "find_free_font_from_image", img).is_error
        assert _call(server, "find_free_font_from_image", img).is_error
        assert not _call(server, "find_free_alternatives", {"font_name": "Arial"}).is_error

    def test_busy_backend_fails_fast(self, backend, monkeypatch):
        import fontmatch.mcp_server.server as srv

        server = srv.create_server(fetch=_fake_fetch, backend=backend)
        monkeypatch.setattr(srv, "_IMAGE_SLOTS", srv.threading.BoundedSemaphore(1))
        srv._IMAGE_SLOTS.acquire()
        try:
            img = {"image": {"download_url": "https://files.example.com/a.png", "file_id": "f"}}
            result = _call(server, "find_free_font_from_image", img)
            assert result.is_error and "busy" in result.content[0].text.lower()
        finally:
            srv._IMAGE_SLOTS.release()

    def test_limiter_does_not_store_denied_subjects(self):
        from fontmatch.mcp_server.server import RateLimiter

        limiter = RateLimiter(per_subject=5, global_limit=1)
        assert limiter.allow("a")
        for i in range(100):
            assert not limiter.allow(f"spoofed-{i}")
        assert len(limiter._hits) == 1

    def test_create_app_requires_internal_token(self, monkeypatch):
        from fontmatch.mcp_server.server import create_app

        monkeypatch.delenv("FONTMATCH_INTERNAL_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="FONTMATCH_INTERNAL_TOKEN"):
            create_app()

    def test_backend_429_is_reported_as_busy(self):
        import httpx

        from fontmatch.mcp_server.server import BackendError, HttpBackend

        backend = HttpBackend(
            "http://x", transport=httpx.MockTransport(lambda r: httpx.Response(429))
        )
        with pytest.raises(BackendError, match="busy"):
            backend.similar("Arial")
