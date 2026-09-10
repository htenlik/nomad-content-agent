"""Shared fixtures and fakes for the test-suite (stdlib unittest, pytest-compatible)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app.brand import load_brand
from app.feed import parse_feed
from app.models import Feed

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEEK1 = DATA / "promotions_feed_week1.json"
WEEK2 = DATA / "promotions_feed_week2.json"


def load_brand_context():
    return load_brand(DATA / "brand_kit.md", DATA / "brand_rules.json")


def raw_feed(path: Path = WEEK1) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def synthetic_feed(**overrides) -> Feed:
    """A small feed that shares nothing with the supplied fixtures.

    Used to prove the validator and fact-sheet logic do not depend on the
    week1/week2 data. Pass `products=[...]` or top-level overrides.
    """
    base = {
        "snapshot_taken_at": "2030-01-06T06:00:00+00:00",
        "products": [
            product("alpha-ridge", name="Alpha — Ridge", origin="Ridge, Alphaland", price_usd=12,
                    promo_code="ALPHA5", promo_discount_percent=5, promo_expires="2030-01-10"),
            product("beta-valley", name="Beta — Valley", origin="Valley, Betania", price_usd=14,
                    new_this_week=True),
            product("gamma-coast", name="Gamma — Coast", origin="Coast, Gammaria", price_usd=16,
                    stock_status="sold_out", units_left=0,
                    promo_code="GAMMA30", promo_discount_percent=30, promo_expires="2030-01-31"),
        ],
    }
    base.update(overrides)
    return parse_feed(copy.deepcopy(base), source="synthetic")


def product(product_id: str, **fields) -> dict:
    item = {
        "id": product_id,
        "name": product_id.title(),
        "origin": "Somewhere",
        "roast_level": "Medium",
        "tasting_notes": "Cocoa, cherry",
        "price_usd": 10,
        "stock_status": "in_stock",
        "units_left": 50,
        "promo_code": None,
        "promo_discount_percent": None,
        "promo_expires": None,
        "new_this_week": False,
    }
    item.update(fields)
    return item


class FakeLLM:
    """Returns scripted responses in order and records every prompt it saw."""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.responses:
            raise AssertionError("FakeLLM was called more times than it has responses")
        return self.responses.pop(0)


def model_json(caption: str, *ids: str) -> str:
    return json.dumps({"featured_product_ids": list(ids), "caption": caption})
