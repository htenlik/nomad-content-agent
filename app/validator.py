"""Deterministic post-generation checks.

The model returns a caption plus the ids of the products it featured. This
module decides whether the caption may be published, using the fact sheet
built from the same feed snapshot the model saw:

* commercial facts — prices, discounts, promo codes and availability are
  checked against the specific products the caption is about, so Product
  A's price cannot validate a claim about Product B;
* brand rules — the mechanically checkable subset of brand_kit.md.

Each failure is a `Violation` whose message is written for the model to
read on the next attempt. Subjective voice quality is not checked here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from app.brand import BrandRules
from app.facts import FactSheet, mentioned_product_ids, normalize_text
from app.models import Product


@dataclass(frozen=True)
class Violation:
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# "$19", "$ 19.50", "19 dollars"
_PRICE_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)|(?<![\d.])(\d+(?:\.\d{1,2})?)\s?dollars?\b", re.IGNORECASE)
# "15%", "15 %", "15 percent"
_PERCENT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE)
# Invented codes: SHOUTY tokens with a digit or underscore (SPRING15, FLASH_SALE).
_SHOUTY_CODE_RE = re.compile(r"\b(?=[A-Z0-9_]*[\d_])[A-Z][A-Z0-9_]{3,}\b")
# Claims of novelty that must be backed by `new_this_week`.
_NEW_CLAIM_RE = re.compile(r"\b(new this week|new roast|brand[\s-]new|newest|new arrival)\b", re.IGNORECASE)


def validate_caption(
    caption: str,
    declared_product_ids: Sequence[str],
    facts: FactSheet,
    rules: BrandRules,
) -> list[Violation]:
    """Return every rule the caption breaks (empty list means publishable)."""
    caption = caption.strip()
    if not caption:
        return [Violation("empty_caption", "The caption is empty.")]

    featured, violations = _resolve_featured_products(caption, declared_product_ids, facts)
    violations += _check_prices(caption, featured)
    violations += _check_discounts(caption, featured)
    violations += _check_promo_codes(caption, featured, facts)
    violations += _check_new_claims(caption, featured)
    violations += _check_brand_rules(caption, rules)
    return violations


# --- product association ---------------------------------------------------


def _resolve_featured_products(
    caption: str,
    declared_ids: Sequence[str],
    facts: FactSheet,
) -> tuple[list[Product], list[Violation]]:
    """Which available products is this caption about?

    Availability is checked for everything declared *and* everything the
    text names. Numbers are checked against the products the text names;
    the declared ids only count when the text names nothing, so declaring
    Product A while writing about Product B cannot let A's price through.
    """
    named = mentioned_product_ids(caption, facts.all_products)
    referenced = list(dict.fromkeys(list(declared_ids) + named))
    fact_scope = set(named) if named else set(declared_ids)
    featured: list[Product] = []
    violations: list[Violation] = []
    for product_id in referenced:
        available = facts.get_available(product_id)
        if available is not None:
            if product_id in fact_scope:
                featured.append(available)
            continue
        unavailable = next((p for p in facts.unavailable if p.id == product_id), None)
        if unavailable is None:
            violations.append(Violation("unknown_product", f"Product id {product_id!r} does not exist in the current feed."))
        else:
            violations.append(
                Violation(
                    "sold_out_product",
                    f"'{unavailable.name}' is {unavailable.stock_status} in the current feed and must not be "
                    "mentioned or promoted. Write about an available product instead.",
                )
            )
    return featured, violations


# --- commercial facts ------------------------------------------------------


def _check_prices(caption: str, featured: list[Product]) -> list[Violation]:
    violations = []
    allowed = {p.price_usd: p.name for p in featured}
    for match in _PRICE_RE.finditer(caption):
        value = float(match.group(1) or match.group(2))
        if value not in allowed:
            violations.append(
                Violation(
                    "unsupported_price",
                    f"'{match.group(0).strip()}' is not the price of any product featured in this caption. "
                    + _allowed_summary({f"${v}": n for v, n in allowed.items()}),
                )
            )
    return violations


def _check_discounts(caption: str, featured: list[Product]) -> list[Violation]:
    violations = []
    allowed = {p.promo.discount_percent: p.name for p in featured if p.promo and p.promo.discount_percent}
    for match in _PERCENT_RE.finditer(caption):
        value = float(match.group(1))
        if value not in allowed:
            violations.append(
                Violation(
                    "unsupported_discount",
                    f"'{match.group(0).strip()}' is not an active discount for any product featured in this caption. "
                    + _allowed_summary({f"{v}%": n for v, n in allowed.items()}),
                )
            )
    return violations


def _check_promo_codes(caption: str, featured: list[Product], facts: FactSheet) -> list[Violation]:
    violations = []
    allowed = {p.promo.code.upper(): p.name for p in featured if p.promo}

    # Codes that exist in the feed but were withheld (sold-out, expired, or
    # another product's), in any letter case, plus anything that looks invented.
    candidates = {
        code.upper()
        for code in facts.known_promo_codes
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(code)}(?![A-Za-z0-9_])", caption, re.IGNORECASE)
    }
    candidates.update(m.group(0).upper() for m in _SHOUTY_CODE_RE.finditer(caption))

    for code in sorted(candidates):
        if code not in allowed:
            violations.append(
                Violation(
                    "unsupported_promo_code",
                    f"'{code}' is not an active promo code for any product featured in this caption. "
                    + _allowed_summary(allowed),
                )
            )
    return violations


def _check_new_claims(caption: str, featured: list[Product]) -> list[Violation]:
    match = _NEW_CLAIM_RE.search(caption)
    if match and not any(p.new_this_week for p in featured):
        return [
            Violation(
                "unsupported_new_claim",
                f"The caption says '{match.group(0)}' but none of the featured products is new this week.",
            )
        ]
    return []


# --- explicit brand rules --------------------------------------------------


def _check_brand_rules(caption: str, rules: BrandRules) -> list[Violation]:
    violations = []
    normalized = normalize_text(caption).replace("’", "'")

    for phrase in rules.banned_phrases:
        if normalize_text(phrase).replace("’", "'") in normalized:
            violations.append(Violation("banned_phrase", f"The brand voice guide forbids the phrase '{phrase}'."))

    for term in rules.health_claim_terms:
        if re.search(rf"\b{re.escape(normalize_text(term))}\b", normalized):
            violations.append(
                Violation("health_claim", f"'{term}' reads as a health or performance claim, which the brand does not make.")
            )

    exclamations = caption.count("!")
    if exclamations > rules.max_exclamation_marks:
        violations.append(
            Violation(
                "too_many_exclamation_marks",
                f"{exclamations} exclamation marks; the brand allows at most {rules.max_exclamation_marks}.",
            )
        )

    emoji = count_emoji(caption)
    if emoji > rules.max_emoji:
        violations.append(Violation("too_many_emoji", f"{emoji} emoji; the brand allows at most {rules.max_emoji}."))

    if len(caption) > rules.max_characters:
        violations.append(
            Violation("too_long", f"{len(caption)} characters; keep it caption-length (max {rules.max_characters}).")
        )
    return violations


def count_emoji(text: str) -> int:
    """Rough emoji count: pictographic code points, ignoring joiners/selectors."""
    count = 0
    for ch in text:
        if ch in "\u200d\ufe0f\ufe0e":
            continue
        cp = ord(ch)
        if 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or unicodedata.category(ch) == "So":
            count += 1
    return count


# --- helpers ---------------------------------------------------------------


def _allowed_summary(allowed: dict[str, str]) -> str:
    if not allowed:
        return "Nothing of this kind is allowed for the featured products; remove it."
    return "Allowed: " + ", ".join(f"{value} ({name})" for value, name in allowed.items()) + "."
