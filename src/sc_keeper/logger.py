import logging
from json import dumps
from logging import Formatter
from time import time

from starlette.middleware.base import BaseHTTPMiddleware


class JsonFormatter(Formatter):
    def __init__(self):
        super(JsonFormatter, self).__init__()

    def format(self, record):
        json_record = {}
        ## TODO event?
        json_record["message"] = record.getMessage()
        for nested in ["client", "req", "res", "timing"]:
            if nested in record.__dict__:
                json_record[nested] = record.__dict__[nested]
        if record.levelno == logging.ERROR and record.exc_info:
            json_record["err"] = self.formatException(record.exc_info)
        return dumps(json_record)


class LogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_time = time()
        response = await call_next(request)
        response_time = time()
        logger.info(
            "access",
            extra={
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
                "res": {
                    "status_code": response.status_code,
                    "length": int(response.headers["content-length"]),
                },
                "timing": {
                    "reponse": response_time,
                    "request": request_time,
                    "duration": round(response_time - request_time, 4),
                },
            },
        )
        return response


logger = logging.root
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.handlers = [handler]
logger.setLevel(logging.DEBUG)
