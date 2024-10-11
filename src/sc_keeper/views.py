from typing import List, Optional

from sc_crawler.insert import insert_items
from sc_crawler.table_bases import HasServerPK, HasVendorPKFK, ScModel
from sc_crawler.table_fields import Allocation, Status
from sc_crawler.tables import BenchmarkScore, ServerPrice, is_table
from sqlmodel import Field, Session, case, func, select

from .currency import currency_converter as cc


class CurrencyBase(ScModel):
    base: str = Field(primary_key=True, description="Base currency, e.g. USD.")
    quote: str = Field(primary_key=True, description="Quote currency, e.g. HUF.")
    rate: float = Field(description="Exchange rate of base and quote currencies.")


class Currency(CurrencyBase, table=True):
    """Currency symbol pairs exchange rates."""

    @classmethod
    def insert(cls, session: Session):
        currencies = cc.converter.currencies
        items = []
        for base in currencies:
            for quote in currencies:
                items.append(
                    {
                        "base": base,
                        "quote": quote,
                        "rate": cc.convert(1, base, quote),
                    }
                )
        insert_items(cls, items, session=session)


class ServerExtraBase(HasServerPK, HasVendorPKFK):
    score: Optional[float]
    score_per_price: Optional[float]
    score1: Optional[float]
    min_price: Optional[float]
    min_price_spot: Optional[float]
    min_price_ondemand: Optional[float]


class ServerExtra(ServerExtraBase, table=True):
    """Poor man's materialized view on the SCore and min prices of servers standardized to USD."""

    @staticmethod
    def query():
        score1 = (
            select(
                BenchmarkScore.vendor_id,
                BenchmarkScore.server_id,
                BenchmarkScore.score.label("score1"),
            )
            .where(BenchmarkScore.status == Status.ACTIVE)
            .where(BenchmarkScore.benchmark_id == "stress_ng:best1")
            .subquery()
        )
        scoren = (
            select(
                BenchmarkScore.vendor_id,
                BenchmarkScore.server_id,
                BenchmarkScore.score.label("score"),
            )
            .where(BenchmarkScore.status == Status.ACTIVE)
            .where(BenchmarkScore.benchmark_id == "stress_ng:bestn")
            .subquery()
        )
        price = (
            select(
                ServerPrice.vendor_id,
                ServerPrice.server_id,
                func.round(func.min(ServerPrice.price * Currency.rate), 4).label(
                    "min_price"
                ),
                func.min(
                    case(
                        (
                            ServerPrice.allocation == Allocation.SPOT,
                            func.round(ServerPrice.price * Currency.rate, 4),
                        )
                    )
                ).label("min_price_spot"),
                func.min(
                    case(
                        (
                            ServerPrice.allocation == Allocation.ONDEMAND,
                            func.round(ServerPrice.price * Currency.rate, 4),
                        )
                    )
                ).label("min_price_ondemand"),
            )
            .where(ServerPrice.status == Status.ACTIVE)
            .join(
                BenchmarkScore,
                (ServerPrice.vendor_id == BenchmarkScore.vendor_id)
                & (ServerPrice.server_id == BenchmarkScore.server_id),
            )
            .join(
                Currency,
                (ServerPrice.currency == Currency.base) & (Currency.quote == "USD"),
            )
            .group_by(ServerPrice.vendor_id, ServerPrice.server_id)
            .order_by(ServerPrice.vendor_id, ServerPrice.server_id)
            .subquery()
        )
        query = select(
            price.c.vendor_id,
            price.c.server_id,
            scoren.c.score,
            case(
                (price.c.min_price.is_(None), None),
                else_=func.round(scoren.c.score / price.c.min_price, 4),
            ).label("score_per_price"),
            score1.c.score1,
            price.c.min_price,
            price.c.min_price_spot,
            price.c.min_price_ondemand,
        ).select_from(
            price.outerjoin(
                score1,
                (price.c.vendor_id == score1.c.vendor_id)
                & (price.c.server_id == score1.c.server_id),
            ).outerjoin(
                scoren,
                (price.c.vendor_id == scoren.c.vendor_id)
                & (price.c.server_id == scoren.c.server_id),
            )
        )

        return query


views: List[ScModel] = [
    o for o in globals().values() if is_table(o) and o.__module__ == "sc_keeper.views"
]
