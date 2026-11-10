"""Data version headers: which sc-data file a response was built from."""

from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware

from .database import DataVersion, session

DATA_HASH_HEADER = "SC-Data-Hash"
DATA_PUBLISHED_AT_HEADER = "SC-Data-Published-At"


def format_published_at(published_at: datetime) -> str:
    """Format a publication time as RFC 3339 in UTC, truncated to whole seconds."""
    return published_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def data_version_headers(version: DataVersion) -> dict[str, str]:
    """Headers describing a data version."""
    headers = {}
    if version.hash is not None:
        headers[DATA_HASH_HEADER] = version.hash
    if version.published_at is not None:
        headers[DATA_PUBLISHED_AT_HEADER] = format_published_at(version.published_at)
    return headers


class DataVersionMiddleware(BaseHTTPMiddleware):
    """Set `SC-Data-Hash` and `SC-Data-Published-At` on every response.

    The version is captured when the request arrives, before any query runs, and
    used for the response even if the database is switched in the meantime: a
    request's session keeps reading the database file it was opened on. The only
    gap is a switch between this point and the endpoint opening its session (the
    time to run the other middlewares and resolve dependencies), which would label
    a response from the new database with the previous version.

    An endpoint that sets `SC-Data-Hash` itself (`/snapshot` responses, describing
    the snapshot's own version) gets neither header from here, so a response never
    pairs one version's hash with another version's publication time.
    """

    async def dispatch(self, request, call_next):
        version = session.version
        response = await call_next(request)
        if version is not None and DATA_HASH_HEADER not in response.headers:
            response.headers.update(data_version_headers(version))
        return response
