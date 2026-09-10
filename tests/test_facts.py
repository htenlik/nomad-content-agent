import json
import unittest
from datetime import date

from app.facts import build_fact_sheet, mentioned_product_ids, product_keywords
from app.feed import load_feed
from tests.helpers import WEEK1, WEEK2, product, synthetic_feed


class SuppliedSnapshots(unittest.TestCase):
    """Assertions about the two supplied fixtures. These are the only tests
    that know week-specific facts, and they exist to pin the expected
    behaviour of the fixtures, not to drive the application."""

    def test_available_and_sold_out_are_split_by_stock_not_promo(self):
        week1 = build_fact_sheet(load_feed(WEEK1))
        self.assertEqual({p.id for p in week1.unavailable}, {"kenya-nyeri"})
        self.assertNotIn("kenya-nyeri", {p.id for p in week1.available})

        week2 = build_fact_sheet(load_feed(WEEK2))
        self.assertEqual({p.id for p in week2.unavailable}, {"peru-cajamarca", "guatemala-huehue"})

    def test_new_this_week_differs_between_snapshots(self):
        new1 = [p.id for p in build_fact_sheet(load_feed(WEEK1)).new_this_week]
        new2 = [p.id for p in build_fact_sheet(load_feed(WEEK2)).new_this_week]
        self.assertEqual(new1, ["ethiopia-guji"])
        self.assertEqual(new2, ["costa-rica-tarrazu"])

    def test_sold_out_promo_codes_never_reach_the_prompt(self):
        for path, withheld in ((WEEK1, "NYERI10"), (WEEK2, "HUEHUE_FLASH")):
            prompt_json = json.dumps(build_fact_sheet(load_feed(path)).to_prompt_dict())
            self.assertNotIn(withheld, prompt_json)
            # ...but the validator still knows the code exists.
            self.assertIn(withheld, build_fact_sheet(load_feed(path)).known_promo_codes)

    def test_prompt_dict_withholds_prices_of_sold_out_products(self):
        facts = build_fact_sheet(load_feed(WEEK2)).to_prompt_dict()
        for entry in facts["sold_out_products_do_not_mention"]:
            self.assertEqual(set(entry), {"id", "name"})


class PromotionExpiry(unittest.TestCase):
    def test_promo_validity_defaults_to_snapshot_date_not_wall_clock(self):
        # The synthetic snapshot is in 2030; its promo expires days later.
        # Whatever today's date is, the promo must count as active.
        facts = build_fact_sheet(synthetic_feed())
        self.assertEqual(facts.as_of, date(2030, 1, 6))
        self.assertEqual(facts.get_available("alpha-ridge").promo.code, "ALPHA5")

    def test_expired_promo_is_dropped_when_as_of_is_later(self):
        facts = build_fact_sheet(synthetic_feed(), as_of=date(2030, 1, 11))
        self.assertIsNone(facts.get_available("alpha-ridge").promo)
        self.assertEqual(facts.get_available("alpha-ridge").price_usd, 12)  # price survives

    def test_promo_expiring_today_is_still_active(self):
        facts = build_fact_sheet(synthetic_feed(), as_of=date(2030, 1, 10))
        self.assertIsNotNone(facts.get_available("alpha-ridge").promo)

    def test_promo_without_expiry_never_expires(self):
        feed = synthetic_feed(products=[product("p", promo_code="FOREVER", promo_discount_percent=5)])
        facts = build_fact_sheet(feed, as_of=date(2099, 12, 31))
        self.assertEqual(facts.get_available("p").promo.code, "FOREVER")


class MentionDetection(unittest.TestCase):
    def test_keywords_are_distinctive_words_from_name_and_origin(self):
        feed = synthetic_feed()
        keywords = product_keywords(feed.products)
        self.assertIn("ridge", keywords["alpha-ridge"])
        self.assertIn("alphaland", keywords["alpha-ridge"])
        self.assertNotIn("alpha", keywords["beta-valley"])

    def test_parenthesised_process_words_are_not_identifiers(self):
        feed = synthetic_feed(products=[
            product("one", name="Alpha — Ridge (Washed)", origin="Ridge, Alphaland"),
            product("two", name="Beta — Valley", origin="Valley, Betania"),
        ])
        self.assertNotIn("washed", product_keywords(feed.products)["one"])
        self.assertEqual(mentioned_product_ids("a washed Beta Valley", feed.products), ["two"])

    def test_shared_words_are_not_used_as_identifiers(self):
        feed = synthetic_feed(products=[
            product("one", name="Blend — House", origin="House, Northland"),
            product("two", name="Blend — Reserve", origin="Reserve, Southland"),
        ])
        keywords = product_keywords(feed.products)
        self.assertNotIn("blend", keywords["one"])
        self.assertNotIn("blend", keywords["two"])
        self.assertEqual(mentioned_product_ids("A great blend", feed.products), [])

    def test_matching_ignores_case_and_accents(self):
        feed = synthetic_feed(products=[product("cr", name="Costa Rica — Tarrazú", origin="Tarrazú, Costa Rica")])
        self.assertEqual(mentioned_product_ids("the tarrazu is lovely", feed.products), ["cr"])
        self.assertEqual(mentioned_product_ids("TARRAZÚ", feed.products), ["cr"])

    def test_adjective_forms_are_detected(self):
        feed = load_feed(WEEK1)
        self.assertEqual(mentioned_product_ids("a washed Ethiopian, jasmine up front", feed.products), ["ethiopia-guji"])
        self.assertEqual(mentioned_product_ids("the Kenyan is back", feed.products), ["kenya-nyeri"])
        self.assertEqual(mentioned_product_ids("a Peruvian with stone fruit", feed.products), ["peru-cajamarca"])

    def test_brief_naming_a_product_is_detected(self):
        feed = load_feed(WEEK2)
        self.assertEqual(mentioned_product_ids("Push the Guatemala flash sale", feed.products), ["guatemala-huehue"])
        self.assertEqual(mentioned_product_ids("Announce this week's new roast.", feed.products), [])
