"""Loading and validating a promotions feed snapshot.

Everything here is deterministic. The loader is strict about the fields the
rest of the system relies on (id, name, price, stock status, unit count,
promo fields) so that a malformed feed fails loudly at startup rather than
producing a caption built on garbage.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.models import Feed, Product, Promotion

REQUIRED_PRODUCT_FIELDS = (
    "id",
    "name",
    "origin",
    "roast_level",
    "tasting_notes",
    "price_usd",
    "stock_status",
    "units_left",
    "promo_code",
    "promo_discount_percent",
    "promo_expires",
    "new_this_week",
)


class FeedError(Exception):
    """Raised when the feed file is missing, unreadable, or structurally wrong."""


def load_feed(path: str | Path) -> Feed:
    path = Path(path)
    if not path.is_file():
        raise FeedError(f"Feed file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeedError(f"Feed file {path} is not valid JSON: {exc}") from exc
    return parse_feed(raw, source=str(path))


def parse_feed(raw: Any, source: str = "<memory>") -> Feed:
    if not isinstance(raw, dict):
        raise FeedError(f"{source}: top-level JSON value must be an object")

    snapshot = raw.get("snapshot_taken_at")
    if not isinstance(snapshot, str):
        raise FeedError(f"{source}: 'snapshot_taken_at' is missing or not a string")
    try:
        snapshot_taken_at = datetime.fromisoformat(snapshot)
    except ValueError as exc:
        raise FeedError(f"{source}: 'snapshot_taken_at' is not an ISO-8601 datetime: {snapshot!r}") from exc

    products_raw = raw.get("products")
    if not isinstance(products_raw, list) or not products_raw:
        raise FeedError(f"{source}: 'products' must be a non-empty list")

    products = []
    seen_ids: set[str] = set()
    for index, item in enumerate(products_raw):
        product = _parse_product(item, f"{source} products[{index}]")
        if product.id in seen_ids:
            raise FeedError(f"{source}: duplicate product id {product.id!r}")
        seen_ids.add(product.id)
        products.append(product)

    return Feed(source=source, snapshot_taken_at=snapshot_taken_at, products=tuple(products))


def _parse_product(item: Any, where: str) -> Product:
    if not isinstance(item, dict):
        raise FeedError(f"{where}: product entry must be an object")
    missing = [field for field in REQUIRED_PRODUCT_FIELDS if field not in item]
    if missing:
        raise FeedError(f"{where}: missing required field(s): {', '.join(missing)}")

    product_id = _require_str(item, "id", where)
    where = f"{where} ({product_id})"

    price = item["price_usd"]
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
        raise FeedError(f"{where}: 'price_usd' must be a non-negative number, got {price!r}")

    units = item["units_left"]
    if isinstance(units, bool) or not isinstance(units, int) or units < 0:
        raise FeedError(f"{where}: 'units_left' must be a non-negative integer, got {units!r}")

    new_this_week = item["new_this_week"]
    if not isinstance(new_this_week, bool):
        raise FeedError(f"{where}: 'new_this_week' must be true/false, got {new_this_week!r}")

    return Product(
        id=product_id,
        name=_require_str(item, "name", where),
        origin=_require_str(item, "origin", where),
        roast_level=_require_str(item, "roast_level", where),
        tasting_notes=_require_str(item, "tasting_notes", where),
        price_usd=float(price),
        stock_status=_require_str(item, "stock_status", where),
        units_left=units,
        new_this_week=new_this_week,
        promo=_parse_promo(item, where),
    )


def _parse_promo(item: dict[str, Any], where: str) -> Promotion | None:
    code = item["promo_code"]
    if code is None:
        return None
    if not isinstance(code, str) or not code.strip():
        raise FeedError(f"{where}: 'promo_code' must be null or a non-empty string, got {code!r}")

    discount = item["promo_discount_percent"]
    if discount is not None:
        if isinstance(discount, bool) or not isinstance(discount, (int, float)):
            raise FeedError(f"{where}: 'promo_discount_percent' must be null or a number, got {discount!r}")
        if not 0 < discount <= 100:
            raise FeedError(f"{where}: 'promo_discount_percent' must be between 0 and 100, got {discount!r}")
        discount = int(discount) if float(discount).is_integer() else discount

    expires_raw = item["promo_expires"]
    expires: date | None = None
    if expires_raw is not None:
        if not isinstance(expires_raw, str):
            raise FeedError(f"{where}: 'promo_expires' must be null or a YYYY-MM-DD string")
        try:
            expires = date.fromisoformat(expires_raw)
        except ValueError as exc:
            raise FeedError(f"{where}: 'promo_expires' is not a YYYY-MM-DD date: {expires_raw!r}") from exc

    return Promotion(code=code.strip(), discount_percent=discount, expires=expires)


def _require_str(item: dict[str, Any], field: str, where: str) -> str:
    value = item[field]
    if not isinstance(value, str) or not value.strip():
        raise FeedError(f"{where}: '{field}' must be a non-empty string, got {value!r}")
    return value.strip()
