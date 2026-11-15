"""FastAPI acceptance: auth, policy gate, candidates, async scoring, HR review."""
import json
import time
import unittest

from fastapi.testclient import TestClient

from config.policy_compiler import JobPolicyCompiler
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from server.app import create_app
from tests.pipeline_support import (PipelineCase, RecordingReview,
                                    RecordingSemantic, resume_for)
from tests.policy_fixtures import offline_policy, policy_input
from tests.test_policy_compiler import RecordedCompilerClient


class ApiCase(PipelineCase):
    def setUp(self):
        super().setUp()
        self.job_version = self.publish_case(job_id="job")
        self.semantic = RecordingSemantic(self.repo)
        self.review = RecordingReview(self.repo)
        app = create_app(
            self.repo,
            compiler_factory=lambda: JobPolicyCompiler(
                client=RecordedCompilerClient(offline_policy("java_backend")),
                model_name="offline-api-compiler"),
            pipeline_factory=lambda repository: PolicyScoringPipeline(
                repository, semantic_service=self.semantic, review_service=self.review))
        self.client = TestClient(app)
        self.addCleanup(lambda: app.state.executor.shutdown(wait=True))
        self.headers = {"X-Org-Id": "org", "X-User-Id": "owner"}

    def wait_task(self, task_id, job_id="job", timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = self.client.get(f"/api/jobs/{job_id}/tasks/{task_id}", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            if body["task"]["status"] in ("SUCCEEDED", "FAILED"):
                return body
            time.sleep(0.05)
        self.fail("pipeline task did not finish in time")

    def import_candidate(self, job_id="job", key="cand", **changes):
        batch = self.client.post(f"/api/jobs/{job_id}/batches", headers=self.headers)
        self.assertEqual(batch.status_code, 200, batch.text)
        imported = self.client.post(
            f"/api/jobs/{job_id}/batches/{batch.json()['batch_id']}/candidates",
            headers=self.headers, json={"candidate_key": key, "resume": resume_for(key, **changes)})
        self.assertEqual(imported.status_code, 200, imported.text)
        return imported.json()


class ApiAccessTests(ApiCase):
    def test_health_and_trusted_identity(self):
        self.assertEqual(self.client.get("/api/health").json()["ok"], True)
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        self.assertEqual(self.client.get("/api/jobs", headers={"X-Org-Id": "org"}).status_code, 401)
        self.assertEqual(self.client.get(
            "/api/jobs", headers={"X-Org-Id": "org", "X-User-Id": "ghost"}).status_code, 403)

    def test_cross_job_access_is_isolated(self):
        created = self.client.post("/api/jobs", headers=self.headers, json={"title": "Other job"})
        self.assertEqual(created.status_code, 200, created.text)
        other_job = created.json()["job_id"]
        imported = self.import_candidate()
        jcid = imported["job_candidate"]["job_candidate_id"]
        response = self.client.get(f"/api/jobs/{other_job}/candidates/{jcid}", headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_jobs_create_and_list(self):
        created = self.client.post("/api/jobs", headers=self.headers, json={"title": "API job"})
        self.assertEqual(created.status_code, 200)
        listing = self.client.get("/api/jobs", headers=self.headers).json()["jobs"]
        self.assertIn("API job", [job["title"] for job in listing])


class ApiPolicyTests(ApiCase):
    def _compile(self):
        payload = policy_input("java_backend")
        response = self.client.post("/api/jobs/job/policies/compile", headers=self.headers,
                                    json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_compile_quality_gate_and_audited_override(self):
        compiled = self._compile()
        self.assertTrue(compiled["publishable"])
        draft_id = compiled["draft"]["draft_id"]
        run_id = compiled["compilation_run"]["run_id"]
        quality = self.client.get(
            f"/api/jobs/job/policies/drafts/{draft_id}/quality", headers=self.headers)
        self.assertEqual(quality.status_code, 200)
        self.assertEqual(quality.json()["status"], "BLOCKED")
        confirmed = self.client.post(
            f"/api/jobs/job/policies/drafts/{draft_id}/confirm", headers=self.headers,
            json={"run_id": run_id, "confirmation_hash": compiled["draft"]["draft_hash"]})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        version = self.client.post(
            f"/api/jobs/job/policies/drafts/{draft_id}/versions", headers=self.headers,
            json={"run_id": run_id, "confirmation_id": confirmed.json()["confirmation_id"]})
        self.assertEqual(version.status_code, 200, version.text)
        policy_id = version.json()["version"]["policy_id"]
        # Default: BLOCKED is refused by the gate (409 + diagnostics).
        blocked = self.client.post(
            f"/api/jobs/job/policies/{policy_id}/publish", headers=self.headers,
            json={"confirmation_hash": compiled["draft"]["draft_hash"]})
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["error_type"], "PolicyQualityBlocked")
        # A non-admin HR cannot bypass the gate.
        self.repo.create_user(self.system, "test:api-hr", user_id="hr1")
        self.repo.add_organization_member(self.ctx, "hr1", org_role="HR")
        denied = self.client.post(
            f"/api/jobs/job/policies/{policy_id}/publish",
            headers={"X-Org-Id": "org", "X-User-Id": "hr1"},
            json={"confirmation_hash": compiled["draft"]["draft_hash"],
                  "quality_override_reason": "HR wants to skip"})
        self.assertEqual(denied.status_code, 403, denied.text)
        # ORG_ADMIN with an explicit reason can override; the audit trail records it.
        allowed = self.client.post(
            f"/api/jobs/job/policies/{policy_id}/publish", headers=self.headers,
            json={"confirmation_hash": compiled["draft"]["draft_hash"],
                  "quality_override_reason": "API acceptance: reviewed offline fixture"})
        self.assertEqual(allowed.status_code, 200, allowed.text)
        events = self.repo.list_audit_events(self.ctx, "job")
        override = [event for event in events if event["action"] == "POLICY_QUALITY_GATE_OVERRIDDEN"]
        self.assertEqual(len(override), 1)
        details = json.loads(override[0]["details_json"])
        self.assertEqual(details["reason"], "API acceptance: reviewed offline fixture")
        self.assertEqual(details["policy_version"], 2)
        self.assertEqual(override[0]["actor_id"], "owner")

    def test_unknown_policy_publish_returns_404(self):
        response = self.client.post("/api/jobs/job/policies/ghost/publish",
                                    headers=self.headers, json={"confirmation_hash": "x"})
        self.assertEqual(response.status_code, 404)


class ApiPipelineTests(ApiCase):
    def test_candidate_scoring_review_and_contact_flow(self):
        imported = self.import_candidate()
        jcid = imported["job_candidate"]["job_candidate_id"]
        listed = self.client.get("/api/jobs/job/candidates", headers=self.headers)
        self.assertEqual(listed.json()["total"], 1)
        started = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": "deep_mode", "idempotency_key": "api-1"})
        self.assertEqual(started.status_code, 200, started.text)
        task_id = started.json()["task"]["pipeline_task_id"]
        finished = self.wait_task(task_id)
        self.assertEqual(finished["task"]["status"], "SUCCEEDED")
        evaluation = finished["evaluation"]
        self.assertEqual(evaluation["scoring_status"], "DEEP_SCORED")
        self.assertEqual(evaluation["routing_suggestion"], "HUMAN_REVIEW")
        detail = self.client.get(
            f"/api/jobs/job/candidates/{jcid}", headers=self.headers).json()
        self.assertEqual(detail["latest_evaluation"]["pipeline_evaluation_id"],
                         evaluation["pipeline_evaluation_id"])
        self.assertEqual(len(detail["evaluations"]), 1)
        self.assertEqual(detail["evaluations"][0]["tier3_json"]["dimensions"][0]["label"],
                         "服务设计")
        ranking = self.client.get("/api/jobs/job/ranking", headers=self.headers).json()["ranking"]
        self.assertEqual(ranking[0]["job_candidate_id"], jcid)
        evaluation_id = evaluation["pipeline_evaluation_id"]
        for action in ("REQUEST_REVIEW", "APPROVE", "NOTE"):
            reviewed = self.client.post(
                f"/api/jobs/job/evaluations/{evaluation_id}/reviews", headers=self.headers,
                json={"action": action, "reason": "acceptance"})
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
        review_history = self.client.get(
            f"/api/jobs/job/evaluations/{evaluation_id}/reviews", headers=self.headers)
        self.assertEqual([item["action"] for item in review_history.json()["reviews"]],
                         ["REQUEST_REVIEW", "APPROVE", "NOTE"])
        contact = self.client.post(
            f"/api/jobs/job/evaluations/{evaluation_id}/contact", headers=self.headers,
            json={"action": "CONTACTED", "note": "manual DM"})
        self.assertEqual(contact.status_code, 200, contact.text)
        events = self.client.get(
            f"/api/jobs/job/evaluations/{evaluation_id}/contact", headers=self.headers)
        self.assertEqual(events.json()["events"][0]["action"], "CONTACTED")

    def test_repeated_scoring_reuses_formal_result_without_model_calls(self):
        imported = self.import_candidate()
        jcid = imported["job_candidate"]["job_candidate_id"]
        first = self.client.post(f"/api/jobs/job/candidates/{jcid}/score",
                                 headers=self.headers, json={"scoring_mode": "deep_mode"})
        self.wait_task(first.json()["task"]["pipeline_task_id"])
        calls = (self.semantic.calls, self.review.calls)
        second = self.client.post(f"/api/jobs/job/candidates/{jcid}/score",
                                  headers=self.headers, json={"scoring_mode": "deep_mode"})
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["reused"])
        self.assertEqual(second.json()["evaluation"]["scoring_status"], "DEEP_SCORED")
        self.assertEqual((self.semantic.calls, self.review.calls), calls)

    def test_unsealed_candidate_is_rejected_before_scoring(self):
        batch = self.repo.create_scan_batch(self.ctx, "job")
        acquired = self.repo.acquire_candidate_for_job(
            self.ctx, "job", batch["batch_id"], "unsealed",
            resume=resume_for("unsealed", data_completeness="PARTIAL"))
        jcid = acquired["job_candidate"]["job_candidate_id"]
        response = self.client.post(f"/api/jobs/job/candidates/{jcid}/score",
                                    headers=self.headers, json={"scoring_mode": "deep_mode"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.semantic.calls, 0)

    def test_fast_mode_never_calls_models(self):
        imported = self.import_candidate(key="fast")
        jcid = imported["job_candidate"]["job_candidate_id"]
        started = self.client.post(f"/api/jobs/job/candidates/{jcid}/score",
                                   headers=self.headers, json={"scoring_mode": "fast_mode"})
        finished = self.wait_task(started.json()["task"]["pipeline_task_id"])
        self.assertEqual(finished["evaluation"]["scoring_status"], "COARSE_ONLY")
        self.assertEqual(self.semantic.calls, 0)
        self.assertEqual(self.review.calls, 0)


if __name__ == "__main__":
    unittest.main()
