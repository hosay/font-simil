"""Shared test setup. This machine is prod: tests must never touch prod state."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_prod_paths(tmp_path, monkeypatch):
    # The MCP app's usage log defaults to /var/lib/fontmatch/mcp_usage.db.
    monkeypatch.setenv("DUPEFONT_MCP_USAGE_DB", str(tmp_path / "mcp_usage.db"))
