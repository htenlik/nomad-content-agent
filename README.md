# Nomad Roasters — Content Agent

Take-home for the VisionBridge internship. Give the agent a one-line brief
("announce this week's new roast") and the current promotions feed; it
returns one Instagram caption in the Nomad Roasters voice that only states
prices, discounts, promo codes and availability that exist in that feed.

The LLM writes the words. Plain Python decides what is true.

## How it works

The brand guide (`data/brand_kit.md`, rarely changes) and the promotions
feed (`--feed`, changes weekly) never meet in code. The feed is first
reduced to a **fact sheet**: products that are actually sellable
(`in_stock`/`low_stock` *and* `units_left > 0`) with their price and any
promotion still active on the snapshot date, plus sold-out products by name
only — their prices and promo codes are withheld, so the model never sees
`HUEHUE_FLASH` on a sold-out bag. The model gets the brand guide verbatim,
the fact sheet as JSON, and the brief, each in its own labelled block, and
returns `{"featured_product_ids": [...], "caption": "..."}`.

The caption is then checked by ordinary code against the same fact sheet:
every `$` price, `%` and promo-code-looking token must belong to a product
the caption names; no sold-out product may be named; "new roast"/"new this
week" wording needs `new_this_week`; and the checkable brand rules apply
(banned phrases, health-claim words, at most one emoji and one exclamation
mark, caption length). Violations go back to the model as corrections, up
to three attempts, after which the run fails instead of returning bad copy.
If the brief only asks about sold-out products, the agent refuses before
calling the model at all.

```
brief ─┐   data/brand_kit.md + brand_rules.json  (durable)
       │   --feed X.json ─► feed.py ─► facts.py ─► fact sheet (available / sold-out, active promos)
       ├───────────────────────────────────┤ brief only about sold-out products? ─► refuse (exit 2)
       ▼                                   ▼
   prompts.py: INSTRUCTIONS | BRAND CONTEXT | CURRENT FACTS | CONTENT BRIEF ─► llm.py
                                           │
                                    validator.py ─► violations? ─► retry with corrections (≤3)
                                           │                             │
                                        caption                     fail safely
```

```
main.py                  CLI: arguments, exit codes, .env loading
app/models.py            Product / Promotion / Feed dataclasses and the availability rule
app/feed.py              JSON loading with strict field checks
app/facts.py             Fact sheet (available vs sold-out, promo expiry) and product-name matching
app/brand.py             Loads brand_kit.md and brand_rules.json
app/prompts.py           Assembles the four-part prompt
app/llm.py               Small client for an OpenAI-compatible chat endpoint (stdlib urllib)
app/validator.py         Deterministic post-generation checks
app/agent.py             Pre-flight refusal, generate, validate, bounded retry
data/                    Supplied brand kit and both feed snapshots, plus brand_rules.json
scripts/run_examples.py  Runs the example briefs below and prints them as markdown
tests/                   unittest suite (also runs under pytest)
DESIGN.md                One-page design write-up
```

`brand_rules.json` is the machine-checkable subset of the brand kit (banned
phrases, limits). The prose kit stays the source of truth for tone.

## Setup and running

Python 3.10+, standard library only (pytest is optional).

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
cp .env.example .env                                # put your API key in .env
python main.py --feed data/promotions_feed_week1.json --brief "Announce this week's new roast."
python main.py --feed data/promotions_feed_week2.json --brief "Announce this week's new roast."
```

`.env` is git-ignored. The examples below were generated with Gemini
(`gemini-3.6-flash`) through its OpenAI-compatible endpoint, which is the
default; any endpoint that speaks the OpenAI chat-completions format works
by setting `LLM_BASE_URL` and `LLM_MODEL`. Only `LLM_API_KEY` is required.

The caption goes to stdout; a one-line summary (feed, snapshot date,
featured products, attempts) goes to stderr. Flags: `--json` for a
machine-readable result, `--show-facts` to print the fact sheet, `--dry-run`
to print the fact sheet and prompts without calling a model (no key
needed), `--as-of YYYY-MM-DD` to override the date used for promo expiry,
`--max-attempts N` (default 3). Exit codes: `0` caption, `1` error (bad
feed, model failure, no valid caption), `2` brief refused for safety.

Promo expiry is judged against the feed's own `snapshot_taken_at` date, not
the wall clock: a snapshot describes the world when it was taken, and using
today's date would make the week-1 fixture's promos silently expire.

## Testing

```bash
python -m unittest discover -v      # or: python -m pytest
```

94 tests, no API calls (the model is a fake). They cover feed parsing and
malformed input, the availability rule, promo expiry, the fact sheet
withholding sold-out promo codes, every validator rule (including a real
price or promo code attached to the wrong product), retry and give-up
behaviour, brief refusal, the HTTP client against a local stub server, the
CLI, and a guard that fails if any week-specific product name or code
appears in application code.

## Example outputs

Produced by `python scripts/run_examples.py` against Gemini
(`gemini-3.6-flash`, temperature 0.4). Captions are exactly what the agent
returned after passing validation. Example D needs no model.

### Example A — Normal generation (week 1)

```bash
python main.py --feed data/promotions_feed_week1.json --brief "Announce this week's new roast."
```

Feed: `data/promotions_feed_week1.json` (snapshot 2026-09-08). The new
product is Ethiopia — Guji (Washed), in stock, promo `GUJI15` (15%, expires
2026-09-14).

```text
New roast in the shop this week: Ethiopia — Guji (Washed). It is a light roast with notes of jasmine and a clean dry finish. Yours for $21, or take 15% off with code GUJI15.
```

Featured: `ethiopia-guji` · attempts: 1.

### Example B — Swap test: same brief, week 2 snapshot

```bash
python main.py --feed data/promotions_feed_week2.json --brief "Announce this week's new roast."
```

Feed: `data/promotions_feed_week2.json` (snapshot 2026-09-15). Same code,
same brief, different feed: the new product is now Costa Rica — Tarrazú
with promo `TARRAZU10` (10%, expires 2026-09-22); Guji is no longer new and
has no promo.

```text
New roast this week: Costa Rica — Tarrazú. It's a medium-light roast with notes of honey, red apple, and a silky body. Traceable to single-origin farms in Tarrazú and shipped in compostable packaging. Grab a bag for $20, or use code TARRAZU10 for 10% off.
```

Featured: `costa-rica-tarrazu` · attempts: 1. The week-1 caption fails
validation under this feed (`unsupported_discount`,
`unsupported_promo_code`, `unsupported_new_claim`); `tests/test_swap.py`
checks that.

### Example C — Promo brief while the product is in stock (week 1)

```bash
python main.py --feed data/promotions_feed_week1.json --brief "Push the Guatemala Huehuetenango flash sale"
```

Feed: `data/promotions_feed_week1.json`. Guatemala — Huehuetenango is in
stock (77 units) with `HUEHUE20` (20%, expires 2026-09-11).

```text
Plum, cocoa nib, and soft acidity from Huehuetenango, Guatemala. Our medium roast is $19, but you can take 20% off with code HUEHUE20 at checkout.
```

Featured: `guatemala-huehue` · attempts: 1.

### Example D — Edge case: same brief, week 2, product sold out but still carrying a promo code

```bash
python main.py --feed data/promotions_feed_week2.json --brief "Push the Guatemala Huehuetenango flash sale"
```

Feed: `data/promotions_feed_week2.json`. Guatemala — Huehuetenango is now
`sold_out` (0 units) yet still carries `HUEHUE_FLASH`, 25% off, expiring
2026-09-20 — a promo that looks perfectly live.

```text
Refused: The brief asks about 'Guatemala — Huehuetenango' (sold_out), which is not available in the feed snapshot (2026-09-15). Refusing to generate a caption that would promote or imply availability of a sold-out product.
Available products in this snapshot: Ethiopia — Guji (Washed), Colombia — Huila, Kenya — Nyeri, Brazil — Mogiana, Costa Rica — Tarrazú
```

Exit code 2, no model call made.

### What the swap test and the edge case show

A/B and C/D are each the same code and the same brief run against
`promotions_feed_week1.json` and then `promotions_feed_week2.json`; the feed
is a command-line argument and nothing in `app/` changed between runs. In
A/B the featured product, price and code follow the feed. In C/D the same
brief flips from a normal caption to a refusal because the stock status
changed, even though the week-2 promo code looks valid.

Sold-out safety rests on three checks that would all have to fail:
availability is decided by stock status and unit count, never by promo
fields, and sold-out prices and codes are withheld from the prompt; a brief
that only names sold-out products is refused before the model is called
(mixed briefs proceed with the available ones and a note); and the validator
rejects any caption that names a sold-out product (including adjective
forms like "Guatemalan") or uses a code that isn't an active promo of a
featured, available product. I chose refusal over silently writing about a
different coffee: a marketer who asked for a specific product is better
served by "that's sold out, here's what you can post". The brand kit allows
truthful "sold out" mentions, but telling those apart from implied
availability isn't something a program can do reliably, so the rule here is
stricter: sold-out products are not mentioned at all.

## Part 1 — Theoretical questions

**Q1. Structuring the brand kit and the feed so updating one never breaks the other.**
Two files with two jobs, and no code path that reads both for the same
purpose. The brand kit is prose that goes to the model verbatim; the feed is
JSON that is parsed into typed objects, reduced to a fact sheet, and injected
as a labelled data block. Here the brand layer (`brand_kit.md`,
`brand_rules.json`, `prompts.py`) contains no product facts and the feed
layer (`feed.py`, `facts.py`, `validator.py`) contains no voice rules, and a
test greps the application code for week-specific product names. A brand
edit can't change what is true and a feed edit can't change how we sound.
The remaining coupling is the feed schema, which is validated strictly at
load time so a schema change fails loudly.

**Q2. Preventing a discount, price, or promo code that isn't in the feed.**
Two things, neither of which is a prompt. First, restrict what the model can
see: it only receives prices and promos for products that are sellable now,
so the most common leak — the bad number was in the context — is closed.
Second, verify what it wrote: the caption is parsed for `$` amounts,
percentages and promo-code-shaped tokens, and each must match a fact of the
product the caption names (the model also declares ids, but the text wins).
A real price attached to the wrong product fails. Failures go back to the
model as corrections; after three tries the run fails rather than
publishing. The prompt still says "don't invent numbers", but nothing
depends on it.

**Q3. Keeping hundreds of posts a week consistent with the voice.**
Make the brand context one versioned artifact that every generation reads
at run time, rather than instructions people paste into prompts. Here that's
`brand_kit.md` plus `brand_rules.json`; per-platform differences (length,
hashtags, emoji tolerance) would be small overlays on the same core. Then
move as many rules as possible from "please remember" to "the validator
rejects it" — banned phrases, exclamation and emoji limits and health-claim
vocabulary already are. Keep a few reference posts in the kit as tone
anchors, and change the voice by changing the file, so drift can be traced
to a specific edit.

**Q4. Continuously evaluating voice and facts without reading every output.**
Split by how well each thing can be measured. Hard facts (unsupported
price/discount/code, sold-out mention) and hard style rules (banned words,
emoji, exclamation marks) are already checked on every run, so log the
validator's verdicts and watch the rejection rate; a rising rate means the
model or the feed changed. Soft voice quality can't be asserted, so keep a
fixed regression set of briefs × feed snapshots, re-run it on every prompt
or model change, score it with an LLM judge against a short rubric from the
brand kit, and have a human read a small random sample (a few percent) each
week. The judge is a trend detector, not a gate.

**Q5. Where I would deliberately not use an LLM.**
Everything that is a lookup or a boolean: parsing the feed, deciding whether
a product is sellable, whether a promo is expired, what a product's price
is, which code belongs to which product, whether a brief asks about
something sold out, and whether the caption's numbers match the feed. An
LLM's failure mode on these is a confident wrong answer; ordinary code
either gets them right or raises. Here the model does exactly one thing —
turn a fact sheet and a brief into sentences in the right voice — and
everything before and after it is deterministic.

## Limitations

- Numbers are checked per caption, not per sentence. The prompt asks for one product per caption, which makes attribution exact in the normal case; a two-product caption with swapped prices would pass.
- Regex catches formatted values ("$19", "19 dollars", "15%", code-like tokens), not prose ("nineteen bucks"). Unit counts and dates in the caption are not verified. Any percentage is treated as a discount, so "100% compostable" would be rejected and rewritten — a deliberate false positive.
- Product mentions are keyword-based (distinctive words from name and origin, plus short adjective forms); "the washed one" is not detected. The "new" check only looks for the usual phrasings.
- Voice is enforced only where it is mechanical; tone itself is left to the model. Competitor mentions aren't checked because no competitor list exists.
- The agent trusts the snapshot it is given; feed freshness is the caller's problem. Unknown stock statuses are treated as unavailable rather than raising.
- The client speaks the OpenAI chat-completions format; a provider without such an endpoint would need a second small client with the same `complete(system, user)` method.
