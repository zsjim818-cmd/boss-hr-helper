"""P0: fast->deep stage upgrade, idempotency identity conflicts, task binding."""
import time
import unittest

from fastapi.testclient import TestClient

from engine.policy_scoring_pipeline import PolicyScoringPipeline
from server.app import create_app
from storage.repositories.pipeline import PipelineConflict
from tests.pipeline_support import (PipelineCase, RecordingReview,
                                    RecordingSemantic, resume_for)


class StageUpgradeRepositoryTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.version = self.publish_case(job_id="job")
        acquired = self.import_resume(key="cand")
        self.jcid = acquired["job_candidate"]["job_candidate_id"]
        self.semantic = RecordingSemantic(self.repo)
        self.review = RecordingReview(self.repo)
        self.pipeline = PolicyScoringPipeline(
            self.repo, semantic_service=self.semantic, review_service=self.review)

    def test_fast_then_deep_upgrade_then_idempotent_deep_replay(self):
        fast = self.pipeline.run_and_persist(self.ctx, "job", self.jcid,
                                             scoring_mode="fast_mode")
        self.assertEqual(fast["evaluation"]["scoring_status"], "COARSE_ONLY")
        self.assertEqual(fast["evaluation"]["scoring_mode"], "fast_mode")
        self.assertEqual(self.semantic.calls, 0)
        self.assertEqual(self.review.calls, 0)

        deep = self.pipeline.run_and_persist(self.ctx, "job", self.jcid,
                                             scoring_mode="deep_mode")
        self.assertFalse(deep["reused"])
        self.assertEqual(deep["evaluation"]["scoring_status"], "DEEP_SCORED")
        self.assertEqual(deep["evaluation"]["scoring_mode"], "deep_mode")
        self.assertEqual(self.semantic.calls, 1)
        self.assertEqual(self.review.calls, 1)
        self.assertNotEqual(deep["evaluation"]["pipeline_evaluation_id"],
                            fast["evaluation"]["pipeline_evaluation_id"])
        history = self.repo.list_pipeline_results(self.ctx, "job", self.jcid)
        self.assertEqual([item["scoring_mode"] for item in history], ["fast_mode", "deep_mode"])
        latest = self.repo.latest_pipeline_result(self.ctx, "job", self.jcid)
        self.assertEqual(latest["scoring_mode"], "deep_mode")

        replay = self.pipeline.run_and_persist(self.ctx, "job", self.jcid,
                                               scoring_mode="deep_mode")
        self.assertTrue(replay["reused"])
        self.assertEqual(replay["evaluation"]["pipeline_evaluation_id"],
                         deep["evaluation"]["pipeline_evaluation_id"])
        self.assertEqual((self.semantic.calls, self.review.calls), (1, 1))
        self.assertEqual(len(self.repo.list_pipeline_results(self.ctx, "job", self.jcid)), 2)

        # A fast request after a deep result reuses the richer deep result.
        fast_after = self.pipeline.run_and_persist(self.ctx, "job", self.jcid,
                                                   scoring_mode="fast_mode")
        self.assertTrue(fast_after["reused"])
        self.assertEqual(fast_after["evaluation"]["scoring_mode"], "deep_mode")
        self.assertEqual(len(self.repo.list_pipeline_results(self.ctx, "job", self.jcid)), 2)

    def test_fast_idempotency_reuses_fast_result(self):
        first = self.pipeline.run_and_persist(self.ctx, "job", self.jcid,
                                              scoring_mode="fast_mode")
        second = self.pipeline.run_and_persist(self.ctx, "job", self.jcid,
                                               scoring_mode="fast_mode")
        self.assertTrue(second["reused"])
        self.assertEqual(second["evaluation"]["pipeline_evaluation_id"],
                         first["evaluation"]["pipeline_evaluation_id"])
        self.assertEqual(len(self.repo.list_pipeline_results(self.ctx, "job", self.jcid)), 1)

    def test_same_idempotency_key_with_different_candidate_is_conflict(self):
        second = self.import_resume(key="cand2")
        jcid2 = second["job_candidate"]["job_candidate_id"]
        self.repo.create_pipeline_task(self.ctx, "job", self.jcid, "deep_mode",
                                       idempotency_key="shared-key")
        with self.assertRaises(PipelineConflict):
            self.repo.create_pipeline_task(self.ctx, "job", jcid2, "deep_mode",
                                           idempotency_key="shared-key")

    def test_same_idempotency_key_with_different_mode_is_conflict(self):
        self.repo.create_pipeline_task(self.ctx, "job", self.jcid, "fast_mode",
                                       idempotency_key="mode-key")
        with self.assertRaises(PipelineConflict):
            self.repo.create_pipeline_task(self.ctx, "job", self.jcid, "deep_mode",
                                           idempotency_key="mode-key")

    def test_task_identity_mismatch_is_rejected(self):
        second = self.import_resume(key="cand2")
        jcid2 = second["job_candidate"]["job_candidate_id"]
        task = self.repo.create_pipeline_task(self.ctx, "job", self.jcid, "deep_mode")
        with self.assertRaises(PipelineConflict):
            self.pipeline.run_and_persist(self.ctx, "job", jcid2, scoring_mode="deep_mode",
                                          task_id=task["pipeline_task_id"])
        with self.assertRaises(PipelineConflict):
            self.pipeline.run_and_persist(self.ctx, "job", self.jcid, scoring_mode="fast_mode",
                                          task_id=task["pipeline_task_id"])
        # The rejected task must never have been executed for a foreign binding.
        self.assertEqual(self.repo.list_pipeline_results(self.ctx, "job", jcid2), [])


class StageUpgradeApiTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.version = self.publish_case(job_id="job")
        app = create_app(self.repo)
        self.client = TestClient(app)
        self.addCleanup(lambda: app.state.executor.shutdown(wait=True))
        self.headers = {"X-Org-Id": "org", "X-User-Id": "owner"}
        batch = self.client.post("/api/jobs/job/batches", headers=self.headers).json()
        imported = self.client.post(
            f"/api/jobs/job/batches/{batch['batch_id']}/candidates", headers=self.headers,
            json={"candidate_key": "api-cand", "resume": resume_for("api-cand")})
        self.jcid = imported.json()["job_candidate"]["job_candidate_id"]

    def wait_task(self, task_id, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = self.client.get(f"/api/jobs/job/tasks/{task_id}",
                                   headers=self.headers).json()
            if body["task"]["status"] in ("SUCCEEDED", "FAILED"):
                return body
            time.sleep(0.05)
        self.fail("task did not finish")

    def score(self, mode, key=None):
        body = {"scoring_mode": mode}
        if key:
            body["idempotency_key"] = key
        response = self.client.post(f"/api/jobs/job/candidates/{self.jcid}/score",
                                    headers=self.headers, json=body)
        return response

    def test_api_fast_to_deep_upgrade_flow(self):
        started = self.score("fast_mode")
        finished = self.wait_task(started.json()["task"]["pipeline_task_id"])
        self.assertEqual(finished["evaluation"]["scoring_status"], "COARSE_ONLY")
        deep_started = self.score("deep_mode")
        self.assertNotEqual(deep_started.status_code, 409)
        deep_finished = self.wait_task(deep_started.json()["task"]["pipeline_task_id"])
        self.assertEqual(deep_finished["evaluation"]["scoring_status"], "DEEP_SCORED")
        evaluations = self.client.get(
            f"/api/jobs/job/candidates/{self.jcid}/evaluations", headers=self.headers).json()
        self.assertEqual([item["scoring_mode"] for item in evaluations["history"]],
                         ["fast_mode", "deep_mode"])
        self.assertEqual(evaluations["latest"]["scoring_mode"], "deep_mode")
        replay = self.score("deep_mode")
        self.assertTrue(replay.json()["reused"])
        self.assertEqual(replay.json()["evaluation"]["pipeline_evaluation_id"],
                         deep_finished["evaluation"]["pipeline_evaluation_id"])

    def test_api_idempotency_conflict_returns_409(self):
        other = self.client.post(
            f"/api/jobs/job/batches/{self.client.post('/api/jobs/job/batches', headers=self.headers).json()['batch_id']}/candidates",
            headers=self.headers,
            json={"candidate_key": "api-cand2", "resume": resume_for("api-cand2")})
        other_jcid = other.json()["job_candidate"]["job_candidate_id"]
        first = self.score("deep_mode", key="api-key")
        self.assertEqual(first.status_code, 200, first.text)
        self.wait_task(first.json()["task"]["pipeline_task_id"])
        conflict = self.client.post(
            f"/api/jobs/job/candidates/{other_jcid}/score", headers=self.headers,
            json={"scoring_mode": "deep_mode", "idempotency_key": "api-key"})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error_type"], "PipelineConflict")


if __name__ == "__main__":
    unittest.main()
