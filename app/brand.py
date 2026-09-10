"""The durable brand layer.

Two files, one concept:

* `brand_kit.md` — the voice guide, given to the model verbatim. It is prose,
  and prose is what the model is good at following.
* `brand_rules.json` — the handful of rules from that guide that can be
  checked mechanically (banned words, emoji/exclamation limits, health-claim
  vocabulary). The validator enforces these; the model is merely told them.

Neither file knows anything about this week's products.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class BrandError(Exception):
    """Raised when brand files are missing or malformed."""


@dataclass(frozen=True)
class BrandRules:
    banned_phrases: tuple[str, ...]
    health_claim_terms: tuple[str, ...]
    max_emoji: int
    max_exclamation_marks: int
    max_characters: int


@dataclass(frozen=True)
class BrandContext:
    voice_guide: str
    rules: BrandRules


def load_brand(kit_path: str | Path, rules_path: str | Path) -> BrandContext:
    kit_path, rules_path = Path(kit_path), Path(rules_path)
    if not kit_path.is_file():
        raise BrandError(f"Brand kit not found: {kit_path}")
    voice_guide = kit_path.read_text(encoding="utf-8").strip()
    if not voice_guide:
        raise BrandError(f"Brand kit is empty: {kit_path}")
    return BrandContext(voice_guide=voice_guide, rules=load_brand_rules(rules_path))


def load_brand_rules(rules_path: str | Path) -> BrandRules:
    rules_path = Path(rules_path)
    if not rules_path.is_file():
        raise BrandError(f"Brand rules file not found: {rules_path}")
    try:
        raw = json.loads(rules_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BrandError(f"Brand rules file {rules_path} is not valid JSON: {exc}") from exc

    try:
        return BrandRules(
            banned_phrases=_str_tuple(raw["banned_phrases"], "banned_phrases"),
            health_claim_terms=_str_tuple(raw["health_claim_terms"], "health_claim_terms"),
            max_emoji=_non_negative_int(raw["max_emoji"], "max_emoji"),
            max_exclamation_marks=_non_negative_int(raw["max_exclamation_marks"], "max_exclamation_marks"),
            max_characters=_non_negative_int(raw["max_characters"], "max_characters"),
        )
    except KeyError as exc:
        raise BrandError(f"Brand rules file {rules_path} is missing key {exc}") from exc


def _str_tuple(value: object, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise BrandError(f"Brand rule '{key}' must be a list of non-empty strings")
    return tuple(v.strip() for v in value)


def _non_negative_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BrandError(f"Brand rule '{key}' must be a non-negative integer")
    return value
