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
        return BrandRules(
            banned_phrases=tuple(raw["banned_phrases"]),
            health_claim_terms=tuple(raw["health_claim_terms"]),
            max_emoji=int(raw["max_emoji"]),
            max_exclamation_marks=int(raw["max_exclamation_marks"]),
            max_characters=int(raw["max_characters"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise BrandError(f"Brand rules file {rules_path} is malformed: {exc!r}") from exc
