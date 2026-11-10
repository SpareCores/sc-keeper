"""Database snapshots: tables pinned to a database hash, served as files.

A hash's snapshots are one directory, `<SNAPSHOT_DIR>/<hash>/`, holding one
gzipped columnar JSON file per route below `/snapshot/<hash>/` (e.g.
`server_price/aws.json.gz`), plus `manifest.json.gz`. The manifest is written last,
so a directory with a manifest holds a complete build.
"""

import gzip
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from pathlib import Path
from typing import Iterable, Iterator, Optional

from sc_crawler.table_fields import ResourceType, Status
from sc_crawler.tables import (
    Benchmark,
    BenchmarkScore,
    ComplianceFramework,
    Country,
    Database,
    DatabasePrice,
    DatabaseStorage,
    DatabaseStoragePrice,
    Ipv4Price,
    Region,
    Server,
    ServerDescription,
    ServerPrice,
    Storage,
    StoragePrice,
    TrafficPrice,
    Vendor,
    VendorComplianceLink,
    Zone,
)
from sqlalchemy import JSON, Table
from sqlalchemy import Enum as SAEnum
from sqlalchemy.engine import Connection

from .currency import currency_converter
from .views import Currency

logger = logging.getLogger(__name__)

# R5: tables that reference a vendor are split by vendor, the others are one file
VENDOR_TABLES = [
    Region,
    Zone,
    Server,
    ServerDescription,
    ServerPrice,
    Storage,
    StoragePrice,
    TrafficPrice,
    Ipv4Price,
    Database,
    DatabasePrice,
    DatabaseStorage,
    DatabaseStoragePrice,
    VendorComplianceLink,
]
# The implementation overrides sc-specs 2026-11-08-keeper-snapshots.md R5, which
# splits every table with a vendor_id column by vendor, including `vendor` itself
# (one row per file). Here, only tables that reference a vendor (vendor_id is a
# foreign key to `vendor`) are split by vendor, so `vendor` is one file: the list
# of vendors.
GLOBAL_TABLES = [Vendor, Country, ComplianceFramework, Benchmark, Currency]
# R11: split by vendor and benchmark
SCORE_TABLE = BenchmarkScore

# R6: available records
AVAILABLE_STATUSES = (Status.ACTIVE, Status.PLANNED_FOR_RETIREMENT)
# R8: every column except these
EXCLUDED_COLUMNS = {"observed_at"}
# R9: prices converted to USD
PRICE_TABLES = {
    "server_price",
    "storage_price",
    "traffic_price",
    "ipv4_price",
    "database_price",
    "database_storage_price",
}
PRICE_COLUMNS = ("price", "price_upfront", "price_monthly")

MANIFEST_PATH = "manifest"
FILE_SUFFIX = ".json.gz"


def snapshot_dir() -> Path:
    """Local directory holding one subdirectory per database hash."""
    default = Path(tempfile.gettempdir()) / "sc-keeper-snapshots"
    return Path(os.environ.get("SNAPSHOT_DIR", default))


def local_retention_seconds() -> int:
    return int(os.environ.get("SNAPSHOT_LOCAL_RETENTION_SECONDS", 6 * 60 * 60))


def file_path(hash_dir: Path, path: str) -> Path:
    """Local file of a snapshot route below `/snapshot/<hash>/`."""
    return hash_dir / f"{path}{FILE_SUFFIX}"


def is_complete(hash_dir: Path) -> bool:
    """Whether a hash's directory holds a complete build (its manifest is written last)."""
    return file_path(hash_dir, MANIFEST_PATH).is_file()


def local_hashes() -> list[str]:
    """Hashes with a complete build on this replica, newest first (R17, R18)."""
    root = snapshot_dir()
    if not root.is_dir():
        return []
    manifests = []
    for hash_dir in root.iterdir():
        try:
            mtime = file_path(hash_dir, MANIFEST_PATH).stat().st_mtime
        except (FileNotFoundError, NotADirectoryError):
            continue  # not a complete build, or deleted meanwhile
        manifests.append((mtime, hash_dir.name))
    return [name for _, name in sorted(manifests, reverse=True)]


# ##############################################################################
# Row selection


def _columns(table: Table) -> list[str]:
    return [c.name for c in table.columns if c.name not in EXCLUDED_COLUMNS]


def _is_json(column) -> bool:
    column_type = getattr(column.type, "impl", column.type)
    return isinstance(column_type, JSON) or isinstance(column.type, JSON)


class RawJson(str):
    """JSON text as stored in the database, written to snapshots verbatim."""


class Selection:
    """Available rows of tables (R6), whose references are available too (R7).

    A row is kept only if every foreign key to a table with a status column points
    to a row that is kept itself, recursively, so every reference between the
    snapshots of one hash resolves. `benchmark_score.resource_id` points to a server
    or a database without a foreign key, and is checked the same way.

    Rows are read with the database driver, without SQLAlchemy's result processing:
    enums are mapped from the names the database stores to their values (R12), and
    JSON columns are kept as their stored text (`RawJson`), never parsed. This keeps
    `config` exactly as stored, and is several times faster on `benchmark_score`.
    Only the status filter runs in SQL. The referenced tables are small (vendors,
    regions, zones, servers, ...), so their kept keys are loaded once into sets.
    """

    def __init__(self, conn: Connection):
        self.conn = conn
        self._keys: dict[tuple[str, tuple[str, ...]], set] = {}

    def rows(self, table: Table) -> Iterator[list]:
        """Kept rows of a table, with `_columns(table)`, sorted by primary key (R12)."""
        columns = _columns(table)
        enums = {
            i: {member.name: member.value for member in table.c[c].type.enum_class}
            for i, c in enumerate(columns)
            if isinstance(table.c[c].type, SAEnum) and table.c[c].type.enum_class
        }
        json_columns = [i for i, c in enumerate(columns) if _is_json(table.c[c])]

        sql = (
            f"SELECT {', '.join(_quote(c) for c in columns)} FROM {_quote(table.name)}"
        )
        if "status" in table.c:
            statuses = ", ".join(f"'{s.name}'" for s in AVAILABLE_STATUSES)
            sql += f" WHERE status IN ({statuses})"
        sql += " ORDER BY " + ", ".join(_quote(c.name) for c in table.primary_key)

        checks = []
        for fk in table.foreign_key_constraints:
            referred = fk.referred_table
            if "status" not in referred.c:
                continue
            local = [columns.index(e.parent.name) for e in fk.elements]
            keys = self.keys(referred, tuple(e.column.name for e in fk.elements))
            checks.append((local, keys))
        resource_check = (
            self._resource_check(columns) if table is SCORE_TABLE.__table__ else None
        )

        for raw_row in self.conn.exec_driver_sql(sql):
            row = list(raw_row)
            for i, names in enums.items():
                if row[i] is not None:
                    row[i] = names.get(row[i], row[i])
            for i in json_columns:
                if row[i] is not None:
                    row[i] = RawJson(row[i])
            if all(_refers_to(row, local, keys) for local, keys in checks) and (
                resource_check is None or resource_check(row)
            ):
                yield row

    def keys(self, table: Table, columns: tuple[str, ...]) -> set:
        """Values of `columns` in the kept rows of a table, memoized."""
        cache_key = (table.name, columns)
        if cache_key not in self._keys:
            names = _columns(table)
            indexes = [names.index(c) for c in columns]
            self._keys[cache_key] = {
                tuple(row[i] for i in indexes) for row in self.rows(table)
            }
        return self._keys[cache_key]

    def _resource_check(self, columns: list[str]):
        vendor, resource_type, resource_id = (
            columns.index(c) for c in ("vendor_id", "resource_type", "resource_id")
        )
        resources = {
            ResourceType.SERVER.value: self.keys(
                Server.__table__, ("vendor_id", "server_id")
            ),
            ResourceType.DATABASE.value: self.keys(
                Database.__table__, ("vendor_id", "database_id")
            ),
        }

        def check(row) -> bool:
            keys = resources.get(row[resource_type])
            return keys is not None and (row[vendor], row[resource_id]) in keys

        return check


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _refers_to(row, local, keys) -> bool:
    key = tuple(row[i] for i in local)
    # a missing (NULL) reference doesn't point to an excluded row
    return None in key or key in keys


# ##############################################################################
# Encoding


def _to_usd(row: dict) -> dict:
    """Convert a price row to USD (R9), unrounded."""
    currency = row.get("currency")
    if currency is None or currency == "USD":
        return row

    def convert(amount):
        return (
            None
            if amount is None
            else currency_converter.convert(amount, currency, "USD")
        )

    try:
        for column in PRICE_COLUMNS:
            if column in row:
                row[column] = convert(row[column])
        if row.get("price_tiered"):
            tiers = json.loads(row["price_tiered"])
            row["price_tiered"] = RawJson(
                _dumps(
                    [{**tier, "price": convert(tier.get("price"))} for tier in tiers]
                )
            )
    except ValueError:
        logger.error(f"Cannot convert {currency} to USD, kept as is: {row}")
        return row
    row["currency"] = "USD"
    return row


def columnar(columns: list[str], rows: Iterable, usd: bool = False) -> dict[str, list]:
    """One array per column, all in the same row order (R12)."""
    doc = {column: [] for column in columns}
    for row in rows:
        if usd:
            row = list(_to_usd(dict(zip(columns, row))).values())
        for column, value in zip(columns, row):
            doc[column].append(value)
    return doc


def _dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _dumps_column(values: list) -> str:
    """A column's JSON array, with `RawJson` values written as stored."""
    if not any(isinstance(v, RawJson) for v in values):
        return _dumps(values)
    return (
        "[" + ",".join(v if isinstance(v, RawJson) else _dumps(v) for v in values) + "]"
    )


def dumps_columnar(doc: dict[str, list]) -> bytes:
    """Compact JSON of a columnar document."""
    return (
        "{" + ",".join(f"{_dumps(k)}:{_dumps_column(v)}" for k, v in doc.items()) + "}"
    ).encode()


# ##############################################################################
# Files


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    rows: int
    sha256: str


def gzip_mtime(published_at: Optional[datetime]) -> int:
    """The gzip header's modification time: the hash's publication time (R15)."""
    return int(published_at.timestamp()) if published_at is not None else 0


def encode_file(doc: dict[str, list], mtime: int) -> tuple[bytes, bytes]:
    """Return the uncompressed JSON and its gzipped form."""
    raw = dumps_columnar(doc)
    return raw, gzip.compress(raw, compresslevel=6, mtime=mtime)


def write_atomically(target: Path, data: bytes) -> None:
    """Write a file under a temporary name and rename it, replacing any existing file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".tmp-", delete=False
    ) as tmp:
        tmp.write(data)
    os.replace(tmp.name, target)


def _write(hash_dir: Path, path: str, doc: dict[str, list], mtime: int) -> SnapshotFile:
    raw, compressed = encode_file(doc, mtime)
    write_atomically(file_path(hash_dir, path), compressed)
    rows = len(next(iter(doc.values()))) if doc else 0
    return SnapshotFile(path=path, rows=rows, sha256=hashlib.sha256(raw).hexdigest())


# ##############################################################################
# Building


def build(
    conn: Connection, hash_dir: Path, published_at: Optional[datetime]
) -> list[SnapshotFile]:
    """Build every snapshot file of a database, then its manifest (R13)."""
    started = time.time()
    mtime = gzip_mtime(published_at)
    files: list[SnapshotFile] = []

    selection = Selection(conn)
    vendors = sorted(v for (v,) in selection.keys(Vendor.__table__, ("vendor_id",)))

    for model in VENDOR_TABLES:
        table = model.__table__
        columns = _columns(table)
        usd = table.name in PRICE_TABLES
        vendor_index = columns.index("vendor_id")
        written = set()
        for vendor_id, rows in groupby(
            selection.rows(table), key=lambda row: row[vendor_index]
        ):
            doc = columnar(columns, rows, usd=usd)
            files.append(_write(hash_dir, f"{table.name}/{vendor_id}", doc, mtime))
            written.add(vendor_id)
        # R5: a vendor with no rows in a table gets empty arrays
        for vendor_id in vendors:
            if vendor_id not in written:
                doc = columnar(columns, [])
                files.append(_write(hash_dir, f"{table.name}/{vendor_id}", doc, mtime))

    for model in GLOBAL_TABLES:
        table = model.__table__
        doc = columnar(_columns(table), selection.rows(table))
        files.append(_write(hash_dir, table.name, doc, mtime))

    table = SCORE_TABLE.__table__
    columns = _columns(table)
    vendor_index, benchmark_index = (
        columns.index("vendor_id"),
        columns.index("benchmark_id"),
    )
    for (vendor_id, benchmark_id), rows in groupby(
        selection.rows(table),
        key=lambda row: (row[vendor_index], row[benchmark_index]),
    ):
        doc = columnar(columns, rows)
        path = f"{table.name}/{vendor_id}/{benchmark_id}"
        files.append(_write(hash_dir, path, doc, mtime))

    files.sort(key=lambda f: f.path)
    manifest = {
        "path": [f.path for f in files],
        "rows": [f.rows for f in files],
        "sha256": [f.sha256 for f in files],
    }
    _write(hash_dir, MANIFEST_PATH, manifest, mtime)
    logger.info(
        f"Built {len(files)} snapshot files in {hash_dir} in {time.time() - started:.1f}s"
    )
    return files


def _age_reference(hash_dir: Path) -> Optional[float]:
    """When a hash's local snapshots were written: its manifest's time, or, without a
    manifest (a build or download in progress, or interrupted), its newest file's."""
    try:
        return file_path(hash_dir, MANIFEST_PATH).stat().st_mtime
    except FileNotFoundError:
        pass
    times = []
    for path in hash_dir.rglob("*"):
        try:
            if path.is_file():  # a directory's time changes with every new file
                times.append(path.stat().st_mtime)
        except FileNotFoundError:
            pass
    return max(times, default=None)


def cleanup(root: Path, keep_hash: str, max_age: int) -> None:
    """Delete the local snapshots of hashes written more than `max_age` ago, except
    `keep_hash`'s (R20 step 3).

    A hash's directory is deleted as a whole, decided by its manifest's age: deleting
    files one by one could keep a manifest whose files are already gone, which would
    still look like a complete build. The manifest goes first, so a directory being
    deleted never looks complete.
    """
    if not root.is_dir():
        return
    now = time.time()
    for hash_dir in root.iterdir():
        if not hash_dir.is_dir() or hash_dir.name == keep_hash:
            continue
        written = _age_reference(hash_dir)
        if written is not None and now - written <= max_age:
            continue
        file_path(hash_dir, MANIFEST_PATH).unlink(missing_ok=True)
        shutil.rmtree(hash_dir, ignore_errors=True)
