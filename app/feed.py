"""Load the weekly promotions feed and work out what may be promoted.

The feed is the only thing that changes week to week, so it is read fresh
on every run. Availability comes from the stock fields only: a promo code
on a sold-out product does not make it sellable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

AVAILABLE_STATUSES = {"in_stock", "low_stock"}


class FeedError(Exception):
    pass


@dataclass(frozen=True)
class Promotion:
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
        # Any status we don't recognise counts as unavailable (fail closed).
        return self.stock_status in AVAILABLE_STATUSES and self.units_left > 0


@dataclass(frozen=True)
class Feed:
    snapshot_date: date
    products: tuple[Product, ...]


def load_feed(path: str | Path) -> Feed:
    path = Path(path)
    if not path.is_file():
        raise FeedError(f"Feed file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeedError(f"{path} is not valid JSON: {exc}") from exc
    return parse_feed(raw)


def parse_feed(raw: dict) -> Feed:
    try:
        snapshot_date = datetime.fromisoformat(raw["snapshot_taken_at"]).date()
        products = tuple(_parse_product(item) for item in raw["products"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FeedError(f"Feed is malformed: {exc!r}") from exc
    if not products:
        raise FeedError("Feed has no products")
    return Feed(snapshot_date=snapshot_date, products=products)


def _parse_product(item: dict) -> Product:
    price, units = item["price_usd"], item["units_left"]
    if not isinstance(price, (int, float)) or not isinstance(units, int) or price < 0 or units < 0:
        raise ValueError(f"bad price/units for product {item.get('id')!r}")
    promo = None
    if item["promo_code"]:
        expires = item["promo_expires"]
        promo = Promotion(
            code=item["promo_code"],
            discount_percent=item["promo_discount_percent"],
            expires=date.fromisoformat(expires) if expires else None,
        )
    return Product(
        id=item["id"],
        name=item["name"],
        origin=item["origin"],
        roast_level=item["roast_level"],
        tasting_notes=item["tasting_notes"],
        price_usd=price,
        stock_status=item["stock_status"],
        units_left=units,
        new_this_week=bool(item["new_this_week"]),
        promo=promo,
    )


@dataclass(frozen=True)
class FactSheet:
    """The safe view of the feed: what the model may say and what it may not."""

    snapshot_date: date
    available: tuple[Product, ...]  # promo is None unless still active
    sold_out: tuple[Product, ...]  # the model only ever sees their names
    all_promo_codes: tuple[str, ...]  # for the validator, so a withheld code is recognised

    def get_available(self, product_id: str) -> Product | None:
        return next((p for p in self.available if p.id == product_id), None)

    def for_prompt(self) -> dict:
        """Only the facts the model is allowed to repeat."""
        return {
            "feed_snapshot_date": self.snapshot_date.isoformat(),
            "available_products": [
                {
                    "id": p.id,
                    "name": p.name,
                    "origin": p.origin,
                    "roast_level": p.roast_level,
                    "tasting_notes": p.tasting_notes,
                    "price_usd": p.price_usd,
                    "stock_status": p.stock_status,
                    "units_left": p.units_left,
                    "new_this_week": p.new_this_week,
                    "promo": None
                    if p.promo is None
                    else {
                        "code": p.promo.code,
                        "discount_percent": p.promo.discount_percent,
                        "expires": p.promo.expires.isoformat() if p.promo.expires else None,
                    },
                }
                for p in self.available
            ],
            "sold_out_products_do_not_mention": [{"id": p.id, "name": p.name} for p in self.sold_out],
        }


def build_fact_sheet(feed: Feed, as_of: date | None = None) -> FactSheet:
    """Split the feed into available and sold-out, dropping expired promos.

    Expiry is judged against the snapshot's own date (unless `as_of` is
    given), so an archived snapshot keeps meaning what it meant that week.
    """
    today = as_of or feed.snapshot_date
    available = []
    for p in feed.products:
        if p.is_available:
            if p.promo and p.promo.expires and p.promo.expires < today:
                p = replace(p, promo=None)
            available.append(p)
    return FactSheet(
        snapshot_date=feed.snapshot_date,
        available=tuple(available),
        sold_out=tuple(p for p in feed.products if not p.is_available),
        all_promo_codes=tuple(p.promo.code for p in feed.products if p.promo),
    )
