import json
import unittest

from app.agent import CaptionGenerationError, CaptionRefused, generate_caption, parse_model_response
from app.feed import load_feed
from tests.helpers import WEEK1, WEEK2, FakeLLM, load_brand_context, model_json, product, synthetic_feed

BRAND = load_brand_context()


class HappyPath(unittest.TestCase):
    def test_valid_first_draft_is_returned_unchanged(self):
        llm = FakeLLM(model_json("Beta Valley just landed. Grab a bag.", "beta-valley"))
        result = generate_caption("announce the new roast", synthetic_feed(), BRAND, llm)
        self.assertEqual(result.caption, "Beta Valley just landed. Grab a bag.")
        self.assertEqual(result.featured_product_ids, ["beta-valley"])
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.notes, [])

    def test_prompt_is_built_from_brand_facts_and_brief(self):
        llm = FakeLLM(model_json("Beta Valley just landed.", "beta-valley"))
        generate_caption("announce the new roast", synthetic_feed(), BRAND, llm)
        system, user = llm.calls[0]
        self.assertIn("=== BRAND CONTEXT", system)
        self.assertIn("Nomad Roasters", system)
        self.assertIn("=== CURRENT FACTS", user)
        self.assertIn('"beta-valley"', user)
        self.assertIn("=== CONTENT BRIEF", user)
        self.assertIn("announce the new roast", user)
        self.assertNotIn("GAMMA30", user)  # sold-out promo withheld
        self.assertNotIn("CORRECTIONS", user)

    def test_model_output_wrapped_in_code_fences_is_accepted(self):
        llm = FakeLLM("```json\n" + model_json("Beta Valley just landed.", "beta-valley") + "\n```")
        result = generate_caption("new roast", synthetic_feed(), BRAND, llm)
        self.assertEqual(result.caption, "Beta Valley just landed.")


class RetryBehaviour(unittest.TestCase):
    def test_invalid_draft_is_retried_with_feedback(self):
        llm = FakeLLM(
            model_json("Alpha Ridge, 50% off with code ALPHA50.", "alpha-ridge"),
            model_json("Alpha Ridge, 5% off with ALPHA5.", "alpha-ridge"),
        )
        result = generate_caption("push the alpha promo", synthetic_feed(), BRAND, llm)
        self.assertEqual(result.caption, "Alpha Ridge, 5% off with ALPHA5.")
        self.assertEqual(len(result.attempts), 2)
        self.assertTrue(result.attempts[0].violations)
        second_user_prompt = llm.calls[1][1]
        self.assertIn("=== CORRECTIONS", second_user_prompt)
        self.assertIn("50%", second_user_prompt)
        self.assertIn("ALPHA50", second_user_prompt)

    def test_gives_up_after_max_attempts_and_never_returns_bad_copy(self):
        bad = model_json("Alpha Ridge, only $1 today.", "alpha-ridge")
        llm = FakeLLM(bad, bad, bad)
        with self.assertRaises(CaptionGenerationError) as ctx:
            generate_caption("alpha deal", synthetic_feed(), BRAND, llm, max_attempts=3)
        self.assertEqual(len(ctx.exception.attempts), 3)
        self.assertEqual(len(llm.calls), 3)
        self.assertIn("$1", str(ctx.exception))

    def test_max_attempts_one_means_no_retry(self):
        llm = FakeLLM(model_json("Alpha Ridge, only $1 today.", "alpha-ridge"))
        with self.assertRaises(CaptionGenerationError):
            generate_caption("alpha deal", synthetic_feed(), BRAND, llm, max_attempts=1)
        self.assertEqual(len(llm.calls), 1)

    def test_unparseable_response_counts_as_a_failed_attempt(self):
        llm = FakeLLM("Sure! Here is a caption: Alpha Ridge is back.", model_json("Alpha Ridge is back.", "alpha-ridge"))
        result = generate_caption("alpha", synthetic_feed(), BRAND, llm)
        self.assertEqual(result.attempts[0].violations[0].code, "bad_response_format")
        self.assertEqual(result.caption, "Alpha Ridge is back.")

    def test_sold_out_draft_is_rejected_and_corrected(self):
        llm = FakeLLM(
            model_json("Gamma Coast flash sale: 30% off with GAMMA30.", "gamma-coast"),
            model_json("Alpha Ridge, 5% off with ALPHA5.", "alpha-ridge"),
        )
        result = generate_caption("push a flash sale", synthetic_feed(), BRAND, llm)
        self.assertEqual(result.featured_product_ids, ["alpha-ridge"])
        self.assertIn("sold_out", llm.calls[1][1])


class BriefPreflight(unittest.TestCase):
    def test_brief_about_only_a_sold_out_product_is_refused_before_calling_the_model(self):
        llm = FakeLLM()
        with self.assertRaises(CaptionRefused) as ctx:
            generate_caption("Promote the Gamma Coast flash sale", synthetic_feed(), BRAND, llm)
        self.assertEqual(llm.calls, [])
        self.assertIn("Gamma — Coast", ctx.exception.reason)
        self.assertIn("Alpha — Ridge", ctx.exception.alternatives)

    def test_week2_guatemala_flash_sale_is_refused(self):
        llm = FakeLLM()
        with self.assertRaises(CaptionRefused):
            generate_caption("Push the Guatemala Huehuetenango flash sale", load_feed(WEEK2), BRAND, llm)
        self.assertEqual(llm.calls, [])

    def test_week1_kenya_is_refused_despite_its_promo_code(self):
        with self.assertRaises(CaptionRefused):
            generate_caption("Share the NYERI10 code for the Kenya", load_feed(WEEK1), BRAND, FakeLLM())

    def test_mixed_brief_proceeds_with_a_note(self):
        llm = FakeLLM(model_json("Alpha Ridge, $12.", "alpha-ridge"))
        result = generate_caption("Post about Alpha Ridge and Gamma Coast", synthetic_feed(), BRAND, llm)
        self.assertEqual(result.featured_product_ids, ["alpha-ridge"])
        self.assertEqual(len(result.notes), 1)
        self.assertIn("Gamma — Coast", result.notes[0])

    def test_feed_with_nothing_available_is_refused(self):
        feed = synthetic_feed(products=[product("only", stock_status="sold_out", units_left=0)])
        with self.assertRaises(CaptionRefused):
            generate_caption("anything", feed, BRAND, FakeLLM())

    def test_empty_brief_is_an_error(self):
        with self.assertRaises(ValueError):
            generate_caption("   ", synthetic_feed(), BRAND, FakeLLM())


class ResponseParsing(unittest.TestCase):
    def test_parses_plain_json(self):
        self.assertEqual(parse_model_response(model_json("hi", "a", "b")), ("hi", ["a", "b"]))

    def test_rejects_malformed_responses(self):
        for raw in ("no json here", "{bad json}", json.dumps({"caption": ""}),
                    json.dumps({"caption": "x", "featured_product_ids": "a"}), "[1, 2]"):
            with self.assertRaises(ValueError, msg=raw):
                parse_model_response(raw)
