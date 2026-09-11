"""main.py end to end, without a model: dry runs, refusal, errors."""

import os
import subprocess
import sys
import unittest

from tests.helpers import ROOT, WEEK1, WEEK2


def run_cli(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    # An empty key is "not set" to the app but stops main.py's .env loader from
    # filling it in, so a developer's real .env can never leak into a test run.
    env["LLM_API_KEY"] = ""
    return subprocess.run([sys.executable, "main.py", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


class CLI(unittest.TestCase):
    def test_dry_run_shows_the_facts_for_each_snapshot_without_a_key(self):
        for path, expected in ((WEEK1, "ethiopia-guji"), (WEEK2, "costa-rica-tarrazu")):
            proc = run_cli("--feed", str(path), "--brief", "Announce this week's new roast.", "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(expected, proc.stdout)
        self.assertIn("GUJI15", run_cli("--feed", str(WEEK1), "--brief", "x", "--dry-run").stdout)

    def test_sold_out_brief_is_refused_with_exit_code_2_and_no_key_needed(self):
        proc = run_cli("--feed", str(WEEK2), "--brief", "Push the Guatemala Huehuetenango flash sale")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Refused", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_missing_key_and_missing_feed_are_clear_errors(self):
        proc = run_cli("--feed", str(WEEK1), "--brief", "Announce this week's new roast.")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("LLM_API_KEY", proc.stderr)
        proc = run_cli("--feed", "data/missing.json", "--brief", "x")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not found", proc.stderr)
