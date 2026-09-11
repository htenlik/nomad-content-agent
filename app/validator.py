"""Deterministic checks on a generated caption.

The model is not trusted with facts. After it writes a caption, this module
checks the commercial facts against the same fact sheet the model was
given, plus the few brand rules that a program can check. Every failure is
a `Violation` whose message is sent back to the model on the next attempt.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from app.feed import FactSheet, Product

# The explicit "don't" rules from data/brand_kit.md that can be checked
# mechanically. Tone itself is the model's job, guided by the kit.
BANNED_PHRASES = ["revolutionary", "life-changing", "game-changing", "elevate your routine"]
HEALTH_WORDS = ["energy", "focus", "metabolism", "antioxidant", "antioxidants", "healthy", "immune", "detox"]
MAX_EXCLAMATION_MARKS = 1
MAX_EMOJI = 1
MAX_CHARACTERS = 700  # "keep it caption-length"

PRICE_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s?dollars?\b", re.IGNORECASE)
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE)
# Anything that looks like a promo code: SHOUTY, with a digit or underscore (SPRING15, FLASH_SALE).
CODE_LIKE_RE = re.compile(r"\b(?=[A-Z0-9_]*[\d_])[A-Z][A-Z0-9_]{3,}\b")
NEW_CLAIM_RE = re.compile(r"\b(new this week|new roast|brand[\s-]new)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Violation:
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def validate_caption(caption: str, declared_ids: list[str], facts: FactSheet) -> list[Violation]:
    """Return every rule the caption breaks; an empty list means it can be published."""
    violations: list[Violation] = []

    # Which products is the caption about? Names in the text count, and so
    # do the ids the model declared. Any of them being sold out is a failure.
    named = mentioned_product_ids(caption, facts.available + facts.sold_out)
    for product in facts.sold_out:
        if product.id in named or product.id in declared_ids:
            violations.append(
                Violation("sold_out_product", f"'{product.name}' is {product.stock_status} and must not be mentioned.")
            )
    for product_id in declared_ids:
        if not facts.get_available(product_id) and product_id not in {p.id for p in facts.sold_out}:
            violations.append(Violation("unknown_product", f"Product id {product_id!r} is not in the feed."))

    # Numbers and codes must belong to the products the text names. The
    # declared ids are only used when the text names nothing, so declaring
    # product A while writing about product B cannot let A's price through.
    scope_ids = named or declared_ids
    featured = [p for p in facts.available if p.id in scope_ids]
    prices = {p.price_usd: p.name for p in featured}
    discounts = {p.promo.discount_percent: p.name for p in featured if p.promo and p.promo.discount_percent}
    codes = {p.promo.code.upper(): p.name for p in featured if p.promo}

    for match in PRICE_RE.finditer(caption):
        value = float(match.group(1) or match.group(2))
        if value not in prices:
            violations.append(Violation("unsupported_price", f"'{match.group(0)}' is not the price of a featured product. {_allowed(prices, '$')}"))
    for match in PERCENT_RE.finditer(caption):
        if float(match.group(1)) not in discounts:
            violations.append(Violation("unsupported_discount", f"'{match.group(0)}' is not an active discount for a featured product. {_allowed(discounts, '', '%')}"))
    found_codes = {m.group(0).upper() for m in CODE_LIKE_RE.finditer(caption)}
    found_codes |= {c.upper() for c in facts.all_promo_codes if re.search(rf"\b{re.escape(c)}\b", caption, re.IGNORECASE)}
    for code in sorted(found_codes - set(codes)):
        violations.append(Violation("unsupported_promo_code", f"'{code}' is not an active promo code for a featured product. {_allowed(codes)}"))

    if NEW_CLAIM_RE.search(caption) and not any(p.new_this_week for p in featured):
        violations.append(Violation("unsupported_new_claim", "The caption calls something new, but no featured product is new this week."))

    violations += check_brand_rules(caption)
    return violations


def check_brand_rules(caption: str) -> list[Violation]:
    violations = []
    lowered = normalize(caption)
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            violations.append(Violation("banned_phrase", f"The brand guide forbids '{phrase}'."))
    for word in HEALTH_WORDS:
        if re.search(rf"\b{word}\b", lowered):
            violations.append(Violation("health_claim", f"'{word}' reads as a health claim, which the brand does not make."))
    if caption.count("!") > MAX_EXCLAMATION_MARKS:
        violations.append(Violation("too_many_exclamation_marks", f"At most {MAX_EXCLAMATION_MARKS} exclamation mark per post."))
    if sum(1 for ch in caption if unicodedata.category(ch) == "So") > MAX_EMOJI:
        violations.append(Violation("too_many_emoji", f"At most {MAX_EMOJI} emoji per post."))
    if len(caption) > MAX_CHARACTERS:
        violations.append(Violation("too_long", f"Keep it under {MAX_CHARACTERS} characters."))
    return violations


# --- product-name matching -------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase and strip accents so 'Café' and 'cafe' match."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _words(text: str) -> set[str]:
    # Alphabetic words of 4+ letters. A token like "RIDGE15" is a promo code,
    # not a mention of the Ridge product, so mixed tokens are skipped.
    return {w for w in re.findall(r"[a-z0-9]+", normalize(text)) if w.isalpha() and len(w) >= 4}


def product_keywords(products: Iterable[Product]) -> dict[str, set[str]]:
    """Words that identify each product: its name and origin, minus words shared with other products."""
    products = list(products)
    words = {p.id: _words(re.sub(r"\([^)]*\)", " ", p.name)) | _words(p.origin) for p in products}
    shared = {w for p in products for w in words[p.id] if sum(w in words[q.id] for q in products) > 1}
    return {pid: ws - shared for pid, ws in words.items()}


def mentioned_product_ids(text: str, products: Iterable[Product]) -> list[str]:
    """Ids of the products named in `text` (also matching 'Kenyan', 'Peruvian' style forms)."""
    words = _words(text)
    return [
        pid
        for pid, keywords in product_keywords(products).items()
        if any(w == k or (w.startswith(k) and len(w) - len(k) <= 4) for w in words for k in keywords)
    ]


def _allowed(allowed: dict, prefix: str = "", suffix: str = "") -> str:
    if not allowed:
        return "Nothing of this kind applies to the featured products; remove it."
    return "Allowed: " + ", ".join(f"{prefix}{v}{suffix} ({n})" for v, n in allowed.items()) + "."
