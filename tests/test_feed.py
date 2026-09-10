import unittest
from datetime import date

from app.feed import FeedError, load_feed, parse_feed
from tests.helpers import WEEK1, WEEK2, product, raw_feed


class LoadSuppliedFeeds(unittest.TestCase):
    def test_both_supplied_snapshots_parse(self):
        for path in (WEEK1, WEEK2):
            feed = load_feed(path)
            self.assertGreater(len(feed.products), 0)
            self.assertEqual(feed.source, str(path))
            self.assertEqual({p.id for p in feed.products}, {p["id"] for p in raw_feed(path)["products"]})

    def test_snapshot_date_is_taken_from_the_feed(self):
        self.assertEqual(load_feed(WEEK1).snapshot_date, date(2026, 9, 8))
        self.assertEqual(load_feed(WEEK2).snapshot_date, date(2026, 9, 15))

    def test_promo_fields_are_parsed_into_a_promotion(self):
        feed = load_feed(WEEK1)
        guji = feed.get("ethiopia-guji")
        self.assertIsNotNone(guji.promo)
        self.assertEqual(guji.promo.code, "GUJI15")
        self.assertEqual(guji.promo.discount_percent, 15)
        self.assertEqual(guji.promo.expires, date(2026, 9, 14))
        self.assertIsNone(feed.get("colombia-huila").promo)


class MalformedInput(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(FeedError) as ctx:
            load_feed("data/does_not_exist.json")
        self.assertIn("not found", str(ctx.exception))

    def test_invalid_json(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{ not json")
        with self.assertRaises(FeedError) as ctx:
            load_feed(handle.name)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_products_must_be_a_non_empty_list(self):
        for bad in ({"snapshot_taken_at": "2026-09-08T06:00:00+03:00"},
                    {"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": []},
                    {"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": {}}):
            with self.assertRaises(FeedError):
                parse_feed(bad)

    def test_missing_required_field_names_the_field(self):
        raw = raw_feed()
        del raw["products"][0]["stock_status"]
        with self.assertRaises(FeedError) as ctx:
            parse_feed(raw)
        self.assertIn("stock_status", str(ctx.exception))

    def test_bad_types_are_rejected(self):
        cases = [
            {"price_usd": "19"},
            {"price_usd": -1},
            {"units_left": "14"},
            {"units_left": 2.5},
            {"new_this_week": "yes"},
            {"promo_code": "X", "promo_discount_percent": 150},
            {"promo_code": "X", "promo_expires": "next week"},
            {"promo_code": ""},
        ]
        for fields in cases:
            raw = {"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": [product("p", **fields)]}
            with self.assertRaises(FeedError, msg=fields):
                parse_feed(raw)

    def test_snapshot_timestamp_is_required(self):
        with self.assertRaises(FeedError):
            parse_feed({"products": [product("p")]})

    def test_duplicate_ids_are_rejected(self):
        raw = {"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": [product("p"), product("p")]}
        with self.assertRaises(FeedError):
            parse_feed(raw)


class Availability(unittest.TestCase):
    def _product(self, **fields):
        raw = {"snapshot_taken_at": "2026-09-08T06:00:00+03:00", "products": [product("p", **fields)]}
        return parse_feed(raw).products[0]

    def test_in_stock_and_low_stock_are_available(self):
        self.assertTrue(self._product(stock_status="in_stock", units_left=5).is_available)
        self.assertTrue(self._product(stock_status="low_stock", units_left=1).is_available)

    def test_sold_out_is_unavailable_even_with_a_promo(self):
        p = self._product(stock_status="sold_out", units_left=0, promo_code="DEAL10",
                          promo_discount_percent=10, promo_expires="2099-01-01")
        self.assertIsNotNone(p.promo)
        self.assertFalse(p.is_available)

    def test_zero_units_overrides_an_optimistic_status(self):
        self.assertFalse(self._product(stock_status="in_stock", units_left=0).is_available)

    def test_unknown_status_fails_closed(self):
        self.assertFalse(self._product(stock_status="backorder", units_left=10).is_available)
