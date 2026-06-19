"""MCP tools for Spare Cores Keeper, mounted on the main FastAPI app at /mcp."""

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from .mcp_invoke import invoke_search_server_prices, invoke_search_servers

SERVERS_MAX_LIMIT = 25
SERVER_PRICES_MAX_LIMIT = 50

_FILTER_SCHEMAS: dict[str, str] | None = None
_tools_registered = False


def _filter_schema(endpoint: str) -> str:
    global _FILTER_SCHEMAS
    if _FILTER_SCHEMAS is None:
        from .ai import convert_swagger_to_json_schema, get_swagger

        swagger = get_swagger()
        _FILTER_SCHEMAS = {
            "/servers": json.dumps(
                convert_swagger_to_json_schema(swagger, "/servers"), indent=2
            ),
            "/server_prices": json.dumps(
                convert_swagger_to_json_schema(swagger, "/server_prices"), indent=2
            ),
        }
    return _FILTER_SCHEMAS[endpoint]


def _mcp_enabled() -> bool:
    return os.environ.get("MCP_ENABLED", "true").lower() in ("1", "true", "yes")


mcp = FastMCP(
    "sc-keeper",
    instructions=(
        "Search cloud server instances and prices across vendors using Spare Cores data. "
        "Use search_servers for aggregated per-server results with best prices. "
        "Use search_server_prices for per-region/allocation price rows."
    ),
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
)


def _format_response(status: int, body: Any, headers: dict[str, str]) -> str:
    if status != 200:
        return json.dumps({"status": status, "detail": body}, indent=2)

    result: dict[str, Any] = {"results": body}
    if total := headers.get("x-total-count"):
        result["total_count"] = int(total)
    return json.dumps(result, indent=2)


def _parse_filters(filters_json: str) -> dict[str, Any]:
    if not filters_json or filters_json.strip() in ("", "{}"):
        return {}
    try:
        parsed = json.loads(filters_json)
    except json.JSONDecodeError as exc:
        return {"__error__": f"Invalid filters_json: {exc}"}
    if not isinstance(parsed, dict):
        return {"__error__": "filters_json must be a JSON object"}
    return parsed


def _build_params(filters: dict[str, Any], limit: int) -> dict[str, Any]:
    if "__error__" in filters:
        return filters
    return {**filters, "limit": limit}


async def search_servers(filters_json: str = "{}", limit: int = 10) -> str:
    filters = _parse_filters(filters_json)
    if "__error__" in filters:
        return json.dumps({"status": 400, "detail": filters["__error__"]}, indent=2)
    limit = min(max(1, limit), SERVERS_MAX_LIMIT)
    status, body, headers = invoke_search_servers(_build_params(filters, limit))
    return _format_response(status, body, headers)


async def search_server_prices(filters_json: str = "{}", limit: int = 10) -> str:
    filters = _parse_filters(filters_json)
    if "__error__" in filters:
        return json.dumps({"status": 400, "detail": filters["__error__"]}, indent=2)
    limit = min(max(1, limit), SERVER_PRICES_MAX_LIMIT)
    status, body, headers = await invoke_search_server_prices(
        _build_params(filters, limit)
    )
    return _format_response(status, body, headers)


def _register_tools() -> None:
    global _tools_registered
    if _tools_registered:
        return

    search_servers.__doc__ = (
        "Search cloud server instances across vendors (GET /servers).\n\n"
        "Returns one row per server with aggregated best prices and optional "
        "benchmark scores.\n\n"
        "Pass filters as a JSON string in filters_json, e.g. "
        '{"vcpus_min": 4, "memory_min": 16, "vendor": ["hcloud"]}. '
        "Supported filter keys:\n\n"
        + _filter_schema("/servers")
    )
    mcp.tool()(search_servers)

    search_server_prices.__doc__ = (
        "Search per-region server price rows across vendors (GET /server_prices).\n\n"
        "Returns one row per price record (vendor/region/zone/allocation) with nested "
        "vendor, region, and server objects.\n\n"
        "Pass filters as a JSON string in filters_json, e.g. "
        '{"partial_name_or_id": "a2-highgpu-1g", "allocation": "ondemand", '
        '"only_active": true, "order_by": "price"}. Supported filter keys:\n\n'
        + _filter_schema("/server_prices")
    )
    mcp.tool()(search_server_prices)
    _tools_registered = True


def mount_mcp(app) -> None:
    """Attach streamable HTTP MCP to the Keeper FastAPI app at /mcp."""
    if not _mcp_enabled():
        return
    _register_tools()
    app.mount("/mcp", mcp.streamable_http_app())


@asynccontextmanager
async def mcp_lifespan():
    """Start StreamableHTTP session manager (required when MCP is mounted on FastAPI)."""
    if not _mcp_enabled():
        yield
        return
    # streamable_http_app() is called from mount_mcp(); session_manager is lazy-init there.
    if mcp._session_manager is None:  # type: ignore[attr-defined]
        yield
        return
    async with mcp.session_manager.run():
        yield
