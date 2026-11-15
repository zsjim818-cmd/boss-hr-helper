from config.settings import DEFAULT_CONFIG_PATH
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from schemas import CandidateCardData
from storage.scoring_cache import ScoringCache
from engine.hybrid_engine import HybridScoringEngine
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import DeterministicEmbedder, DeterministicLLMClient
from tests.mock_models import valid_review


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        self.write_config()
        self.cache = ScoringCache()
        self.e = HybridScoringEngine(str(self.path), embedder=DeterministicEmbedder(),
                                     llm_client=DeterministicLLMClient(response=valid_review), cache=self.cache)
        self.data = copy.deepcopy(BENCHMARK_CANDIDATES[0])

    def write_config(self):
        self.path.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")

    def test_identical_input_reuses_validated_score(self):
        first = self.e.evaluate(self.data)
        calls = len(self.e.llm_client.calls)
        second = self.e.evaluate(self.data)
        self.assertEqual(first.scoring_status, "DEEP_SCORED")
        self.assertTrue(second.cache_hit)
        self.assertEqual(calls, len(self.e.llm_client.calls))
        self.assertEqual(first.model_dump(exclude={"cache_hit"}), second.model_dump(exclude={"cache_hit"}))
        second.highlights.clear()
        self.assertTrue(self.e.evaluate(self.data).highlights)

    def test_content_candidate_mode_and_model_changes_invalidate(self):
        original = self.e.evaluate(self.data)
        for mutate in [lambda d: d.update(skills=d["skills"] + " Selenium"),
                       lambda d: d["_meta"].update(candidate_key="another-job:candidate")]:
            data = copy.deepcopy(self.data)
            mutate(data)
            result = self.e.evaluate(data)
            self.assertFalse(result.cache_hit)
            self.assertNotEqual(result.cache_key, original.cache_key)
        fast = self.e.evaluate(self.data, "fast_mode")
        self.assertNotEqual(fast.cache_key, original.cache_key)
        self.assertIsNone(fast.total_score)
        self.e.embedder.version = "mock-feature-v2"
        changed = self.e.evaluate(self.data)
        self.assertNotEqual(changed.cache_key, original.cache_key)
        self.e.llm_model_name = "mock-qwen-v2"
        self.assertNotEqual(self.e.evaluate(self.data).cache_key, changed.cache_key)

    def test_config_hash_changes_even_without_label_change(self):
        first = self.e.evaluate(self.data)
        self.config["scoring_config"]["thresholds"]["auto_greet_threshold"] = 95
        self.write_config()
        self.assertEqual(self.e.evaluate(self.data).cache_key, first.cache_key)
        self.e.reload_config()
        second = self.e.evaluate(self.data)
        self.assertFalse(second.cache_hit)
        self.assertNotEqual(first.version_info["job_config_version"], second.version_info["job_config_version"])
        self.assertEqual(second.routing_decision, "PENDING_REVIEW")

    def test_prompt_version_invalidates_cache(self):
        first = self.e.evaluate(self.data)
        with patch("engine.scoring_session.PROMPT_VERSION", "rubric-test-v3"):
            second = self.e.evaluate(self.data)
        self.assertNotEqual(first.cache_key, second.cache_key)

    def test_batch_snapshot_and_detached_config(self):
        original = self.e.config
        original["scoring_config"]["thresholds"]["auto_greet_threshold"] = 100
        self.assertNotEqual(original, self.e.config)
        def records():
            yield self.data
            self.config["config_version"] = "next-batch"
            self.write_config()
            with self.assertRaises(RuntimeError):
                self.e.reload_config()
            yield self.data
        results = self.e.evaluate_batch(records())
        self.assertEqual(results[0].version_info, results[1].version_info)
        self.e.reload_config()
        self.assertNotEqual(results[0].version_info, self.e.evaluate(self.data).version_info)

    def test_failed_and_incomplete_results_not_cached(self):
        for edits in [{"ok": False}, {"data_completeness": "PARTIAL"}, {"unexpected_field": "preserved"}]:
            data = copy.deepcopy(self.data)
            data.update(edits)
            self.assertFalse(self.e.evaluate(data).cache_hit)
            self.assertFalse(self.e.evaluate(data).cache_hit)
        with patch.object(self.e, "_run_tier3_llm", side_effect=TimeoutError()):
            self.assertEqual(self.e.evaluate(self.data).scoring_status, "SCORING_FAILED")
        self.assertEqual(self.e.evaluate(self.data).scoring_status, "DEEP_SCORED")

    def test_mapping_error_and_card_never_score(self):
        invalid = copy.deepcopy(self.data)
        invalid["work_experience"][0]["responsibility"] = {"wrong": "shape"}
        result = self.e.evaluate(invalid)
        self.assertEqual(result.scoring_status, "SCORING_FAILED")
        self.assertTrue(any(i.kind == "MAPPING_ERROR" and "responsibility" in i.path for i in result.data_issues))
        card = CandidateCardData(action_status={"button_text": "打招呼", "is_contacted": False},
                                 basic_info={"name": "卡片"}, _dom_anchor={"dom_card_index": 0})
        self.assertEqual(self.e.evaluate(card).scoring_status, "SCORING_FAILED")
        invalid = copy.deepcopy(self.data)
        invalid["_meta"]["candidate_key"] = " "
        self.assertEqual(self.e.evaluate(invalid).scoring_status, "SCORING_FAILED")

    def test_invalid_cache_and_config_fail_closed(self):
        original = self.e.evaluate(self.data)
        self.cache._values = {key: {**value, "scoring_mode": "fast_mode", "routing_decision": "AUTO_GREET"}
                              for key, value in self.cache._values.items()}
        result = self.e.evaluate(self.data)
        self.assertFalse(result.cache_hit)
        self.assertEqual(result.total_score, original.total_score)
        self.assertIn("cache_output_validation_failed", self.cache.issues)
        self.config["scoring_config"]["dimension_weights"]["objective_job_fit"] = -1
        self.write_config()
        with self.assertRaises(ValueError):
            self.e.reload_config()
        self.assertEqual(self.e.evaluate(self.data).version_info, original.version_info)

    def test_fresh_evaluations_stable_with_mock_models(self):
        self.config["cache"]["enabled"] = False
        self.write_config()
        self.e.reload_config()
        first, second = self.e.evaluate(self.data), self.e.evaluate(self.data)
        self.assertFalse(second.cache_hit)
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual(len(self.e.llm_client.calls), 2)

    def test_cached_missing_evidence_or_low_confidence_is_revalidated(self):
        self.e.evaluate(self.data)
        for corrupt in [lambda d: d["debug_trace"].update(tier3_dimension_evidence={}),
                        lambda d: d["debug_trace"]["tier3_dimension_evidence"]["efficiency"].update(confidence=.1)]:
            for value in self.cache._values.values():
                corrupt(value)
            result = self.e.evaluate(self.data)
            self.assertFalse(result.cache_hit)
            self.assertEqual(result.scoring_status, "DEEP_SCORED")
        self.assertEqual(len(self.e.llm_client.calls), 3)
