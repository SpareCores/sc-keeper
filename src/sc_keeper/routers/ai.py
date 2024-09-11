import logging

from fastapi import APIRouter, Request

from ..ai import openai_extract_filters
from ..logger import get_request_id

router = APIRouter()


def assister(text: str, endpoint: str) -> dict:
    res = openai_extract_filters(text, endpoint=endpoint)
    logging.info(
        "openai response",
        extra={
            "event": "assister response",
            "res": res,
            "request_id": get_request_id(),
        },
        stacklevel=2,
    )
    return res


@router.get("/assist_server_filters")
def assist_server_filters(text: str, request: Request) -> dict:
    """Extract Server JSON filters from freetext."""
    return assister(text, "/servers")


@router.get("/assist_server_price_filters")
def assist_server_price_filters(text: str, request: Request) -> dict:
    """Extract ServerPrice JSON filters from freetext."""
    return assister(text, "/server_prices")


@router.get("/assist_storage_price_filters")
def assist_storage_price_filters(text: str, request: Request) -> dict:
    """Extract StoragePrice JSON filters from freetext."""
    return assister(text, "/storage_prices")


@router.get("/assist_traffic_price_filters")
def assist_storage_price_filters(text: str, request: Request) -> dict:
    """Extract TrafficPrice JSON filters from freetext."""
    return assister(text, "/traffic_prices")
