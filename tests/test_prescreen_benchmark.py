"""Fixed synthetic and preserved-fixture Tier0/1 acceptance, without models."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from tests.mock_data import BENCHMARK_CANDIDATES
from tests.prescreen_benchmark import DATASET_PATH, JOB_CASES, run_benchmark, write_report


class FixedPrescreenBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_before = deepcopy(BENCHMARK_CANDIDATES)
        with patch("socket.create_connection", side_effect=AssertionError("Benchmark must not use a network")) as connection:
            with patch("openai.OpenAI", side_effect=AssertionError("Benchmark must not initialize an LLM")) as llm:
                cls.report = run_benchmark()
                cls.network_calls, cls.llm_constructions = connection.call_count, llm.call_count
        cls.fixed = json.loads(DATASET_PATH.read_text(encoding="utf-8"))["cases"]

    def test_exactly_nine_fixed_profiles_per_job_and_separate_original_group(self):
        self.assertEqual(len(self.fixed), 27)
        self.assertEqual(len({case["case_id"] for case in self.fixed}), 27)
        for job in JOB_CASES:
            self.assertEqual(sum(case["job_case_id"] == job for case in self.fixed), 9)
        self.assertEqual(self.report["groups"], {"synthetic_profiles": 27, "original_fixture_job_evaluations": 15})
        self.assertEqual(len(self.report["records"]), 42)
        self.assertEqual(BENCHMARK_CANDIDATES, self.original_before)
        self.assertEqual(self.report["dataset_version"], "prescreen-fixed-v2")
        self.assertTrue(self.report["fixture_control_checks"]["ok"])
        def tokens(value):
            if isinstance(value, str):
                return set(re.findall(r"[\w+#]+", value.casefold()))
            if isinstance(value, list):
                return set().union(*(tokens(item) for item in value)) if value else set()
            if isinstance(value, dict):
                return set().union(*(tokens(item) for key, item in value.items() if key not in ("name", "_meta")))
            return set()
        for job in JOB_CASES:
            complete = next(case["resume"] for case in self.fixed if case["job_case_id"] == job and case["profile"] == "complete_match")
            repeated = next(case["resume"] for case in self.fixed if case["job_case_id"] == job and case["profile"] == "repeated_keywords")
            self.assertEqual(repeated["work_experience"], complete["work_experience"] * 3)
            self.assertEqual(repeated["skills"], complete["skills"] * 3)
            self.assertEqual(tokens(repeated), tokens(complete), job + ": repetition must preserve source vocabulary")

    def test_all_fixed_assertions_pass_without_silently_omitting_results(self):
        self.assertTrue(self.report["ok"], self.report["assertions"]["failures"])
        self.assertEqual(self.report["assertions"]["failures"], [])
        self.assertGreater(self.report["assertions"]["assertions"], 200)

    def test_each_job_uses_its_own_bound_policy_and_dynamic_dimensions(self):
        by_job = {}
        for row in self.report["records"]:
            output = row["new"]
            self.assertEqual(output["job_id"], row["job_case_id"])
            self.assertEqual(output["policy_version"], 1)
            self.assertEqual([dimension["dimension_id"] for dimension in output["dimension_scores"]], row["policy_dimension_ids"])
            self.assertEqual(output["assessment_scope"], "TIER0_TIER1_ONLY")
            self.assertIsNone(output["deep_score"])
            by_job.setdefault(row["job_case_id"], set()).add(output["policy_id"])
        self.assertTrue(all(len(ids) == 1 for ids in by_job.values()))
        self.assertEqual(len(set().union(*by_job.values())), 3)

    def test_negative_and_label_evidence_does_not_reach_practical_full_score(self):
        for job in JOB_CASES:
            values = {row["profile"]: row["new"] for row in self.report["records"]
                      if row["job_case_id"] == job and row["origin"] == "human_authored_synthetic_candidate"}
            complete = values["complete_match"]["preliminary_score"]
            self.assertEqual(values["repeated_keywords"]["preliminary_score"], complete)
            self.assertLess(values["keyword_only"]["preliminary_score"], complete)
            self.assertLess(values["negated_experience"]["preliminary_score"], complete)
            negative = [item for item in values["negated_experience"]["keyword_evidence"]
                        if item["context"] in ("NEGATED", "NO_PRACTICE")]
            self.assertTrue(negative)
            self.assertTrue(all(item["source_weight"] == 0 for item in negative))
            self.assertTrue(all(item["source_weight"] == 0 for item in values["negated_experience"]["keyword_evidence"]),
                            job + ": an exclusively negative resume must not claim positive practice")
            learning = [item for item in values["partial_match"]["keyword_evidence"] if item["context"] == "LEARNING"]
            self.assertTrue(learning, job + ": fixed partial profile must preserve learning context")
            self.assertTrue(all(item["source_weight"] <= .35 for item in learning))

    def test_every_output_evidence_span_resolves_to_the_original_input(self):
        fixed = {case["case_id"]: case["resume"] for case in self.fixed}
        checked = 0
        for row in self.report["records"]:
            if row["origin"] == "human_authored_synthetic_candidate":
                raw = fixed[row["case_id"]]
            else:
                index = int(row["profile"].rsplit("_", 1)[1]) - 1
                raw = BENCHMARK_CANDIDATES[index]
            evidence = list(row["new"]["evidence"])
            for dimension in row["new"]["dimension_scores"]:
                evidence.extend(dimension["evidence"])
            for item in evidence:
                source = raw
                for field, number in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", item["source_path"]):
                    source = source[field] if field else source[int(number)]
                self.assertIsInstance(source, str)
                self.assertEqual(source[item["start"]:item["end"]], item["quote"])
                checked += 1
        self.assertGreater(checked, 100)

    def test_observed_performance_and_cache_metrics_are_not_mock_model_data(self):
        self.assertEqual(self.network_calls, 0)
        self.assertEqual(self.llm_constructions, 0)
        self.assertEqual(self.report["model_calls_this_run"], 0)
        performance = self.report["performance"]
        self.assertEqual(performance["repository_bound_end_to_end"]["samples"], 42)
        self.assertEqual(performance["cold_compile"]["samples"], 3)
        self.assertEqual(performance["warm_compile"]["samples"], 300)
        self.assertEqual(performance["interpreter_cache"]["misses"], 3)
        self.assertEqual(performance["interpreter_cache"]["hits"], 39)
        self.assertEqual(performance["interpreter_cache"]["size"], 3)
        for field in ("cold_compile", "warm_compile", "repository_bound_end_to_end"):
            self.assertGreaterEqual(performance[field]["min_ms"], 0)
            self.assertGreaterEqual(performance[field]["max_ms"], performance[field]["median_ms"])

    def test_report_retains_differences_and_old_tier1_diagnostic_scope(self):
        for row in self.report["records"]:
            self.assertTrue(row["difference_explanations"])
            if row["legacy"]["hard_filter_status"] == "FAIL":
                self.assertFalse(row["legacy"]["production_would_run_tier1"])
            self.assertLessEqual(row["legacy"]["tier1_diagnostic_score_45"], 45)
        with tempfile.TemporaryDirectory(prefix="prescreen_report_test_") as temporary:
            write_report(self.report, temporary)
            saved = json.loads((Path(temporary) / "benchmark_results.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["assertions"], self.report["assertions"])
            text = (Path(temporary) / "benchmark_comparison.md").read_text(encoding="utf-8")
            self.assertIn("来源未核实", text)
            self.assertIn("不是生产SLA", text)
            self.assertIn("不能相减当作匹配质量提升", text)
