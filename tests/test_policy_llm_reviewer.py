"""Stage 6 generic Tier 3 reviewer: dynamics, evidence rules and metrics."""
from copy import deepcopy
import json
import unittest

from engine.llm_reviewer import llm_source_map
from engine.policy_llm_reviewer import (
    MISSING_EVIDENCE_MAX_CONFIDENCE,
    PipelineReviewError,
    PolicyReviewService,
    build_pipeline_request,
    routing_suggestion,
    validate_pipeline_assessment,
    weighted_total,
)
from schemas import ResumeData
from schemas.job_policy import DynamicPolicyAssessment
from schemas.pipeline import PipelineDimensionReview
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import DeterministicLLMClient
from tests.policy_fixtures import offline_policy


SOURCE = {"work_experience[0].responsibility":
          "负责Java服务端与SQL事务设计，交付Spring Boot接口并完成接口自动化测试。"}


def assessment(policy, *, scores=None, evidence="负责Java服务端与SQL事务设计",
               missing=None, confidence=0.8):
    dimensions = []
    for index, dimension in enumerate(policy.scoring_dimensions):
        dimensions.append({
            "dimension_id": dimension.dimension_id,
            "score": float(scores[index]) if scores else 70.0,
            "evidence_quotes": [evidence] if evidence else [],
            "missing_evidence": list(missing or []),
            "confidence": confidence})
    return {"dimensions": dimensions}


class PipelineRequestTests(unittest.TestCase):
    def test_request_dimensions_are_fully_dynamic_per_policy(self):
        # No job-specific dimension may leak into the generic prompt/schema.
        for case_id in ("ai_automation", "java_backend", "b2b_sales"):
            with self.subTest(case_id=case_id):
                policy = offline_policy(case_id)
                from schemas.job_policy import JobScoringPolicy
                parsed = JobScoringPolicy.model_validate(policy)
                request = build_pipeline_request(parsed, SOURCE, model="offline-tier3-mock")
                system = request["messages"][0]["content"]
                payload = json.loads(system.split("维度(含权重与Rubric):", 1)[1].split("\n", 1)[0])
                self.assertEqual([item["dimension_id"] for item in payload],
                                 [item.dimension_id for item in parsed.scoring_dimensions])
                for item, dimension in zip(payload, parsed.scoring_dimensions):
                    self.assertEqual(item["label"], dimension.label)
                    self.assertEqual(item["weight"], dimension.weight)
                    self.assertEqual(len(item["rubric"]), len(dimension.rubric))
                for legacy in ("ai_automation_practice", "engineering_foundation",
                               "ai_efficiency_mindset", "job_fit_and_stability",
                               "AI应用与自动化实战"):
                    self.assertNotIn(legacy, system)
                schema = request["response_format"]["json_schema"]
                self.assertEqual(schema["name"], "dynamic_policy_assessment")
                self.assertIs(schema["strict"], True)
                item_schema = schema["schema"]["$defs"]["DynamicDimensionAssessment"]
                self.assertEqual(set(item_schema["required"]),
                                 {"dimension_id", "score", "evidence_quotes",
                                  "missing_evidence", "confidence"})


class PipelineEvidenceRuleTests(unittest.TestCase):
    def setUp(self):
        self.policy = offline_policy("java_backend")
        from schemas.job_policy import JobScoringPolicy
        self.policy = JobScoringPolicy.model_validate(self.policy)

    def _validate(self, payload):
        return validate_pipeline_assessment(
            self.policy, DynamicPolicyAssessment.model_validate(payload), SOURCE)

    def test_grounded_review_is_accepted_with_path(self):
        reviews = self._validate(assessment(self.policy))
        self.assertEqual(len(reviews), len(self.policy.scoring_dimensions))
        for review in reviews:
            self.assertEqual(len(review.evidence), 1)
            self.assertEqual(review.evidence[0].source_path, "work_experience[0].responsibility")

    def test_ungrounded_quote_is_rejected(self):
        payload = assessment(self.policy, evidence="经历过并不存在的量子计算项目")
        with self.assertRaisesRegex(PipelineReviewError, "逐字原文"):
            self._validate(payload)

    def test_short_quote_is_rejected(self):
        payload = assessment(self.policy, evidence="SQL")
        with self.assertRaisesRegex(PipelineReviewError, "过短"):
            self._validate(payload)

    def test_evidence_free_high_score_is_rejected(self):
        payload = assessment(self.policy, evidence=None, scores=[90.0] * 5,
                             confidence=0.9)
        with self.assertRaisesRegex(PipelineReviewError, "无原文证据"):
            self._validate(payload)

    def test_evidence_free_low_score_is_accepted(self):
        payload = assessment(self.policy, evidence=None, scores=[40.0] * 5, confidence=0.4)
        reviews = self._validate(payload)
        self.assertTrue(all(review.evidence == [] for review in reviews))

    def test_missing_evidence_overconfidence_is_rejected(self):
        payload = assessment(self.policy, missing=["缺少性能调优证据"],
                             confidence=MISSING_EVIDENCE_MAX_CONFIDENCE + 0.1)
        with self.assertRaisesRegex(PipelineReviewError, "缺失证据"):
            self._validate(payload)

    def test_dimension_set_must_equal_bound_policy(self):
        payload = assessment(self.policy)
        payload["dimensions"].pop()
        with self.assertRaises(ValueError):
            self._validate(payload)


class PipelineAggregationTests(unittest.TestCase):
    def test_weighted_total_uses_policy_weights(self):
        reviews = [
            PipelineDimensionReview(dimension_id="a", label="A", weight=0.75,
                                    score=80.0, confidence=0.9),
            PipelineDimensionReview(dimension_id="b", label="B", weight=0.25,
                                    score=40.0, confidence=0.9)]
        self.assertEqual(weighted_total(reviews), 70.0)

    def test_routing_follows_policy_thresholds(self):
        from schemas.job_policy import JobScoringPolicy
        policy = JobScoringPolicy.model_validate(offline_policy("java_backend"))
        self.assertEqual(policy.thresholds.deep_review, 72.0)
        self.assertEqual(policy.thresholds.deep_recommend, 92.0)
        self.assertEqual(routing_suggestion(95.0, policy), "DEEP_RECOMMEND")
        self.assertEqual(routing_suggestion(92.0, policy), "DEEP_RECOMMEND")
        self.assertEqual(routing_suggestion(91.99, policy), "HUMAN_REVIEW")
        self.assertEqual(routing_suggestion(72.0, policy), "HUMAN_REVIEW")
        self.assertEqual(routing_suggestion(71.99, policy), "NOT_RECOMMENDED")


class PipelineExecutorTests(unittest.TestCase):
    def setUp(self):
        from schemas.job_policy import JobScoringPolicy
        self.policy = JobScoringPolicy.model_validate(offline_policy("java_backend"))
        resume = ResumeData.model_validate(deepcopy(BENCHMARK_CANDIDATES[0]))
        self.resume = resume
        self.request = build_pipeline_request(self.policy, llm_source_map(resume),
                                              model="offline-tier3-mock")

    def _service(self, *, response=None, error=None, finish_reason="stop"):
        client = DeterministicLLMClient(response=response, error=error)
        client.create_kwargs = {}
        service = PolicyReviewService(None, client=client, model_name="offline-tier3-mock")
        return service

    def test_model_error_marks_failed_metrics_without_leaking_prompt(self):
        service = PolicyReviewService(
            None, client=DeterministicLLMClient(error=RuntimeError("endpoint down")),
            model_name="offline-tier3-mock")
        with self.assertRaises(RuntimeError):
            service._request_assessment(self.request)
        diagnostics = service.last_llm_diagnostics
        self.assertEqual(diagnostics["status"], "FAILED")
        self.assertEqual(diagnostics["stage"], "api_call")
        self.assertEqual(diagnostics["failure_type"], "RuntimeError")
        self.assertNotIn("messages", json.dumps(diagnostics))

    def test_truncated_generation_fails_with_recorded_stage(self):
        client = DeterministicLLMClient(response={"dimensions": []}, )
        client.chat.completions.create = lambda **kwargs: (
            _truncated_response())
        service = PolicyReviewService(None, client=client, model_name="offline-tier3-mock")
        with self.assertRaisesRegex(PipelineReviewError, "incomplete"):
            service._request_assessment(self.request)
        diagnostics = service.last_llm_diagnostics
        self.assertEqual(diagnostics["status"], "FAILED")
        self.assertEqual(diagnostics["stage"], "response_validation")
        self.assertEqual(diagnostics["failure_type"], "PipelineReviewError")
        self.assertNotIn("messages", json.dumps(diagnostics))  # no prompt/credential leak

    def test_metrics_record_request_and_response_metadata(self):
        payload = assessment(self.policy)
        client = DeterministicLLMClient(response=payload)
        service = PolicyReviewService(None, client=client, model_name="offline-tier3-mock")
        assessment_value = service._request_assessment(self.request)
        self.assertEqual(len(assessment_value.dimensions), 5)
        diagnostics = service.last_llm_diagnostics
        self.assertTrue(diagnostics["pydantic_valid"])
        self.assertEqual(diagnostics["request"]["model"], "offline-tier3-mock")
        self.assertIn(diagnostics["finish_reason"], (None, "stop"))
        self.assertGreaterEqual(diagnostics["elapsed_seconds"], 0)
        self.assertIn("prompt_sha256", diagnostics["request"])


def _truncated_response():
    from types import SimpleNamespace
    message = SimpleNamespace(content='{"dimensions": []}', refusal=None)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=message)])


class ModelIdentityTests(unittest.TestCase):
    """P0: the Tier 3 model name must resolve explicitly and never be blank."""

    def test_default_model_comes_from_runtime_settings(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with patch("engine.policy_llm_reviewer.load_runtime_settings") as loader:
            loader.return_value = SimpleNamespace(llm_model_name="qwen-from-settings")
            service = PolicyReviewService(None)
        self.assertEqual(service.model_name, "qwen-from-settings")

    def test_explicit_model_overrides_runtime_settings(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with patch("engine.policy_llm_reviewer.load_runtime_settings") as loader:
            loader.return_value = SimpleNamespace(llm_model_name="qwen-from-settings")
            service = PolicyReviewService(None, model_name=" explicit-model ")
        self.assertEqual(service.model_name, "explicit-model")

    def test_missing_model_name_is_rejected_clearly(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with patch("engine.policy_llm_reviewer.load_runtime_settings") as loader:
            loader.return_value = SimpleNamespace(llm_model_name="   ")
            with self.assertRaisesRegex(ValueError, "LLM_MODEL_NAME"):
                PolicyReviewService(None)

    def test_mock_client_injection_still_works_with_default_model(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        client = DeterministicLLMClient(response={"dimensions": []})
        with patch("engine.policy_llm_reviewer.load_runtime_settings") as loader:
            loader.return_value = SimpleNamespace(llm_model_name="qwen-from-settings")
            service = PolicyReviewService(None, client=client)
        self.assertIs(service._client(), client)


if __name__ == "__main__":
    unittest.main()
