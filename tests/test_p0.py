from engine.resume_text import get_core_practice_texts
import copy
import unittest
from unittest.mock import patch

from engine.hard_filter import parse_salary
from schemas import ResumeData
from engine.hybrid_engine import HybridScoringEngine
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import DeterministicEmbedder, DeterministicLLMClient


def engine():
    return HybridScoringEngine(embedder=DeterministicEmbedder(), llm_client=DeterministicLLMClient())


class P0Tests(unittest.TestCase):
    def test_salary(self):
        for text, low in [("12.5-15K", 12.5), ("1.2-1.5万/月", 12), ("8000-12000元/月", 8), ("18-25K·13薪", 18), ("月薪8千元", 8)]:
            with self.subTest(text=text):
                self.assertEqual(parse_salary(text).lower_k, low)
        for text in ["", "面议", "8千-1万元", "8-12", "薪资未知"]:
            with self.subTest(text=text):
                self.assertEqual(parse_salary(text).status, "UNKNOWN")
                self.assertIsNone(parse_salary(text).lower_k)

    def test_unknown_degree_not_bachelor(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        data.update(degree="未知", education=[])
        e = engine()
        r = e.evaluate(data, force_mode="fast_mode")
        self.assertEqual(r.debug_trace.hard_filter_checks["degree"][0], "UNKNOWN")
        self.assertNotEqual(r.routing_decision, "AUTO_GREET")
        data["education"] = [{"degree": "大专"}]
        self.assertEqual(e.evaluate(data).scoring_status, "HARD_FILTERED")
        data["education"] = [{"degree": "硕士"}]
        self.assertTrue(e._run_tier0_hard_filter(ResumeData.model_validate(data))[0])

    def test_daily_or_missing_salary_unknown(self):
        e = engine()
        for sal in ["150-200元/天", "面议", "", "20万/年"]:
            data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
            data["expectation"]["salary"] = sal
            r = e.evaluate(data, force_mode="fast_mode")
            self.assertTrue(r.passed_hard_filter)
            self.assertEqual(r.debug_trace.hard_filter_checks["salary"][0], "UNKNOWN")

    def test_tags_are_not_practice(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        data.update(advantage="", skills="", project_experience=[], education=[])
        data["work_experience"] = [{"position": "LangGraph开发", "keywords": ["LangGraph", "Python"]}]
        resume = ResumeData.model_validate(data)
        self.assertEqual(get_core_practice_texts(resume), [])
        raw, _, _, hits = engine()._run_tier1_rules(resume)
        self.assertLess(raw, (14 + 8) * 1.2)
        self.assertFalse(any("实战" in h for h in hits))

    def test_historic_customer_service_with_project(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        data["work_experience"] = [{"position": "客服", "responsibility": "处理咨询"}]
        self.assertTrue(engine()._run_tier0_hard_filter(ResumeData.model_validate(data))[0])

    def test_failure_and_fast_never_greet(self):
        e = engine()
        for error in [TimeoutError("offline timeout"), ValueError("invalid JSON")]:
            with patch.object(e, "_run_tier3_llm", side_effect=error), patch.object(e, "_run_tier2_semantic", return_value=(55, 0, {}, [])):
                r = e.evaluate(BENCHMARK_CANDIDATES[0])
                self.assertEqual(r.scoring_status, "SCORING_FAILED")
                self.assertIsNone(r.total_score)
                self.assertEqual(r.routing_decision, "PENDING_REVIEW")
        with patch.object(e, "_run_tier2_semantic", return_value=(55, 0, {}, [])):
            self.assertEqual(e.evaluate(BENCHMARK_CANDIDATES[0], "fast_mode").routing_decision, "PENDING_REVIEW")

    def test_collection_failed_and_partial(self):
        e = engine()
        for changes, expected in [({"ok": False}, "SCORING_FAILED"), ({"data_completeness": "PARTIAL"}, "DATA_INCOMPLETE")]:
            data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
            data.update(changes)
            r = e.evaluate(data)
            self.assertEqual(r.scoring_status, expected)
            self.assertNotEqual(r.routing_decision, "AUTO_GREET")

    def test_low_scores_are_not_hiring_rejections(self):
        e = engine()
        with patch.object(e, "_run_tier2_semantic", return_value=(0, 0, {}, [])):
            r = e.evaluate(BENCHMARK_CANDIDATES[3])
            self.assertEqual(r.routing_decision, "NOT_RECOMMENDED")


if __name__ == "__main__":
    unittest.main()
