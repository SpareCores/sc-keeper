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


def calculate_tiered_price(
    price_tiers: list[PriceTier],
    usage: float,
    fallback_unit_price: float | None = None,
    round_digits: int = 4,
) -> float | None:
    """
    Calculate price from tiered pricing structure based on usage.

    Generic function that works for any unit, e.g. to compute the monthly price
    of a server or the price of x amount of traffic.

    Args:
        price_tiers: List of [PriceTier][sc_crawler.table_fields.PriceTier]
            objects with lower/upper bounds and unit prices. Can be empty list if no tiers are available.
        usage: Amount of usage (e.g., 730 hours/month, 1000 GB traffic).
        fallback_unit_price: Unit price to use if tiered pricing is empty or None.
            Will be multiplied by usage to get total price.
        round_digits: Number of decimal places to round the result to (default: 4).

    Returns:
        Calculated total price or None if no pricing is available
    """
    if not price_tiers:
        if fallback_unit_price is not None:
            return round(fallback_unit_price * usage, round_digits)
        else:
            return None

    total_cost = 0.0
    usage_remaining = usage

    sorted_tiers = sorted(price_tiers, key=lambda x: float(x.lower))
    for tier in sorted_tiers:
        if usage_remaining <= 0:
            break
        tier_usage = min(usage_remaining, float(tier.upper) - float(tier.lower))
        total_cost += tier_usage * float(tier.price)
        usage_remaining -= tier_usage

    return round(total_cost, round_digits)
