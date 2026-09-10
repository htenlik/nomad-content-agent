# Nomad Roasters — Content Agent

A small content agent for the VisionBridge take-home. Give it a one-line brief
("announce this week's new roast") and the current promotions feed, and it
returns one Instagram caption in the Nomad Roasters voice that only states
prices, discounts, promo codes and availability that exist in that feed.

The LLM writes the words. Plain Python decides what is true.

## Design summary

There are four layers, and the important property is which ones are allowed
to know what.

| Layer | Lives in | Changes | Knows about |
|---|---|---|---|
| Durable brand context | `data/brand_kit.md` (prose, given to the model verbatim) and `data/brand_rules.json` (the few rules a program can check) | rarely | voice, do/don'ts, limits |
| Dynamic feed | `data/promotions_feed_*.json`, chosen at run time with `--feed` | weekly | products, stock, prices, promos |
| Generation | `app/prompts.py`, `app/llm.py` | — | receives both layers as clearly delimited data |
| Deterministic validation | `app/facts.py` (before the model), `app/validator.py` (after) | — | the exact feed used for this run |

Before the model is called, `app/facts.py` reduces the feed to a **fact sheet**:
products that are actually sellable (status `in_stock`/`low_stock` *and*
`units_left > 0`) with their price and any promotion still active on the
snapshot date; sold-out products by name only, with their prices and promo
codes withheld. A promo code on a sold-out product is never shown to the model.

After the model answers, `app/validator.py` checks the caption against that
same fact sheet: every `$` price, every `%`, every promo-code-looking token
must belong to a product the caption actually names; no sold-out product
may be named; no "new roast" / "new this week" wording without
`new_this_week`; plus the checkable brand rules (banned phrases, health-claim words, at most one emoji and one
exclamation mark, caption length). Failures go back to the model as
corrections, up to three attempts, after which the run fails rather than
returning bad copy.

If the brief itself only asks about sold-out products, the agent refuses
before spending a model call (exit code 2) and lists what *is* available.

## Architecture

```
brief ─┐
       │   data/brand_kit.md ──────────────┐  (durable)
       │   data/brand_rules.json ──────────┤
       │                                   ▼
       │   --feed X.json ─► feed.py ─► facts.py ─► FactSheet
       │                    (parse,     (available / sold-out,
       │                     validate)   active promos, as-of date)
       │                                   │
       ├───────────────────────────────────┤ pre-flight: brief only about sold-out? ─► refuse
       ▼                                   ▼
   prompts.py: INSTRUCTIONS | BRAND CONTEXT | CURRENT FACTS | CONTENT BRIEF
                                           │
                                     llm.py (HTTP)
                                           │
                       {"featured_product_ids": [...], "caption": "..."}
                                           │
                                    validator.py ──► violations? ──► retry with corrections (≤3)
                                           │                                │
                                        caption                       fail safely
```

## Project structure

```
main.py                  CLI entry point (argument parsing, exit codes, .env loading)
app/models.py            Product / Promotion / Feed dataclasses; the availability rule
app/feed.py              JSON loading and strict field validation
app/facts.py             FactSheet derivation, promo expiry, product-mention detection
app/brand.py             Loads brand_kit.md and brand_rules.json
app/prompts.py           Assembles the four-part prompt
app/llm.py               Minimal OpenAI-compatible chat client (stdlib urllib)
app/validator.py         Deterministic post-generation checks
app/agent.py             Orchestration: pre-flight, generate, validate, bounded retry
data/                    The supplied brand kit and both feed snapshots, plus brand_rules.json
scripts/run_examples.py  Runs the README example briefs and prints them as markdown
tests/                   unittest suite (also runs under pytest)
DESIGN.md                One-page design write-up
```

## Setup

Requires Python 3.10+. The application uses only the standard library; the
only optional dependency is pytest.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # optional: only installs pytest
cp .env.example .env               # then put a real key in .env
```

`.env` is git-ignored and loaded by `main.py`. The examples below were
generated with Gemini (`gemini-3.6-flash`) through its OpenAI-compatible
endpoint, which is the default; any endpoint that speaks the OpenAI
chat-completions format works by setting `LLM_BASE_URL` and `LLM_MODEL`.
Only `LLM_API_KEY` is required.

## Running

```bash
# week 1
python main.py --feed data/promotions_feed_week1.json --brief "Announce this week's new roast."

# week 2 — same code, different snapshot
python main.py --feed data/promotions_feed_week2.json --brief "Announce this week's new roast."
```

The caption goes to stdout; a one-line summary (feed, snapshot date, featured
product ids, attempts used) goes to stderr. Useful flags:

- `--json` — machine-readable result.
- `--show-facts` — also print the fact sheet the model was given.
- `--dry-run` — print the fact sheet and both prompts without calling a model (no key needed).
- `--as-of YYYY-MM-DD` — date used to decide whether a promo has expired. Defaults to the feed's own `snapshot_taken_at` date (see "Date semantics" below).
- `--max-attempts N` — generation attempts before failing (default 3).

Exit codes: `0` caption produced, `1` error (bad feed, model failure, no valid
caption after retries), `2` brief refused for safety.

Failure behaviour: a draft that fails validation is sent back to the model
with the specific violations, up to `--max-attempts` times; then the run
fails and prints the last violations (and the raw response, if it could not
be parsed). HTTP 429/5xx from the provider are retried three times with a
growing delay, which matters on free tiers with per-minute quotas. An empty
model response — the usual symptom of a reasoning model spending its whole
token budget on thinking — is reported as such with a pointer to
`LLM_MAX_TOKENS`.

## Testing

```bash
python -m unittest discover -v      # standard library, no install needed
python -m pytest                    # if you installed pytest
```

The suite (94 tests) uses fakes for the model and never calls an API. It
covers feed parsing and malformed input, the availability rule, promo expiry
relative to the snapshot date, the fact sheet withholding sold-out promo
codes, every validator rule (including a real price attached to the wrong
product and a real promo code attached to a sold-out product), retry and
give-up behaviour, brief refusal, the HTTP client against a local stub
server (including 429 retry and empty-content handling), the CLI's
dry-run/refusal/error paths, and a guard that greps
`app/` and `main.py` for any week-specific product name or code.

## Example outputs

Everything below was produced by `python scripts/run_examples.py` (it prints
this section as markdown) against Google Gemini, model `gemini-3.6-flash`,
through its OpenAI-compatible endpoint, with the default settings
(temperature 0.4). Captions are shown exactly as the agent returned them
after passing the validator. Example D needs no model.

### Example A — Normal generation (week 1)

```bash
python main.py --feed data/promotions_feed_week1.json --brief "Announce this week's new roast."
```

Feed: `data/promotions_feed_week1.json` (snapshot 2026-09-08). In this snapshot
the new product is Ethiopia — Guji (Washed), in stock, with promo `GUJI15`
(15%, expires 2026-09-14).

```text
New roast in the shop this week: Ethiopia — Guji (Washed). It is a light roast with notes of jasmine and a clean dry finish. Yours for $21, or take 15% off with code GUJI15.
```

Featured: `ethiopia-guji` · attempts: 1 · every number traces to the Guji entry in the week-1 feed.

### Example B — Swap test: same brief, week 2 snapshot

```bash
python main.py --feed data/promotions_feed_week2.json --brief "Announce this week's new roast."
```

Feed: `data/promotions_feed_week2.json` (snapshot 2026-09-15). Same code,
same brief, different feed: the new product is now Costa Rica — Tarrazú with
promo `TARRAZU10` (10%, expires 2026-09-22), and Guji is no longer new and no
longer has a promo.

```text
New roast this week: Costa Rica — Tarrazú. It's a medium-light roast with notes of honey, red apple, and a silky body. Traceable to single-origin farms in Tarrazú and shipped in compostable packaging. Grab a bag for $20, or use code TARRAZU10 for 10% off.
```

Featured: `costa-rica-tarrazu` · attempts: 1. Run the week-1 caption through the
validator with this feed and it fails with `unsupported_discount`,
`unsupported_promo_code` and `unsupported_new_claim` (`tests/test_swap.py`
does this) — the facts really did change, and the same code followed them.

### Example C — Promo brief while the product is in stock (week 1)

```bash
python main.py --feed data/promotions_feed_week1.json --brief "Push the Guatemala Huehuetenango flash sale"
```

Feed: `data/promotions_feed_week1.json`. Guatemala — Huehuetenango is in stock
(77 units) with `HUEHUE20` (20%, expires 2026-09-11), so a caption is generated.

```text
Plum, cocoa nib, and soft acidity from Huehuetenango, Guatemala. Our medium roast is $19, but you can take 20% off with code HUEHUE20 at checkout.
```

Featured: `guatemala-huehue` · attempts: 1.

### Example D — Edge case: same brief, week 2, product sold out but still carrying a promo code

```bash
python main.py --feed data/promotions_feed_week2.json --brief "Push the Guatemala Huehuetenango flash sale"
```

Feed: `data/promotions_feed_week2.json`. In this snapshot Guatemala —
Huehuetenango is `sold_out` (0 units) yet carries `HUEHUE_FLASH`, 25% off,
expiring 2026-09-20 — a promo that looks perfectly live. Output:

```text
Refused: The brief asks about 'Guatemala — Huehuetenango' (sold_out), which is not available in the feed snapshot (2026-09-15). Refusing to generate a caption that would promote or imply availability of a sold-out product.
Available products in this snapshot: Ethiopia — Guji (Washed), Colombia — Huila, Kenya — Nyeri, Brazil — Mogiana, Costa Rica — Tarrazú
```

Exit code 2, no model call made. The same brief against week 1 (Example C)
generates normally, which is the swap test in the other direction. Had the
model somehow been asked anyway, the validator rejects a caption such as
"Flash sale: 25% off with HUEHUE_FLASH" under this feed with
`sold_out_product`, `unsupported_discount` and `unsupported_promo_code`
(`tests/test_validator.py`).

## Swap test

Examples A/B and C/D are each the **same application code and the same brief
run against `promotions_feed_week1.json` and then
`promotions_feed_week2.json`**. Nothing in `app/` or `main.py` was edited
between runs; the feed is a command-line argument. `tests/test_swap.py`
enforces this in two ways: it runs the same brief through the agent against
both snapshots (with a fake model that follows the prompt) and checks the
featured product and promo code change accordingly, and it fails if any
week-specific product name, promo code, or the strings `week1`/`week2` appear
in application code.

## Edge-case behaviour (sold-out products)

Three guards, each of which would have to fail for a sold-out product to be
promoted:

1. **Fact sheet.** `Product.is_available` is `stock_status in {in_stock, low_stock} and units_left > 0`. The promo fields are not consulted. Unavailable products reach the model as names on a "do not mention" list; their price and promo code are withheld entirely, so the model cannot repeat `HUEHUE_FLASH` because it never sees it.
2. **Pre-flight refusal.** If the brief names products and all of them are unavailable, the agent refuses (exit 2) with the available alternatives. If the brief names a mix, it proceeds with the available ones and attaches a note saying what was dropped. I chose refusal over silently redirecting a "push the Guatemala sale" brief to a different coffee: a marketer asked for a specific thing, and the honest answer is "that's sold out, here's what you can post instead", not a caption about something else.
3. **Validator.** Any caption that names an unavailable product (by distinctive name/origin words, including adjective forms like "Guatemalan"), or uses a promo code that exists anywhere in the feed but isn't an active promo of a featured available product, is rejected and the model is told why.

The brand kit allows truthful "sold out" mentions ("first batch sold out in
four days last time"), but distinguishing that from implied availability in
free text is not something a program can do reliably, so the rule here is
stricter than the brand's: sold-out products are not mentioned at all.

## Date semantics

`promo_expires` is compared against the feed's own `snapshot_taken_at` date,
not the wall clock. A snapshot is a statement about the world at the moment it
was taken; judging it by today's date would make the week-1 fixture's promos
"expire" and the swap test silently change meaning over time. When the feed
is genuinely current the two dates coincide anyway. `--as-of` overrides this
for the case where a snapshot is known to be a few days old.

## Part 1 — Theoretical questions

**Q1. Structuring the brand kit and the feed so updating one never breaks the other.**
Keep them as two files with two jobs and make sure no code path reads both
for the same purpose. The brand kit is prose that goes to the model verbatim;
the feed is JSON that is parsed into typed objects, reduced to a fact sheet,
and injected as a clearly labelled data block. In this repo the brand layer
(`brand_kit.md`, `brand_rules.json`, `prompts.py`) contains no product facts
and the feed layer (`feed.py`, `facts.py`, `validator.py`) contains no voice
rules, and a test greps the application code for any week-specific product
name. A brand edit therefore cannot change what is true, and a feed edit
cannot change how we sound. The remaining coupling is the feed *schema*, which
is validated strictly at load time so a schema change fails loudly instead of
quietly.

**Q2. Preventing a discount, price, or promo code that isn't in the feed.**
Two things, neither of which is a prompt. First, restrict what the model can
see: it only receives prices and promos for products that are sellable now,
so the most common way to leak a bad number — it was in the context — is
closed. Second, verify what it wrote: the caption is parsed for `$` amounts,
percentages and promo-code-shaped tokens, and each one must match a fact of
the product the caption names (the model also declares ids, but the text
wins, so it can't dodge the check by declaring something else). A real price
attached to the wrong product fails. Anything that fails goes back to the
model as a correction; after three tries the run fails rather than publishing.
The prompt still says "don't invent numbers", but nothing depends on it.

**Q3. Keeping hundreds of posts a week consistent with the voice.**
Make the brand context a single versioned artifact that every generation
reads at run time, rather than instructions people paste into prompts. Here
that's `brand_kit.md` plus `brand_rules.json`; per-platform differences
(length, hashtags, emoji tolerance) would be small overlays on top of the same
core, not separate rewrites. Then move as many rules as possible from "please
remember" to "the validator rejects it": banned phrases, exclamation and emoji
limits, health-claim vocabulary are already mechanical. Keep a small set of
reference posts with the kit as few-shot anchors, and change the voice by
changing the file and its version, so a drift in output can be traced to a
specific edit.

**Q4. Continuously evaluating voice and facts without reading every output.**
Split it by how well each thing can be measured. Hard facts (unsupported
price/discount/code, sold-out mention) are already checked on every run, so
log the validator's verdicts and alert on the rejection rate rather than on
individual posts; a rising rate means the model or feed changed. Hard style
rules (banned words, emoji, exclamation marks) are the same. Soft voice
quality can't be asserted, so use a fixed regression set of briefs × feed
snapshots re-run on every prompt or model change, scored by an LLM judge
against a short rubric derived from the brand kit, plus a random sample —
say 2–5% of production output — for a human to read weekly. The judge is a
trend detector, not a gate: it catches "we got 20% more hype-y this month",
not whether one specific caption is fine.

**Q5. Where I would deliberately not use an LLM.**
Everything that is a lookup or a boolean: parsing the feed, deciding whether
a product is sellable, whether a promo is expired, what a product's price is,
which code belongs to which product, whether a brief is asking about
something that's sold out, and whether the caption's numbers match the feed.
An LLM is a bad fit for these because its failure mode is a confident wrong
answer, while ordinary code either gets them right or raises. In this repo
the model is used for exactly one thing — turning a fact sheet and a brief
into sentences in the right voice — and everything before and after it is
deterministic.

## Limitations and assumptions

- **Association is per caption, not per sentence.** Numbers are checked against the products the text names (falling back to the declared ids when it names none). If a caption names two products, a price is valid if it belongs to either. The prompt asks for one product per caption unless the brief needs more, which makes the association exact in the normal case; sentence-level attribution would need an NLP layer I didn't think was worth it here.
- **Regex catches formatted numbers, not prose.** "$19", "19 dollars", "15%", "15 percent" and code-like tokens are checked; "nineteen bucks" or "about a fifth off" are not. Unit counts ("14 bags left") and dates ("through the 14th") are not verified. Any percentage is treated as a discount, so "100% compostable" would be rejected and rewritten — a deliberate false positive.
- **Product mentions are keyword-based.** Distinctive words from the name and origin (plus short adjective suffixes such as "Peruvian") are matched; an oblique reference ("the washed one") is not detected. This is why sold-out promo codes are withheld from the prompt and checked independently, and why the "new" check only looks for the usual phrasings ("new roast", "new this week", "brand new") rather than every use of the word.
- **Brand voice is only partly checkable.** Banned phrases, health-claim words, emoji/exclamation counts and length are enforced; "dry humour" and "sounds like a friend" are not. Competitor mentions aren't checked because no competitor list exists.
- **Feed freshness is the caller's problem.** The agent trusts the snapshot it is given; promo expiry is judged against the snapshot date unless `--as-of` says otherwise.
- **Unknown stock statuses fail closed** (treated as unavailable) rather than erroring, so a new status like `backorder` can't accidentally become sellable but also won't be promoted until someone decides what it means.
- **One provider shape.** The client speaks the OpenAI chat-completions format, which OpenAI, Anthropic, Gemini, Groq and Ollama all serve. A provider without such an endpoint would need a second small client class implementing `complete(system, user)`.
