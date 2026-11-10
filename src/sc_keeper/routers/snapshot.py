"""Database table snapshots pinned to a hash.

Every route serves a columnar JSON file built in advance, gzipped, including all
active records, without the `observed_at` timestamp.
"""

import gzip
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Request, Security
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, TypeAdapter, create_model
from pydantic.errors import PydanticSchemaGenerationError

from .. import parameters as options
from .. import snapshot_storage, snapshots
from ..auth import User, current_user
from ..data_version import (
    DATA_HASH_HEADER,
    DATA_PUBLISHED_AT_HEADER,
    format_published_at,
)
from ..database import session

router = APIRouter()

# R16: immutable for a hash
CACHE_CONTROL = "private, max-age=86400, immutable"
# R17: problem types
NOT_FOUND = "/problems/not-found"
NOT_AVAILABLE = "/problems/snapshot-not-available"

_HASH = re.compile(r"^[0-9a-f]{40,64}$")
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_:.-]*$")

ERROR_RESPONSES = {
    401: {"description": "No API key."},
    404: {
        "description": (
            "Hash not available (problem type `/problems/snapshot-not-available`), "
            "or unknown table, vendor or malformed id (`/problems/not-found`)."
        )
    },
    406: {"description": "The request doesn't accept gzip."},
}


# ##############################################################################
# Response models (documentation only)


def _documentable(annotation) -> Any:
    """A list of the column's type, or of any JSON value when pydantic can't describe
    the type (e.g. sc-crawler's HashableDict of benchmark configs)."""
    try:
        TypeAdapter(List[annotation]).json_schema()
        return List[annotation]
    except PydanticSchemaGenerationError:
        return List[Any]


def columnar_model(model) -> type[BaseModel]:
    """A columnar response model of a table: one list per snapshot column (R12)."""
    table = model.__table__
    fields = {}
    for column in snapshots._columns(table):
        info = model.model_fields.get(column)
        if info is not None:
            annotation, description = info.annotation, info.description
        else:  # columns added by sc-keeper, e.g. price_monthly
            annotation = Optional[table.c[column].type.python_type]
            description = table.c[column].doc
        fields[column] = (_documentable(annotation), Field(description=description))
    return create_model(
        f"{model.__name__}Snapshot",
        __doc__=f"Columnar snapshot of the `{table.name}` table.",
        **fields,
    )


class SnapshotManifest(BaseModel):
    """Every file of a hash's snapshots, sorted by path (R13)."""

    path: List[str] = Field(
        description="Route below `/snapshot/{hash}/`, e.g. `server_price/aws`."
    )
    rows: List[int] = Field(description="Number of rows in the file.")
    sha256: List[str] = Field(
        description="SHA-256 of the file's uncompressed JSON, in lowercase hex."
    )


# ##############################################################################
# Serving


def _problem(status: int, title: str, type_: Optional[str] = None, **extra):
    content = {"type": type_ or "about:blank", "title": title, "status": status}
    return JSONResponse(
        content | extra, status_code=status, media_type="application/problem+json"
    )


def _not_found() -> JSONResponse:
    return _problem(404, "Unknown snapshot", NOT_FOUND)


def _not_available(db_hash: str, current: Optional[str]) -> JSONResponse:
    return _problem(
        404,
        "Snapshot not available",
        NOT_AVAILABLE,
        requested=db_hash,
        current=current,
        available=snapshots.local_hashes(),
    )


def accepts_gzip(accept_encoding: Optional[str]) -> bool:
    """Whether `Accept-Encoding` lists `gzip` or `*` with a non-zero quality (R14)."""
    for item in (accept_encoding or "").split(","):
        coding, *params = [part.strip() for part in item.split(";")]
        if coding.lower() not in ("gzip", "*"):
            continue
        quality = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def _gzip_mtime(path: Path) -> int:
    with open(path, "rb") as f:
        return int.from_bytes(f.read(10)[4:8], "little")


def _headers(db_hash: str, mtime: int) -> dict[str, str]:
    headers = {
        "Content-Encoding": "gzip",
        "Cache-Control": CACHE_CONTROL,
        "Vary": "Accept-Encoding",
        DATA_HASH_HEADER: db_hash,
    }
    if mtime:
        published_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
        headers[DATA_PUBLISHED_AT_HEADER] = format_published_at(published_at)
    return headers


@lru_cache(maxsize=16)
def _vendors(hash_dir: Path) -> frozenset[str]:
    """Vendors in a complete build, from its `vendor` snapshot."""
    try:
        with gzip.open(snapshots.file_path(hash_dir, "vendor")) as f:
            return frozenset(json.load(f)["vendor_id"])
    except FileNotFoundError:
        return frozenset()


def _serve(
    request: Request,
    db_hash: str,
    path: str,
    vendor: Optional[str] = None,
    empty_columns: Optional[list[str]] = None,
) -> Response:
    """Serve a snapshot file from the first source that has it (R21)."""
    if not accepts_gzip(request.headers.get("accept-encoding")):
        return _problem(406, "Send Accept-Encoding: gzip")
    version = session.version
    current = version.hash if version else None
    if not _HASH.match(db_hash):
        return _not_available(db_hash, current)

    hash_dir = snapshots.snapshot_dir() / db_hash
    target = snapshots.file_path(hash_dir, path)
    if not target.is_file():
        complete = snapshot_storage.ensure(
            db_hash,
            current,
            session.engine,
            version.published_at if version else None,
        )
        if not target.is_file():
            if not complete:
                return _not_available(db_hash, current)
            # R11: a well-formed benchmark id without rows gets empty arrays
            if empty_columns is not None and vendor in _vendors(hash_dir):
                mtime = _gzip_mtime(
                    snapshots.file_path(hash_dir, snapshots.MANIFEST_PATH)
                )
                doc = {column: [] for column in empty_columns}
                _, compressed = snapshots.encode_file(doc, mtime)
                return Response(
                    compressed,
                    media_type="application/json",
                    headers=_headers(db_hash, mtime),
                )
            return _not_found()

    return FileResponse(
        target,
        media_type="application/json",
        headers=_headers(db_hash, _gzip_mtime(target)),
    )


# ##############################################################################
# Route descriptions

_CONSISTENCY = """Snapshots let you read several tables from the same version of the database:
every file of one `{hash}` comes from the same database, so references between them
always resolve, e.g. every price's server is in the `server` snapshot of that hash.
Pin `database_hash` from `/healthcheck` at the start of a refresh, and fetch every file
with it. `/snapshot/{hash}` lists the files with checksums, so you can skip the ones
that didn't change since your last refresh."""

_USAGE = """The response is immutable for a hash, requires an API key, and is sent gzipped only:
send `Accept-Encoding: gzip` (e.g. `curl --compressed`), or you get a `406`. A `404` of
type `/problems/snapshot-not-available` means the hash isn't available: switch to the
`current` hash in the response body, and fetch again the files you already fetched. A
`404` of type `/problems/not-found` is an unknown table, vendor or id: don't retry."""


def _contents(prices: bool = False) -> str:
    lines = [
        "- Only available records: status `active` or `planned-for-retirement`. "
        "Inactive and retired records are left out, and so are rows that reference "
        "them (e.g. the prices of a retired server, or of an inactive zone).",
        "- Every column of the table except `observed_at`: the hash already identifies "
        "the data version. For per-row timestamps, use `/table/*`.",
    ]
    if prices:
        lines.append(
            "- Prices (`price`, `price_upfront`, `price_monthly` and the tiers of "
            "`price_tiered`) converted to USD, unrounded, with `currency` set to `USD`."
        )
    lines += [
        "- Sorted by the table's primary key.",
        "- Columnar JSON: one array per column, with one element per row, all arrays "
        "of the same length.",
    ]
    return "\n".join(lines)


def _description(intro: str, prices: bool = False) -> str:
    return f"{intro}\n\n{_CONSISTENCY}\n\nContents:\n\n{_contents(prices)}\n\n{_USAGE}"


# ##############################################################################
# Routes


@router.get(
    "/{hash}",
    response_model=SnapshotManifest,
    responses=ERROR_RESPONSES,
    summary="Snapshot manifest",
    description=(
        "Every snapshot file of a database hash, sorted by path: its route below "
        "`/snapshot/{hash}/` (`path`), its number of rows (`rows`), and the SHA-256 of "
        "its uncompressed JSON (`sha256`). Compare the checksums with your last refresh "
        "to skip files that didn't change, e.g. specs, regions and zones, which rarely "
        "do. The manifest also lists which tables, vendors and benchmarks the hash "
        "has.\n\n" + _CONSISTENCY + "\n\n" + _USAGE
    ),
)
def snapshot_manifest(
    request: Request, hash: options.snapshot_hash, user: User = Security(current_user)
) -> Response:
    """List every snapshot file of a database hash."""
    return _serve(request, hash, snapshots.MANIFEST_PATH)


def _add_global_route(model) -> None:
    table = model.__table__.name

    def endpoint(
        request: Request,
        hash: options.snapshot_hash,
        user: User = Security(current_user),
    ) -> Response:
        return _serve(request, hash, table)

    endpoint.__doc__ = f"Snapshot of the `{table}` table."
    router.add_api_route(
        f"/{{hash}}/{table}",
        endpoint,
        description=_description(f"Snapshot of the `{table}` table."),
        methods=["GET"],
        response_model=columnar_model(model),
        responses=ERROR_RESPONSES,
        name=f"snapshot_{table}",
        summary=f"Snapshot of {table}",
    )


def _add_vendor_route(model) -> None:
    table = model.__table__.name

    def endpoint(
        request: Request,
        hash: options.snapshot_hash,
        vendor: options.snapshot_vendor,
        user: User = Security(current_user),
    ) -> Response:
        if not _ID.match(vendor):
            return _not_found()
        return _serve(request, hash, f"{table}/{vendor}")

    endpoint.__doc__ = f"Snapshot of the `{table}` table for one vendor."
    router.add_api_route(
        f"/{{hash}}/{table}/{{vendor}}",
        endpoint,
        description=_description(
            f"Snapshot of the `{table}` table for one vendor. A vendor without rows in "
            "the table gets empty arrays.",
            prices=table in snapshots.PRICE_TABLES,
        ),
        methods=["GET"],
        response_model=columnar_model(model),
        responses=ERROR_RESPONSES,
        name=f"snapshot_{table}",
        summary=f"Snapshot of {table}",
    )


for _model in snapshots.GLOBAL_TABLES:
    _add_global_route(_model)
for _model in snapshots.VENDOR_TABLES:
    _add_vendor_route(_model)

_score_columns = snapshots._columns(snapshots.SCORE_TABLE.__table__)


@router.get(
    "/{hash}/benchmark_score/{vendor}/{benchmark_id}",
    response_model=columnar_model(snapshots.SCORE_TABLE),
    responses=ERROR_RESPONSES,
    summary="Snapshot of benchmark_score",
    description=_description(
        "Benchmark scores of one vendor and benchmark, with every config, for servers "
        "and databases (`resource_type`). Tell configs apart by the `config` column, "
        "and join `resource_id` to the `server_id` of the `server` snapshot (or the "
        "`database_id` of the `database` snapshot), e.g. to get `api_reference`, which "
        "differs from the id at some vendors. A benchmark without rows for the vendor "
        "gets empty arrays. The `benchmark` snapshot lists every benchmark."
    ),
)
def snapshot_benchmark_score(
    request: Request,
    hash: options.snapshot_hash,
    vendor: options.snapshot_vendor,
    benchmark_id: options.snapshot_benchmark_id,
    user: User = Security(current_user),
) -> Response:
    """Scores of one vendor and benchmark, with every config."""
    if not _ID.match(vendor) or not _ID.match(benchmark_id):
        return _not_found()
    return _serve(
        request,
        hash,
        f"benchmark_score/{vendor}/{benchmark_id}",
        vendor=vendor,
        empty_columns=_score_columns,
    )
