from datetime import timedelta
from sys import maxsize
from typing import List

from cachier import cachier
from sc_crawler.tables import ServerPrice
from sqlmodel import Session, func, select

from .currency import CurrencyConverter

currency_converter = CurrencyConverter()


def dummy_hash(*args, **kwargs):
    return True


@cachier(stale_after=timedelta(minutes=10), hash_func=dummy_hash, backend="memory")
def min_server_prices(db: Session) -> List[dict]:
    """Generate lookup table for the lowest price (USD) of all vendors/servers."""
    query = select(
        ServerPrice.vendor_id,
        ServerPrice.server_id,
        ServerPrice.currency,
        func.min(ServerPrice.price).label("score"),
    ).group_by(ServerPrice.vendor_id, ServerPrice.server_id, ServerPrice.currency)
    prices = db.exec(query).all()
    # store in lookup dict in USD
    lookup = {}
    for price in prices:
        usdprice = price[3]
        if price[2] != "USD":
            usdprice = round(
                currency_converter.convert(price[3], price[2], "USD"),
                4,
            )
        if lookup.get((price[0], price[1]), maxsize) > usdprice:
            lookup[(price[0], price[1])] = usdprice
    return lookup


def min_server_price(db: Session, vendor_id: str, server_id: str) -> float:
    return min_server_prices(db)[(vendor_id, server_id)]
