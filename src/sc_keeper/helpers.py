from datetime import timedelta
from json import loads as json_loads

from cachier import cachier
from fastapi import HTTPException
from sc_crawler.table_bases import ServerBase
from sc_crawler.table_fields import PriceTier
from sc_crawler.tables import Server
from sc_crawler.utils import nesteddefaultdict
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import contains_eager
from sqlmodel import Session, select

from .database import get_db
from .references import ServerPKs


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


def parse_price_tiers(price_tiers_json: str | None) -> list[PriceTier]:
    """
    Parse JSON string of price tiers into PriceTier objects.

    Converts "Infinity" strings to float('inf') for upper bounds.

    Args:
        price_tiers_json: JSON string representation of price tiers from database

    Returns:
        List of PriceTier objects, or empty list if parsing fails or input is None/empty
    """
    if not price_tiers_json:
        return []

    try:
        tier_dicts = json_loads(price_tiers_json)
        if not tier_dicts or not isinstance(tier_dicts, list):
            return []

        price_tiers = []
        for tier in tier_dicts:
            if tier.get("upper") == "Infinity":
                tier["upper"] = float("inf")
            price_tiers.append(PriceTier(**tier))

        return price_tiers
    except Exception:
        return []


def calculate_tiered_monthly_price(
    price_tiers: list[PriceTier],
    fallback_hourly_price: float | None,
    hours_per_month: float = 730.0,
) -> float | None:
    """
    Calculate monthly price from tiered pricing structure.

    Args:
        price_tiers: List of [PriceTier][sc_crawler.table_fields.PriceTier] objects with lower/upper bounds and prices.
            Can be empty list if no tiers are available.
        fallback_hourly_price: Hourly price to use if tiered pricing is empty or None.
            Will be multiplied by hours_per_month to get monthly price.
        hours_per_month: Number of hours in a month (default: 730)

    Returns:
        Calculated monthly price or None if no pricing is available
    """
    if not price_tiers:
        if fallback_hourly_price:
            return fallback_hourly_price * hours_per_month
        else:
            return None

    total_cost = 0.0
    hours_remaining = hours_per_month

    sorted_tiers = sorted(price_tiers, key=lambda x: float(x.lower))
    for tier in sorted_tiers:
        if hours_remaining <= 0:
            break
        tier_hours = min(hours_remaining, tier.upper - tier.lower)
        total_cost += tier_hours * tier.price
        hours_remaining -= tier_hours

    return round(total_cost, 2)
