# Design write-up

**What I built.** A small Python program that turns a one-line brief and
the current promotions feed into one Instagram caption. The model only
writes the words; every commercial fact is filtered before the call and
checked after it by ordinary code. `feed.py` loads the feed and works out
what may be promoted, `agent.py` builds the prompt and runs the
generate → validate → retry loop, `validator.py` holds the checks, and
`llm.py` makes the API call.

**Why the brand guide and the feed stay separate.** They change at
different rates and for different reasons. The brand kit is prose, so I
give it to the model as-is instead of copying it into Python strings that
would drift from the original; the only brand rules in code are the four
explicit "don't" items that a program can check. The feed is data, so it is
parsed, filtered and inserted as a labelled block. A test fails if any
product name from the sample feeds appears in application code, which is
how I made sure week 2 works without touching the source.

**Why I validate outside the model.** Telling a model not to invent a
price is not a guarantee. So I do two cheaper, certain things. Before the
call, the model only sees prices and promos for products that are actually
sellable; sold-out products are listed by name only, so a code like
`HUEHUE_FLASH` on a sold-out bag never enters the prompt. After the call,
regexes pull every `$`, `%` and code-like token out of the caption and each
one must belong to the product the caption names — a real price attached
to the wrong product is rejected, not just an invented one. Failures go
back to the model as corrections, three times at most, and then the run
fails rather than publishing.

**The main trade-off.** I used simple text matching instead of anything
that understands English. It catches the things that matter for this
workflow (formatted numbers, promo codes, product names, adjective forms
like "Kenyan"), and it is honest about what it can't catch: "nineteen
dollars", a two-product caption with swapped prices, or a sold-out product
referred to only as "the washed one". I preferred a short list of known
gaps to a heuristic engine I couldn't fully explain. In the same spirit,
any percentage is treated as a discount, so "100% compostable" gets
rewritten — a false positive I accept.

**Other decisions.** A brief that only asks about a sold-out product is
refused with the available alternatives, rather than quietly answered with
a different coffee. Promo expiry is judged against the feed's own snapshot
date so the archived week-1 file keeps meaning what it meant that week.
The client is one `urllib` POST with a small retry, because the free tier
answers with 429 a few times a minute, and a generous token budget, because
the model I used spends hidden reasoning tokens before the caption.

**With more time.** A regression set of briefs × snapshots that runs on
every prompt or model change, with the validator's rejection rate tracked
and a second model scoring tone against a rubric from the brand kit.
Sentence-level attribution so two-product captions are checked properly.
And structured generation — have the model return the product id and
which facts to mention, and let the code render the numbers itself — which
would remove most of the regex layer.
