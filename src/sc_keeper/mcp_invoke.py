"""Invoke Keeper search route handlers in-process for MCP tools."""

import inspect
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.requests import Request
from starlette.responses import Response


def _make_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 0),
            "server": ("localhost", 80),
        }
    )


def _invoke_route(
    path: str, handler_name: str, params: dict[str, Any]
) -> tuple[int, Any, dict[str, str]]:
    # isort: off
    from sc_keeper.database import session as db_session

    # Import after api module finished loading (called from MCP tools at runtime).
    from sc_keeper import api

    # isort: on
    handler = getattr(api, handler_name)
    sig = inspect.signature(handler)
    kwargs = {k: v for k, v in params.items() if k in sig.parameters}
    response = Response()
    db = db_session.sessionmaker
    try:
        call_kwargs: dict[str, Any] = {"response": response, "db": db, **kwargs}
        if "request" in sig.parameters:
            call_kwargs["request"] = _make_request(path)
        result = handler(**call_kwargs)
        return 200, jsonable_encoder(result), dict(response.headers)
    except HTTPException as exc:
        detail = exc.detail
        if not isinstance(detail, (dict, list)):
            detail = {"detail": detail}
        return exc.status_code, detail, {}
    finally:
        db.close()


def invoke_search_servers(params: dict[str, Any]) -> tuple[int, Any, dict[str, str]]:
    return _invoke_route("/servers", "search_servers", params)


async def invoke_search_server_prices(
    params: dict[str, Any],
) -> tuple[int, Any, dict[str, str]]:
    from sc_keeper.limits import _heavy_jobs_limiter

    async with _heavy_jobs_limiter:
        return _invoke_route("/server_prices", "search_server_prices", params)
