# Design write-up: decisions, trade-offs, and what I would do differently

**The shape of the problem.** A caption has two kinds of content: the words,
which should sound like Nomad Roasters, and the facts, which must match a feed
that changes every week. An LLM is the right tool for the first and the wrong
tool for the second, so the design is a thin LLM call wrapped on both sides by
ordinary Python. The feed is parsed and reduced to a fact sheet *before* the
model sees anything, and the caption is checked against that same fact sheet
*after*. The feed, not the model, is the source of truth for every number.

**Why the brand and the feed are separate files, separate modules, and
separate prompt sections.** They change on different clocks and are owned by
different people. `brand_kit.md` is given to the model verbatim, because it is
prose and paraphrasing it into Python strings would just create a second,
drifting copy; `brand_rules.json` sits next to it holding the handful of rules
a program can actually enforce. The feed goes through `feed.py` → `facts.py`
and is injected as a labelled JSON block. Nothing in the brand path reads
product data and nothing in the feed path reads voice rules, and a test greps
the application code for fixture-specific names so the demo can't quietly
depend on week 1.

**Why validation is association-aware.** Collecting every number in the feed
into an allow-list would accept "Colombia Huila, $21" because $21 is *someone's*
price. Instead the model returns `{featured_product_ids, caption}`; the
validator detects which products the text names, checks that those and the
declared ids are all available, and requires every `$`, `%` and code-like
token to belong to a product the text names (the declared ids are the
fallback when the text names nothing, so declaring A while writing about B
does not let A's price through). Sold-out products contribute nothing to the
allow-list and their codes are withheld from the prompt entirely, so a
"valid-looking" `HUEHUE_FLASH` on a sold-out bag is unreachable from the front
and rejected from the back.

**What was kept deliberately simple.** No framework, no vector store, no
database: a handful of products fit in a prompt, and retrieval would only add a way
to retrieve the wrong week. No SDK either — one `urllib` POST to any
OpenAI-compatible endpoint keeps the dependency list empty and makes provider
choice an environment variable. Retry is a three-iteration loop that feeds the
violation messages back as corrections; after that the run fails with the
reasons rather than publishing. Sold-out briefs are refused before the model
call rather than redirected, because a marketer who asked for a specific
product is better served by "that's sold out, here's what you can post" than
by a caption about something else. Promo expiry is judged against the
snapshot's own date so archived fixtures don't rot as the calendar moves.

**Trade-offs and limitations.** Regex sees "$19" and "15%" but not "nineteen
dollars"; the mitigation is that the model is asked for plain formatted
numbers, and the dangerous case (a number the model *did* format) is the one
the checks are good at. Product association is per caption, not per sentence,
so a two-product caption with swapped prices would pass — the prompt asks for
one product per caption, which makes attribution exact in the normal case.
Any percentage is read as a discount, so "100% compostable" gets rejected and
rewritten; I preferred that false positive to the false negative. Name matching
is keyword-based and would miss "the washed one". Voice is enforced only where
it is mechanical; tone itself is left to the model and to review.

**With more time.** First, an evaluation harness: a fixed set of briefs ×
snapshots re-run on every prompt or model change, with the validator's
rejection rate tracked and an LLM judge scoring tone against a rubric from the
brand kit, plus a small human sample. Second, structured generation — ask the
model for `{product_id, mention_price, mention_promo, sentences}` and have the
code render the numbers itself, which removes the regex layer for the common
case. Third, sentence-level attribution for multi-product captions. A
production version would also need a real runtime date policy for promo
expiry, per-platform overlays on the brand kit (length, hashtags), and
logging of every attempt and violation for auditing. The first live run was
instructive on its own: the configured model name had been retired, the
replacement was a reasoning model that produced no usable output under the
original 400-token budget (raising it fixed that), and the free tier's
per-minute quota answered with 429. All three were handled in the client
rather than the prompt — a clear error naming `LLM_MAX_TOKENS`, a larger
default, and a small 429/5xx retry — which is the same lesson as the rest of
the design: make failures legible and handle them in code.
