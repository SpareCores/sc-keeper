"""Smoke tests for MCP server tools."""

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from sc_keeper.mcp_server import _register_tools, search_server_prices, search_servers

_register_tools()


def test_search_servers():
    result = asyncio.run(
        search_servers(filters_json='{"vcpus_min": 2}', limit=1)
    )
    data = json.loads(result)
    assert "results" in data
    assert len(data["results"]) == 1
    server = data["results"][0]
    assert "server_id" in server
    assert server["vcpus"] >= 2


def test_search_server_prices():
    result = asyncio.run(
        search_server_prices(
            filters_json='{"vendor": ["hcloud"], "vcpus_min": 2}', limit=1
        )
    )
    data = json.loads(result)
    assert "results" in data
    assert len(data["results"]) == 1
    row = data["results"][0]
    assert "price" in row
    assert "server" in row
    assert row["server"]["vcpus"] >= 2
