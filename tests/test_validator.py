import unittest
from datetime import date

from app.feed import build_fact_sheet, load_feed
from app.validator import mentioned_product_ids, validate_caption
from tests.helpers import WEEK1, WEEK2, product, synthetic_feed


def codes(violations):
    return [v.code for v in violations]


class CommercialFacts(unittest.TestCase):
    """Synthetic feed: alpha-ridge $12 with ALPHA5 (5%), beta-valley $14 (new, no promo),
    gamma-coast $16 sold out with GAMMA30 (30%)."""

    def setUp(self):
        self.facts = build_fact_sheet(synthetic_feed())

    def check(self, caption, *ids):
        return codes(validate_caption(caption, list(ids), self.facts))

    def test_supported_caption_passes(self):
        self.assertEqual(self.check("Alpha Ridge is back. $12 a bag, 5% off with ALPHA5 until the 10th.", "alpha-ridge"), [])
        self.assertEqual(self.check("Alpha Ridge: $12.00 — 15 bags left, code ALPHA5!", "alpha-ridge"), [])

    def test_invented_price_discount_or_code_is_rejected(self):
        self.assertIn("unsupported_price", self.check("Alpha Ridge, now $9.", "alpha-ridge"))
        self.assertIn("unsupported_price", self.check("Alpha Ridge for 9 dollars.", "alpha-ridge"))
        self.assertIn("unsupported_discount", self.check("Alpha Ridge, 20% off.", "alpha-ridge"))
        self.assertIn("unsupported_discount", self.check("Beta Valley, 5% off.", "beta-valley"))  # no promo at all
        self.assertIn("unsupported_promo_code", self.check("Alpha Ridge. Use code NOMAD20.", "alpha-ridge"))

    def test_real_fact_attached_to_the_wrong_product_is_rejected(self):
        self.assertIn("unsupported_price", self.check("Alpha Ridge, $14 a bag.", "alpha-ridge"))  # $14 is Beta's
        # The text names Beta Valley, so declaring Alpha Ridge cannot lend it Alpha's price or discount.
        self.assertIn("unsupported_price", self.check("Beta Valley, $12 a bag.", "alpha-ridge"))
        self.assertIn("unsupported_discount", self.check("Beta Valley, 5% off.", "alpha-ridge"))
        # "ALPHA5" contains "alpha" but is a code, not a mention of Alpha Ridge.
        self.assertIn("unsupported_promo_code", self.check("Beta Valley, $14. Use code ALPHA5.", "beta-valley"))
        # When the text names nothing, the declared product is the reference.
        self.assertEqual(self.check("This week's roast: $12, 5% off with ALPHA5.", "alpha-ridge"), [])
        self.assertIn("unsupported_price", self.check("Good coffee, $12."))  # no product at all

    def test_sold_out_product_is_rejected_however_it_gets_in(self):
        self.assertIn("sold_out_product", self.check("Big flash sale on the Coast roast.", "gamma-coast"))
        self.assertIn("sold_out_product", self.check("Gamma Coast is 30% off with GAMMA30.", "alpha-ridge"))
        self.assertIn("unsupported_promo_code", self.check("30% off with GAMMA30 this week.", "alpha-ridge"))

    def test_expired_promo_is_rejected(self):
        facts = build_fact_sheet(synthetic_feed(), as_of=date(2030, 2, 1))
        found = codes(validate_caption("Alpha Ridge, 5% off with ALPHA5.", ["alpha-ridge"], facts))
        self.assertIn("unsupported_discount", found)
        self.assertIn("unsupported_promo_code", found)

    def test_new_claim_needs_new_this_week(self):
        self.assertIn("unsupported_new_claim", self.check("New roast: Alpha Ridge.", "alpha-ridge"))
        self.assertEqual(self.check("New roast: Beta Valley.", "beta-valley"), [])


class SuppliedFixtures(unittest.TestCase):
    def test_week1_kenya_and_week2_guatemala_promos_are_rejected(self):
        week1 = build_fact_sheet(load_feed(WEEK1))
        found = codes(validate_caption("Kenya Nyeri, 10% off with NYERI10.", ["kenya-nyeri"], week1))
        self.assertEqual(set(found), {"sold_out_product", "unsupported_discount", "unsupported_promo_code"})
        week2 = build_fact_sheet(load_feed(WEEK2))
        found = codes(validate_caption("Flash sale: 25% off with HUEHUE_FLASH.", ["costa-rica-tarrazu"], week2))
        self.assertEqual(set(found), {"unsupported_discount", "unsupported_promo_code"})

    def test_week2_tarrazu_caption_passes(self):
        week2 = build_fact_sheet(load_feed(WEEK2))
        caption = "New this week: Costa Rica Tarrazú. Honey, red apple, silky. $20, or 10% off with TARRAZU10."
        self.assertEqual(validate_caption(caption, ["costa-rica-tarrazu"], week2), [])


class BrandRules(unittest.TestCase):
    def check(self, caption):
        return codes(validate_caption(caption, ["alpha-ridge"], build_fact_sheet(synthetic_feed())))

    def test_mechanical_brand_rules(self):
        self.assertIn("banned_phrase", self.check("Alpha Ridge will elevate your routine."))
        self.assertIn("health_claim", self.check("Alpha Ridge: all the energy you need."))
        self.assertIn("too_many_exclamation_marks", self.check("Alpha Ridge is back! Grab a bag!"))
        self.assertIn("too_many_emoji", self.check("Alpha Ridge is back ☕🔥"))
        self.assertEqual(self.check("Alpha Ridge is back! ☕"), [])


class ProductNameMatching(unittest.TestCase):
    def test_names_origins_and_adjective_forms(self):
        feed = load_feed(WEEK1)
        self.assertEqual(mentioned_product_ids("Push the Guatemala flash sale", feed.products), ["guatemala-huehue"])
        self.assertEqual(mentioned_product_ids("a washed Ethiopian, jasmine up front", feed.products), ["ethiopia-guji"])
        self.assertEqual(mentioned_product_ids("Announce this week's new roast.", feed.products), [])
        self.assertEqual(mentioned_product_ids("the TARRAZÚ is lovely", load_feed(WEEK2).products), ["costa-rica-tarrazu"])

    def test_words_shared_between_products_do_not_identify_either(self):
        feed = synthetic_feed([
            product("one", name="Blend — House (Washed)", origin="House, Northland"),
            product("two", name="Blend — Reserve", origin="Reserve, Southland"),
        ])
        self.assertEqual(mentioned_product_ids("A great blend, washed", feed.products), [])
        self.assertEqual(mentioned_product_ids("the Reserve", feed.products), ["two"])
