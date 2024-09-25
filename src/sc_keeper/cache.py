from typing import Callable, Optional, Tuple, Dict, Any
from hashlib import sha1
from fastapi import Request, Response
from os import environ

from fastapi_cache import FastAPICache


def no_db_session_key_builder(
    func: Callable[..., Any],
    namespace: str = "",
    *,
    request: Optional[Request] = None,
    response: Optional[Response] = None,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> str:
    """Eclude the yielde DB session from the request hash input."""
    kwargs.pop("db")
    key = sha1(
        f"{func.__module__}:{func.__name__}:{args}:{kwargs}".encode()
    ).hexdigest()
    return f"{namespace}:{key}"


def cache_init():
    if environ.get("CACHE_BACKEND_MEMCACHE"):
        from fastapi_cache.backends.memcached import MemcachedBackend
        from aiomcache import Client

        client = Client(environ["CACHE_BACKEND_MEMCACHE"])
        print("YOOO")
        backend = MemcachedBackend(client)
    elif environ.get("CACHE_BACKEND_REDIS"):
        from fastapi_cache.backends.redis import RedisBackend
        from redis import asyncio as aioredis

        client = aioredis.from_url(environ["CACHE_BACKEND_REDIS"])
        backend = RedisBackend(client)
    else:
        from fastapi_cache.backends.inmemory import InMemoryBackend

        backend = InMemoryBackend()

    FastAPICache.init(backend, key_builder=no_db_session_key_builder)
