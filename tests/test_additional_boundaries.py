import copy
import unittest

from engine.hard_filter import check_hard_filters, parse_salary, years_check
from schemas import ResumeData
from tests.mock_models import make_engine, valid_review
from tests.mock_data import BENCHMARK_CANDIDATES


class AdditionalBoundaryTests(unittest.TestCase):
    def test_salary_periods_and_bad_ranges(self):
        for text, low, period in [("日薪150-200元", .15, "day"), ("年薪20-30万", 200, "year"), ("8千-12千/月", 8, "month"), ("8K-12K·13薪", 8, "month")]:
            with self.subTest(text=text):
                parsed = parse_salary(text)
                self.assertEqual((parsed.lower_k, parsed.period), (low, period))
        for text in ["15-12K", "0K", "-2K", "100元/小时", "8K-1万元"]:
            self.assertEqual(parse_salary(text).status, "UNKNOWN")

    def test_configured_activity_and_years_are_not_ignored(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        data.pop("active_status", None)
        cfg = make_engine().config
        cfg["hard_filters"]["active_status_allowed"] = ["刚刚活跃"]
        self.assertEqual(check_hard_filters(ResumeData.model_validate(data), cfg)[0], "UNKNOWN")
        data["active_status"] = "半年前活跃"
        self.assertEqual(check_hard_filters(ResumeData.model_validate(data), cfg)[0], "FAIL")
        for value, expected in [("2年", "PASS"), ("4年", "FAIL"), ("", "UNKNOWN"), ("2-4年", "UNKNOWN")]:
            self.assertEqual(years_check(value, ["1-3年"])[0], expected)

    def test_only_label_responsibility_cannot_support_llm_high_score(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        data["work_experience"] = [{"responsibility": "Python,LangGraph,RAG"}]
        data["project_experience"] = []
        data["education"] = [{"degree": "本科"}]
        e = make_engine()
        with self.assertRaises(ValueError):
            e._run_tier3_llm(ResumeData.model_validate(data), [])

    def test_hallucinated_highlight_rejected(self):
        def response(kwargs):
            out = valid_review(kwargs)
            out["highlights"] = ["不存在的十万QPS生产指标"]
            return out
        e = make_engine(response)
        with self.assertRaises(ValueError):
            e._run_tier3_llm(ResumeData.model_validate(BENCHMARK_CANDIDATES[0]), [])

    def test_single_technology_citation_cannot_support_high_scores(self):
        for dim, path, quote in [("ai_automation", "work_experience[0].responsibility", "LangGraph"),
                                 ("engineering", "work_experience[0].responsibility", "Redis"),
                                 ("efficiency", "skills", "Python")]:
            def response(kwargs):
                out = valid_review(kwargs)
                out[dim]["evidence"] = [{"source_path": path, "quote": quote}]
                return out
            with self.subTest(dim=dim), self.assertRaises(ValueError):
                make_engine(response)._run_tier3_llm(ResumeData.model_validate(BENCHMARK_CANDIDATES[0]), [])

    def test_proficiency_list_is_not_practice(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        data.update(advantage="", skills="", project_experience=[], education=[])
        data["work_experience"] = [{"responsibility": "熟悉Python、LangGraph、RAG"}]
        raw, _, _, hits = make_engine()._run_tier1_rules(ResumeData.model_validate(data))
        self.assertLess(raw, (8 + 14 + 12) * 1.2)
        self.assertTrue(all("work_tags:" in h or h.startswith("category:") for h in hits))
        data["work_experience"][0]["responsibility"] = "熟悉 Python 开发与 LangGraph 框架"
        _, _, _, hits = make_engine()._run_tier1_rules(ResumeData.model_validate(data))
        self.assertTrue(all("work_tags:" in h or h.startswith("category:") for h in hits))
