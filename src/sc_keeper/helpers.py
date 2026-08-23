from datetime import timedelta
from json import JSONDecodeError
from json import loads as json_loads

from cachier import cachier
from fastapi import HTTPException
from sc_crawler.table_bases import DatabaseBase, ServerBase
from sc_crawler.table_fields import Status
from sc_crawler.tables import Database, Server
from sc_crawler.utils import nesteddefaultdict
from sqlalchemy.exc import NoResultFound
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import contains_eager
from sqlmodel import Session, and_, or_, select

from .currency import currency_converter
from .database import get_db
from .references import DatabasePKs, ServerPKs

_PRICE_NDIGITS = 4
_MONTHLY_PRICE_NDIGITS = 2

def status_filter(status_column, only_active: bool | None, only_orderable: bool | None):
    """Return a SQLAlchemy status filter, or None if neither flag applies."""
    if only_active:
        return status_column == Status.ACTIVE
    if only_orderable:
        return status_column.in_([s for s in Status if s.is_orderable])
    return None


@cachier(stale_after=timedelta(minutes=10), backend="memory")
def get_server_dicts():
    with next(get_db()) as db:
        server_rows = db.exec(select(Server)).all()
    servers = nesteddefaultdict()
    for server_row in server_rows:
        serverobj = server_row.model_dump()
        servers[server_row.vendor_id][server_row.server_id] = serverobj
        servers[server_row.vendor_id][server_row.api_reference] = serverobj
    return servers


def get_server_dict(vendor: str, server: str):
    serverobj = get_server_dicts()[vendor][server]
    if serverobj:
        return serverobj
    raise HTTPException(status_code=404, detail="Server not found")


def get_server_base(vendor_id: str, server_id: str, db: Session) -> ServerBase:
    try:
        return db.exec(
            select(Server)
            .where(Server.vendor_id == vendor_id)
            .where(Server.server_id == server_id)
        ).one()
    except NoResultFound as e:
        raise HTTPException(status_code=404, detail="Server not found") from e


def get_server_pks(vendor: str, server: str, db: Session) -> ServerPKs:
    try:
        return db.exec(
            select(Server)
            .where(Server.vendor_id == vendor)
            .where((Server.server_id == server) | (Server.api_reference == server))
            .join(Server.vendor)
            .options(contains_eager(Server.vendor))
        ).one()
    except NoResultFound as e:
        raise HTTPException(status_code=404, detail="Server not found") from e


@cachier(stale_after=timedelta(minutes=10), backend="memory")
def get_database_dicts():
    with next(get_db()) as db:
        database_rows = db.exec(select(Database)).all()
    databases = nesteddefaultdict()
    for database_row in database_rows:
        databaseobj = database_row.model_dump()
        databases[database_row.vendor_id][database_row.database_id] = databaseobj
        databases[database_row.vendor_id][database_row.api_reference] = databaseobj
    return databases


def get_database_dict(vendor: str, database: str):
    databaseobj = get_database_dicts()[vendor][database]
    if databaseobj:
        return databaseobj
    raise HTTPException(status_code=404, detail="Database not found")


def get_database_base(vendor_id: str, database_id: str, db: Session) -> DatabaseBase:
    try:
        return db.exec(
            select(Database)
            .where(Database.vendor_id == vendor_id)
            .where(Database.database_id == database_id)
        ).one()
    except NoResultFound as e:
        raise HTTPException(status_code=404, detail="Database not found") from e


def get_database_pks(vendor: str, database: str, db: Session) -> DatabasePKs:
    try:
        return db.exec(
            select(Database)
            .where(Database.vendor_id == vendor)
            .where(
                (Database.database_id == database)
                | (Database.api_reference == database)
            )
            .join(Database.vendor)
            .options(contains_eager(Database.vendor))
        ).one()
    except NoResultFound as e:
        raise HTTPException(status_code=404, detail="Database not found") from e


def mapped_class_has_column(mapped_entity, column_key: str) -> bool:
    """True if ``column_key`` is a persisted mapper column, not a relationship or plain descriptor."""
    insp = sa_inspect(mapped_entity, raiseerr=False)
    if insp is None:
        return False
    mapper = getattr(insp, "mapper", None)
    if mapper is None:
        return False
    return column_key in mapper.columns


def vendor_region_filter(vendor_regions, model):
    """Return an OR-filter matching any (vendor_id, region_id) pair in vendor_regions."""
    return or_(
        *[
            and_(model.vendor_id == v, model.region_id == r)
            for vr in vendor_regions
            for v, r in [vr.split("~", 1)]
        ]
    )


def add_extra_to_price(base_price, extra_price, ndigits):
    return round(base_price + extra_price, ndigits) if base_price is not None else None


def update_server_price_currency(
    server_obj,
    to_currency: str = "USD",
    price_ndigits: int = _PRICE_NDIGITS,
    monthly_price_ndigits: int = _MONTHLY_PRICE_NDIGITS,
):
    """In-place conversion of server price attributes to the target currency.

    Args:
        server_obj: The server object to update, e.g. ServerBase or ServerPKs.
        to_currency: The target currency code, default is USD.
        price_ndigits: The number of decimal places to round the price to, default is 4.
        monthly_price_ndigits: The number of decimal places to round the monthly price to, default is 2.
    """
    from_currency = getattr(server_obj, "currency", "USD")
    if from_currency != to_currency:
        for attr, ndigits in [
            ("price", price_ndigits),
            ("price_monthly", monthly_price_ndigits),
            ("min_price", price_ndigits),
            ("min_price_spot", price_ndigits),
            ("min_price_ondemand", price_ndigits),
            ("min_price_ondemand_monthly", monthly_price_ndigits),
        ]:
            value = getattr(server_obj, attr, None)
            if value:
                setattr(
                    server_obj,
                    attr,
                    round(
                        currency_converter.convert(value, from_currency, to_currency),
                        ndigits,
                    ),
                )
        if hasattr(server_obj, "price_breakdown") and server_obj.price_breakdown:
            for attr, ndigits in [
                ("compute_min_price", price_ndigits),
                ("compute_min_price_spot", price_ndigits),
                ("compute_min_price_ondemand", price_ndigits),
                ("compute_min_price_ondemand_monthly", monthly_price_ndigits),
                ("traffic_inbound_hourly", price_ndigits),
                ("traffic_inbound_monthly", monthly_price_ndigits),
                ("traffic_outbound_hourly", price_ndigits),
                ("traffic_outbound_monthly", monthly_price_ndigits),
                ("traffic_hourly", price_ndigits),
                ("traffic_monthly", monthly_price_ndigits),
                ("extra_storage_hourly", price_ndigits),
                ("extra_storage_monthly", monthly_price_ndigits),
            ]:
                value = getattr(server_obj.price_breakdown, attr, None)
                if value:
                    setattr(
                        server_obj.price_breakdown,
                        attr,
                        round(
                            currency_converter.convert(
                                value, from_currency, to_currency
                            ),
                            ndigits,
                        ),
                    )
        if hasattr(server_obj, "price_tiered") and server_obj.price_tiered:
            for tier in server_obj.price_tiered:
                tier.price = round(
                    currency_converter.convert(tier.price, from_currency, to_currency),
                    price_ndigits,
                )
        if hasattr(server_obj, "currency"):
            server_obj.currency = to_currency
    return server_obj


def update_database_price_currency(
    database_obj,
    to_currency: str = "USD",
    price_ndigits: int = _PRICE_NDIGITS,
    monthly_price_ndigits: int = _MONTHLY_PRICE_NDIGITS,
):
    """In-place conversion of database price attributes to the target currency."""
    from_currency = getattr(database_obj, "currency", "USD")
    if from_currency != to_currency:
        for attr, ndigits in [
            ("price", price_ndigits),
            ("price_monthly", monthly_price_ndigits),
            ("min_price", price_ndigits),
            ("min_price_ondemand", price_ndigits),
            ("min_price_ondemand_monthly", monthly_price_ndigits),
        ]:
            value = getattr(database_obj, attr, None)
            if value:
                setattr(
                    database_obj,
                    attr,
                    round(
                        currency_converter.convert(value, from_currency, to_currency),
                        ndigits,
                    ),
                )
        if hasattr(database_obj, "price_breakdown") and database_obj.price_breakdown:
            for attr, ndigits in [
                ("compute_min_price", price_ndigits),
                ("compute_min_price_ondemand", price_ndigits),
                ("compute_min_price_ondemand_monthly", monthly_price_ndigits),
                ("extra_storage_hourly", price_ndigits),
                ("extra_storage_monthly", monthly_price_ndigits),
            ]:
                value = getattr(database_obj.price_breakdown, attr, None)
                if value:
                    setattr(
                        database_obj.price_breakdown,
                        attr,
                        round(
                            currency_converter.convert(
                                value, from_currency, to_currency
                            ),
                            ndigits,
                        ),
                    )
        if hasattr(database_obj, "currency"):
            database_obj.currency = to_currency
    return database_obj


def get_sort_key_for_benchmark_configs(item):
    """Helper function to determine the sort order for benchmark configs"""

    category_order = [
        "stress-ng",
        "Geekbench",
        "Passmark",
        "Memory bandwidth",
        "OpenSSL",
        "Compression algos",
        "Static web server",
        "Redis",
        "LLM inference speed",
        "Database",
        "Other",
    ]
    sub_category_order = [
        "geekbench:score",
        "passmark:cpu_mark",
        "passmark:memory_mark",
        "llm_speed:prompt_processing",
        "llm_speed:text_generation",
        "membench:latency",
        "pgbench:heavy_read_only",
    ]
    model_order = [
        "SmolLM-135M.Q4_K_M.gguf",
        "qwen1_5-0_5b-chat-q4_k_m.gguf",
        "gemma-2b.Q4_K_M.gguf",
        "llama-7b.Q4_K_M.gguf",
        "phi-4-q4.gguf",
        "Llama-3.3-70B-Instruct-Q4_K_M.gguf",
    ]

    config = item.get("config") or {}
    if isinstance(config, str):
        try:
            parsed = json_loads(config)
            config = parsed if isinstance(parsed, dict) else {}
        except JSONDecodeError:
            config = {}
    elif not isinstance(config, dict):
        config = {}

    environment = item.get("environment")
    if not isinstance(environment, dict):
        environment = None

    # primary sort by category
    category = item.get("category") or "Other"
    category_idx = (
        category_order.index(category)
        if category in category_order
        else len(category_order)
    )

    # secondary sort by benchmark_id
    if item["benchmark_id"] in sub_category_order:
        subcategory_idx = sub_category_order.index(item["benchmark_id"])
    else:
        subcategory_idx = len(sub_category_order)

    # then sort by cores (single-core first)
    cores_idx = (
        0
        if config.get("cores", "") in ["single", "Single-Core Performance", 1, 1.0]
        else 1
    )

    # then sort by LLM model (if present)
    model_idx = len(model_order)
    if "model" in config and config["model"] in model_order:
        model_idx = model_order.index(config["model"])

    # then sort by concurrency (single-core first) - pgbench only
    concurrency_idx = 0 if config.get("concurrency", "") == "single" else 1

    # then sort by database engine version - pgbench only
    database_engine_version_idx = (float("inf"),)
    if environment:
        raw_version = environment.get("database_engine_version")
        if raw_version is not None and raw_version != "":
            try:
                database_engine_version_idx = tuple(
                    int(part) for part in str(raw_version).split(".")
                )
            except (ValueError, TypeError):
                pass

    # then sort by tokens (if present)
    tokens = 0
    if "tokens" in config:
        try:
            tokens = int(config["tokens"])
        except (ValueError, TypeError):
            pass

    # then sort by algo (if present)
    algo = config.get("algo", "")

    # then sort by int type fields
    int_type_field = 0
    for key in ["size", "size_kb", "block_size", "threads"]:
        if key in config:
            try:
                int_type_field = int(config[key])
            except (ValueError, TypeError):
                pass

    # finally, sort by original order
    return (
        category_idx,
        subcategory_idx,
        cores_idx,
        model_idx,
        concurrency_idx,
        database_engine_version_idx,
        tokens,
        algo,
        int_type_field,
        item.get("original_order", 0),
    )
