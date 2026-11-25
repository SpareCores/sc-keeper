# sc-keeper Developer Guide

A reference for AI-assisted and human development. Read this before making changes.

## Project Overview

`sc-keeper` is a **FastAPI** application that exposes the [Spare Cores](https://sparecores.com) Navigator dataset of cloud compute resources (servers, managed databases, prices, benchmarks) as an HTTP API. It wraps a SQLite database (provided by `sparecores-data` / `sc-data`) using **SQLModel** (SQLAlchemy + Pydantic) for queries and response validation.

The database schema and table definitions live in the upstream `sparecores-crawler` (`sc_crawler`) package. This repo does **not** define the core tables — it queries them, extends them, and exposes them.

## Repository Layout

```
src/sc_keeper/
├── api.py               # FastAPI app, middleware wiring, and the search endpoints (/servers, /databases, ...)
├── api_description.md   # Top-level API description shown in the OpenAPI docs
├── routers/
│   ├── __init__.py      # Re-exports all router modules
│   ├── administrative.py  # /healthcheck, /ping, /me, /stats, /debug, /benchmark_score_stats
│   ├── server.py        # /server/{vendor}/{server}/... details, prices, benchmarks, similar servers
│   ├── database.py      # /database/{vendor}/{database}/... details, prices, benchmarks
│   ├── inspector.py     # .../task_block_reason_codes for servers and databases
│   ├── snapshot.py      # /snapshot/{hash}/... table snapshots pinned to a database hash (auth required)
│   ├── tables.py        # /table/* raw table dumps
│   ├── table_metadata.py
│   └── ai.py            # /ai/assist_* filter helpers
├── references.py        # Pydantic response models + startup-time enums from DB values
├── parameters.py        # Annotated FastAPI Query/Path parameter types (shared across routers)
├── validators.py        # Request validation helpers (currency, filter limits)
├── database.py          # Database thread, engine management, get_db() dependency
├── data_version.py      # SC-Data-Hash / SC-Data-Published-At response headers
├── views.py             # SQLModel "materialized views" (ServerExtra, DatabaseExtra, Currency)
├── crawler_extend.py    # TableExtender base class + ServerPriceExtender / DatabasePriceExtender
├── indexes.py           # Extra SQLite indexes created at startup
├── queries.py           # Reusable SQLModel query fragments (e.g. gen_benchmark_query)
├── helpers.py           # Utilities (status_filter, not_archived, get_server_pks, vendor_region_filter, ...)
├── snapshots.py         # Building snapshot files for a database hash
├── snapshot_storage.py  # Snapshot storage: local dir, shared S3 bucket, or on-the-fly build
├── auth.py              # Auth (token introspection, JWT, API keys, static tokens), User model, middleware
├── cache.py             # CacheHeaderMiddleware (sets Cache-Control headers)
├── rate_limit.py        # Credit-based rate limiting middleware (in-memory or Redis)
├── limits.py            # Concurrency limit for heavy endpoints (heavy_job_dep)
├── openapi_extras.py    # Additions to the generated OpenAPI spec (e.g. enum member docs)
├── currency.py          # Currency conversion via currencyconverter
├── ai.py                # AI assist logic
├── logger.py            # Logging middleware
├── redis_client.py      # Redis connection helper
└── sentry.py            # Sentry integration hook
```

## Key Dependencies

| Package | Role |
|---|---|
| `sparecores-crawler` (`sc_crawler`) | All table definitions and enums (`Status`, `Allocation`, ...) |
| `sparecores-data` (`sc_data`) | Provides the SQLite database file and its hash/path |
| `fastapi` | HTTP framework |
| `sqlmodel` | ORM layer (SQLAlchemy + Pydantic); used for all queries and model validation |
| `uvicorn` | ASGI server |

## Database Architecture

The database is **SQLite**, managed by a background `Database` thread (`database.py`):

1. At startup, it copies `sc_data`'s SQLite file to a temp file.
2. Runs `TableExtender.add_columns()` / `update()` for each extender (adds computed columns, e.g. `price_monthly`).
3. Creates extra indexes (`indexes.py`).
4. Creates and populates "materialized view" tables (`views.py`).
5. Checks every 60 seconds whether `sc_data`'s hash changed, and switches to the new file if so.

A request keeps using the database version it started on, so its data and its `SC-Data-Hash` header stay in sync (see `get_db()` and `data_version.py`).

**Never write to the database from an endpoint.** All writes happen at startup/refresh time through extenders and views.

Use `db: Session = Depends(get_db)` to get a read-only session in any endpoint.

## Database Schema

Table definitions are in `sc_crawler.tables`, enums in `sc_crawler.table_fields`.
**Do not guess field names** — the schema changes between crawler releases (e.g.
`gpu_*` became `accelerator_*`, `network_speed` became `network_speed_baseline`).
Check the installed version instead:

```bash
python -c "from sc_crawler.tables import Server; print(list(Server.model_fields))"
python -c "import sc_crawler.tables as t; print([n for n in dir(t) if n[0].isupper()])"
python -c "from sc_crawler.table_fields import Status; print(list(Status))"
```

Field descriptions: https://docs.sparecores.com/crawler/reference/tables (append `.md`
for a Markdown version, e.g. https://docs.sparecores.com/crawler/reference/tables.md) —
visual ERD: https://dbdocs.io/spare-cores/sc-crawler

The main resource types are **servers** (`Server`, `ServerPrice`) and **managed
databases** (`Database`, `DatabasePrice`, `DatabaseStorage`, `DatabaseStoragePrice`).
Other tables: `Country`, `Vendor`, `Region`, `Zone`, `Storage`, `StoragePrice`,
`TrafficPrice`, `Ipv4Price`, `Benchmark`, `BenchmarkScore`, `ComplianceFramework`,
`VendorComplianceLink`, `ServerDescription`.

### Gotchas

- **`BenchmarkScore` is keyed by `resource_type` + `resource_id`**, as it covers both
  servers and databases. `BenchmarkScore.server_id` still works as a hybrid property.
- `BenchmarkScore.config` is a JSON column; to compare it as a string use
  `.cast(String)`, e.g. `BenchmarkScore.config.cast(String) == '{"bandwidth": 4096}'`.
- `BenchmarkScore.score` can be NULL; filter `.isnot(None)` when computing statistics.
- Benchmark IDs follow `family:variant` (e.g. `stress_ng:cpu_all`). The canonical
  "SCore" benchmarks are `stress_ng:bestn` (multi-core) and `stress_ng:best1`
  (single-core). To extract the family in SQL, see the `benchmark_family` case
  expression in `routers/administrative.py`.
- Prices are stored in their source `currency`. Use the `Currency` view to convert to USD.

### Status

Every table has `status` and `observed_at` columns. `Status` values: `ACTIVE`,
`INACTIVE`, `PLANNED_FOR_RETIREMENT`, `RETIRED`, `ARCHIVED`.

- **`ARCHIVED` records are never returned by any endpoint** (replaced or deliberately
  dropped records). Every query must leave them out: use `helpers.not_archived()`.
- For the `only_active` / `only_orderable` query parameters use `helpers.status_filter()`.
  Orderable statuses are the ones with `Status.is_orderable` (`ACTIVE`,
  `PLANNED_FOR_RETIREMENT`).
- Price and score aggregates (views, extenders) use `Status.ACTIVE` only.
- Use the ORM for status filters (`Table.status == Status.ACTIVE`). In raw SQL
  (`text(...)`), bind **`Status.ACTIVE.name`** (`'ACTIVE'`), not `.value`
  (`'active'`) — the DB stores the enum name.

### Table sizes

Most tables are small (at most a few thousand rows, e.g. `Server` ~7k) and can be
loaded fully. Two are large — **never call `.all()` on them without a restrictive
`WHERE` clause**, and push aggregation into SQL:

- `ServerPrice` (~650k rows)
- `BenchmarkScore` (~3M rows)

`DatabasePrice` (~60k rows) also deserves a filter.

### Materialized views and extended columns (sc_keeper-specific)

Built at startup (`views.py`) and stored alongside the crawler tables:

- **`ServerExtra`** (`vendor_id`, `server_id`): `score` (`stress_ng:bestn`),
  `score1` (`stress_ng:best1`), `score_per_price`, and USD-normalised `min_price`,
  `min_price_spot`, `min_price_ondemand` (hourly) and `min_price_ondemand_monthly`.
- **`DatabaseExtra`** (`vendor_id`, `database_id`): USD-normalised `min_price`,
  `min_price_ondemand`, `min_price_ondemand_monthly`.
- **`Currency`** (`base`, `quote`, `rate`): exchange rates for all currency pairs.

Join the `*Extra` views to their resource table with an **outer join** — not all
servers/databases have prices or scores. Prefer them over re-aggregating
`ServerPrice` / `DatabasePrice` unless you need region-level granularity.

Columns added by `crawler_extend.py` (queryable, but not in `model_dump`):

- `ServerPrice.price_monthly` and `DatabasePrice.price_monthly`: monthly price for
  `ONDEMAND` allocation (730 hours, tiered-aware) in the **source currency**, not USD.

---

## Query Performance Guidelines

- **Prefer aggregates in SQL over Python loops** for heavy tables: use `func.count()`, `func.min()`, `func.max()`, `func.sum()` directly in the query.
- **Prefer two bulk queries + Python grouping over N+1** for multi-group stats: fetch all relevant rows in one query, then `defaultdict`-group in Python. See `get_benchmark_score_stats()` in `routers/administrative.py` for a worked example.
- Extra indexes are defined in `indexes.py`; check it before adding a new frequent filter combination on a heavy table, and add an index if none covers it.
- Endpoints that scan heavy tables should depend on `limits.heavy_job_dep`, which caps how many run concurrently (`HEAVY_JOBS_MAX_CONCURRENT`):
  `@router.get("/...", dependencies=[Depends(heavy_job_dep)])`.

---

## How to Add a New API Endpoint

### 1. Decide which router file to use

| Router | Use for |
|---|---|
| `routers/administrative.py` | Internal stats, debug info, healthcheck, computed aggregates |
| `routers/server.py` | Server details (path params: vendor + server) |
| `routers/database.py` | Database details (path params: vendor + database) |
| `routers/tables.py` | Raw table dumps with optional filtering |
| `api.py` | Major search endpoints (`/servers`, `/databases`, `/regions`, ...) |

### 2. Define the response model

Add Pydantic models to **`references.py`** following the existing pattern:

```python
from pydantic import BaseModel, Field
from typing import List, Optional


class MyNewResponse(BaseModel):
    """One-line description shown in API docs."""

    field_a: str = Field(description="...")
    field_b: Optional[int] = Field(default=None, description="...")


MyNewResponse.model_config["json_schema_extra"] = {
    "examples": [{"field_a": "example", "field_b": 42}]
}
```

Use `Optional[X]` with `default=None` for nullable fields. Always add a `json_schema_extra` example for the OpenAPI docs.

If the response uses an existing `sc_crawler` model directly (e.g. `List[Benchmark]`), you can skip adding to `references.py`.

### 3. Write the endpoint function

```python
from typing import List
from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from sc_crawler.tables import SomeTable
from ..database import get_db
from ..helpers import not_archived
from ..references import MyNewResponse

router = APIRouter()


@router.get("/my_endpoint")
def my_endpoint(
    db: Session = Depends(get_db),
) -> List[MyNewResponse]:
    """Docstring becomes the endpoint description in OpenAPI docs."""
    rows = db.exec(select(SomeTable).where(not_archived(SomeTable.status))).all()
    return [MyNewResponse(field_a=r.some_field) for r in rows]
```

### 4. Common query patterns

**Aggregate with group-by (preferred over Python loops for heavy tables):**
```python
select(
    BenchmarkScore.benchmark_id,
    func.count().label("n"),
    func.min(BenchmarkScore.score).label("min_score"),
    func.max(BenchmarkScore.score).label("max_score"),
).where(
    BenchmarkScore.status == Status.ACTIVE,
    BenchmarkScore.score.isnot(None),
).group_by(BenchmarkScore.benchmark_id)
```

**Join to the ServerExtra materialized view:**
```python
select(Server, ServerExtra.min_price).join(
    ServerExtra,
    (Server.vendor_id == ServerExtra.vendor_id)
    & (Server.server_id == ServerExtra.server_id),
    isouter=True,  # outer join: not all servers have prices
)
```

**Null checks:** Use `.isnot(None)` not `!= None` (SQLAlchemy column method).

### 5. Register the router (only if it's a new file)

In `routers/__init__.py`, add an import. Then in `api.py`:

```python
app.include_router(routers.my_new_router.router, tags=["My Tag"])
```

Existing routers are already registered — just add endpoints to them.

### 6. Update the changelog

Document the change in `CHANGELOG.md`, following `.agents/changelog.md`.

---

## Shared Query Parameters

Reusable `Annotated` parameter types live in **`parameters.py`**. Import and use them as default values:

```python
from .. import parameters as options


@router.get("/my_endpoint")
def my_endpoint(
    vendor: options.vendor = None,  # Optional[List[Vendors]]
    only_active: options.only_active = True,  # Optional[bool]
    db: Session = Depends(get_db),
): ...
```

To add a new shared parameter, define an `Annotated` type in `parameters.py` following the existing style (include `title`, `description`, and `json_schema_extra` with `category_id`).

---

## Adding a New Pydantic Enum for Filter Options

Enums used as filter option types are built from live DB values at import time in `references.py`:

```python
with session.sessionmaker as db:
    MyEnum = StrEnum(
        "MyEnum",
        {m.my_id: m.my_id for m in db.exec(select(MyTable)).all()},
    )
```

Then expose it in `parameters.py` as an `Annotated` query parameter with `"enum": [m.value for m in MyEnum]` in `json_schema_extra`. Only do this for lightweight tables.

---

## Extending Tables with New Columns

To add a computed column to an existing `sc_crawler` table (queryable, but not visible via `model_dump`):

1. Create a subclass of `TableExtender` in `crawler_extend.py`.
2. Define `table` (the SQLModel class) and `new_columns` (list of `NewColumn`).
3. Implement `update(engine)` to populate the column via raw SQL.

The extender will automatically be picked up by the `extenders` list at the bottom of `crawler_extend.py`. See `ServerPriceExtender` as the reference implementation.

---

## Snapshots

`/snapshot/{hash}/...` serves every table pinned to a database hash as gzipped
columnar JSON (API key required). Only available records are included
(`ACTIVE` and `PLANNED_FOR_RETIREMENT`), with prices in USD. Snapshots are built once
per hash into `SNAPSHOT_DIR` and shared between instances through S3
(`SNAPSHOT_S3_URI`). See the module docstrings of `snapshots.py` and
`snapshot_storage.py` for the file layout and lifecycle.

---

## Authentication

Most endpoints are **unauthenticated**. To require auth:

```python
from ..auth import User, current_user
from fastapi import Security


@router.get("/protected")
def protected(user: User = Security(current_user)) -> ...: ...
```

This also causes `CacheHeaderMiddleware` to set `Cache-Control: private, no-store` automatically.

Auth backends are configured by the `AUTH_*` environment variables (see below); see `auth.py` for details.

---

## Response Caching

`CacheHeaderMiddleware` (`cache.py`) sets the CDN/proxy cache headers. There is no
per-endpoint cache annotation — adjust the middleware if a new endpoint needs a
different TTL.

- Default TTL: **1 hour**; paths containing both `"server"` and `"prices"`: **15 minutes**.
- Responses also allow stale serving (`stale-while-revalidate`, `stale-if-error`),
  with a longer stale window for `/benchmark_score_stats` and `/debug`.
- No caching (`private, no-store`) for authenticated requests, `/healthcheck`, `/ping`,
  `/ai/assist*`, and error responses.

---

## Rate Limiting

Credit costs are configured in `rate_limit.py` via `CUSTOM_RATE_LIMIT_COSTS`, matched by
path prefix. Routes not listed cost `RATE_LIMIT_DEFAULT_CREDIT_COST` (1). Add an entry
if a new endpoint is expensive (e.g. scans `ServerPrice` or `BenchmarkScore`):

```python
CUSTOM_RATE_LIMIT_COSTS: dict[str, int] = {
    "/servers": 3,
    "/databases": 3,
    "/server_prices": 5,
    "/table/server_prices": 10,
    "/table/database_price": 10,
    "/benchmark_score_stats": 10,
    "/my_expensive_endpoint": 4,  # add here
}
```

---

## Running Locally

```bash
# install in editable mode
pip install -e ".[all]"

# minimal run (uses bundled sc-data)
uvicorn sc_keeper.api:app --reload

# with debug SQL logging and a specific DB file
KEEPER_DEBUG=True SC_DATA_NO_UPDATE=TRUE SC_DATA_DB_PATH=/path/to/sc-data-all.db \
    uvicorn sc_keeper.api:app --reload
```

Environment variables (all optional unless noted; never commit real values):

| Variable | Purpose |
|---|---|
| `KEEPER_DEBUG` | Enable SQLAlchemy query echo |
| `SC_DATA_DB_PATH` | Override path to the SQLite database file (disables snapshots, as there is no hash) |
| `SC_DATA_NO_UPDATE` | Disable background DB refresh (useful in dev) |
| `OPENAI_API_KEY` | Required for `/ai/*` endpoints |
| `RATE_LIMIT_ENABLED`, `RATE_LIMIT_BACKEND`, `RATE_LIMIT_CREDITS_PER_MINUTE`, `RATE_LIMIT_DEFAULT_CREDIT_COST` | Rate limiting |
| `REDIS_URL` | Redis for the rate limiter and the auth token cache |
| `HEAVY_JOBS_MAX_CONCURRENT`, `HEAVY_JOBS_ACQUIRE_TIMEOUT_SEC` | Concurrency limit of heavy endpoints |
| `AUTH_TOKEN_INTROSPECTION_URL` (+ `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`) | OAuth 2.0 token introspection |
| `AUTH_JWT_JWKS_URL` or `AUTH_JWT_PUBLIC_KEY` | JWT verification |
| `AUTH_API_KEY_VERIFY_URL`, `AUTH_API_KEY_VERIFY_BEARER`, ... | API key verification against an external service |
| `AUTH_STATIC_TOKENS` | JSON list of static tokens |
| `AUTH_TOKEN_VALIDATION_CEL`, `AUTH_TOKEN_EXTRA_FIELDS_CEL` | CEL rules for validating tokens and extracting user fields |
| `SNAPSHOT_DIR`, `SNAPSHOT_S3_URI`, `SNAPSHOT_LOCAL_RETENTION_SECONDS`, `SNAPSHOT_REQUEST_WAIT_SECONDS` | Snapshot storage |
| `SENTRY_DSN` | Enable Sentry |

For the full list: `grep -rhoE 'environ(\.get)?\(\s*"[A-Z_]+"' src/`, and see
`auth.py` for all `AUTH_*` options.

Swagger UI: http://localhost:8000/docs  
ReDoc: http://localhost:8000/redoc

---

## Versioning and Changelog

The API is deployed on every push to `main` and is not versioned semantically.

- Every user-facing change must be documented in `CHANGELOG.md` in the same PR.
- Read `.agents/changelog.md` before editing `CHANGELOG.md`.
- The package version is **not bumped manually**. `pyproject.toml` holds the fixed
  placeholder `2026.0+dev` (never changed); the Docker build workflow sets it to
  `<year>.<month>+g<short sha>` (e.g. `2026.10+g5e02c8c`, month from the commit date,
  not zero-padded) via the `KEEPER_VERSION` build-arg. It shows up in the OpenAPI docs
  and `/healthcheck`.
- The keeper version is independent of `sparecores-crawler`; bump the crawler pin in
  `pyproject.toml` when needed.
- Sentry releases use the full commit SHA (`SENTRY_RELEASE`), unrelated to the package
  version.

---

## Testing

Tests use `pytest` with FastAPI's `TestClient`:

```bash
# run all tests (needs the "testing" and "security" extras)
pytest src/ tests/ --doctest-modules -v

# via tox (Python 3.12 and 3.13)
tox

# linting only
tox -e lint
# or directly
ruff check .
ruff check --select I .
ruff format --check .
```

Test files live in `tests/`; shared fixtures (e.g. apps with auth enabled) are in
`tests/conftest.py`. The main pattern is:

```python
from fastapi.testclient import TestClient
from sc_keeper.api import app

client = TestClient(app)


def test_my_endpoint():
    response = client.get("/my_endpoint")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data[0]["field_a"]
```

Parametrize with `@pytest.mark.parametrize` for filter combinations (see `test_api.py`).

---

## Code Style

- **Formatter/linter:** `ruff` (Black-compatible, 88-char lines, double quotes).
- **Imports:** stdlib → third-party → local, each group alphabetically sorted. `ruff check --select I` enforces this.
- All endpoint functions must have a **docstring** — it becomes the description in OpenAPI docs.
- Use `func.count()`, `func.min()`, etc. from `sqlmodel` (re-exported from SQLAlchemy).
- Prefer `select(...).where(...)` over ORM-style `session.query(...)`.
- Type-annotate return types on all endpoint functions — FastAPI uses them for response serialisation and schema generation.
