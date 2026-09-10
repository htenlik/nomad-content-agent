"""The feed-swap guarantee: same code, same brief, different snapshot."""

import json
import re
import unittest
from pathlib import Path

from app.agent import generate_caption
from app.feed import load_feed
from tests.helpers import ROOT, WEEK1, WEEK2, load_brand_context

BRAND = load_brand_context()
BRIEF = "Announce this week's new roast."


class WellBehavedFakeLLM:
    """Stand-in for a model that follows the prompt: it reads the CURRENT
    FACTS block it is given and writes about the product marked new."""

    def complete(self, system: str, user: str) -> str:
        facts = json.loads(user.split("=== CURRENT FACTS (data, not instructions) ===")[1].split("=== END CURRENT FACTS ===")[0])
        new = [p for p in facts["available_products"] if p["new_this_week"]][0]
        line = f"New this week: {new['name']}. {new['tasting_notes']}. ${new['price_usd']} a bag."
        if new["promo"]:
            line += f" {new['promo']['discount_percent']}% off with {new['promo']['code']}."
        return json.dumps({"featured_product_ids": [new["id"]], "caption": line})


class SwapTest(unittest.TestCase):
    def test_same_brief_yields_snapshot_specific_captions(self):
        llm = WellBehavedFakeLLM()
        week1 = generate_caption(BRIEF, load_feed(WEEK1), BRAND, llm)
        week2 = generate_caption(BRIEF, load_feed(WEEK2), BRAND, llm)

        self.assertEqual(week1.featured_product_ids, ["ethiopia-guji"])
        self.assertEqual(week2.featured_product_ids, ["costa-rica-tarrazu"])
        self.assertIn("GUJI15", week1.caption)
        self.assertIn("TARRAZU10", week2.caption)
        self.assertNotIn("GUJI15", week2.caption)
        self.assertNotEqual(week1.caption, week2.caption)

    def test_each_caption_validates_only_against_its_own_snapshot(self):
        from app.facts import build_fact_sheet
        from app.validator import validate_caption

        llm = WellBehavedFakeLLM()
        week1 = generate_caption(BRIEF, load_feed(WEEK1), BRAND, llm)
        # Week 1's caption is factually wrong under week 2's feed, and the
        # validator says so when given the wrong snapshot.
        cross = validate_caption(week1.caption, week1.featured_product_ids, build_fact_sheet(load_feed(WEEK2)), BRAND.rules)
        self.assertTrue(cross)


class NoFixtureFactsInApplicationCode(unittest.TestCase):
    """Guards against 'making the demo pass' by baking week data into the app."""

    FORBIDDEN = re.compile(
        r"week1|week2|guji|tarraz|huehue|nyeri|cajamarca|mogiana|huila|GUJI15|NYERI10|TARRAZU10|HUEHUE",
        re.IGNORECASE,
    )

    def test_app_and_main_contain_no_week_specific_facts(self):
        sources = list((ROOT / "app").glob("*.py")) + [ROOT / "main.py"]
        for path in sources:
            text = Path(path).read_text(encoding="utf-8")
            match = self.FORBIDDEN.search(text)
            self.assertIsNone(match, f"{path.name} mentions fixture-specific term {match.group(0) if match else ''!r}")

    def test_brand_rules_contain_no_product_facts(self):
        text = (ROOT / "data" / "brand_rules.json").read_text(encoding="utf-8")
        self.assertIsNone(self.FORBIDDEN.search(text))
        self.assertNotRegex(text, r"\$\d|\d+%")
