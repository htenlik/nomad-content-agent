# Design write-up: decisions, trade-offs, and what I would do differently

**The shape of the problem.** A caption has two kinds of content: the words,
which should sound like Nomad Roasters, and the facts, which must match a
feed that changes every week. An LLM is the right tool for the first and the
wrong tool for the second, so the design is a thin LLM call wrapped on both
sides by ordinary Python. The feed is reduced to a fact sheet *before* the
model sees anything, and the caption is checked against that same fact sheet
*after*. The feed, not the model, is the source of truth for every number.

**Why the brand and the feed stay apart.** They change on different clocks
and are owned by different people. `brand_kit.md` is given to the model
verbatim — it is prose, and paraphrasing it into Python strings would just
create a second, drifting copy. `brand_rules.json` next to it holds the few
rules a program can enforce (banned phrases, emoji and exclamation limits,
health-claim words). The feed goes through `feed.py` → `facts.py` and is
injected as a labelled JSON block. Nothing in the brand path reads product
data, nothing in the feed path reads voice rules, and a test greps the
application code for fixture-specific names so the demo can't quietly
depend on week 1.

**Why validation is association-aware.** Allow-listing every number in the
feed would accept "Colombia Huila, $21" because $21 is *someone's* price.
Instead the model returns `{featured_product_ids, caption}`, the validator
works out which products the text names, checks that all of them are
available, and requires every `$`, `%` and code-like token to belong to one
of *those* products. Sold-out products contribute nothing to the allow-list
and their codes are withheld from the prompt, so a valid-looking
`HUEHUE_FLASH` on a sold-out bag can't get in from the front and is rejected
from the back.

**What I kept deliberately simple.** No framework, no vector store, no
database: a handful of products fit in a prompt, and retrieval would only add
a way to retrieve the wrong week. No SDK — one `urllib` POST to an
OpenAI-compatible endpoint keeps the dependency list empty. Retry is a
three-iteration loop that feeds the violation messages back as corrections;
after that the run fails with the reasons rather than publishing. Briefs that
only ask about sold-out products are refused before the model call rather
than redirected, because a marketer who asked for a specific product is
better served by "that's sold out, here's what you can post" than by a
caption about something else. Promo expiry is judged against the snapshot's
own date so archived fixtures don't rot as the calendar moves.

**Trade-offs and limitations.** Regex sees "$19" and "15%" but not
"nineteen dollars"; the prompt asks for plainly formatted numbers, and the
dangerous case (a number the model *did* format) is the one the checks are
good at. Product association is per caption, not per sentence, so a
two-product caption with swapped prices would pass; the prompt asks for one
product per caption, which makes attribution exact in the normal case. Any
percentage is read as a discount, so "100% compostable" gets rejected and
rewritten — I preferred that false positive to the false negative. Name
matching is keyword-based and would miss "the washed one". Voice is enforced
only where it is mechanical; tone is left to the model and to review. The
first live run also taught me two practical things that ended up in the
client: a reasoning model needs a bigger token budget than the caption
itself, and free tiers hand out 429s, so there is a small bounded retry.

**With more time.** First, an evaluation harness: a fixed set of briefs ×
snapshots re-run on every prompt or model change, with the validator's
rejection rate tracked and an LLM judge scoring tone against a rubric from
the brand kit, plus a small human sample. Second, structured generation —
ask the model for `{product_id, mention_price, mention_promo, sentences}`
and have the code render the numbers itself, which removes the regex layer
for the common case. Third, sentence-level attribution for multi-product
captions. A production version would also need a real runtime date policy
for promo expiry, per-platform overlays on the brand kit (length, hashtags),
and logging of every attempt and violation for auditing.
