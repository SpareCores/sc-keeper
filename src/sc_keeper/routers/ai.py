import logging

from fastapi import APIRouter, Request

from ..ai import openai_extract_filters
from ..logger import get_request_id

logger = logging.getLogger(__name__)

router = APIRouter()


async def assister(text: str, endpoint: str) -> dict:
    res = await openai_extract_filters(text, endpoint=endpoint)
    logger.info(
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
async def assist_server_filters(text: str, request: Request) -> dict:
    """Extract Server JSON filters from freetext."""
    return await assister(text, "/servers")


@router.get("/assist_server_price_filters")
async def assist_server_price_filters(text: str, request: Request) -> dict:
    """Extract ServerPrice JSON filters from freetext."""
    return await assister(text, "/server_prices")


@router.get("/assist_storage_price_filters")
async def assist_storage_price_filters(text: str, request: Request) -> dict:
    """Extract StoragePrice JSON filters from freetext."""
    return await assister(text, "/storage_prices")


@router.get("/assist_traffic_price_filters")
async def assist_traffic_price_filters(text: str, request: Request) -> dict:
    """Extract TrafficPrice JSON filters from freetext."""
    return await assister(text, "/traffic_prices")
