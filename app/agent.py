"""Orchestration: brief + brand + feed -> validated caption.

    brief
      -> build the fact sheet from the feed (deterministic)
      -> refuse early if the brief only asks about sold-out products
      -> ask the model for {featured_product_ids, caption}
      -> validate deterministically
      -> return, or retry with the violations as feedback, or fail

Nothing that fails validation is ever returned as a caption.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from app.brand import BrandContext
from app.facts import FactSheet, build_fact_sheet, mentioned_product_ids
from app.llm import TextCompleter
from app.models import Feed
from app.prompts import build_system_prompt, build_user_prompt
from app.validator import Violation, validate_caption

DEFAULT_MAX_ATTEMPTS = 3


class CaptionRefused(Exception):
    """The brief cannot be fulfilled safely, so no caption was generated."""

    def __init__(self, reason: str, alternatives: list[str]):
        super().__init__(reason)
        self.reason = reason
        self.alternatives = alternatives


@dataclass
class Attempt:
    raw_response: str
    caption: str | None
    violations: list[Violation]


class CaptionGenerationError(Exception):
    """Every attempt failed validation (or could not be parsed)."""

    def __init__(self, attempts: list[Attempt]):
        last = attempts[-1]
        message = f"No valid caption after {len(attempts)} attempt(s). Last problems: " + "; ".join(
            str(v) for v in last.violations
        )
        if last.caption is None:
            message += f" Raw response began: {last.raw_response[:300]!r}"
        super().__init__(message)
        self.attempts = attempts


@dataclass
class CaptionResult:
    caption: str
    featured_product_ids: list[str]
    attempts: list[Attempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def generate_caption(
    brief: str,
    feed: Feed,
    brand: BrandContext,
    llm: TextCompleter,
    *,
    as_of: date | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> CaptionResult:
    if not brief.strip():
        raise ValueError("The content brief is empty.")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    facts = build_fact_sheet(feed, as_of=as_of)
    notes = check_brief_against_facts(brief, facts)

    system_prompt = build_system_prompt(brand)
    attempts: list[Attempt] = []
    corrections: list[str] | None = None

    for _ in range(max_attempts):
        raw = llm.complete(system_prompt, build_user_prompt(facts, brief, corrections))
        try:
            caption, declared_ids = parse_model_response(raw)
        except ValueError as exc:
            caption, declared_ids, violations = None, [], [Violation("bad_response_format", str(exc))]
        else:
            violations = validate_caption(caption, declared_ids, facts, brand.rules)

        attempts.append(Attempt(raw, caption, violations))
        if not violations:
            assert caption is not None
            featured = dict.fromkeys(declared_ids + mentioned_product_ids(caption, facts.all_products))
            return CaptionResult(caption, list(featured), attempts, notes)
        corrections = [v.message for v in violations]

    raise CaptionGenerationError(attempts)


def check_brief_against_facts(brief: str, facts: FactSheet) -> list[str]:
    """Deterministic pre-flight. Raises `CaptionRefused` when unsafe.

    If the brief names products and every one of them is unavailable, there
    is nothing honest to write, so stop before spending a model call. If it
    names a mix, drop the unavailable ones and say so in a note.
    """
    if not facts.available:
        raise CaptionRefused(
            f"No product in the feed snapshot ({facts.snapshot_date}) is available, so nothing can be promoted.",
            alternatives=[],
        )

    mentioned = mentioned_product_ids(brief, facts.all_products)
    unavailable = [p for p in facts.unavailable if p.id in mentioned]
    available = [p for p in facts.available if p.id in mentioned]

    if unavailable and not available:
        names = ", ".join(f"'{p.name}' ({p.stock_status})" for p in unavailable)
        raise CaptionRefused(
            f"The brief asks about {names}, which is not available in the feed snapshot "
            f"({facts.snapshot_date}). Refusing to generate a caption that would promote or imply "
            "availability of a sold-out product.",
            alternatives=[p.name for p in facts.available],
        )

    return [
        f"The brief mentions '{p.name}', which is {p.stock_status} in this snapshot; it was excluded."
        for p in unavailable
    ]


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_model_response(raw: str) -> tuple[str, list[str]]:
    """Extract (caption, declared product ids); raise ValueError if malformed.

    Tolerates code fences and stray text around the JSON object, because
    small models do that, but does not try to rescue anything less.
    """
    match = _JSON_OBJECT_RE.search(raw or "")
    if not match:
        raise ValueError("Response did not contain a JSON object.")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response was not valid JSON: {exc}.") from exc
    if not isinstance(data, dict):
        raise ValueError("Response JSON was not an object.")

    caption = data.get("caption")
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError("Response JSON has no non-empty 'caption' string.")
    ids = data.get("featured_product_ids", [])
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError("'featured_product_ids' must be a list of product id strings.")
    return caption.strip(), ids
