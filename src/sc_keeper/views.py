from typing import Optional

from sc_crawler.table_bases import (
    HasServerPK,
    HasVendorPKFK,
)
from sc_crawler.table_fields import Allocation, Status
from sc_crawler.tables import (
    ServerPrice,
)
from sqlmodel import case, func, select


class ServerPriceMinBase(HasServerPK, HasVendorPKFK):
    min_price: float
    min_spot_price: Optional[float]
    min_ondemand_price: float


class ServerPriceMin(ServerPriceMinBase, table=True):
    """Poor man's materialized view on min price of servers."""

    _query = (
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
