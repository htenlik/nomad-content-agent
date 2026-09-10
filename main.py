"""Command-line entry point for the Nomad Roasters content agent.

    python main.py --feed path/to/promotions_feed.json --brief "Announce this week's new roast."

Exit codes: 0 caption produced, 1 error (bad input, model failure, no valid
caption), 2 brief refused for safety (e.g. it only asks about a sold-out
product).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from app.agent import CaptionGenerationError, CaptionRefused, check_brief_against_facts, generate_caption
from app.brand import BrandError, load_brand
from app.facts import build_fact_sheet
from app.feed import FeedError, load_feed
from app.llm import LLMConfig, LLMError, OpenAICompatibleClient
from app.prompts import build_system_prompt, build_user_prompt

DEFAULT_BRAND_KIT = Path("data/brand_kit.md")
DEFAULT_BRAND_RULES = Path("data/brand_rules.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Generate one on-brand Instagram caption for Nomad Roasters from a content brief.",
    )
    parser.add_argument("--feed", required=True, help="Path to the current promotions/inventory feed JSON.")
    parser.add_argument("--brief", required=True, help='Content brief, e.g. "Announce this week\'s new roast."')
    parser.add_argument("--brand-kit", default=DEFAULT_BRAND_KIT, help="Path to the brand voice guide (markdown).")
    parser.add_argument("--brand-rules", default=DEFAULT_BRAND_RULES, help="Path to the machine-checkable brand rules JSON.")
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Date used to judge promo expiry. Defaults to the feed's own snapshot date.",
    )
    parser.add_argument("--max-attempts", type=int, default=3, help="Generation attempts before giving up (default 3).")
    parser.add_argument("--show-facts", action="store_true", help="Also print the fact sheet the model was given.")
    parser.add_argument("--dry-run", action="store_true", help="Print the fact sheet and prompts without calling the model.")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON instead of text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(Path(".env"))

    try:
        feed = load_feed(args.feed)
        brand = load_brand(args.brand_kit, args.brand_rules)
    except (FeedError, BrandError) as exc:
        return fail(str(exc))

    facts = build_fact_sheet(feed, as_of=args.as_of)
    if args.show_facts or args.dry_run:
        print("--- fact sheet ---", file=sys.stderr)
        print(json.dumps(facts.to_prompt_dict(), indent=2, ensure_ascii=False), file=sys.stderr)
    if args.dry_run:
        print("--- system prompt ---\n" + build_system_prompt(brand))
        print("\n--- user prompt ---\n" + build_user_prompt(facts, args.brief))
        return 0

    try:
        # The safety pre-check needs no model, so run it before requiring an
        # API key: an unsafe brief is refused even on a machine with no key.
        # generate_caption() repeats the check; it is cheap and deterministic.
        check_brief_against_facts(args.brief, facts)
        llm = OpenAICompatibleClient(LLMConfig.from_env())
        result = generate_caption(
            args.brief, feed, brand, llm, as_of=args.as_of, max_attempts=args.max_attempts
        )
    except CaptionRefused as exc:
        message = f"Refused: {exc.reason}"
        if exc.alternatives:
            message += "\nAvailable products in this snapshot: " + ", ".join(exc.alternatives)
        if args.json:
            print(json.dumps({"status": "refused", "reason": exc.reason, "alternatives": exc.alternatives}, indent=2, ensure_ascii=False))
        else:
            print(message, file=sys.stderr)
        return 2
    except CaptionGenerationError as exc:
        return fail(str(exc))
    except LLMError as exc:
        return fail(str(exc))

    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "caption": result.caption,
                    "featured_product_ids": result.featured_product_ids,
                    "feed": feed.source,
                    "snapshot_date": facts.snapshot_date.isoformat(),
                    "attempts": len(result.attempts),
                    "notes": result.notes,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(result.caption)
        print(
            f"\n[feed: {feed.source} | snapshot: {facts.snapshot_date} | "
            f"featured: {', '.join(result.featured_product_ids) or '-'} | attempts: {len(result.attempts)}]",
            file=sys.stderr,
        )
        for note in result.notes:
            print(f"[note] {note}", file=sys.stderr)
    return 0


def fail(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return 1


def load_dotenv(path: Path) -> None:
    """Tiny .env loader: KEY=VALUE lines, '#' comments, no overriding of real env."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    sys.exit(main())
