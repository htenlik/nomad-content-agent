"""End-to-end checks of main.py that need no model: dry runs, refusals, errors."""

import os
import subprocess
import sys
import unittest

from tests.helpers import ROOT, WEEK1, WEEK2


def run_cli(*args, env_overrides=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    # An empty key is "not set" to the app but stops main.py's .env loader from
    # filling it in, so a developer's real .env can never leak into a test run.
    env["LLM_API_KEY"] = ""
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class DryRun(unittest.TestCase):
    def test_dry_run_prints_prompts_for_both_snapshots_without_a_key(self):
        for path, expected_new in ((WEEK1, "ethiopia-guji"), (WEEK2, "costa-rica-tarrazu")):
            proc = run_cli("--feed", str(path), "--brief", "Announce this week's new roast.", "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("=== CURRENT FACTS", proc.stdout)
            self.assertIn(expected_new, proc.stdout)
            self.assertIn("--- fact sheet ---", proc.stderr)

    def test_dry_run_uses_as_of_for_promo_expiry(self):
        proc = run_cli("--feed", str(WEEK1), "--brief", "x", "--dry-run", "--as-of", "2026-09-30")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("GUJI15", proc.stdout)  # expired by the 30th
        proc = run_cli("--feed", str(WEEK1), "--brief", "x", "--dry-run")
        self.assertIn("GUJI15", proc.stdout)  # active on the snapshot date


class SafetyAndErrors(unittest.TestCase):
    def test_sold_out_brief_is_refused_with_exit_code_2_and_no_key_needed(self):
        proc = run_cli("--feed", str(WEEK2), "--brief", "Push the Guatemala Huehuetenango flash sale")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Refused", proc.stderr)
        self.assertIn("sold_out", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_missing_api_key_is_a_clear_error(self):
        proc = run_cli("--feed", str(WEEK1), "--brief", "Announce this week's new roast.")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("LLM_API_KEY", proc.stderr)

    def test_missing_feed_file(self):
        proc = run_cli("--feed", "data/missing.json", "--brief", "x")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not found", proc.stderr)

    def test_missing_brief_argument(self):
        proc = run_cli("--feed", str(WEEK1))
        self.assertEqual(proc.returncode, 2)  # argparse usage error
        self.assertIn("--brief", proc.stderr)

    def test_unreachable_endpoint_fails_cleanly(self):
        proc = run_cli(
            "--feed", str(WEEK1), "--brief", "Announce this week's new roast.",
            env_overrides={"LLM_API_KEY": "test", "LLM_BASE_URL": "http://127.0.0.1:9"},
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Could not reach", proc.stderr)
