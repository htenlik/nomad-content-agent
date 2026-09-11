import unittest

from app.agent import CaptionGenerationError, CaptionRefused, generate_caption
from app.feed import load_feed
from tests.helpers import BRAND_KIT, WEEK1, WEEK2, FakeLLM, model_json, synthetic_feed


class Generation(unittest.TestCase):
    def test_valid_first_draft_is_returned_and_prompt_has_the_right_parts(self):
        llm = FakeLLM(model_json("Beta Valley just landed. Grab a bag.", "beta-valley"))
        result = generate_caption("announce the new roast", synthetic_feed(), BRAND_KIT, llm)
        self.assertEqual(result.caption, "Beta Valley just landed. Grab a bag.")
        self.assertEqual(result.featured_product_ids, ["beta-valley"])
        system, user = llm.calls[0]
        self.assertIn("=== BRAND GUIDE", system)
        self.assertIn("Nomad Roasters", system)
        self.assertIn("=== CURRENT FACTS", user)
        self.assertIn("announce the new roast", user)
        self.assertNotIn("GAMMA30", user)  # the sold-out product's promo is withheld

    def test_code_fences_around_the_json_are_tolerated(self):
        llm = FakeLLM("```json\n" + model_json("Beta Valley just landed.", "beta-valley") + "\n```")
        self.assertEqual(generate_caption("new roast", synthetic_feed(), BRAND_KIT, llm).caption, "Beta Valley just landed.")

    def test_invalid_draft_is_retried_with_the_violations_as_feedback(self):
        llm = FakeLLM(
            model_json("Gamma Coast flash sale: 50% off with code ALPHA50.", "gamma-coast"),
            model_json("Alpha Ridge, 5% off with ALPHA5.", "alpha-ridge"),
        )
        result = generate_caption("push a flash sale", synthetic_feed(), BRAND_KIT, llm)
        self.assertEqual(result.caption, "Alpha Ridge, 5% off with ALPHA5.")
        self.assertEqual(len(result.attempts), 2)
        second_prompt = llm.calls[1][1]
        for expected in ("=== CORRECTIONS", "sold_out", "50%", "ALPHA50"):
            self.assertIn(expected, second_prompt)

    def test_unparseable_response_counts_as_a_failed_attempt(self):
        llm = FakeLLM("Sure! Here is a caption: Alpha Ridge is back.", model_json("Alpha Ridge is back.", "alpha-ridge"))
        result = generate_caption("alpha", synthetic_feed(), BRAND_KIT, llm)
        self.assertEqual(result.attempts[0].violations[0].code, "bad_response_format")
        self.assertEqual(result.caption, "Alpha Ridge is back.")

    def test_gives_up_after_max_attempts_and_never_returns_bad_copy(self):
        bad = model_json("Alpha Ridge, only $1 today.", "alpha-ridge")
        llm = FakeLLM(bad, bad, bad)
        with self.assertRaises(CaptionGenerationError) as ctx:
            generate_caption("alpha deal", synthetic_feed(), BRAND_KIT, llm)
        self.assertEqual(len(llm.calls), 3)
        self.assertIn("$1", str(ctx.exception))


class SoldOutBriefs(unittest.TestCase):
    def test_brief_about_only_a_sold_out_product_is_refused_without_calling_the_model(self):
        llm = FakeLLM()
        with self.assertRaises(CaptionRefused) as ctx:
            generate_caption("Promote the Gamma Coast flash sale", synthetic_feed(), BRAND_KIT, llm)
        self.assertEqual(llm.calls, [])
        self.assertIn("Gamma — Coast", ctx.exception.reason)
        self.assertIn("Alpha — Ridge", ctx.exception.alternatives)

    def test_supplied_edge_cases_are_refused(self):
        with self.assertRaises(CaptionRefused):
            generate_caption("Push the Guatemala Huehuetenango flash sale", load_feed(WEEK2), BRAND_KIT, FakeLLM())
        with self.assertRaises(CaptionRefused):
            generate_caption("Share the NYERI10 code for the Kenya", load_feed(WEEK1), BRAND_KIT, FakeLLM())

    def test_mixed_brief_proceeds_with_the_available_product_and_a_note(self):
        llm = FakeLLM(model_json("Alpha Ridge, $12.", "alpha-ridge"))
        result = generate_caption("Post about Alpha Ridge and Gamma Coast", synthetic_feed(), BRAND_KIT, llm)
        self.assertEqual(result.featured_product_ids, ["alpha-ridge"])
        self.assertIn("Gamma — Coast", result.notes[0])
