"""Deterministic derivation of the "safe" view of the current feed.

The LLM never sees the raw feed. It sees a `FactSheet`:

* available products, with their price and any promotion that is still
  active on the reference date;
* sold-out (or otherwise unavailable) products by name only, so the model
  knows what NOT to mention. Their prices and promo codes are withheld.

Promotion validity is judged against the feed's own snapshot date by
default (see `build_fact_sheet`), so historical snapshots keep producing the
same fact sheet no matter when the code is run.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from app.models import Feed, Product


@dataclass(frozen=True)
class ActivePromotion:
    code: str
    discount_percent: int | None
    expires: date | None


@dataclass(frozen=True)
class ProductFacts:
    """Everything the model is allowed to say about one available product."""

    id: str
    name: str
    origin: str
    roast_level: str
    tasting_notes: str
    price_usd: float
    stock_status: str
    units_left: int
    new_this_week: bool
    promo: ActivePromotion | None


@dataclass(frozen=True)
class UnavailableProduct:
    id: str
    name: str
    origin: str
    stock_status: str


@dataclass(frozen=True)
class FactSheet:
    source: str
    snapshot_date: date
    as_of: date
    available: tuple[ProductFacts, ...]
    unavailable: tuple[UnavailableProduct, ...]
    # Every promo code present anywhere in the feed, including on sold-out
    # products and expired promos. Never sent to the model; the validator
    # uses it to recognise a withheld code if the model produces one anyway.
    known_promo_codes: tuple[str, ...]

    @property
    def all_products(self) -> tuple[ProductFacts | UnavailableProduct, ...]:
        return self.available + self.unavailable

    def get_available(self, product_id: str) -> ProductFacts | None:
        for product in self.available:
            if product.id == product_id:
                return product
        return None

    @property
    def new_this_week(self) -> tuple[ProductFacts, ...]:
        return tuple(p for p in self.available if p.new_this_week)

    def to_prompt_dict(self) -> dict[str, Any]:
        """The JSON handed to the model. Only facts it may repeat."""
        return {
            "feed_snapshot_date": self.snapshot_date.isoformat(),
            "available_products": [
                {
                    "id": p.id,
                    "name": p.name,
                    "origin": p.origin,
                    "roast_level": p.roast_level,
                    "tasting_notes": p.tasting_notes,
                    "price_usd": _number(p.price_usd),
                    "stock_status": p.stock_status,
                    "units_left": p.units_left,
                    "new_this_week": p.new_this_week,
                    "promo": (
                        None
                        if p.promo is None
                        else {
                            "code": p.promo.code,
                            "discount_percent": p.promo.discount_percent,
                            "expires": p.promo.expires.isoformat() if p.promo.expires else None,
                        }
                    ),
                }
                for p in self.available
            ],
            "sold_out_products_do_not_mention": [
                {"id": p.id, "name": p.name} for p in self.unavailable
            ],
        }


def build_fact_sheet(feed: Feed, as_of: date | None = None) -> FactSheet:
    """Split the feed into what may be promoted and what may not.

    `as_of` is the date used to decide whether a promotion has expired.
    It defaults to the snapshot's own date: a snapshot is a statement about
    the world at the moment it was taken, and validating against the wall
    clock would make an archived snapshot's promos silently vanish.
    """
    reference = as_of or feed.snapshot_date
    available = []
    unavailable = []
    for product in feed.products:
        if product.is_available:
            available.append(_available_facts(product, reference))
        else:
            unavailable.append(
                UnavailableProduct(
                    id=product.id, name=product.name, origin=product.origin, stock_status=product.stock_status
                )
            )
    return FactSheet(
        source=feed.source,
        snapshot_date=feed.snapshot_date,
        as_of=reference,
        available=tuple(available),
        unavailable=tuple(unavailable),
        known_promo_codes=tuple(p.promo.code for p in feed.products if p.promo is not None),
    )


def _available_facts(product: Product, reference: date) -> ProductFacts:
    promo = None
    if product.promo is not None and (product.promo.expires is None or product.promo.expires >= reference):
        promo = ActivePromotion(
            code=product.promo.code,
            discount_percent=product.promo.discount_percent,
            expires=product.promo.expires,
        )
    return ProductFacts(
        id=product.id,
        name=product.name,
        origin=product.origin,
        roast_level=product.roast_level,
        tasting_notes=product.tasting_notes,
        price_usd=product.price_usd,
        stock_status=product.stock_status,
        units_left=product.units_left,
        new_this_week=product.new_this_week,
        promo=promo,
    )


# --- Product mention detection -------------------------------------------
#
# Used in two places: to see which products a *brief* is asking about, and
# to see which products a *caption* talks about. Both are plain text, so we
# match on the distinctive words of each product's name and origin (the
# region and country). Words shared by several products are ignored, and
# short words are skipped to avoid accidental matches.

# Whole alphabetic words only: the letters inside a token like "RIDGE15" are
# a promo code, not a mention of the Ridge product.
_WORD_RE = re.compile(r"(?<![a-z0-9])[a-z]+(?![a-z0-9])")
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_MIN_TOKEN_LENGTH = 4


def normalize_text(text: str) -> str:
    """Lowercase and strip accents so 'Café' and 'cafe' match."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(normalize_text(text)) if len(w) >= _MIN_TOKEN_LENGTH}


def product_keywords(products: Iterable[Product | ProductFacts | UnavailableProduct]) -> dict[str, set[str]]:
    """Map product id -> the words that identify it and only it."""
    products = list(products)
    # Parenthesised parts of a name ("(Washed)") describe process, not
    # identity, and would otherwise make a generic word identify a product.
    raw = {p.id: _tokens(_PARENTHETICAL_RE.sub(" ", p.name)) | _tokens(p.origin) for p in products}
    counts: dict[str, int] = {}
    for words in raw.values():
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    return {pid: {w for w in words if counts[w] == 1} for pid, words in raw.items()}


def mentioned_product_ids(text: str, products: Iterable[Product | ProductFacts | UnavailableProduct]) -> list[str]:
    """Ids of products whose distinctive keywords appear in `text`."""
    words = _tokens(text)
    return [
        pid
        for pid, keywords in product_keywords(products).items()
        if any(_word_matches(word, keyword) for word in words for keyword in keywords)
    ]


def _word_matches(word: str, keyword: str) -> bool:
    """Exact match, or a short suffix on the keyword ("kenyan", "peruvian")."""
    return word == keyword or (word.startswith(keyword) and len(word) - len(keyword) <= 4)


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value
