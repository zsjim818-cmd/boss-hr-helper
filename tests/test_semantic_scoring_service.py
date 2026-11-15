"""Repository-bound Step 6 integration tests for the SemanticScoringService."""
import unittest

from engine.prescreen_engine import PreliminaryScreeningEngine
from engine.embedding_service import PolicyEmbeddingService, ResumeEmbeddingService
from engine.semantic_scoring_service import SemanticScoringService
from tests.test_policy_embeddings import RecordingEmbedder, profiles
from tests.prescreen_support import PrescreenDatabaseCase


class SemanticScoringServiceTests(PrescreenDatabaseCase):
    def setUp(self):
        super().setUp()
        self.v1 = self.publish("java_backend", "job")
        self.acquired = self.acquire("job", "candidate")
        self.jcid = self.acquired["job_candidate"]["job_candidate_id"]
        self.profiles = profiles()
        self.embedder = RecordingEmbedder()
        self.resume_service = ResumeEmbeddingService(self.repo, self.embedder, profiles=self.profiles)
        self.service = SemanticScoringService(self.repo, profiles=self.profiles)

    def _prepare_assets(self):
        PolicyEmbeddingService(self.repo, self.embedder, profiles=self.profiles).prepare_policy(
            self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"])
        self.resume_service.prepare_candidate(self.ctx, "job", self.jcid)

    def test_scored_evaluation_binds_full_identity_and_is_tier2_only(self):
        self._prepare_assets()
        evaluation = self.service.evaluate(self.ctx, "job", self.jcid)
        self.assertIn(evaluation.scoring_status, {"SEMANTIC_SCORED", "PARTIAL_SEMANTIC"})
        self.assertIsNotNone(evaluation.semantic_score)
        self.assertEqual(evaluation.org_id, "org")
        self.assertEqual(evaluation.job_id, "job")
        self.assertEqual(evaluation.job_candidate_id, self.jcid)
        self.assertEqual(evaluation.policy_id, self.v1["policy_id"])
        self.assertEqual(evaluation.policy_version, self.v1["policy_version"])
        self.assertEqual(evaluation.resume_id, self.acquired["sealed_input"]["resume_id"])
        self.assertEqual(evaluation.resume_content_hash,
                         self.acquired["resume_snapshot"]["content_hash"])
        self.assertEqual(evaluation.embedding_profile_id, self.profiles.embedding.embedding_profile_id)
        self.assertEqual(evaluation.embedding_profile_hash, self.profiles.embedding_hash)
        self.assertEqual(evaluation.chunker_profile_hash, self.profiles.chunker_hash)
        self.assertEqual(evaluation.semantic_profile_hash, self.profiles.semantic_hash)
        self.assertIsNotNone(evaluation.anchor_manifest_hash)
        self.assertIsNotNone(evaluation.chunk_manifest_hash)
        self.assertIsNotNone(evaluation.candidate_embedding_manifest_hash)
        self.assertEqual(evaluation.score_kind, "POLICY_TIER2_SEMANTIC")
        self.assertEqual(evaluation.assessment_scope, "TIER2_ONLY")
        self.assertFalse(evaluation.routing_authority)
        self.assertFalse(evaluation.hard_filter_override)
        self.assertFalse(evaluation.thresholds_applied)
        self.assertFalse(evaluation.formal_evaluation)
        self.assertTrue(evaluation.dimension_scores)

    def test_missing_assets_report_asset_incomplete_without_fabricated_score(self):
        evaluation = self.service.evaluate(self.ctx, "job", self.jcid)
        self.assertEqual(evaluation.scoring_status, "ASSET_INCOMPLETE")
        self.assertIsNone(evaluation.semantic_score)
        self.assertEqual(evaluation.semantic_coverage, 0.0)
        self.assertEqual(evaluation.dimension_scores, [])
        self.assertIsNone(evaluation.anchor_manifest_hash)

    def test_semantic_result_never_overrides_a_tier0_decision(self):
        self._prepare_assets()
        preliminary = PreliminaryScreeningEngine(self.repo).evaluate(self.ctx, "job", self.jcid)
        evaluation = self.service.evaluate(self.ctx, "job", self.jcid, preliminary=preliminary)
        self.assertEqual(evaluation.upstream_preliminary_status, preliminary.scoring_status)
        self.assertFalse(evaluation.hard_filter_override)
        self.assertFalse(evaluation.routing_authority)
        self.assertIsNotNone(evaluation.semantic_score)

    def test_candidate_keeps_bound_policy_version_after_a_newer_policy_is_published(self):
        self._prepare_assets()
        self.publish("java_backend", "job")
        evaluation = self.service.evaluate(self.ctx, "job", self.jcid)
        self.assertEqual(evaluation.policy_id, self.v1["policy_id"])
        self.assertEqual(evaluation.policy_version, self.v1["policy_version"])

    def test_repeated_evaluation_is_deterministic_and_adds_no_model_calls(self):
        self._prepare_assets()
        before = self.embedder.encoded_count
        first = self.service.evaluate(self.ctx, "job", self.jcid)
        second = self.service.evaluate(self.ctx, "job", self.jcid)
        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertEqual(self.embedder.encoded_count, before)


if __name__ == "__main__":
    unittest.main()
