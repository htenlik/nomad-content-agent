import json
import tempfile
import unittest
from datetime import date

from app.feed import FeedError, build_fact_sheet, load_feed, parse_feed
from tests.helpers import WEEK1, WEEK2, product, synthetic_feed


class Loading(unittest.TestCase):
    def test_both_supplied_snapshots_load(self):
        self.assertEqual(load_feed(WEEK1).snapshot_date, date(2026, 9, 8))
        self.assertEqual(load_feed(WEEK2).snapshot_date, date(2026, 9, 15))
        guji = next(p for p in load_feed(WEEK1).products if p.id == "ethiopia-guji")
        self.assertEqual((guji.promo.code, guji.promo.discount_percent, guji.promo.expires), ("GUJI15", 15, date(2026, 9, 14)))

    def test_missing_file_and_invalid_json(self):
        with self.assertRaises(FeedError):
            load_feed("data/does_not_exist.json")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{ not json")
        with self.assertRaises(FeedError):
            load_feed(handle.name)

    def test_malformed_feed_is_rejected(self):
        for raw in (
            {"products": [product("p")]},                                          # no snapshot date
            {"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": []},     # nothing in it
            {"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": [{"id": "p"}]},  # fields missing
            {"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": [product("p", price_usd="19")]},
        ):
            with self.assertRaises(FeedError, msg=json.dumps(raw)):
                parse_feed(raw)


class Availability(unittest.TestCase):
    def _product(self, **fields):
        return parse_feed({"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": [product("p", **fields)]}).products[0]

    def test_stock_status_and_units_decide_availability(self):
        self.assertTrue(self._product(stock_status="in_stock", units_left=5).is_available)
        self.assertTrue(self._product(stock_status="low_stock", units_left=1).is_available)
        self.assertFalse(self._product(stock_status="in_stock", units_left=0).is_available)
        self.assertFalse(self._product(stock_status="backorder", units_left=10).is_available)

    def test_sold_out_stays_unavailable_even_with_a_valid_looking_promo(self):
        p = self._product(stock_status="sold_out", units_left=0, promo_code="DEAL10",
                          promo_discount_percent=10, promo_expires="2099-01-01")
        self.assertFalse(p.is_available)


class FactSheet(unittest.TestCase):
    def test_supplied_snapshots_split_by_stock_not_promo(self):
        week1, week2 = build_fact_sheet(load_feed(WEEK1)), build_fact_sheet(load_feed(WEEK2))
        self.assertEqual({p.id for p in week1.sold_out}, {"kenya-nyeri"})
        self.assertEqual({p.id for p in week2.sold_out}, {"peru-cajamarca", "guatemala-huehue"})
        self.assertEqual([p.id for p in week1.available if p.new_this_week], ["ethiopia-guji"])
        self.assertEqual([p.id for p in week2.available if p.new_this_week], ["costa-rica-tarrazu"])

    def test_sold_out_promo_codes_never_reach_the_prompt(self):
        for path, code in ((WEEK1, "NYERI10"), (WEEK2, "HUEHUE_FLASH")):
            facts = build_fact_sheet(load_feed(path))
            self.assertNotIn(code, json.dumps(facts.for_prompt()))
            self.assertIn(code, facts.all_promo_codes)  # but the validator still knows it exists
        for entry in facts.for_prompt()["sold_out_products_do_not_mention"]:
            self.assertEqual(set(entry), {"id", "name"})  # no price, no promo

    def test_promo_expiry_is_judged_against_the_snapshot_date(self):
        # The synthetic snapshot is in 2030: whatever today is, its promo is active by default.
        self.assertEqual(build_fact_sheet(synthetic_feed()).get_available("alpha-ridge").promo.code, "ALPHA5")
        later = build_fact_sheet(synthetic_feed(), as_of=date(2030, 1, 11))
        self.assertIsNone(later.get_available("alpha-ridge").promo)
        self.assertEqual(later.get_available("alpha-ridge").price_usd, 12)  # the price survives
