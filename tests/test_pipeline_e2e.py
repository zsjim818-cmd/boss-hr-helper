"""Stage 6 end-to-end: three heterogeneous jobs through Tier 0 -> 1 -> 2 -> 3.

Every tier provably reads one immutable bound Policy version (same hash), the
Tier 3 prompt/schema dimensions come from that policy (4/5/4 different
dimensions across the cases), and the persisted attempt is immutable.

Offline by construction: deterministic synthetic vectors for Tier 2 and a
deterministic mock LLM for Tier 3; no BGE download and no network.
"""
from copy import deepcopy
import json
import sqlite3
import unittest

from engine.pipeline_cli import offline_response
from engine.policy_interpreter import PolicyInterpreter
from engine.policy_llm_reviewer import PolicyReviewService, weighted_total
from engine.policy_semantic_ranker import evaluate_policy_semantics
from engine.prescreen_engine import PreliminaryScreeningEngine
from engine.semantic_profiles import load_semantic_profiles
from schemas.job_policy import JobScoringPolicy
from tests.mock_models import DeterministicLLMClient
from tests.prescreen_support import PrescreenDatabaseCase
from tests.test_policy_semantic_ranker import anchor_rows, candidate_row, unit


CASE_PROFILES = {
    "ai_automation": {
        "job_id": "job-ai",
        "skills": "Python, LangGraph, RAG, Playwright, Docker",
        "responsibility": "负责AI自动化工作流、RAG知识库与接口测试交付。",
    },
    "java_backend": {
        "job_id": "job-java",
        "skills": "Java, SQL, Spring Boot, Quarkus, 接口自动化测试, Kafka",
        "responsibility": "负责Java服务端开发、SQL事务设计与接口自动化测试。",
    },
    "b2b_sales": {
        "job_id": "job-sales",
        "skills": "企业客户销售, CRM, 合同谈判, 销售漏斗",
        "responsibility": "负责企业客户需求访谈、合同谈判、签约与回款跟进。",
    },
}


class PipelineEndToEndTests(PrescreenDatabaseCase):
    def setUp(self):
        super().setUp()
        self.semantic_profile = load_semantic_profiles().semantic

    def _resume(self, case_id):
        profile = CASE_PROFILES[case_id]
        return self.resume(
            key="cand-" + case_id,
            skills=profile["skills"],
            work_experience=[{"company": "Synthetic company", "position": "Engineer",
                              "responsibility": profile["responsibility"],
                              "keywords": []}])

    def _tier2(self, plan, candidate_rows=None):
        anchors = plan.policy.semantic_anchors
        vectors = {anchor.anchor_id: unit(len(anchors), index)
                   for index, anchor in enumerate(anchors)}
        rows = candidate_rows or [candidate_row("chunk_" + anchor.anchor_id, vectors[anchor.anchor_id])
                                  for anchor in anchors]
        return evaluate_policy_semantics(plan, rows, anchor_rows(plan.policy, vectors),
                                         self.semantic_profile)

    def test_three_heterogeneous_jobs_run_the_full_tier_pipeline(self):
        observed_dimensions = {}
        for case_id, profile in CASE_PROFILES.items():
            with self.subTest(case_id=case_id):
                job_id = profile["job_id"]
                version = self.publish(case_id, job_id=job_id)
                acquired = self.acquire(job_id, key="cand-" + case_id,
                                        resume=self._resume(case_id))
                jcid = acquired["job_candidate"]["job_candidate_id"]
                policy = JobScoringPolicy.model_validate_json(version["policy_json"])
                expected_dimensions = [item.dimension_id for item in policy.scoring_dimensions]
                observed_dimensions[case_id] = expected_dimensions

                # Tier 0/1: repository-bound preliminary screening.
                preliminary = PreliminaryScreeningEngine(self.repo).evaluate(
                    self.ctx, job_id, jcid)
                self.assertEqual([item.dimension_id for item in preliminary.dimension_scores],
                                 expected_dimensions)
                self.assertEqual(preliminary.hard_filter_status, "PASS")
                self.assertEqual(preliminary.policy_hash, version["policy_hash"])
                self.assertIn(preliminary.scoring_status,
                              ("READY_FOR_DEEP", "BELOW_COARSE"))

                # Tier 2: same interpreter binding, deterministic vectors.
                interpreter = PolicyInterpreter(self.repo)
                bound = interpreter.load_for_candidate(self.ctx, job_id, jcid)
                plan = bound.plan
                self.assertEqual(plan.policy_hash, version["policy_hash"])
                semantic = self._tier2(plan)
                self.assertEqual([item.dimension_id for item in semantic.dimension_scores],
                                 expected_dimensions)
                self.assertIsNotNone(semantic.semantic_score)

                # Tier 3: dynamic prompt/schema from this policy only.
                service = PolicyReviewService(
                    self.repo, interpreter=interpreter,
                    client=DeterministicLLMClient(response=offline_response),
                    model_name="offline-tier3-mock")
                evaluation = service.evaluate(self.ctx, job_id, jcid)
                self.assertEqual([item.dimension_id for item in evaluation.dimensions],
                                 expected_dimensions)
                self.assertEqual(evaluation.policy_hash, version["policy_hash"])
                self.assertEqual(evaluation.policy_version, version["policy_version"])
                self.assertEqual(evaluation.total_score, weighted_total(evaluation.dimensions))
                thresholds = policy.thresholds
                if evaluation.total_score >= thresholds.deep_recommend:
                    expected_route = "DEEP_RECOMMEND"
                elif evaluation.total_score >= thresholds.deep_review:
                    expected_route = "HUMAN_REVIEW"
                else:
                    expected_route = "NOT_RECOMMENDED"
                self.assertEqual(evaluation.routing_suggestion, expected_route)
                self.assertTrue(all(review.evidence for review in evaluation.dimensions))

                # Persistence: append-only attempt with immutable guards.
                outcome = service.evaluate_and_persist(self.ctx, job_id, jcid)
                attempt = outcome["attempt"]
                self.assertEqual(attempt["status"], "SUCCEEDED")
                stored = json.loads(attempt["result_json"])
                self.assertEqual(stored["total_score"], outcome["evaluation"].total_score)
                self.assertEqual(stored["policy_hash"], version["policy_hash"])
                connection = sqlite3.connect(self.path)
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute("UPDATE evaluation_attempts SET status='FAILED'")
                    connection.rollback()
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute("DELETE FROM evaluation_attempts")
                finally:
                    connection.close()
        # Heterogeneity: the three policies really do score different dimensions.
        dimension_sets = {case: tuple(ids) for case, ids in observed_dimensions.items()}
        self.assertEqual(len({ids for ids in dimension_sets.values()}), 3)
        self.assertEqual({case: len(ids) for case, ids in dimension_sets.items()},
                         {"ai_automation": 4, "java_backend": 5, "b2b_sales": 4})

    def test_incomplete_sealed_input_refuses_tier3_score(self):
        job_id = CASE_PROFILES["java_backend"]["job_id"]
        self.publish("java_backend", job_id=job_id)
        incomplete = self.resume(key="cand-incomplete", data_completeness="PARTIAL",
                                 collection_errors=["missing work history"])
        acquired = self.acquire(job_id, key="cand-incomplete", resume=incomplete)
        jcid = acquired["job_candidate"]["job_candidate_id"]
        service = PolicyReviewService(
            self.repo, client=DeterministicLLMClient(response=offline_response),
            model_name="offline-tier3-mock")
        with self.assertRaisesRegex(ValueError, "DATA_INCOMPLETE"):
            service.evaluate(self.ctx, job_id, jcid)

    def test_tier3_requires_an_authorized_identity(self):
        job_id = CASE_PROFILES["java_backend"]["job_id"]
        self.publish("java_backend", job_id=job_id)
        acquired = self.acquire(job_id, key="cand-auth", resume=self._resume("java_backend"))
        jcid = acquired["job_candidate"]["job_candidate_id"]
        from storage.models import AccessContext
        from storage.repositories import PermissionDenied
        service = PolicyReviewService(
            self.repo, client=DeterministicLLMClient(response=offline_response),
            model_name="offline-tier3-mock")
        with self.assertRaises(PermissionDenied):
            service.evaluate(AccessContext("org", "not-a-member"), job_id, jcid)


if __name__ == "__main__":
    unittest.main()
