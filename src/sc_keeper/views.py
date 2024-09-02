from typing import List, Optional

from sc_crawler.table_bases import HasServerPK, HasVendorPKFK, ScModel
from sc_crawler.table_fields import Allocation, Status
from sc_crawler.tables import ServerPrice, is_table
from sc_crawler.insert import insert_items
from sqlmodel import Field, Session, case, func, select


from .currency import currency_converter as cc


class CurrencyBase(ScModel):
    base: str = Field(primary_key=True, description="Base currency, e.g. USD.")
    quote: str = Field(primary_key=True, description="Quote currency, e.g. HUF.")
    rate: float = Field(description="Exchange rate of base and quote currencies.")


class Currency(CurrencyBase, table=True):
    """Currency symbol pairs exchange rates."""

    @classmethod
    def insert(self, session: Session):
        currencies = cc.converter.currencies
        items = []
        for base in currencies:
            for quote in currencies:
                items.append(
                    {"base": base, "quote": quote, "rate": cc.convert(1, base, quote)}
                )
        insert_items(self, items, session=session)


class ServerPriceMinBase(HasServerPK, HasVendorPKFK):
    min_price: float
    min_spot_price: Optional[float]
    min_ondemand_price: float


class ServerPriceMin(ServerPriceMinBase, table=True):
    """Poor man's materialized view on min price of servers."""

    @staticmethod
    def query():
        return (
            select(
                ServerPrice.vendor_id,
                ServerPrice.server_id,
                func.min(ServerPrice.price).label("min_price"),
                func.min(
                    case((ServerPrice.allocation == Allocation.SPOT, ServerPrice.price))
                ).label("min_spot_price"),
                func.min(
                    case(
                        (
                            ServerPrice.allocation == Allocation.ONDEMAND,
                            ServerPrice.price,
                        )
                    )
                ).label("min_ondemand_price"),
            )
            .where(ServerPrice.status == Status.ACTIVE)
            .group_by(ServerPrice.vendor_id, ServerPrice.server_id)
            .order_by(ServerPrice.vendor_id, ServerPrice.server_id)
        )


views: List[ScModel] = [
    o for o in globals().values() if is_table(o) and o.__module__ == "sc_keeper.views"
]
