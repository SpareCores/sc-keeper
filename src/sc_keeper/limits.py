from __future__ import annotations

import asyncio
import threading
from logging import getLogger
from os import environ

from fastapi import HTTPException

logger = getLogger(__name__)


class SemaphoreLimiter:
    """Process-local concurrency limiter backed by a threading.BoundedSemaphore.

    Designed to be used via an async FastAPI dependency wrapper that acquires
    a permit with timeout and releases it once the request is finished.
    """

    def __init__(self, name: str, max_concurrent: int, timeout: float) -> None:
        self.name = name
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._timeout = timeout

    async def __aenter__(self) -> "SemaphoreLimiter":
        # push the potentially blocking acquire into a worker thread to keep async endpoints responsive
        acquired = await asyncio.to_thread(
            self._semaphore.acquire, timeout=self._timeout
        )
        if not acquired:
            raise HTTPException(
                status_code=503,
                detail="Temporarily unavailable: too many heavy jobs running",
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._semaphore.release()


HEAVY_JOBS_MAX_CONCURRENT = int(environ.get("HEAVY_JOBS_MAX_CONCURRENT", 2))
HEAVY_JOBS_ACQUIRE_TIMEOUT_SEC = float(environ.get("HEAVY_JOBS_ACQUIRE_TIMEOUT_SEC", 2))

_heavy_jobs_limiter = SemaphoreLimiter(
    name="heavy_jobs",
    max_concurrent=HEAVY_JOBS_MAX_CONCURRENT,
    timeout=HEAVY_JOBS_ACQUIRE_TIMEOUT_SEC,
)


async def heavy_job_dep():
    """FastAPI dependency that limits the number of concurrent heavy jobs.

    Uses a process-local semaphore to cap concurrency for endpoints that are
    expensive in terms of DB or CPU usage. Returns 503 when the limit is hit.
    """
    async with _heavy_jobs_limiter:
        yield
