"""Stage 6 pipeline orchestration: status matrix and model-call boundaries."""
import unittest

from storage.repositories import PermissionDenied
from tests.pipeline_support import PipelineCase, RecordingReview, RecordingSemantic, raise_if_called


class PipelineStatusTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.version = self.publish_case()

    def _acquire(self, **changes):
        acquired = self.import_resume(**changes)
        return acquired["job_candidate"]["job_candidate_id"]

    def test_fast_mode_stops_before_semantic_and_tier3(self):
        jcid = self._acquire()
        semantic = RecordingSemantic(self.repo)
        review = RecordingReview(self.repo)
        result = self.pipeline(semantic=semantic, review=review).run(
            self.ctx, "job", jcid, scoring_mode="fast_mode")
        self.assertEqual(result.status, "COARSE_ONLY")
        self.assertEqual(semantic.calls, 0)
        self.assertEqual(review.calls, 0)
        self.assertEqual(result.total_score, result.preliminary["preliminary_score"])
        self.assertIsNone(result.routing_suggestion)

    def test_hard_filter_failure_never_calls_models(self):
        # The java policy requires a bachelor degree; a junior-college resume fails.
        jcid = self._acquire(degree="大专",
                             education=[{"school": "Synthetic school", "degree": "大专"}])
        semantic = RecordingSemantic(self.repo)
        review = RecordingReview(self.repo)
        result = self.pipeline(semantic=semantic, review=review).run(self.ctx, "job", jcid)
        self.assertEqual(result.status, "HARD_FILTERED")
        self.assertEqual(result.hard_filter_status, "FAIL")
        self.assertEqual(semantic.calls, 0)
        self.assertEqual(review.calls, 0)

    def test_hard_unknown_runs_semantic_but_never_tier3(self):
        # "面议" salary makes the hard condition unknown, not a failure.
        jcid = self._acquire(expectation={"city": "北京", "salary": "面议"})
        semantic = RecordingSemantic(self.repo)
        review = RecordingReview(self.repo, error=RuntimeError("Tier3 must not be called"))
        result = self.pipeline(semantic=semantic, review=review).run(self.ctx, "job", jcid)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertEqual(result.hard_filter_status, "UNKNOWN")
        self.assertEqual(semantic.calls, 1)
        self.assertEqual(review.calls, 0)
        self.assertIsNotNone(result.semantic)

    def test_below_coarse_stops_before_any_model_work(self):
        # A sales-flavoured resume on the java policy falls below the coarse gate.
        jcid = self._acquire(
            skills="企业客户销售, CRM, 合同谈判",
            work_experience=[{"company": "Synthetic company", "position": "Sales",
                              "responsibility": "负责企业客户需求访谈、合同谈判与回款跟进。",
                              "keywords": []}],
            expectation={"city": "北京", "salary": "12K/月"})
        semantic = RecordingSemantic(self.repo)
        review = RecordingReview(self.repo)
        result = self.pipeline(semantic=semantic, review=review).run(self.ctx, "job", jcid)
        self.assertEqual(result.status, "COARSE_ONLY")
        self.assertEqual(semantic.calls, 0)
        self.assertEqual(review.calls, 0)

    def test_partial_semantic_coverage_routes_to_review_without_tier3(self):
        jcid = self._acquire()
        semantic = RecordingSemantic(self.repo, status="PARTIAL_SEMANTIC", coverage=0.5)
        review = RecordingReview(self.repo, error=RuntimeError("Tier3 must not be called"))
        result = self.pipeline(semantic=semantic, review=review).run(self.ctx, "job", jcid)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertEqual(semantic.calls, 1)
        self.assertEqual(review.calls, 0)
        self.assertTrue(any("语义" in reason for reason in result.review_reasons))

    def test_policy_anchor_missing_cannot_be_masked_by_high_scores(self):
        jcid = self._acquire()
        semantic = RecordingSemantic(self.repo, dimension_status="POLICY_ANCHOR_MISSING",
                                     coverage=0.0)
        review = RecordingReview(self.repo, error=RuntimeError("Tier3 must not be called"))
        result = self.pipeline(semantic=semantic, review=review).run(self.ctx, "job", jcid)
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertEqual(review.calls, 0)

    def test_full_pass_runs_tier3_and_records_same_binding(self):
        jcid = self._acquire()
        semantic = RecordingSemantic(self.repo)
        review = RecordingReview(self.repo)
        outcome = self.pipeline(semantic=semantic, review=review).run_and_persist(
            self.ctx, "job", jcid, scoring_mode="deep_mode")
        evaluation = outcome["evaluation"]
        self.assertEqual(evaluation["scoring_status"], "DEEP_SCORED")
        self.assertEqual(semantic.calls, 1)
        self.assertEqual(review.calls, 1)
        binding = self.repo.get_job_candidate(self.ctx, "job", jcid)
        self.assertEqual(evaluation["policy_hash"], binding["policy_id"] and
                         self.version["policy_hash"])
        self.assertEqual(evaluation["policy_version"], binding["policy_version"])
        import json
        tier3 = json.loads(evaluation["tier3_json"])
        self.assertEqual(tier3["total_score"], evaluation["total_score"])
        self.assertEqual(tier3["routing_suggestion"], evaluation["routing_suggestion"])
        self.assertEqual(outcome["task"]["status"], "SUCCEEDED")

    def test_tier3_technical_failure_leaves_no_formal_result(self):
        jcid = self._acquire()
        semantic = RecordingSemantic(self.repo)
        review = RecordingReview(self.repo, error=RuntimeError("provider exploded"))
        outcome = self.pipeline(semantic=semantic, review=review).run_and_persist(
            self.ctx, "job", jcid)
        self.assertIsNone(outcome["evaluation"])
        self.assertEqual(outcome["task"]["status"], "FAILED")
        self.assertEqual(outcome["task"]["error_type"], "RuntimeError")
        self.assertEqual(self.repo.list_pipeline_results(self.ctx, "job", jcid), [])
        attempts = self.repo.list_pipeline_attempts(self.ctx, "job", jcid)
        self.assertEqual([item["status"] for item in attempts], ["TECHNICAL_FAILED"])

    def test_repeated_requests_reuse_the_formal_result_without_model_calls(self):
        jcid = self._acquire()
        semantic = RecordingSemantic(self.repo)
        review = RecordingReview(self.repo)
        pipeline = self.pipeline(semantic=semantic, review=review)
        first = pipeline.run_and_persist(self.ctx, "job", jcid, idempotency_key="k1")
        second = pipeline.run_and_persist(self.ctx, "job", jcid, idempotency_key="k2")
        self.assertTrue(second["reused"])
        self.assertEqual(second["evaluation"]["pipeline_evaluation_id"],
                         first["evaluation"]["pipeline_evaluation_id"])
        self.assertEqual(semantic.calls, 1)
        self.assertEqual(review.calls, 1)
        self.assertEqual(len(self.repo.list_pipeline_results(self.ctx, "job", jcid)), 1)
        # Same idempotency key returns the same task.
        task_a = self.repo.create_pipeline_task(self.ctx, "job", jcid, "deep_mode",
                                                idempotency_key="same")
        task_b = self.repo.create_pipeline_task(self.ctx, "job", jcid, "deep_mode",
                                                idempotency_key="same")
        self.assertEqual(task_a["pipeline_task_id"], task_b["pipeline_task_id"])

    def test_unauthorized_identity_is_rejected(self):
        from storage.models import AccessContext
        jcid = self._acquire()
        pipeline = self.pipeline(semantic=RecordingSemantic(self.repo),
                                 review=RecordingReview(self.repo))
        with self.assertRaises(PermissionDenied):
            pipeline.run(AccessContext("org", "not-a-member"), "job", jcid)

    def test_unsealed_candidate_cannot_be_scored(self):
        batch = self.repo.create_scan_batch(self.ctx, "job")
        incomplete = self.import_resume()
        # Reuse the same helper but strip the seal via a fresh incomplete acquisition.
        from tests.pipeline_support import resume_for
        acquired = self.repo.acquire_candidate_for_job(
            self.ctx, "job", batch["batch_id"], "unsealed",
            resume=resume_for("unsealed", data_completeness="PARTIAL"))
        jcid = acquired["job_candidate"]["job_candidate_id"]
        semantic = RecordingSemantic(self.repo)
        outcome = self.pipeline(semantic=semantic, review=RecordingReview(self.repo)).run_and_persist(
            self.ctx, "job", jcid)
        self.assertIsNone(outcome["evaluation"])
        self.assertEqual(outcome["task"]["status"], "FAILED")
        self.assertEqual(semantic.calls, 0)


    def test_three_jobs_are_fully_isolated(self):
        # Criterion 1/2: three heterogeneous jobs in one database.
        from schemas.job_policy import JobScoringPolicy
        java_jcid = self._acquire()
        dimensions = {}
        for case_id, job_id in (("ai_automation", "job-ai"), ("b2b_sales", "job-b2b")):
            self.publish_case(case_id, job_id=job_id)
            acquired = self.import_resume(job_id=job_id, key="k-" + case_id)
            jcid = acquired["job_candidate"]["job_candidate_id"]
            result = self.pipeline(semantic=RecordingSemantic(self.repo),
                                   review=RecordingReview(self.repo)).run(
                self.ctx, job_id, jcid)
            self.assertEqual(result.job_id, job_id)
            self.assertEqual(result.policy_id,
                             self.repo.get_job_candidate(self.ctx, job_id, jcid)["policy_id"])
            policy = JobScoringPolicy.model_validate_json(
                self.repo.get_current_policy(self.ctx, job_id)["policy_json"])
            dimensions[case_id] = [item.dimension_id for item in policy.scoring_dimensions]
        java_policy = JobScoringPolicy.model_validate_json(
            self.repo.get_current_policy(self.ctx, "job")["policy_json"])
        dimensions["java_backend"] = [item.dimension_id for item in java_policy.scoring_dimensions]
        self.assertEqual(len({tuple(value) for value in dimensions.values()}), 3)
        self.assertEqual({key: len(value) for key, value in dimensions.items()},
                         {"ai_automation": 4, "java_backend": 5, "b2b_sales": 4})
        with self.assertRaises(LookupError):
            self.repo.get_job_candidate(self.ctx, "job-ai", java_jcid)

    def test_concurrent_scoring_creates_one_formal_result(self):
        from concurrent.futures import ThreadPoolExecutor
        jcid = self._acquire()
        semantic = RecordingSemantic(self.repo)
        review = RecordingReview(self.repo)

        def run_once():
            return self.pipeline(semantic=semantic, review=review).run_and_persist(
                self.ctx, "job", jcid, idempotency_key="concurrent")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_once), pool.submit(run_once)]
            [future.result() for future in futures]
        self.assertEqual(len(self.repo.list_pipeline_results(self.ctx, "job", jcid)), 1)


if __name__ == "__main__":
    unittest.main()
