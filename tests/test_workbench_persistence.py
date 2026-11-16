"""Sprint 5 Batch 2: 012 persistence, server pagination and PARTIAL re-extract."""
import time
import unittest

from fastapi.testclient import TestClient

from browser.mock_adapter import MockBossAdapter
from browser.mock_data import mock_cards
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from server.app import create_app
from tests.pipeline_support import RecordingReview, RecordingSemantic, resume_for
from tests.test_api_pipeline import ApiCase


class WorkbenchPersistenceTests(ApiCase):
    def _batch(self, job_id="job"):
        response = self.client.post(f"/api/jobs/{job_id}/batches",
                                    headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["batch_id"]

    def _scan(self, batch_id, **overrides):
        payload = {"pages": 1, "page_size": 5, "filters_confirmed": True}
        payload.update(overrides)
        response = self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/scan",
            headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _score(self, jcid, mode="fast_mode", key="score"):
        started = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": mode, "idempotency_key": key})
        self.assertEqual(started.status_code, 200, started.text)
        body = started.json()
        if body.get("reused"):
            return body["evaluation"]
        task_id = body["task"]["pipeline_task_id"]
        for _ in range(400):
            status = self.client.get(f"/api/jobs/job/tasks/{task_id}",
                                     headers=self.headers).json()
            if status["task"]["status"] == "SUCCEEDED":
                return status["evaluation"]
            if status["task"]["status"] == "FAILED":
                self.fail(status)
            time.sleep(0.05)
        self.fail("pipeline task did not finish")

    def test_precheck_confirmation_persists_on_the_batch(self):
        batch_id = self._batch()
        self.client.post("/api/browser/session/connect", headers=self.headers)
        self.client.post("/api/browser/session/navigate", headers=self.headers,
                         json={"job_id": "job"})
        precheck = self.client.get(
            f"/api/jobs/job/browser/precheck?batch_id={batch_id}",
            headers=self.headers).json()
        self.assertFalse(precheck["confirmed"])

        confirmed = self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/precheck/confirm",
            headers=self.headers,
            json={"platform_job_id": precheck["platform_job_id"],
                  "filter_summary": precheck["filter_summary"],
                  "visible_cards": precheck["visible_cards"]})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        body = confirmed.json()
        self.assertEqual(body["confirmed_by"], "owner")
        self.assertIsNotNone(body["filters_confirmed_at"])
        self.assertEqual(body["platform_job_id"], precheck["platform_job_id"])

        restored = self.client.get(
            f"/api/jobs/job/batches/{batch_id}", headers=self.headers).json()
        self.assertIsNotNone(restored["filters_confirmed_at"])
        self.assertEqual(restored["confirmed_by"], "owner")
        self.assertIn("summary", restored["filter_summary_json"])
        # The F5 reload path reads the same persisted batch row.
        listed = self.client.get("/api/jobs/job/batches",
                                 headers=self.headers).json()["batches"]
        self.assertIsNotNone(
            next(item for item in listed
                 if item["batch_id"] == batch_id)["filters_confirmed_at"])
        again = self.client.get(
            f"/api/jobs/job/browser/precheck?batch_id={batch_id}",
            headers=self.headers).json()
        self.assertTrue(again["confirmed"])
        self.assertEqual(again["confirmed_by"], "owner")

        # A page showing another job must never be confirmed for this job.
        other = self.client.post("/api/jobs", headers=self.headers,
                                 json={"title": "Other"}).json()["job_id"]
        self.client.post("/api/browser/session/navigate", headers=self.headers,
                         json={"job_id": other})
        mismatch = self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/precheck/confirm",
            headers=self.headers,
            json={"platform_job_id": precheck["platform_job_id"],
                  "filter_summary": "", "visible_cards": 0})
        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        self.assertEqual(mismatch.json()["code"], "PAGE_MISMATCH")

    def test_scan_metrics_survive_restart_and_stale_token_cannot_overwrite(self):
        batch_id = self._batch()
        report = self._scan(batch_id)
        self.assertEqual(report["imported"], 3)
        status = self.client.get(
            f"/api/jobs/job/batches/{batch_id}/browser/scan",
            headers=self.headers).json()
        self.assertGreaterEqual(status["metrics"]["cards_seen"], 3)
        self.assertEqual(status["metrics"]["details_completed"], 3)
        self.assertEqual(status["metrics"]["stop_reason"], "MAX_PAGES")
        self.assertTrue(status["run_token"])

        # Simulate a process restart: drop the in-memory progress map.
        self.client.app.state.browser_scan_progress.clear()
        restored = self.client.get(
            f"/api/jobs/job/batches/{batch_id}/browser/scan",
            headers=self.headers).json()
        self.assertEqual(restored["progress"]["status"], "COMPLETED")
        self.assertGreaterEqual(restored["progress"]["listed"], 3)
        self.assertEqual(restored["progress"]["detail_completed"], 3)
        self.assertEqual(restored["progress"]["stop_reason"], "MAX_PAGES")

        # Old async completion loses the token race against a newer run.
        second_batch = self._batch()
        first = self.repo.start_browser_scan(
            self.ctx, "job", second_batch, source="mock")
        stale_token = first["run_token"]
        self.repo.finish_browser_scan(
            self.ctx, "job", second_batch, status="COMPLETED", collected=1,
            imported=1, reused=0, failed=0, run_token=stale_token)
        second = self.repo.start_browser_scan(
            self.ctx, "job", second_batch, source="mock")
        self.assertNotEqual(stale_token, second["run_token"])
        ignored = self.repo.finish_browser_scan(
            self.ctx, "job", second_batch, status="COMPLETED", collected=99,
            imported=99, reused=0, failed=0, run_token=stale_token)
        self.assertIsNone(ignored)
        row = self.repo.get_browser_scan_run(self.ctx, "job", second_batch)
        self.assertEqual(row["status"], "RUNNING")
        self.assertEqual(row["run_token"], second["run_token"])
        self.assertEqual(row["collected"], 0)
        finished = self.repo.finish_browser_scan(
            self.ctx, "job", second_batch, status="COMPLETED", collected=1,
            imported=1, reused=0, failed=0, run_token=second["run_token"])
        self.assertEqual(finished["status"], "COMPLETED")

    def test_server_side_pagination_tabs_and_keyword(self):
        batch_id = self._batch()
        self._scan(batch_id)
        for index in range(1, 6):
            imported = self.client.post(
                f"/api/jobs/job/batches/{batch_id}/candidates",
                headers=self.headers,
                json={"candidate_key": f"job:extra-{index}",
                      "resume": resume_for(f"job:extra-{index}")})
            self.assertEqual(imported.status_code, 200, imported.text)

        first = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id, "limit": 3, "offset": 0}).json()
        self.assertEqual(first["total"], 8)
        self.assertEqual(len(first["items"]), 3)
        second = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id, "limit": 3, "offset": 3}).json()
        self.assertEqual(second["total"], 8)
        first_ids = {item["job_candidate"]["job_candidate_id"]
                     for item in first["items"]}
        second_ids = {item["job_candidate"]["job_candidate_id"]
                      for item in second["items"]}
        self.assertFalse(first_ids & second_ids)

        unscored = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id, "tab": "unscored"}).json()
        self.assertEqual(unscored["total"], 8)
        target = unscored["items"][0]["job_candidate"]["job_candidate_id"]
        self._score(target, "fast_mode", "pagination-fast")
        fast = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id, "tab": "fast"}).json()
        self.assertEqual(fast["total"], 1)
        self.assertEqual(fast["items"][0]["job_candidate"]["job_candidate_id"],
                         target)
        deep = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id, "tab": "deep"}).json()
        self.assertEqual(deep["total"], 0)
        review = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id, "tab": "needs_review_or_failed"}).json()
        self.assertGreaterEqual(review["total"], 7)

        keyword = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id, "keyword": "extra-4"}).json()
        self.assertEqual(keyword["total"], 1)
        self.assertIn("extra-4",
                      keyword["items"][0]["job_candidate"]["candidate_key"])

        invalid = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"tab": "bogus"})
        self.assertEqual(invalid.status_code, 400, invalid.text)


class PartialReextractTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.semantic = RecordingSemantic(self.repo)
        self.review = RecordingReview(self.repo)
        self.cards = mock_cards("job", "java", 1)
        # Remove a required section so the first detail read is PARTIAL.
        self.cards[0]["resume"].pop("project_experience", None)
        self.adapter = MockBossAdapter(self.cards, page_size=5)
        self.app = create_app(
            self.repo,
            browser_adapter_factory=lambda *args: self.adapter,
            pipeline_factory=lambda repository: PolicyScoringPipeline(
                repository, semantic_service=self.semantic,
                review_service=self.review))
        self.client = TestClient(self.app)
        self.addCleanup(lambda: self.app.state.executor.shutdown(wait=True))
        self.headers = {"X-Org-Id": "org", "X-User-Id": "owner"}

    def _score(self, jcid, mode="deep_mode", key="partial-reextract"):
        started = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": mode, "idempotency_key": key})
        self.assertEqual(started.status_code, 200, started.text)
        body = started.json()
        if body.get("reused"):
            return body["evaluation"]
        for _ in range(400):
            status = self.client.get(
                f"/api/jobs/job/tasks/{body['task']['pipeline_task_id']}",
                headers=self.headers).json()
            if status["task"]["status"] == "SUCCEEDED":
                return status["evaluation"]
            if status["task"]["status"] == "FAILED":
                self.fail(status)
            time.sleep(0.05)
        self.fail("pipeline task did not finish")

    def test_partial_reextract_seals_new_version_and_allows_rescoring(self):
        batch = self.client.post("/api/jobs/job/batches",
                                 headers=self.headers).json()
        batch_id = batch["batch_id"]
        scanned = self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/scan",
            headers=self.headers,
            json={"pages": 1, "page_size": 5, "filters_confirmed": True})
        self.assertEqual(scanned.status_code, 200, scanned.text)
        listing = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id}).json()
        self.assertEqual(listing["total"], 1)
        item = listing["items"][0]
        self.assertFalse(item["sealed"])
        self.assertEqual(item["resume_summary"]["completeness"], "PARTIAL")
        jcid = item["job_candidate"]["job_candidate_id"]
        partial = self.client.get(
            f"/api/jobs/job/candidates/{jcid}/resume",
            headers=self.headers).json()
        self.assertFalse(partial["sealed"])
        self.assertEqual(partial["completeness"], "PARTIAL")
        blocked = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": "deep_mode"})
        self.assertEqual(blocked.status_code, 409, blocked.text)

        # The detail re-read now returns a complete resume.
        identity = self.cards[0]["identity"]
        complete = mock_cards("job", "java", 1)[0]["resume"]
        self.adapter._cards[identity]["resume"] = complete
        reextracted = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/re-extract",
            headers=self.headers, json={"batch_id": batch_id})
        self.assertEqual(reextracted.status_code, 200, reextracted.text)
        body = reextracted.json()
        self.assertTrue(body["sealed"])
        self.assertTrue(body["updated"])
        self.assertEqual(body["input_version"], 1)
        self.assertEqual(body["completeness"], "COMPLETE")
        sealed = self.client.get(
            f"/api/jobs/job/candidates/{jcid}/resume",
            headers=self.headers).json()
        self.assertTrue(sealed["sealed"])
        self.assertEqual(sealed["completeness"], "COMPLETE")
        evaluation = self._score(jcid)
        self.assertEqual(evaluation["scoring_status"], "DEEP_SCORED")

        # A further re-read appends version 2 instead of rewriting version 1.
        changed = mock_cards("job", "java", 1)[0]["resume"]
        changed["advantage"] = "新增可核验的线上故障演练与容量治理证据"
        self.adapter._cards[identity]["resume"] = changed
        second = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/re-extract",
            headers=self.headers, json={"batch_id": batch_id}).json()
        self.assertEqual(second["input_version"], 2)
        versions = self.repo.list_input_versions(self.ctx, "job", jcid)
        self.assertEqual([item["input_version"] for item in versions], [1, 2])
        self.assertEqual(versions[0]["is_current"], 0)
        self.assertIsNotNone(versions[0]["superseded_at"])
        rescored = self._score(jcid, "deep_mode", "partial-reextract")
        self.assertEqual(rescored["resume_id"], second["resume_id"])


if __name__ == "__main__":
    unittest.main()
