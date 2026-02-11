import logging
from contextvars import ContextVar
from json import dumps
from logging import Formatter
from resource import RUSAGE_SELF, getrusage
from time import time
from uuid import uuid4

from psutil import Process
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import User

_request_id_ctx_var: ContextVar[str] = ContextVar("request_id", default=None)


def get_request_id() -> str:
    """Return the request id of the current request based on the related context variable."""
    return _request_id_ctx_var.get()


class JsonFormatter(Formatter):
    """Format the log record as a JSON blob."""

    def __init__(self, *args, **kwargs):
        super(JsonFormatter, self).__init__()

    def format(self, record):
        json_record = {
            "timestamp": time(),
            "message": record.getMessage(),
            "level": record.__dict__["levelname"],
            "caller": {
                k: record.__dict__[k]
                for k in ["lineno", "funcName", "name", "module", "logger"]
                if k in record.__dict__
            },
        }
        for nested in [
            "event",
            "request_id",
            "client",
            "req",
            "res",
            "rate_limit",
            "proc",
        ]:
            if nested in record.__dict__:
                json_record[nested] = record.__dict__[nested]
        if record.levelno == logging.ERROR and record.exc_info:
            json_record["err"] = self.formatException(record.exc_info)
        return dumps(json_record)


class LogMiddleware(BaseHTTPMiddleware):
    """Logs requests and responses with metadata."""

    async def dispatch(self, request, call_next):
        request_id = _request_id_ctx_var.set(str(uuid4()))
        request_time = time()
        request_resources = getrusage(RUSAGE_SELF)
        # not available on Mac OS
        request_cpu_times = Process().cpu_times()
        # not available on Mac OS
        request_io = (
            Process().io_counters() if hasattr(Process(), "io_counters") else None
        )

        request_info = {
            "method": request.method,
            "path": request.url.path,
            "parameters": {
                "query": request.query_params._dict,
                "path": request.path_params,
            },
            "referer": request.headers.get("Referer"),
        }

        client_info = {
            "application": request.headers.get("X-Application-ID"),
            "ip": request.headers.get(
                "X-Forwarded-For",
                request.client.host if request.client else "Unknown",
            ),
            "ua": request.headers.get("User-Agent"),
            "hints": {
                "ua": request.headers.get("Sec-Ch-Ua"),
                "platform": request.headers.get("Sec-Ch-Ua-Platform"),
                "mobile": request.headers.get("Sec-CH-UA-Mobile"),
                "arch": request.headers.get("Sec-CH-UA-Arch"),
            },
        }

        user = getattr(request.state, "user", None)
        if user and isinstance(user, User):
            client_info["user"] = user.model_dump()

        logging.info(
            "request received",
            extra={
                "event": "request",
                "request_id": get_request_id(),
                "client": client_info,
                "req": request_info,
            },
        )

        response = await call_next(request)
        current_time = time()
        current_resources = getrusage(RUSAGE_SELF)
        # not available on Mac OS
        current_cpu_times = Process().cpu_times()
        # not available on Mac OS
        current_io = (
            Process().io_counters() if hasattr(Process(), "io_counters") else None
        )

        response.headers["X-Request-ID"] = get_request_id()
        content_length = response.headers.get("content-length")

        def _io_diff(attr_name):
            """Calculate IO difference if request_io exists, otherwise None."""
            return (
                getattr(current_io, attr_name) - getattr(request_io, attr_name)
                if request_io
                else None
            )

        def _cpu_times_diff(attr_name):
            """Calculate CPU time difference if both current and request CPU times have the attribute, otherwise None."""
            return (
                round(
                    getattr(current_cpu_times, attr_name)
                    - getattr(request_cpu_times, attr_name),
                    2,
                )
                if hasattr(current_cpu_times, attr_name)
                and hasattr(request_cpu_times, attr_name)
                else None
            )

        logging.info(
            "response returned",
            extra={
                "event": "response",
                "request_id": get_request_id(),
                "req": request_info,
                "res": {
                    "status_code": response.status_code,
                    "length": int(content_length) if content_length else None,
                },
                "rate_limit": getattr(request.state, "rate_limit", {}),
                "proc": {
                    "real": round(current_time - request_time, 4),
                    "user": round(
                        current_resources.ru_utime - request_resources.ru_utime, 2
                    ),
                    "sys": round(
                        current_resources.ru_stime - request_resources.ru_stime, 2
                    ),
                    "iowait": _cpu_times_diff("iowait"),
                    "read_bytes": _io_diff("read_bytes"),
                    "write_bytes": _io_diff("write_bytes"),
                    "max_rss": current_resources.ru_maxrss,
                },
            },
        )
        _request_id_ctx_var.reset(request_id)
        return response
