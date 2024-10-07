import logging
from contextvars import ContextVar
from json import dumps
from logging import Formatter
from psutil import Process, cpu_times
from resource import RUSAGE_SELF, getrusage
from time import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware

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
        for nested in ["event", "request_id", "client", "req", "res", "proc"]:
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
        request_cpu_times = Process().cpu_times()
        request_io = Process().io_counters()

        request_info = {
            "method": request.method,
            "path": request.url.path,
            "parameters": {
                "query": request.query_params._dict,
                "path": request.path_params,
            },
            "referer": request.headers.get("Referer"),
        }

        logging.info(
            "request received",
            extra={
                "event": "request",
                "request_id": get_request_id(),
                "client": {
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
                },
                "req": request_info,
            },
        )

        response = await call_next(request)
        current_time = time()
        current_resources = getrusage(RUSAGE_SELF)
        current_cpu_times = Process().cpu_times()
        current_io = Process().io_counters()

        response.headers["X-Request-ID"] = get_request_id()
        logging.info(
            "response returned",
            extra={
                "event": "response",
                "request_id": get_request_id(),
                "req": request_info,
                "res": {
                    "status_code": response.status_code,
                    "length": int(response.headers["content-length"]),
                },
                "proc": {
                    "real": round(current_time - request_time, 4),
                    "user": round(
                        current_resources.ru_utime - request_resources.ru_utime, 2
                    ),
                    "sys": round(
                        current_resources.ru_stime - request_resources.ru_stime, 2
                    ),
                    "iowait": round(
                        current_cpu_times.iowait - request_cpu_times.iowait, 2
                    ),
                    "read_bytes": current_io.read_bytes - request_io.read_bytes,
                    "write_bytes": current_io.write_bytes - request_io.write_bytes,
                    "max_rss": current_resources.ru_maxrss,
                },
            },
        )
        _request_id_ctx_var.reset(request_id)
        return response
