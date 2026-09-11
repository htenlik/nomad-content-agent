"""Command-line entry point.

    python main.py --feed data/promotions_feed_week1.json --brief "Announce this week's new roast."

Exit codes: 0 caption printed, 1 error (bad feed, model failure, no valid
caption), 2 brief refused because it only asks about sold-out products.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.agent import CaptionGenerationError, CaptionRefused, build_prompts, check_brief, generate_caption
from app.feed import FeedError, build_fact_sheet, load_feed
from app.llm import LLM, LLMError

BRAND_KIT = Path("data/brand_kit.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate one on-brand Instagram caption for Nomad Roasters.")
    parser.add_argument("--feed", required=True, help="Path to the current promotions feed JSON.")
    parser.add_argument("--brief", required=True, help='Content brief, e.g. "Announce this week\'s new roast."')
    parser.add_argument("--dry-run", action="store_true", help="Print the facts and prompts without calling the model.")
    args = parser.parse_args(argv)
    load_dotenv(Path(".env"))

    try:
        feed = load_feed(args.feed)
    except FeedError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    brand_kit = BRAND_KIT.read_text(encoding="utf-8")
    facts = build_fact_sheet(feed)

    if args.dry_run:
        system, user = build_prompts(brand_kit, facts, args.brief)
        print(f"--- fact sheet ---\n{json.dumps(facts.for_prompt(), indent=2, ensure_ascii=False)}", file=sys.stderr)
        print(f"--- system prompt ---\n{system}\n\n--- user prompt ---\n{user}")
        return 0

    try:
        check_brief(args.brief, facts)  # needs no model, so an unsafe brief is refused even without a key
        result = generate_caption(args.brief, feed, brand_kit, LLM.from_env())
    except CaptionRefused as exc:
        print(f"Refused: {exc.reason}", file=sys.stderr)
        if exc.alternatives:
            print("Available products in this snapshot: " + ", ".join(exc.alternatives), file=sys.stderr)
        return 2
    except (CaptionGenerationError, LLMError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(result.caption)
    print(
        f"\n[feed: {args.feed} | snapshot: {facts.snapshot_date} | "
        f"featured: {', '.join(result.featured_product_ids) or '-'} | attempts: {len(result.attempts)}]",
        file=sys.stderr,
    )
    for note in result.notes:
        print(f"[note] {note}", file=sys.stderr)
    return 0


def load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from .env into the environment (without overriding real variables)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


if __name__ == "__main__":
    sys.exit(main())
