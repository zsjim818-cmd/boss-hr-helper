from engine.resume_text import get_evidence, get_source_texts
import copy
import unittest

from pydantic import ValidationError

from schemas import CandidateCardData, EvaluationOutput, Evidence, LLMStructuredEvaluation, ResumeData
from tests.mock_data import BENCHMARK_CANDIDATES


class SchemaV2Tests(unittest.TestCase):
    def test_all_five_original_candidates_remain_complete(self):
        self.assertEqual(len(BENCHMARK_CANDIDATES), 5)
        for data in BENCHMARK_CANDIDATES:
            with self.subTest(candidate=data["name"]):
                parsed = ResumeData.model_validate(data)
                self.assertEqual(parsed.data_completeness, "COMPLETE")
                self.assertFalse(parsed.data_issues)

    def test_missing_sections_are_partial_with_explicit_issues(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        del data["project_experience"]
        del data["skills"]
        data["data_completeness"] = "COMPLETE"
        parsed = ResumeData.model_validate(data)
        self.assertEqual(parsed.data_completeness, "PARTIAL")
        missing = {issue.path for issue in parsed.data_issues if issue.kind == "MISSING_SECTION"}
        self.assertEqual(missing, {"project_experience", "skills"})

    def test_explicit_partial_and_failed_capture_are_distinct(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        data["data_completeness"] = "PARTIAL"
        self.assertEqual(ResumeData.model_validate(data).data_completeness, "PARTIAL")
        data["ok"] = False
        self.assertEqual(ResumeData.model_validate(data).data_completeness, "COLLECTION_FAILED")
        data["ok"] = True
        data["data_completeness"] = "COMPLETE"
        data["collection_errors"] = ["project tab timed out"]
        self.assertEqual(ResumeData.model_validate(data).data_completeness, "PARTIAL")

    def test_unknown_nested_fields_preserved_and_reported_once(self):
        data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
        data["collector_revision"] = "v-next"
        data["work_experience"][0]["new_description"] = {"raw": "not silently lost"}
        data["education"][0]["new_tag"] = "unmapped"
        data["expectation"]["salary_currency"] = "CNY"
        data["_meta"]["capture_id"] = "capture-123"
        parsed = ResumeData.model_validate(data)
        paths = {issue.path for issue in parsed.data_issues if issue.kind == "UNKNOWN_FIELD"}
        self.assertEqual(paths, {"collector_revision", "work_experience[0].new_description", "education[0].new_tag", "expectation.salary_currency", "_meta.capture_id"})
        dumped = parsed.model_dump(by_alias=True)
        self.assertEqual(dumped["work_experience"][0]["new_description"], data["work_experience"][0]["new_description"])
        reparsed = ResumeData.model_validate(dumped)
        self.assertEqual(len(reparsed.data_issues), len(parsed.data_issues))
        self.assertEqual(reparsed.model_dump(by_alias=True)["_meta"]["capture_id"], "capture-123")

    def test_mapping_type_errors_are_not_silently_coerced(self):
        for changes in [{"work_experience": "not-a-list"}, {"ok": "false"}, {"degree": 4}, {"skills": {"Python": True}}, {"data_completeness": "ASSUMED"}]:
            with self.subTest(changes=changes):
                data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
                data.update(changes)
                with self.assertRaises(ValidationError):
                    ResumeData.model_validate(data)

    def test_evidence_preserves_sources_and_raw_snippets(self):
        parsed = ResumeData.model_validate(BENCHMARK_CANDIDATES[0])
        evidence = get_evidence(parsed)
        self.assertTrue(evidence)
        expected = get_source_texts(parsed)
        self.assertEqual([(item.source, item.source_path, item.snippet) for item in evidence], expected)
        self.assertTrue(all(item.confidence is None for item in evidence))
        self.assertTrue(all(isinstance(item, Evidence) for item in evidence))

    def test_card_contract_cannot_be_used_as_complete_resume(self):
        card = {
            "action_status": {"button_text": "打招呼", "is_contacted": False},
            "basic_info": {"name": "卡片候选人", "degree": "本科", "expected_salary": "10-14K"},
            "preview_clues": {"advantage_snippet": "Python开发...", "highlight_tags": ["Python"], "brief_timeline": [{"role_or_major": "开发"}]},
            "_dom_anchor": {"dom_card_index": 0, "dom_data_lid": "card-001"},
        }
        parsed = CandidateCardData.model_validate(card)
        self.assertNotIsInstance(parsed, ResumeData)
        self.assertEqual(parsed.basic_info.expected_salary, "10-14K")
        with self.assertRaises(ValidationError):
            ResumeData.model_validate(card)

    def test_card_unknown_fields_are_also_visible(self):
        parsed = CandidateCardData.model_validate({
            "action_status": {"button_text": "聊一聊", "is_contacted": False},
            "basic_info": {"name": "卡片", "new_salary": "new-format"},
            "_dom_anchor": {"dom_card_index": 2},
        })
        self.assertEqual(parsed.data_issues[0].path, "basic_info.new_salary")
        self.assertEqual(parsed.basic_info.model_extra["new_salary"], "new-format")

    def test_llm_output_still_forbids_unknown_fields(self):
        dimension = {"score": 10, "evidence": [], "missing_evidence": ["未提供具体实现"], "confidence": .1}
        payload = {key: copy.deepcopy(dimension) for key in ["ai_automation", "engineering", "efficiency", "job_fit"]}
        payload.update(highlights=[], risk_point="证据不足", injected_field="untrusted")
        with self.assertRaises(ValidationError):
            LLMStructuredEvaluation.model_validate(payload)

    def test_output_contract_blocks_unsafe_auto_greet_from_storage(self):
        valid = {"candidate_key": "job:candidate", "name": "candidate", "routing_decision": "AUTO_GREET",
                 "scoring_mode": "deep_mode", "scoring_status": "DEEP_SCORED", "data_completeness": "COMPLETE",
                 "total_score": 90, "passed_tier2_coarse": True}
        self.assertEqual(EvaluationOutput.model_validate(valid).routing_decision, "AUTO_GREET")
        for changes in [{"scoring_mode": "fast_mode"}, {"scoring_status": "SCORING_FAILED"}, {"data_completeness": "PARTIAL"},
                        {"total_score": None}, {"passed_tier2_coarse": False}, {"passed_hard_filter": False},
                        {"debug_trace": {"hard_filter_status": "UNKNOWN"}}]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):
                    EvaluationOutput.model_validate({**valid, **changes})

    def test_output_contract_keeps_coarse_and_deep_scores_distinct(self):
        with self.assertRaises(ValidationError):
            EvaluationOutput(candidate_key="key", name="candidate", routing_decision="PENDING_REVIEW",
                             scoring_mode="fast_mode", total_score=90)
        result = EvaluationOutput(candidate_key="key", name="candidate", routing_decision="PENDING_REVIEW",
                                  scoring_mode="fast_mode", coarse_score=90)
        self.assertIsNone(result.total_score)


if __name__ == "__main__":
    unittest.main()
