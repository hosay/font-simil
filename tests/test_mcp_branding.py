"""What ChatGPT sees from the MCP server must carry the Dupefont brand only."""

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
    assert "Dupefont/" in src and "FontMatcher" not in src


def test_api_docs_use_https_behind_proxy(tmp_path):
    from fontmatch.service.app import create_app

    client = create_app(db_path=tmp_path / "t.db", testing=True).test_client()
    html = client.get(
        "/api/docs",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "1.2.3.4", "Host": "dupefont.com"},
    ).data.decode()
    assert "https://dupefont.com/api/health" in html
