"""Regression checks for package moves, CLI dispatch, and caller working directories."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from config.settings import DEFAULT_CONFIG_PATH, PROJECT_ROOT
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import make_engine


class EntrypointTests(unittest.TestCase):
    def test_default_config_is_independent_of_callers_working_directory(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                engine = make_engine()
                result = engine.evaluate(BENCHMARK_CANDIDATES[0], "fast_mode")
                self.assertEqual(engine.config_path, DEFAULT_CONFIG_PATH)
                self.assertEqual(result.coarse_score, 88.82)
            finally:
                os.chdir(original)

    def test_unified_diagnostic_cli_runs_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "diagnostic.json"
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(PROJECT_ROOT / "main.py"),
                 "diagnose", "--mode", "fast_mode", "--json-output", str(output)],
                cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["execution"], "offline_mock")
            self.assertEqual(len(report["candidates"]), 5)
            self.assertTrue(all(row["total_score"] is None and row["routing_decision"] != "AUTO_GREET"
                                for row in report["candidates"]))

    def test_single_sample_cli_remains_callable_after_move(self):
        from tests.test_engine import main
        output = io.StringIO()
        with redirect_stdout(output):
            main(["--mode", "fast_mode"])
        warning, content = output.getvalue().split("\n", 1)
        self.assertIn("OFFLINE MOCK", warning)
        report = json.loads(content)
        self.assertEqual(report["candidate_key"], "job_ai_dev:geek_zhongsheng")
        self.assertEqual(report["scoring_mode"], "fast_mode")
        self.assertIsNone(report["total_score"])
