import unittest

from app.facts import build_fact_sheet
from app.feed import load_feed
from app.validator import count_emoji, validate_caption
from tests.helpers import WEEK1, WEEK2, load_brand_context, product, synthetic_feed

RULES = load_brand_context().rules


def codes(violations):
    return [v.code for v in violations]


class CommercialFacts(unittest.TestCase):
    """Synthetic feed: alpha-ridge $12 with ALPHA5 (5%), beta-valley $14 (new,
    no promo), gamma-coast $16 sold out with GAMMA30 (30%)."""

    def setUp(self):
        self.facts = build_fact_sheet(synthetic_feed())

    def check(self, caption, *ids):
        return validate_caption(caption, list(ids), self.facts, RULES)

    def test_fully_supported_caption_passes(self):
        caption = "Alpha Ridge is back. $12 a bag, 5% off with ALPHA5 until the 10th. Grab one."
        self.assertEqual(self.check(caption, "alpha-ridge"), [])

    def test_unsupported_price_is_rejected(self):
        self.assertIn("unsupported_price", codes(self.check("Alpha Ridge, now $9.", "alpha-ridge")))
        self.assertIn("unsupported_price", codes(self.check("Alpha Ridge for 9 dollars.", "alpha-ridge")))

    def test_unsupported_discount_is_rejected(self):
        self.assertIn("unsupported_discount", codes(self.check("Alpha Ridge, 20% off this week.", "alpha-ridge")))
        self.assertIn("unsupported_discount", codes(self.check("Alpha Ridge, 20 percent off.", "alpha-ridge")))

    def test_discount_on_product_without_promo_is_rejected(self):
        self.assertIn("unsupported_discount", codes(self.check("Beta Valley, 5% off.", "beta-valley")))

    def test_invented_promo_code_is_rejected(self):
        self.assertIn("unsupported_promo_code", codes(self.check("Alpha Ridge. Use code NOMAD20.", "alpha-ridge")))
        self.assertIn("unsupported_promo_code", codes(self.check("Alpha Ridge. Use code: 'freshbeans' at checkout.", "alpha-ridge")))
        self.assertIn("unsupported_promo_code", codes(self.check("Alpha Ridge. Use code FRESHBEANS.", "alpha-ridge")))
        self.assertIn("unsupported_promo_code", codes(self.check("Alpha Ridge with SUPER_DEAL.", "alpha-ridge")))

    def test_prose_after_the_word_code_is_not_a_code(self):
        # A real code followed by ordinary words must not burn a retry.
        self.assertEqual(self.check("Alpha Ridge, 5% off with ALPHA5. The code expires on the 10th.", "alpha-ridge"), [])
        self.assertEqual(self.check("Alpha Ridge, $12. Code in bio.", "alpha-ridge"), [])

    def test_price_of_another_product_is_rejected(self):
        # $14 is a real price in the feed, but it belongs to Beta Valley.
        violations = self.check("Alpha Ridge, $14 a bag.", "alpha-ridge")
        self.assertIn("unsupported_price", codes(violations))
        self.assertIn("$12", violations[0].message)

    def test_declared_product_cannot_lend_its_facts_to_the_product_named_in_text(self):
        # The model declares Alpha Ridge but writes about Beta Valley: only
        # Beta Valley's facts may appear, whatever was declared.
        self.assertIn("unsupported_price", codes(self.check("Beta Valley, $12 a bag.", "alpha-ridge")))
        self.assertIn("unsupported_discount", codes(self.check("Beta Valley, 5% off.", "alpha-ridge")))
        self.assertEqual(self.check("Beta Valley, $14 a bag.", "alpha-ridge"), [])

    def test_declared_product_is_used_when_the_text_names_nothing(self):
        self.assertEqual(self.check("This week's roast: $12, 5% off with ALPHA5.", "alpha-ridge"), [])
        self.assertIn("unsupported_price", codes(self.check("This week's roast: $14.", "alpha-ridge")))

    def test_promo_code_of_another_product_is_rejected(self):
        feed = synthetic_feed(products=[
            product("a", name="A — One", origin="One, Aland", price_usd=10,
                    promo_code="ONE10", promo_discount_percent=10),
            product("b", name="B — Two", origin="Two, Bland", price_usd=11,
                    promo_code="TWO20", promo_discount_percent=20),
        ])
        facts = build_fact_sheet(feed)
        self.assertIn("unsupported_promo_code", codes(validate_caption("B Two: code ONE10.", ["b"], facts, RULES)))
        self.assertIn("unsupported_discount", codes(validate_caption("B Two, 10% off.", ["b"], facts, RULES)))
        self.assertEqual(validate_caption("B Two, 20% off with TWO20.", ["b"], facts, RULES), [])

    def test_code_embedding_a_product_name_does_not_count_as_naming_it(self):
        # "ALPHA5" contains "alpha" but is a code, not a mention of Alpha Ridge,
        # so it cannot bring Alpha Ridge's facts into a Beta Valley caption.
        self.assertIn("unsupported_promo_code", codes(self.check("Beta Valley, $14. Use code ALPHA5.", "beta-valley")))

    def test_sold_out_product_cannot_be_featured_even_via_declaration(self):
        violations = self.check("Big flash sale on the Coast roast.", "gamma-coast")
        self.assertIn("sold_out_product", codes(violations))

    def test_sold_out_product_named_in_text_is_caught_without_declaration(self):
        violations = self.check("Gamma Coast is 30% off with GAMMA30.", "alpha-ridge")
        self.assertIn("sold_out_product", codes(violations))
        self.assertIn("unsupported_discount", codes(violations))
        self.assertIn("unsupported_promo_code", codes(violations))

    def test_sold_out_products_promo_code_is_rejected_even_without_naming_it(self):
        # The code is real and "valid-looking" but attached to a sold-out product.
        self.assertIn("unsupported_promo_code", codes(self.check("30% off with GAMMA30 this week.", "alpha-ridge")))

    def test_expired_promo_is_rejected(self):
        from datetime import date

        facts = build_fact_sheet(synthetic_feed(), as_of=date(2030, 2, 1))
        violations = validate_caption("Alpha Ridge, 5% off with ALPHA5.", ["alpha-ridge"], facts, RULES)
        self.assertIn("unsupported_discount", codes(violations))
        self.assertIn("unsupported_promo_code", codes(violations))

    def test_price_with_no_identifiable_product_is_rejected(self):
        self.assertIn("unsupported_price", codes(self.check("Good coffee, $12.")))

    def test_unknown_product_id_is_rejected(self):
        self.assertIn("unknown_product", codes(self.check("Nice roast.", "does-not-exist")))

    def test_new_claim_requires_new_this_week(self):
        self.assertIn("unsupported_new_claim", codes(self.check("New roast: Alpha Ridge.", "alpha-ridge")))
        self.assertEqual(self.check("New roast: Beta Valley.", "beta-valley"), [])

    def test_empty_caption_is_rejected(self):
        self.assertEqual(codes(self.check("   ")), ["empty_caption"])

    def test_plain_numbers_are_not_policed(self):
        # units_left and similar bare numbers come from the feed; only
        # prices, percentages and codes are treated as commercial claims.
        self.assertEqual(self.check("Alpha Ridge: 50 bags left.", "alpha-ridge"), [])


class SuppliedFixtureAdversarialCases(unittest.TestCase):
    def test_week1_kenya_promo_code_is_rejected(self):
        facts = build_fact_sheet(load_feed(WEEK1))
        violations = validate_caption("Kenya Nyeri, 10% off with NYERI10.", ["kenya-nyeri"], facts, RULES)
        self.assertEqual(set(codes(violations)), {"sold_out_product", "unsupported_discount", "unsupported_promo_code"})

    def test_week2_guatemala_flash_code_is_rejected(self):
        facts = build_fact_sheet(load_feed(WEEK2))
        violations = validate_caption("Flash sale: 25% off with HUEHUE_FLASH.", ["costa-rica-tarrazu"], facts, RULES)
        self.assertIn("unsupported_promo_code", codes(violations))
        self.assertIn("unsupported_discount", codes(violations))

    def test_week2_tarrazu_caption_passes(self):
        facts = build_fact_sheet(load_feed(WEEK2))
        caption = "New this week: Costa Rica Tarrazú. Honey, red apple, silky. $20, or 10% off with TARRAZU10 through the 22nd."
        self.assertEqual(validate_caption(caption, ["costa-rica-tarrazu"], facts, RULES), [])


class BrandRules(unittest.TestCase):
    def setUp(self):
        self.facts = build_fact_sheet(synthetic_feed())

    def check(self, caption):
        return codes(validate_caption(caption, ["alpha-ridge"], self.facts, RULES))

    def test_banned_phrases(self):
        self.assertIn("banned_phrase", self.check("Alpha Ridge will elevate your routine."))
        self.assertIn("banned_phrase", self.check("A Life-Changing cup."))

    def test_health_claims(self):
        self.assertIn("health_claim", self.check("Alpha Ridge: all the energy you need."))
        self.assertIn("health_claim", self.check("Packed with antioxidants."))

    def test_exclamation_limit(self):
        self.assertNotIn("too_many_exclamation_marks", self.check("Alpha Ridge is back!"))
        self.assertIn("too_many_exclamation_marks", self.check("Alpha Ridge is back! Grab a bag!"))

    def test_emoji_limit(self):
        self.assertNotIn("too_many_emoji", self.check("Alpha Ridge is back ☕"))
        self.assertIn("too_many_emoji", self.check("Alpha Ridge is back ☕🔥"))

    def test_length_limit(self):
        self.assertIn("too_long", self.check("Alpha Ridge. " + "Good coffee. " * 80))

    def test_count_emoji(self):
        self.assertEqual(count_emoji("plain text — with dashes"), 0)
        self.assertEqual(count_emoji("☕"), 1)
        self.assertEqual(count_emoji("🔥🔥"), 2)
