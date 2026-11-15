"""Offline request/transport-contract tests. These fixtures are not live Qwen scores."""
from copy import deepcopy
import hashlib
import json
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import httpx2
from openai import APITimeoutError
from openai.types import CompletionUsage

from config.settings import DEFAULT_CONFIG_PATH
from config.validation import validate_config
from engine.llm_diagnostics import Tier3DiagnosticReviewer, diagnose_once
from engine.llm_observability import request_metadata
from engine.llm_reviewer import RUBRIC
from schemas import LLMStructuredEvaluation, ResumeData
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import valid_review


class ObservableOfflineClient:
    """No HTTP transport: fixed valid source citations plus response metadata."""
    max_retries = 0

    def __init__(self, response=valid_review, finish_reason="stop", usage=True, failure=None):
        self.response, self.finish_reason, self.usage = response, finish_reason, usage
        self.failure, self.calls = failure, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls.append(request)
        if self.failure:
            raise self.failure
        output = self.response(request) if callable(self.response) else self.response
        content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        usage = CompletionUsage(prompt_tokens=1000, completion_tokens=1600, total_tokens=2600,
                                completion_tokens_details={"reasoning_tokens": 700}) if self.usage else None
        message = SimpleNamespace(content=content, refusal=None,
                                  reasoning_content="OFFLINE PRIVATE REASONING MUST NOT BE LOGGED")
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=self.finish_reason)],
                               model="offline-fixed-model", usage=usage, _request_id="offline-request-id")


class LLMTransportTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        # Explicitly restore the original request's omitted provider options.
        for key in ("enable_thinking", "thinking_budget", "max_tokens"):
            self.config["scoring_config"]["llm_review"].pop(key, None)
        self.resume_data = deepcopy(BENCHMARK_CANDIDATES[0])
        self.resume = ResumeData.model_validate(self.resume_data)

    def reviewer(self, client=None):
        return Tier3DiagnosticReviewer(self.config, client or ObservableOfflineClient(), "offline-fixed-model")

    def diagnose(self, client):
        return diagnose_once(self.resume_data, self.config, client, "offline-fixed-model")

    def test_request_options_preserve_rubric_prompt_and_strict_contract(self):
        original = self.reviewer()._build_tier3_request(self.resume)
        self.assertEqual(hashlib.sha256(RUBRIC.encode("utf-8")).hexdigest(),
                         "73b2989d6141a4c56d1ad28a8c90fa5b86ca104bbd650043a14e488193cef818")
        self.assertIn(RUBRIC, original["messages"][0]["content"])
        self.assertEqual(original["response_format"]["type"], "json_schema")
        self.assertIs(original["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(original["response_format"]["json_schema"]["schema"],
                         LLMStructuredEvaluation.model_json_schema())
        self.assertEqual(original["timeout"], 45)
        self.assertEqual(original["temperature"], 0)
        self.assertNotIn("max_tokens", original)
        self.assertNotIn("extra_body", original)
        self.config["scoring_config"]["llm_review"].update(enable_thinking=False, max_tokens=4096)
        changed = self.reviewer()._build_tier3_request(self.resume)
        self.assertEqual(changed["messages"], original["messages"])
        self.assertEqual(changed["response_format"], original["response_format"])
        self.assertEqual(changed["max_tokens"], 4096)
        self.assertEqual(changed["extra_body"], {"enable_thinking": False})
        self.config["scoring_config"]["llm_review"].update(enable_thinking=True, thinking_budget=1024)
        self.assertEqual(self.reviewer()._build_tier3_request(self.resume)["extra_body"],
                         {"enable_thinking": True, "thinking_budget": 1024})

    def test_request_metadata_measures_actual_payload_without_resume_contents(self):
        request = self.reviewer()._build_tier3_request(self.resume)
        metrics = request_metadata(request)
        self.assertTrue(metrics["strict"])
        self.assertEqual(metrics["system_chars"], len(request["messages"][0]["content"]))
        self.assertEqual(metrics["user_chars"], len(request["messages"][1]["content"]))
        self.assertGreater(metrics["body_utf8_bytes"], metrics["user_chars"])
        self.assertEqual(len(metrics["prompt_sha256"]), 64)
        self.assertNotIn("messages", metrics)
        self.assertNotIn("api_key", metrics)

    def test_usage_reasoning_finish_reason_and_validation_are_recorded(self):
        reviewer = self.reviewer()
        review = reviewer._run_tier3_llm(self.resume, [])
        self.assertIsInstance(review, LLMStructuredEvaluation)
        metrics = reviewer.last_llm_diagnostics
        self.assertEqual(metrics["status"], "SUCCEEDED")
        self.assertEqual(metrics["sdk_max_retries"], 0)
        self.assertEqual(metrics["usage"]["total_tokens"], 2600)
        self.assertEqual(metrics["reasoning_tokens"], 700)
        self.assertEqual(metrics["finish_reason"], "stop")
        self.assertTrue(metrics["pydantic_valid"])
        self.assertTrue(metrics["evidence_valid"])
        self.assertGreaterEqual(metrics["elapsed_seconds"], metrics["api_elapsed_seconds"])
        self.assertNotIn("OFFLINE PRIVATE REASONING", json.dumps(metrics))

    def test_single_tier3_success_does_not_initialize_bge_or_claim_coarse_pass(self):
        class ForbiddenBGE(ModuleType):
            def __getattr__(self, name):
                raise AssertionError("Tier3-only diagnosis must not access BGE")
        client = ObservableOfflineClient()
        with patch.dict(sys.modules, {"sentence_transformers": ForbiddenBGE("sentence_transformers")}), \
                patch("engine.hybrid_engine.HybridScoringEngine", side_effect=AssertionError("full engine forbidden")) as engine:
            report = self.diagnose(client)
        engine.assert_not_called()
        result = report["evaluation"]
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(report["skipped_stages"], ["tier1", "tier2"])
        self.assertEqual(result["scoring_status"], "DEEP_SCORED")
        self.assertEqual(result["total_score"], 86.5)  # Fixed offline fixture only.
        self.assertEqual(result["routing_decision"], "PENDING_REVIEW")
        self.assertFalse(result["passed_tier2_coarse"])
        self.assertEqual(result["version_info"]["embedding_model_version"], "NOT_RUN")

    def test_length_finish_reason_fails_even_when_json_is_valid(self):
        report = self.diagnose(ObservableOfflineClient(finish_reason="length"))
        self.assertEqual(report["evaluation"]["scoring_status"], "SCORING_FAILED")
        self.assertIsNone(report["evaluation"]["total_score"])
        self.assertEqual(report["metrics"]["failure_type"], "LLMOutputTruncated")
        self.assertEqual(report["metrics"]["finish_reason"], "length")
        self.assertFalse(report["metrics"]["pydantic_valid"])
        self.assertEqual(report["metrics"]["usage"]["total_tokens"], 2600)

    def test_pydantic_and_original_evidence_failures_keep_distinct_stages(self):
        def invalid_score(request):
            output = valid_review(request)
            output["engineering"]["score"] = 101
            return output
        invalid = self.diagnose(ObservableOfflineClient(response=invalid_score))
        self.assertEqual(invalid["evaluation"]["scoring_status"], "SCORING_FAILED")
        self.assertEqual(invalid["metrics"]["failure_type"], "ValidationError")
        self.assertEqual(invalid["metrics"]["stage"], "pydantic_validation")
        self.assertTrue(any(e["path"] == "engineering.score" for e in invalid["metrics"]["validation_errors"]))
        def invented_quote(request):
            output = valid_review(request)
            output["ai_automation"]["evidence"][0]["quote"] = "OFFLINE INVENTED EXPERIENCE"
            return output
        invalid = self.diagnose(ObservableOfflineClient(response=invented_quote))
        self.assertEqual(invalid["metrics"]["failure_type"], "ValueError")
        self.assertEqual(invalid["metrics"]["stage"], "evidence_validation")
        self.assertTrue(invalid["metrics"]["pydantic_valid"])
        self.assertFalse(invalid["metrics"]["evidence_valid"])
        self.assertIsNone(invalid["evaluation"]["total_score"])

    def test_timeout_preserves_underlying_phase_without_fabricated_usage(self):
        request = httpx2.Request("POST", "https://offline.invalid/no-request-is-sent")
        failure = APITimeoutError(request=request)
        failure.__cause__ = httpx2.ReadTimeout("offline read timeout", request=request)
        report = self.diagnose(ObservableOfflineClient(failure=failure))
        self.assertEqual(report["evaluation"]["scoring_status"], "SCORING_FAILED")
        self.assertEqual(report["evaluation"]["routing_decision"], "PENDING_REVIEW")
        self.assertEqual(report["metrics"]["cause_chain"], ["APITimeoutError", "ReadTimeout"])
        self.assertEqual(report["metrics"]["stage"], "api_call")
        self.assertIsNone(report["metrics"]["usage"])
        self.assertIsNone(report["metrics"]["reasoning_tokens"])
        self.assertIsNone(report["metrics"]["finish_reason"])

    def test_success_without_provider_usage_still_records_unknown(self):
        report = self.diagnose(ObservableOfflineClient(usage=False))
        self.assertEqual(report["evaluation"]["scoring_status"], "DEEP_SCORED")
        self.assertIsNone(report["metrics"]["usage"])
        self.assertIsNone(report["metrics"]["reasoning_tokens"])

    def test_incomplete_resume_is_not_sent_to_provider(self):
        self.resume_data["data_completeness"] = "PARTIAL"
        client = ObservableOfflineClient()
        report = self.diagnose(client)
        self.assertEqual(client.calls, [])
        self.assertEqual(report["evaluation"]["scoring_status"], "DATA_INCOMPLETE")
        self.assertIsNone(report["evaluation"]["total_score"])
        self.assertEqual(report["metrics"]["status"], "NOT_CALLED")

    def test_optional_request_configuration_is_typed_and_bounded(self):
        for key, value in [("enable_thinking", "false"), ("max_tokens", 0), ("max_tokens", True),
                           ("thinking_budget", 0), ("thinking_budget", False)]:
            config = deepcopy(self.config)
            config["scoring_config"]["llm_review"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_config(config)


if __name__ == "__main__":
    unittest.main()
