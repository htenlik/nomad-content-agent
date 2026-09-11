# Nomad Roasters — Content Agent

Take-home for the VisionBridge internship. You give the agent a short brief
("announce this week's new roast") and the current promotions feed, and it
writes one Instagram caption in the Nomad Roasters voice. The caption can
only use prices, discounts, promo codes and availability that are in that
week's feed — the model writes the words, ordinary Python checks the facts.

## How it works

1. Load the brand guide (`data/brand_kit.md`). It rarely changes.
2. Load the weekly feed (`--feed`). It changes every week, so nothing from
   it is hard-coded anywhere.
3. Build a "fact sheet": products that can actually be sold (`in_stock` or
   `low_stock` **and** `units_left > 0`) with their price and any promo that
   hasn't expired. Sold-out products appear by name only, on a "do not
   mention" list — their prices and promo codes are never shown to the
   model, so a sold-out product with a live-looking code stays invisible.
4. If the brief only asks about sold-out products, stop here and say so
   (exit code 2). No model call.
5. Send the model the brand guide, the fact sheet as JSON, the brief and a
   short list of rules. It answers with JSON: the caption plus the ids of
   the products it wrote about.
6. Check the caption with plain code: every `$` price, `%` discount and
   promo-code-looking token must belong to the product the caption names;
   no sold-out product may be named; "new roast" wording needs
   `new_this_week`; plus the brand kit's mechanical rules (banned phrases,
   no health claims, at most one emoji and one exclamation mark).
7. If anything fails, send the reasons back to the model and try again, up
   to three times. If it still fails, the run errors instead of printing a
   bad caption.

```
main.py           command line: --feed, --brief, --dry-run
app/feed.py       load the feed; decide what is available; build the fact sheet
app/llm.py        one call to an OpenAI-compatible chat endpoint (stdlib only)
app/validator.py  the deterministic checks, plus product-name matching
app/agent.py      prompt, generate → validate → retry loop, sold-out refusal
data/             brand kit and the two feed snapshots
scripts/run_examples.py   reproduces the examples below
tests/            unittest suite
DESIGN.md         one-page write-up
```

## Setup and running

Python 3.10+, no third-party packages (pytest optional).

```bash
cp .env.example .env      # put your API key in it
python main.py --feed data/promotions_feed_week1.json --brief "Announce this week's new roast."
python main.py --feed data/promotions_feed_week2.json --brief "Announce this week's new roast."
```

The examples were generated with Gemini (`gemini-3.6-flash`) through its
OpenAI-compatible endpoint, which is the default in `.env.example`; any
endpoint that speaks the OpenAI chat-completions format works by changing
`LLM_BASE_URL` and `LLM_MODEL`. `--dry-run` prints the fact sheet and the
prompts without calling a model. The caption goes to stdout, a one-line
summary to stderr. Exit codes: 0 caption, 1 error, 2 brief refused.

Promo expiry is judged against the feed's own `snapshot_taken_at` date, not
today's date, so an old snapshot still means what it meant that week.

## Tests

```bash
python -m unittest discover -v      # or: python -m pytest
```

37 tests, no API calls (the model is a fake). They cover feed loading, the
availability rule, the fact sheet, every commercial-fact check (including a
real price or code attached to the wrong product), sold-out handling, the
retry loop, the week1/week2 swap, and the command line.

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

### What this shows

A/B and C/D are the same code and the same brief run against week 1 and
then week 2; only the `--feed` argument changed. In A/B the featured
product, price and code follow the feed. In C/D the same brief flips from
a normal caption to a refusal because the stock status changed, even though
the week-2 promo code looks valid. Availability is decided by stock status
and unit count only; promo fields are never consulted for it.

I chose to refuse rather than quietly write about a different coffee: if a
teammate asked for a specific product, "that's sold out, here's what you can
post" is the more useful answer. The brand kit does allow truthful "sold
out" mentions, but a program can't reliably tell those apart from implied
availability, so sold-out products are simply never mentioned.

## Part 1 — Theoretical questions

**Q1. How would you structure the brand kit and the feed so updating one never breaks the other?**
Keep them as two separate files with two separate jobs, and never let code
read both for the same purpose. The brand kit is prose and goes to the model
as-is; the feed is JSON that gets parsed, filtered and inserted as a
labelled data block. In this project the feed code (`feed.py`,
`validator.py`) has no voice rules in it and the brand guide has no product
facts, and a test fails if any product name from the sample feeds shows up
in application code. A brand edit can't change what is true, and a feed edit
can't change how we sound. The only thing they share is the feed's field
names, which are checked when the file loads so a format change fails
loudly.

**Q2. How would you stop the agent from stating a discount, price, or promo code that isn't in the current feed?**
Two things, and neither is a prompt. First, filter the feed in normal Python
before the model sees it: it only receives prices and promos for products
that are actually sellable, so the most common way to leak a bad number —
it was sitting in the context — is closed. Second, check what it wrote: the
caption is scanned for `$` amounts, percentages and code-like tokens, and
each has to match a fact of the product the caption names. A real price
attached to the wrong product fails. Failures are sent back to the model
with the reasons; after three tries the run fails instead of publishing. The
prompt still says "don't invent numbers", but nothing depends on it.

**Q3. Hundreds of posts a week, different platforms — how do you keep them consistent with the brand voice without a human rewriting instructions each time?**
Keep one brand guide file that every generation reads at run time, instead
of instructions people paste into prompts. Platform differences (length,
hashtags, emoji) would be small overlays on top of the same guide, not
rewrites. Turn whatever rules can be checked by code into checks — banned
phrases, exclamation and emoji limits, health words already are — so they
don't depend on the model remembering. Keep a few example posts in the
guide so the tone has something concrete to copy, and change the voice by
editing the file, so drift can be traced to an edit.

**Q4. How would you continuously evaluate voice and facts without reading every output?**
Facts and mechanical style rules are already checked on every run, so log
the validator's results and watch the rejection rate; if it climbs, the
model or the feed changed. Tone can't be asserted by code, so keep a fixed
set of briefs × feed snapshots, re-run it whenever the prompt or model
changes, have a second model score the results against a short rubric taken
from the brand guide, and read a small random sample by hand every week. The
scoring model is for spotting drift, not for approving individual posts.

**Q5. Where would you deliberately not use an LLM, and why?**
Anywhere the answer is a lookup or a yes/no: reading the feed, deciding
whether a product is sellable, whether a promo has expired, what something
costs, which code belongs to which product, whether a brief is about
something sold out, and whether the caption's numbers match the feed. A
model's failure mode on these is a confident wrong answer; normal code either
gets them right or raises an error. Here the model does one thing — turn the
facts and the brief into sentences in the right voice — and everything
before and after it is plain Python.

## Limitations

- Numbers are checked per caption, not per sentence, so a caption about two
  products with their prices swapped would pass. The prompt asks for one
  product per caption, which is the normal case.
- The checks are regexes: "$19", "19 dollars", "15%" and code-like tokens
  are caught; "nineteen bucks" is not. Unit counts and dates in the caption
  aren't verified. Any percentage is treated as a discount, so "100%
  compostable" would be rejected and rewritten.
- Product mentions are matched on the distinctive words of the name and
  origin (plus forms like "Kenyan"); "the washed one" wouldn't be detected.
  The "new" check only looks for the usual phrasings.
- Tone is the model's job, guided by the brand kit; only the mechanical
  rules are enforced. Competitor mentions aren't checked.
- The agent trusts the snapshot it is given. Unknown stock statuses are
  treated as unavailable.
