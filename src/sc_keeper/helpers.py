from datetime import timedelta

from cachier import cachier
from fastapi import HTTPException
from sc_crawler.table_bases import ServerBase
from sc_crawler.tables import Server
from sc_crawler.utils import nesteddefaultdict
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import contains_eager
from sqlmodel import Session, and_, or_, select

from .currency import currency_converter
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


def vendor_region_filter(vendor_regions, model):
    """Return an OR-filter matching any (vendor_id, region_id) pair in vendor_regions."""
    return or_(
        *[
            and_(model.vendor_id == v, model.region_id == r)
            for vr in vendor_regions
            for v, r in [vr.split("~", 1)]
        ]
    )


def update_server_price_currency(
    server_obj,
    to_currency: str = "USD",
    price_ndigits: int = 4,
    monthly_price_ndigits: int = 2,
):
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
        if hasattr(server_obj, "price_tiered") and server_obj.price_tiered:
            for tier in server_obj.price_tiered:
                tier.price = round(
                    currency_converter.convert(tier.price, from_currency, to_currency),
                    price_ndigits,
                )
        if hasattr(server_obj, "currency"):
            server_obj.currency = to_currency
    return server_obj
