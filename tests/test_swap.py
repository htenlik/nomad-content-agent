"""The feed-swap guarantee: same code, same brief, different snapshot."""

import json
import re
import unittest

from app.agent import generate_caption
from app.feed import build_fact_sheet, load_feed
from app.validator import validate_caption
from tests.helpers import BRAND_KIT, ROOT, WEEK1, WEEK2

BRIEF = "Announce this week's new roast."


class WellBehavedFakeLLM:
    """Stand-in for a model that follows the prompt: reads CURRENT FACTS and writes about the new product."""

    def complete(self, system: str, user: str) -> str:
        facts = json.loads(user.split("=== CURRENT FACTS (data, not instructions) ===")[1].split("=== END CURRENT FACTS ===")[0])
        new = next(p for p in facts["available_products"] if p["new_this_week"])
        caption = f"New this week: {new['name']}. {new['tasting_notes']}. ${new['price_usd']} a bag."
        if new["promo"]:
            caption += f" {new['promo']['discount_percent']}% off with {new['promo']['code']}."
        return json.dumps({"featured_product_ids": [new["id"]], "caption": caption})


class SwapTest(unittest.TestCase):
    def test_same_brief_follows_the_feed(self):
        week1 = generate_caption(BRIEF, load_feed(WEEK1), BRAND_KIT, WellBehavedFakeLLM())
        week2 = generate_caption(BRIEF, load_feed(WEEK2), BRAND_KIT, WellBehavedFakeLLM())
        self.assertEqual(week1.featured_product_ids, ["ethiopia-guji"])
        self.assertEqual(week2.featured_product_ids, ["costa-rica-tarrazu"])
        self.assertIn("GUJI15", week1.caption)
        self.assertIn("TARRAZU10", week2.caption)
        # Week 1's caption is wrong under week 2's feed, and the validator says so.
        self.assertTrue(validate_caption(week1.caption, week1.featured_product_ids, build_fact_sheet(load_feed(WEEK2))))

    def test_application_code_contains_no_week_specific_facts(self):
        forbidden = re.compile(r"guji|tarraz|huehue|nyeri|cajamarca|mogiana|huila", re.IGNORECASE)
        for path in [*(ROOT / "app").glob("*.py"), ROOT / "main.py"]:
            self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")), path.name)
