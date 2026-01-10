"""Shared Redis client utilities."""

import logging
from os import environ
from typing import Optional

logger = logging.getLogger(__name__)


def get_redis_client(
    redis_url: Optional[str] = None, must_succeed: bool = False
) -> Optional:
    """Get Redis client.

    Args:
        redis_url: Optional Redis URL. If not provided, reads from REDIS_URL env var.

    Returns:
        Redis client if available.

    Raises:
        ImportError: If redis package is not installed.
        KeyError: If Redis URL is not set.
    """
    import redis

    redis_url = redis_url or environ["REDIS_URL"]
    return redis.from_url(redis_url, decode_responses=True)
