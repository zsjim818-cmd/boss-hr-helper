"""Run modes and semantic preparation API (deterministic + live wiring)."""
import json
import time
import unittest

from fastapi.testclient import TestClient

from server.app import create_app
from tests.pipeline_support import PipelineCase, resume_for


class SemanticApiCase(PipelineCase):
    def setUp(self):
        super().setUp()
        self.version = self.publish_case(job_id="job")
        self.headers = {"X-Org-Id": "org", "X-User-Id": "owner"}

    def app_client(self, **kwargs):
        app = create_app(self.repo, **kwargs)
        self.addCleanup(lambda: app.state.executor.shutdown(wait=True))
        return TestClient(app), app

    def wait_task(self, client, task_id, job_id="job", timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            response = client.get(f"/api/jobs/{job_id}/tasks/{task_id}", headers=self.headers)
            body = response.json()
            if body["task"]["status"] in ("SUCCEEDED", "FAILED"):
                return body
            time.sleep(0.05)
        self.fail("pipeline task did not finish in time")


class SemanticModeTests(SemanticApiCase):
    def test_live_mode_without_gateway_refuses_trusted_headers(self):
        client, app = self.app_client(mode="live")
        self.assertFalse(app.state.allow_trusted_headers)
        body = client.get("/api/runtime-mode").json()
        self.assertEqual(body["auth_mode"], "gateway-required")
        self.assertFalse(body["local_only"])
        response = client.get("/api/jobs", headers=self.headers)
        self.assertEqual(response.status_code, 503, response.text)

    def test_live_mode_local_dev_can_opt_in_to_trusted_headers(self):
        client, app = self.app_client(mode="live", allow_trusted_headers=True)
        self.assertTrue(app.state.allow_trusted_headers)
        self.assertEqual(client.get("/api/runtime-mode").json()["auth_mode"],
                         "trusted-headers")
        self.assertEqual(client.get("/api/jobs", headers=self.headers).status_code, 200)

    def test_local_identity_endpoint_only_in_deterministic_trusted_mode(self):
        client, app = self.app_client()
        response = client.get("/api/local-identities")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(item["org_id"] == "org"
                            for item in response.json()["identities"]))
        live_client, live_app = self.app_client(mode="live")
        self.assertEqual(live_client.get("/api/local-identities").status_code, 404)

    def test_runtime_mode_defaults_to_deterministic(self):
        client, app = self.app_client()
        self.assertEqual(app.state.mode, "deterministic")
        body = client.get("/api/runtime-mode").json()
        self.assertEqual(body["mode"], "deterministic")
        self.assertFalse(body["models_required"])

    def test_live_mode_wiring_reports_real_models_without_loading_them(self):
        client, app = self.app_client(mode="live")
        self.assertEqual(app.state.mode, "live")
        body = client.get("/api/runtime-mode").json()
        self.assertEqual(body["mode"], "live")
        self.assertTrue(body["models_required"])
        self.assertTrue(body["embedding_model_path"])
        # The BGE model is lazily loaded only on a real preparation call.
        self.assertIsNone(app.state.semantic_runtime._model)

    def test_deterministic_compile_publish_flow_uses_demo_compiler(self):
        client, app = self.app_client()
        payload = {
            "job_title": "AI自动化应用开发工程师",
            "jd_text": "岗位职责：开发AI自动化应用。\n必须熟悉Python与LangGraph工作流。\n加分项：了解Docker容器部署。",
            "hr_requirements": "重视可核验项目"}
        compiled = client.post("/api/jobs/job/policies/compile", headers=self.headers,
                               json=payload)
        self.assertEqual(compiled.status_code, 200, compiled.text)
        draft_id = compiled.json()["draft"]["draft_id"]
        self.assertTrue(compiled.json()["publishable"])
        quality = client.get(f"/api/jobs/job/policies/drafts/{draft_id}/quality",
                             headers=self.headers).json()
        self.assertIn(quality["status"], ("PASS", "WARNING"))
        self.assertIn("issues", quality)
        confirmation = client.post(
            f"/api/jobs/job/policies/drafts/{draft_id}/confirm", headers=self.headers,
            json={"run_id": compiled.json()["compilation_run"]["run_id"],
                  "confirmation_hash": compiled.json()["draft"]["draft_hash"]})
        self.assertEqual(confirmation.status_code, 200, confirmation.text)
        version = client.post(
            f"/api/jobs/job/policies/drafts/{draft_id}/versions", headers=self.headers,
            json={"run_id": compiled.json()["compilation_run"]["run_id"],
                  "confirmation_id": confirmation.json()["confirmation_id"]})
        self.assertEqual(version.status_code, 200, version.text)
        published = client.post(
            f"/api/jobs/job/policies/{version.json()['version']['policy_id']}/publish",
            headers=self.headers,
            json={"confirmation_hash": compiled.json()["draft"]["draft_hash"]})
        self.assertEqual(published.status_code, 200, published.text)
        current = client.get("/api/jobs/job/policies/current", headers=self.headers).json()
        self.assertEqual(current["policy_json"]["provenance"]["model_version"],
                         "deterministic-demo")

    def test_semantic_prepare_is_authenticated_and_explicit_in_deterministic_mode(self):
        client, app = self.app_client()
        self.assertEqual(client.post("/api/jobs/job/semantic-prepare").status_code, 401)
        prepared = client.post("/api/jobs/job/semantic-prepare", headers=self.headers,
                               json={})
        self.assertEqual(prepared.status_code, 200, prepared.text)
        body = prepared.json()
        self.assertEqual(body["mode"], "deterministic")
        self.assertEqual(body["status"], "DETERMINISTIC_NO_ASSETS_REQUIRED")
        self.assertNotEqual(client.post("/api/jobs/job/semantic-prepare",
                                        headers=self.headers, json={}).status_code, 500)

    def test_semantic_prepare_rejects_unknown_job_and_foreign_candidate(self):
        client, app = self.app_client()
        self.assertEqual(client.post("/api/jobs/ghost/semantic-prepare",
                                     headers=self.headers, json={}).status_code, 404)
        created = client.post("/api/jobs", headers=self.headers, json={"title": "Other"})
        other_job = created.json()["job_id"]
        batch = client.post("/api/jobs/job/batches", headers=self.headers).json()
        imported = client.post(
            f"/api/jobs/job/batches/{batch['batch_id']}/candidates",
            headers=self.headers,
            json={"candidate_key": "c1", "resume": resume_for("c1")})
        jcid = imported.json()["job_candidate"]["job_candidate_id"]
        response = client.post(f"/api/jobs/{other_job}/semantic-prepare",
                               headers=self.headers, json={"job_candidate_id": jcid})
        self.assertEqual(response.status_code, 404)

    def test_deterministic_pipeline_uses_mode_defaults(self):
        client, app = self.app_client()
        batch = client.post("/api/jobs/job/batches", headers=self.headers).json()
        imported = client.post(
            f"/api/jobs/job/batches/{batch['batch_id']}/candidates", headers=self.headers,
            json={"candidate_key": "mode-default", "resume": resume_for("mode-default")})
        jcid = imported.json()["job_candidate"]["job_candidate_id"]
        started = client.post(f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
                              json={"scoring_mode": "deep_mode"})
        finished = self.wait_task(client, started.json()["task"]["pipeline_task_id"])
        self.assertEqual(finished["task"]["status"], "SUCCEEDED")
        evaluation = finished["evaluation"]
        self.assertEqual(evaluation["scoring_status"], "DEEP_SCORED")
        semantic = evaluation["tier2_json"]
        self.assertIn("DETERMINISTIC_MODE", " ".join(semantic["review_reasons"]))
        self.assertEqual(evaluation["tier3_json"]["llm_model"], "offline-demo-model")


if __name__ == "__main__":
    unittest.main()
