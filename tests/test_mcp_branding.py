"""What ChatGPT sees from the MCP server must carry the DupeFont brand only."""

import json

import anyio


def test_mcp_tool_listing_has_no_old_brand():
    from mcp import Client

    from fontmatch.mcp_server.server import create_server

    async def go():
        async with Client(create_server()) as client:
            return (await client.list_tools()).tools

    dumped = json.dumps([t.model_dump(by_alias=True, mode="json") for t in anyio.run(go)])
    assert "fontmatch" not in dumped.lower()


def test_crawler_user_agent_is_dupefont():
    import inspect

    from fontmatch.scrape import crawler

    src = inspect.getsource(crawler)
    assert "DupeFont/" in src and "FontMatcher" not in src


def test_api_docs_use_https_behind_proxy(tmp_path):
    from fontmatch.service.app import create_app

    client = create_app(db_path=tmp_path / "t.db", testing=True).test_client()
    html = client.get(
        "/api/docs",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "1.2.3.4", "Host": "dupefont.com"},
    ).data.decode()
    assert "https://dupefont.com/api/health" in html


def test_server_identity_carries_display_name_and_icon():
    """ChatGPT's app header reads serverInfo, not the plugin package."""
    from mcp import Client

    from fontmatch.mcp_server.server import SITE_URL, create_server

    async def go():
        async with Client(create_server()) as client:
            return client.server_info

    info = anyio.run(go)
    assert info.name == "dupefont"  # routing and the @-mention key off name, not title
    assert info.title == "DupeFont"
    assert info.icons, "server advertises no icon"
    icon = info.icons[0].model_dump(by_alias=True, exclude_none=True)
    assert icon["src"] == f"{SITE_URL}/static/favicon.svg"
    assert icon["mimeType"] == "image/svg+xml"
    assert icon["sizes"] == ["any"]


def test_advertised_icon_url_is_actually_served(tmp_path):
    """A broken icon URL fails silently in ChatGPT: the header just stays generic."""
    from urllib.parse import urlparse

    from mcp import Client

    from fontmatch.mcp_server.server import create_server
    from fontmatch.service.app import create_app

    async def go():
        async with Client(create_server()) as client:
            return client.server_info

    path = urlparse(anyio.run(go).icons[0].src).path
    resp = create_app(db_path=tmp_path / "t.db", testing=True).test_client().get(path)
    assert resp.status_code == 200, f"{path} is advertised to ChatGPT, got {resp.status_code}"
    assert "svg" in resp.headers["Content-Type"]


def test_server_info_carries_no_old_brand():
    """serverInfo is ChatGPT-visible but was not covered by the tool-listing guard."""
    from mcp import Client

    from fontmatch.mcp_server.server import create_server

    async def go():
        async with Client(create_server()) as client:
            return client.server_info

    info = anyio.run(go)
    assert "fontmatch" not in info.model_dump_json(by_alias=True).lower()
