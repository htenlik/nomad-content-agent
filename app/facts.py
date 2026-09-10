"""The safe view of the current feed: what the model is allowed to know.

The feed is split into products that may be promoted and products that may
not. Available products keep their price and any promotion still active on
the reference date; unavailable products reach the model as names only.
Promotion validity is judged against the feed's own snapshot date by default
(see `build_fact_sheet`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Iterable

from app.models import Feed, Product, Promotion


@dataclass(frozen=True)
class FactSheet:
    snapshot_date: date
    as_of: date
    available: tuple[Product, ...]  # promo is None unless still active
    unavailable: tuple[Product, ...]  # only id and name are shown to the model
    # Every promo code in the feed, including on sold-out products and
    # expired promos. Never shown to the model; the validator uses it to
    # recognise a withheld code if the model produces one anyway.
    known_promo_codes: tuple[str, ...]

    @property
    def all_products(self) -> tuple[Product, ...]:
        return self.available + self.unavailable

    def get_available(self, product_id: str) -> Product | None:
        return next((p for p in self.available if p.id == product_id), None)

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
            "sold_out_products_do_not_mention": [{"id": p.id, "name": p.name} for p in self.unavailable],
        }


def build_fact_sheet(feed: Feed, as_of: date | None = None) -> FactSheet:
    """Split the feed into what may be promoted and what may not.

    `as_of` decides whether a promotion has expired. It defaults to the
    snapshot's own date: a snapshot describes the world when it was taken,
    and judging it by today's date would make an archived snapshot's promos
    silently vanish.
    """
    reference = as_of or feed.snapshot_date
    available = [replace(p, promo=_active(p.promo, reference)) for p in feed.products if p.is_available]
    unavailable = [p for p in feed.products if not p.is_available]
    return FactSheet(
        snapshot_date=feed.snapshot_date,
        as_of=reference,
        available=tuple(available),
        unavailable=tuple(unavailable),
        known_promo_codes=tuple(p.promo.code for p in feed.products if p.promo is not None),
    )


def _active(promo: Promotion | None, reference: date) -> Promotion | None:
    if promo is None or (promo.expires is not None and promo.expires < reference):
        return None
    return promo


# --- Product mention detection -------------------------------------------
#
# Used to see which products a brief asks about and which products a caption
# talks about. Matching is on the distinctive words of each product's name
# and origin (region, country); words shared by several products are
# ignored, and short words are skipped to avoid accidental matches.

# Whole alphabetic words only: the letters inside a token like "RIDGE15" are
# a promo code, not a mention of the Ridge product.
_WORD_RE = re.compile(r"(?<![a-z0-9])[a-z]+(?![a-z0-9])")
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")  # "(Washed)" describes process, not identity
_MIN_TOKEN_LENGTH = 4


def normalize_text(text: str) -> str:
    """Lowercase and strip accents so 'Café' and 'cafe' match."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(normalize_text(text)) if len(w) >= _MIN_TOKEN_LENGTH}


def product_keywords(products: Iterable[Product]) -> dict[str, set[str]]:
    """Map product id -> the words that identify it and only it."""
    raw = {p.id: _tokens(_PARENTHETICAL_RE.sub(" ", p.name)) | _tokens(p.origin) for p in products}
    counts: dict[str, int] = {}
    for words in raw.values():
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    return {pid: {w for w in words if counts[w] == 1} for pid, words in raw.items()}


def mentioned_product_ids(text: str, products: Iterable[Product]) -> list[str]:
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
