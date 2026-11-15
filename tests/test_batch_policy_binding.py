"""Batch-scoped Policy binding: v1 batch keeps v1, a new v2 batch scores v2."""
import time
import unittest

from fastapi.testclient import TestClient

from engine.policy_scoring_pipeline import PolicyScoringPipeline
from server.app import create_app
from storage.models import AccessContext
from tests.pipeline_support import (PipelineCase, RecordingReview,
                                    RecordingSemantic, resume_for)
from config.policy_compiler import JobPolicyCompiler
from tests.test_api_pipeline import ApiCase
from tests.test_policy_compiler import RecordedCompilerClient
from tests.policy_fixtures import offline_policy


def browser_resume(key, job_id="job"):
    value = resume_for(key)
    geek = key.split(":", 1)[1] if ":" in key else key
    value["_meta"]["encrypt_geek_id"] = geek
    value["_meta"]["encrypt_job_id"] = job_id
    return value


class BatchPolicyPipelineTests(PipelineCase):
    def test_same_candidate_scores_v1_in_batch_a_and_v2_in_batch_b(self):
        v1 = self.publish_case(job_id="job")
        batch_a = self.repo.create_scan_batch(self.ctx, "job")
        resume = browser_resume("job:geek-1")
        acquired = self.repo.acquire_candidate_for_job(
            self.ctx, "job", batch_a["batch_id"], "job:geek-1",
            source="browser", resume=resume)
        jcid = acquired["job_candidate"]["job_candidate_id"]

        pipeline = PolicyScoringPipeline(
            self.repo, semantic_service=RecordingSemantic(self.repo),
            review_service=RecordingReview(self.repo))
        result_a = pipeline.run_and_persist(
            self.ctx, "job", jcid, scoring_mode="deep_mode",
            batch_id=batch_a["batch_id"])
        evaluation_a = result_a["evaluation"]
        self.assertEqual(evaluation_a["policy_id"], v1["policy_id"])
        self.assertEqual(evaluation_a["policy_version"], 1)

        v2 = self.publish("java_backend", job_id="job")
        self.assertEqual(v2["policy_version"], 2)
        batch_b = self.repo.create_scan_batch(self.ctx, "job")
        reacquired = self.repo.acquire_candidate_for_job(
            self.ctx, "job", batch_b["batch_id"], "job:geek-1",
            source="browser", resume=resume)
        self.assertEqual(reacquired["job_candidate"]["job_candidate_id"], jcid)
        self.assertFalse(reacquired["is_new"])
        # The original binding row keeps its immutable v1 identity.
        self.assertEqual(
            self.repo.get_job_candidate(self.ctx, "job", jcid)["policy_version"], 1)

        result_b = pipeline.run_and_persist(
            self.ctx, "job", jcid, scoring_mode="deep_mode",
            batch_id=batch_b["batch_id"])
        evaluation_b = result_b["evaluation"]
        self.assertEqual(evaluation_b["policy_id"], v2["policy_id"])
        self.assertEqual(evaluation_b["policy_version"], 2)
        self.assertNotEqual(evaluation_b["pipeline_evaluation_id"],
                            evaluation_a["pipeline_evaluation_id"])
        self.assertEqual(evaluation_b["job_candidate_id"], jcid)

        results = self.repo.list_pipeline_results(self.ctx, "job", jcid)
        self.assertEqual({item["policy_version"] for item in results}, {1, 2})
        self.assertEqual(len(results), 2)

        # Re-scoring batch A reuses the immutable v1 result.
        again_a = pipeline.run_and_persist(
            self.ctx, "job", jcid, scoring_mode="deep_mode",
            batch_id=batch_a["batch_id"])
        self.assertTrue(again_a["reused"])
        self.assertEqual(again_a["evaluation"]["pipeline_evaluation_id"],
                         evaluation_a["pipeline_evaluation_id"])
        # No batch keeps the candidate's original binding (backwards compatible).
        default = pipeline.run_and_persist(
            self.ctx, "job", jcid, scoring_mode="deep_mode")
        self.assertTrue(default["reused"])
        self.assertEqual(default["evaluation"]["policy_version"], 1)

    def test_batch_must_contain_the_candidate(self):
        self.publish_case(job_id="job")
        batch_a = self.repo.create_scan_batch(self.ctx, "job")
        acquired = self.repo.acquire_candidate_for_job(
            self.ctx, "job", batch_a["batch_id"], "cand-1",
            resume=resume_for("cand-1"))
        jcid = acquired["job_candidate"]["job_candidate_id"]
        other_batch = self.repo.create_scan_batch(self.ctx, "job")
        with self.assertRaises(ValueError):
            self.repo.resolve_batch_policy(
                self.ctx, "job", other_batch["batch_id"], jcid)


class BatchPolicyApiTests(ApiCase):
    def create_batch(self):
        response = self.client.post("/api/jobs/job/batches", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["batch_id"]

    def import_into(self, batch_id, key="job:geek-1"):
        response = self.client.post(
            f"/api/jobs/job/batches/{batch_id}/candidates", headers=self.headers,
            json={"candidate_key": key, "source": "browser",
                  "resume": browser_resume(key)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["job_candidate"]["job_candidate_id"]

    def score(self, jcid, batch_id, key):
        started = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": "deep_mode", "batch_id": batch_id,
                  "idempotency_key": key})
        self.assertEqual(started.status_code, 200, started.text)
        body = started.json()
        if body.get("reused"):
            return body["evaluation"]
        finished = self.wait_task(body["task"]["pipeline_task_id"])
        self.assertEqual(finished["task"]["status"], "SUCCEEDED", finished)
        return finished["evaluation"]

    def publish_v2(self):
        return self.publish("java_backend", job_id="job")

    def test_idempotency_key_is_scoped_to_the_batch(self):
        batch_a = self.create_batch()
        jcid = self.import_into(batch_a)
        first = self.score(jcid, batch_a, "shared-key")
        self.assertEqual(first["policy_version"], 1)

        self.publish_v2()
        batch_b = self.create_batch()
        self.import_into(batch_b)
        conflict = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": "deep_mode", "batch_id": batch_b,
                  "idempotency_key": "shared-key"})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["error_type"], "PipelineConflict")

    def test_http_scoring_uses_the_batch_policy_version(self):
        batch_a = self.create_batch()
        jcid = self.import_into(batch_a)
        evaluation_a = self.score(jcid, batch_a, "batch-a")
        self.assertEqual(evaluation_a["policy_version"], 1)

        v2 = self.publish_v2()
        batch_b = self.create_batch()
        jcid_b = self.import_into(batch_b)
        self.assertEqual(jcid_b, jcid)
        evaluation_b = self.score(jcid, batch_b, "batch-b")
        self.assertEqual(evaluation_b["policy_id"], v2["policy_id"])
        self.assertEqual(evaluation_b["policy_version"], 2)
        self.assertNotEqual(evaluation_b["pipeline_evaluation_id"],
                            evaluation_a["pipeline_evaluation_id"])

        detail = self.client.get(
            f"/api/jobs/job/candidates/{jcid}", headers=self.headers).json()
        versions = sorted(item["policy_version"] for item in detail["evaluations"])
        self.assertEqual(versions, [1, 2])

        # Scoring batch A again is reused; no duplicate v1 result is created.
        again = self.score(jcid, batch_a, "batch-a-again")
        self.assertEqual(again["pipeline_evaluation_id"],
                         evaluation_a["pipeline_evaluation_id"])


if __name__ == "__main__":
    unittest.main()
