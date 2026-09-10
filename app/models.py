"""Typed representation of the promotions/inventory feed.

These are plain dataclasses on purpose: the feed is small and the fields are
few, so a validation library would add a dependency without adding safety.
Field checks happen in `feed.py` while parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Statuses under which a product may be promoted. Anything else (including
# "sold_out" and any status we have never seen) is treated as NOT available.
# Availability is defined positively so that an unexpected new status value
# fails closed instead of accidentally becoming sellable.
AVAILABLE_STATUSES = frozenset({"in_stock", "low_stock"})


@dataclass(frozen=True)
class Promotion:
    """A promotion exactly as the feed states it. Not yet judged for expiry."""

    code: str
    discount_percent: int | None
    expires: date | None


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    origin: str
    roast_level: str
    tasting_notes: str
    price_usd: float
    stock_status: str
    units_left: int
    new_this_week: bool
    promo: Promotion | None

    @property
    def is_available(self) -> bool:
        """True only when both the status and the unit count say so.

        A promo code attached to the product is deliberately not consulted:
        the feed contains sold-out products with "valid-looking" promos.
        """
        return self.stock_status in AVAILABLE_STATUSES and self.units_left > 0


@dataclass(frozen=True)
class Feed:
    source: str
    snapshot_taken_at: datetime
    products: tuple[Product, ...]

    @property
    def snapshot_date(self) -> date:
        return self.snapshot_taken_at.date()

    def get(self, product_id: str) -> Product | None:
        for product in self.products:
            if product.id == product_id:
                return product
        return None
