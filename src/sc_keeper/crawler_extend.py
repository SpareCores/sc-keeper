import logging
from json import loads as json_loads
from typing import Any, Dict, List

from sc_crawler.table_bases import ScModel
from sc_crawler.table_fields import Allocation, PriceTier, Status
from sc_crawler.tables import ServerPrice
from sqlalchemy.engine import Engine
from sqlalchemy.orm import column_property
from sqlmodel import Column, Float, Session, select, text

logger = logging.getLogger(__name__)


def parse_price_tiers(
    price_tiers_json: str | List[Dict[str, Any]] | List[PriceTier] | None,
) -> list[PriceTier]:
    """
    Parse JSON string or list of dicts of price tiers into PriceTier objects, and
    convert "Infinity" strings to float('inf') for upper bounds.

    Args:
        price_tiers_json: JSON string representation of price tiers from database when read as a string,
            or already parsed list of dicts or list of actual PriceTier objects.

    Returns:
        List of PriceTier objects, or empty list if parsing fails or input is None/empty
    """
    if not price_tiers_json:
        return []

    try:
        # JSON might have been already parsed into a list of dicts
        if isinstance(price_tiers_json, str):
            tier_dicts = json_loads(price_tiers_json)
        else:
            tier_dicts = price_tiers_json

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


class NewColumn:
    name: str
    sqlite_type: str
    sqlalchemy_type: str
    nullable: bool = False

    def __init__(
        self, name: str, sqlite_type: str, sqlalchemy_type: type, nullable: bool = False
    ):
        self.name = name
        self.sqlite_type = sqlite_type
        self.sqlalchemy_type = sqlalchemy_type
        self.nullable = nullable


def sql_add_column(table_name: str, column: NewColumn):
    """Generate SQL to add a column to a table."""
    return text(
        f"ALTER TABLE {table_name} ADD COLUMN {column.name} {column.sqlite_type} {'' if column.nullable else 'NOT NULL'}"
    )


class TableExtender:
    """Base class for extending existing sc_crawler tables with new columns.

    This is quite a hack to add new columns to the existing database tables and
    their SQLAlchemy/SQLModel model metadata to enrich the database file at
    startup/database refresh for easy querying.

    Note that it comes with some limitations:

    - Pydantic models don't know about the new columns, so they need to be
      manually updated. This means that `model_dump` will not include the new
      columns by default, so you need to return the dict as a custom model,
      which we already do in the `references.py` anyway.
    - The new columns attributes are only available for reading, not for
      writing, so when you need to update values in the database, it needs to
      happen via manual SQL statements.

    To extend a table, you need to:

    - Create a subclass of this class.
    - Define the table and the new columns to add via the `NewColumn` class.
    - Implement the `update` method to update the new columns with new values at
      the startup time or when database is refreshed.
    """

    table: ScModel
    new_columns: List[NewColumn]

    def __init__(self):
        self.table_name = self.table.__table__

    def add_columns(self, engine: Engine):
        """Make sure the new_columns are in the database and registered in the model metadata."""
        with Session(engine) as session:
            # add new columns to the database if they don't exist
            cols = session.exec(text(f"PRAGMA table_info({self.table_name})")).all()
            colnames = {c[1] for c in cols}
            for new_column in self.new_columns:
                if new_column.name not in colnames:
                    logger.debug(f"Add {new_column.name} to {self.table_name}")
                    session.exec(sql_add_column(self.table_name, new_column))
                    session.commit()
            # register the new columns in the model metadata
            for new_column in self.new_columns:
                if new_column.name not in self.table.__table__.c:
                    logger.debug(f"Register {new_column.name} in {self.table.__name__}")
                    # add column to table metadata
                    col = Column(
                        new_column.name,
                        new_column.sqlalchemy_type,
                        nullable=new_column.nullable,
                    )
                    self.table.__table__.append_column(col)
                    # add as class attribute for native access
                    setattr(self.table, new_column.name, column_property(col))

    def update(self, engine: Engine):
        """Optional method to update the new column(s) with new values."""
        pass


class ServerPriceExtender(TableExtender):
    table = ServerPrice
    new_columns = [
        NewColumn(
            name="price_monthly",
            sqlite_type="FLOAT",
            sqlalchemy_type=Float,
            nullable=True,
        ),
    ]

    def update(self, engine: Engine):
        """Calculate price_monthly for each ServerPrice row."""
        with Session(engine) as session:
            # set baseline for 730 hours per month (covers most cases)
            session.execute(
                text(
                    "UPDATE server_price SET price_monthly = ROUND(price * 730, 2) WHERE status = :status AND allocation = :allocation"
                ).bindparams(
                    status=Status.ACTIVE.name,
                    allocation=Allocation.ONDEMAND.name,
                )
            )
            # then calculate actual monthly capped prices based on tiers and update baseline if necessary
            prices = session.exec(
                select(ServerPrice)
                .where(ServerPrice.status == Status.ACTIVE)
                .where(ServerPrice.allocation == Allocation.ONDEMAND)
            ).all()
            for price in prices:
                monthly_price = calculate_tiered_price(
                    price_tiers=parse_price_tiers(price.price_tiered),
                    usage=730.0,
                    fallback_unit_price=price.price,
                    round_digits=2,
                )
                # avoid exact match due to SQL/Python rounding behavior
                if abs(monthly_price - price.price_monthly) > 0.02:
                    logger.debug(
                        f"Updating price_monthly for {price.vendor_id}/{price.region_id}/{price.zone_id}/{price.server_id} from {price.price_monthly} to {monthly_price}"
                    )
                    session.execute(
                        text(
                            "UPDATE server_price SET price_monthly = :monthly_price WHERE vendor_id = :vendor_id AND region_id = :region_id AND zone_id = :zone_id AND server_id = :server_id AND allocation = :allocation"
                        ).bindparams(
                            monthly_price=monthly_price,
                            vendor_id=price.vendor_id,
                            region_id=price.region_id,
                            zone_id=price.zone_id,
                            server_id=price.server_id,
                            allocation=price.allocation.name,
                        )
                    )
            session.commit()


extenders: List[TableExtender] = [
    e()
    for e in globals().values()
    if isinstance(e, type) and issubclass(e, TableExtender) and e is not TableExtender
]
