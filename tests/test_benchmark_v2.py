from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.benchmark_runner import ROOT, collect_benchmark, write_reports
from schemas import EvaluationOutput
from tests.mock_data import BENCHMARK_CANDIDATES


class BenchmarkV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = collect_benchmark()

    def test_all_five_original_fixtures_present_and_unchanged(self):
        snapshot = deepcopy(BENCHMARK_CANDIDATES)
        self.assertEqual(len(self.report["candidates"]), 5)
        self.assertEqual([row["candidate_key"] for row in self.report["candidates"]],
                         [data["_meta"]["candidate_key"] for data in snapshot])
        self.assertEqual(self.report["fixture_count"], 5)
        self.assertEqual(BENCHMARK_CANDIDATES, snapshot)

    def test_fast_mode_never_sends_or_publishes_formal_score(self):
        for row in self.report["candidates"]:
            result = row["fast"]
            self.assertEqual(result["scoring_mode"], "fast_mode")
            self.assertNotEqual(result["routing_decision"], "AUTO_GREET")
            self.assertIsNone(result["total_score"])

    def test_deep_score_only_published_after_success(self):
        for row in self.report["candidates"]:
            result = EvaluationOutput.model_validate(row["deep"])
            self.assertLessEqual(result.debug_trace.tier1_capped_score, 45)
            self.assertLessEqual(result.debug_trace.tier2_semantic_score, 55)
            if result.scoring_status == "DEEP_SCORED":
                self.assertEqual(result.total_score, 86.5)
            else:
                self.assertIsNone(result.total_score)
        self.assertEqual(self.report["candidates"][4]["deep"]["scoring_status"], "HARD_FILTERED")

    def test_failure_and_incomplete_boundaries_never_auto_greet(self):
        boundaries = self.report["boundaries"]
        for key, expected in [("llm_timeout", "SCORING_FAILED"), ("llm_invalid_json", "SCORING_FAILED"),
                              ("partial_resume", "DATA_INCOMPLETE"), ("collection_failed", "SCORING_FAILED")]:
            with self.subTest(boundary=key):
                self.assertEqual(boundaries[key]["scoring_status"], expected)
                self.assertIsNone(boundaries[key]["total_score"])
                self.assertNotEqual(boundaries[key]["routing_decision"], "AUTO_GREET")
        for key in ["unknown_degree", "daily_salary", "missing_salary", "labels_only", "decimal_over_budget"]:
            self.assertNotEqual(boundaries[key]["routing_decision"], "AUTO_GREET")
        self.assertTrue(boundaries["history_customer_service_with_agent_project"]["passed_hard_filter"])

    def test_identical_config_and_model_scores_stable(self):
        self.assertEqual(len(self.report["stability_checks"]), 5)
        self.assertTrue(all(row["repeat_same_output_except_cache_hit"] for row in self.report["stability_checks"]))
        self.assertTrue(all(row["fresh_engine_same_output"] for row in self.report["stability_checks"]))

    def test_reports_have_complete_diagnostics_and_mock_label(self):
        self.assertIn("MOCK", self.report["warning"])
        self.assertFalse(self.report["model_execution"]["real_models_loaded"])
        self.assertEqual(self.report["model_execution"]["network_calls"], 0)
        first = self.report["candidates"][0]["deep"]
        self.assertTrue(first["debug_trace"]["tier2_chunk_details"])
        self.assertTrue(first["evidence_details"])
        self.assertTrue(first["version_info"])
        self.assertIn("semantic_scoring", self.report["config_snapshot"]["scoring_config"])

    def test_report_writer_runs_without_constructing_remote_client(self):
        with tempfile.TemporaryDirectory() as directory, patch("engine.hybrid_engine.OpenAI", side_effect=AssertionError("real client constructed")):
            report = write_reports(ROOT / "config.json", directory)
            saved = json.loads((Path(directory) / "benchmark_v2_mock.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, report)
            markdown = (Path(directory) / "benchmark_comparison.md").read_text(encoding="utf-8")
            self.assertIn("离线 Mock", markdown)
            self.assertIn("134.10", markdown)
            self.assertIn("NOT_RECOMMENDED", markdown)


if __name__ == "__main__":
    unittest.main()
