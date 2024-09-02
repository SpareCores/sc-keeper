import logging

from fastapi import APIRouter, Request

from ..ai import openai_extract_filters
from ..logger import get_request_id

router = APIRouter()


@router.get("/assist_server_filters")
def assist_server_filters(text: str, request: Request) -> dict:
    """Extract Server JSON filters from freetext."""
    res = openai_extract_filters(text, endpoint="/servers")
    logging.info(
        "openai response",
        extra={
            "event": "assist_filters response",
            "res": res,
            "request_id": get_request_id(),
        },
    )
    return res


@router.get("/assist_server_price_filters")
def assist_server_price_filters(text: str, request: Request) -> dict:
    """Extract ServerPrice JSON filters from freetext."""
    res = openai_extract_filters(text, endpoint="/server_prices")
    logging.info(
        "openai response",
        extra={
            "event": "assist_filters response",
            "res": res,
            "request_id": get_request_id(),
        },
    )
    return res


@router.get("/assist_storage_price_filters")
def assist_storage_price_filters(text: str, request: Request) -> dict:
    """Extract StoragePrice JSON filters from freetext."""
    res = openai_extract_filters(text, endpoint="/storage_prices")
    logging.info(
        "openai response",
        extra={
            "event": "assist_filters response",
            "res": res,
            "request_id": get_request_id(),
        },
    )
    return res
