import logging
from contextvars import ContextVar
from json import dumps
from logging import Formatter
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
        for nested in ["event", "request_id", "client", "req", "res", "elapsed_time"]:
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
        logging.info(
            "request received",
            extra={
                "event": "request",
                "request_id": get_request_id(),
                "client": {
                    "ip": request.headers.get("X-Forwarded-For", request.client.host),
                    "ua": request.headers.get("User-Agent"),
                    "hints": {
                        "ua": request.headers.get("Sec-Ch-Ua"),
                        "platform": request.headers.get("Sec-Ch-Ua-Platform"),
                        "mobile": request.headers.get("Sec-CH-UA-Mobile"),
                        "arch": request.headers.get("Sec-CH-UA-Arch"),
                    },
                },
                "req": {
                    "method": request.method,
                    "path": request.url.path,
                    "parameters": {
                        "query": request.query_params._dict,
                        "path": request.path_params,
                    },
                },
            },
        )

        response = await call_next(request)
        response_time = time()

        logging.info(
            "response returned",
            extra={
                "event": "response",
                "request_id": get_request_id(),
                "res": {
                    "status_code": response.status_code,
                    "length": int(response.headers["content-length"]),
                },
                "elapsed_time": round(response_time - request_time, 4),
            },
        )
        _request_id_ctx_var.reset(request_id)
        return response
