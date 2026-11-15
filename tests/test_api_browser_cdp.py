"""HTTP E2E through a real Edge CDP (9222-style) browser + local Mock DOM.

Chain: TestClient/Vue-style HTTP -> FastAPI -> CDPConnectionManager -> Edge ->
rendered Mock DOM -> CandidateListCollector -> repository -> pipeline -> review
-> greeting dry-run.  No real BOSS session is used.
"""
import socket
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from browser.contracts import BrowserError
from browser.runtime import BrowserRuntime, CDPConnectionManager, find_edge
from browser.sessions import BrowserSessionRegistry
from browser.source import BrowserSourceConfig
from config.policy_compiler import JobPolicyCompiler
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from server.app import create_app
from tests.pipeline_support import (PipelineCase, RecordingReview,
                                    RecordingSemantic, resume_for)
from tests.policy_fixtures import offline_policy
from tests.test_policy_compiler import RecordedCompilerClient

EDGE_AVAILABLE = True
try:
    find_edge()
    import playwright.sync_api  # noqa: F401
except Exception:  # pragma: no cover - environment dependent
    EDGE_AVAILABLE = False


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ApiCdpCase(PipelineCase):
    def setUp(self):
        super().setUp()
        self.job_version = self.publish_case(job_id="job")
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="api_cdp_")
        self.addCleanup(self.directory.cleanup)
        self.source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=free_port(),
            user_data_dir=str(Path(self.directory.name) / "profile"),
            headless=True, auto_launch=True)
        self.runtime = BrowserRuntime(self.source)
        self.addCleanup(self.runtime.close)
        self.app = create_app(
            self.repo, mode="deterministic", browser_source=self.source,
            browser_runtime=self.runtime,
            compiler_factory=lambda: JobPolicyCompiler(
                client=RecordedCompilerClient(offline_policy("java_backend")),
                model_name="offline-api-compiler"),
            pipeline_factory=lambda repository: PolicyScoringPipeline(
                repository, semantic_service=RecordingSemantic(repository),
                review_service=RecordingReview(repository)))
        self.client = TestClient(self.app)
        self.addCleanup(lambda: self.app.state.executor.shutdown(wait=True))
        self.headers = {"X-Org-Id": "org", "X-User-Id": "owner"}

    def create_batch(self):
        response = self.client.post("/api/jobs/job/batches", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["batch_id"]

    def scan(self, batch_id):
        return self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/scan", headers=self.headers,
            json={"pages": 2, "page_size": 5, "filters_confirmed": True})

    def score(self, jcid):
        started = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": "deep_mode", "idempotency_key": "cdp-http"})
        self.assertEqual(started.status_code, 200, started.text)
        body = started.json()
        if body.get("reused"):
            return body["evaluation"]
        deadline = time.time() + 20
        while time.time() < deadline:
            status = self.client.get(
                f"/api/jobs/job/tasks/{body['task']['pipeline_task_id']}",
                headers=self.headers).json()
            if status["task"]["status"] in ("SUCCEEDED", "FAILED"):
                self.assertEqual(status["task"]["status"], "SUCCEEDED", status)
                return status["evaluation"]
            time.sleep(0.05)
        self.fail("pipeline task did not finish")


@unittest.skipUnless(EDGE_AVAILABLE, "Playwright/Edge CDP unavailable")
class ApiCdpMockTests(ApiCdpCase):
    def test_http_to_edge_cdp_dom_full_chain(self):
        batch_id = self.create_batch()
        response = self.scan(batch_id)
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["source"], "cdp_mock")
        self.assertEqual(report["browser"], "edge-cdp")
        self.assertTrue(report["page_url"].startswith("file://"))
        self.assertEqual(report["imported"], 3)

        # The Mock page really exists in the Edge instance (not in memory).
        pages = self.runtime.manager.list_pages()
        self.assertTrue(any(page["url"] == report["page_url"] for page in pages))

        status = self.client.get(
            f"/api/jobs/job/batches/{batch_id}/browser/scan",
            headers=self.headers).json()
        self.assertEqual(status["status"], "COMPLETED")
        self.assertEqual(status["source"], "cdp_mock")
        self.assertEqual(status["batch_id"], batch_id)

        jcid = report["imported_candidates"][0]["job_candidate_id"]
        evaluation = self.score(jcid)
        evaluation_id = evaluation["pipeline_evaluation_id"]
        approved = self.client.post(
            f"/api/jobs/job/evaluations/{evaluation_id}/reviews", headers=self.headers,
            json={"action": "APPROVE", "reason": "cdp e2e"})
        self.assertEqual(approved.status_code, 200, approved.text)

        first = self.client.post(
            f"/api/jobs/job/evaluations/{evaluation_id}/greeting-dry-run",
            headers=self.headers, json={"message": "hello"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["state"], "DRY_RUN_READY")
        self.assertTrue(first.json()["dry_run"])
        self.assertFalse(first.json()["idempotent"])

        second = self.client.post(
            f"/api/jobs/job/evaluations/{evaluation_id}/greeting-dry-run",
            headers=self.headers, json={"message": "hello"})
        self.assertTrue(second.json()["idempotent"])
        self.assertEqual(second.json()["contact_event"]["contact_event_id"],
                         first.json()["contact_event"]["contact_event_id"])

        events = self.client.get(
            f"/api/jobs/job/evaluations/{evaluation_id}/contact",
            headers=self.headers).json()["events"]
        self.assertEqual([event["action"] for event in events], ["PENDING"])

        summary = self.client.get(
            f"/api/jobs/job/candidates/{jcid}/contact-summary",
            headers=self.headers).json()
        self.assertFalse(summary["contacted"])
        self.assertEqual(summary["pending_dry_run_count"], 1)

    def test_scan_status_is_isolated_per_batch_over_cdp(self):
        first = self.create_batch()
        first_report = self.scan(first).json()
        self.assertEqual(first_report["imported"], 3)
        self.assertNotIn("zhipin.com", first_report["page_url"])

        pages_after_first = len(self.runtime.manager.list_pages())
        second = self.create_batch()
        second_report = self.scan(second).json()
        self.assertEqual(second_report["imported"], 0)
        self.assertEqual(second_report["reused"], 3)
        # Repeated HTTP requests reuse the same CDP tab instead of leaking tabs.
        self.assertEqual(len(self.runtime.manager.list_pages()), pages_after_first)

        first_status = self.client.get(
            f"/api/jobs/job/batches/{first}/browser/scan", headers=self.headers).json()
        second_status = self.client.get(
            f"/api/jobs/job/batches/{second}/browser/scan", headers=self.headers).json()
        self.assertEqual(first_status["result"]["imported"], 3)
        self.assertEqual(second_status["result"]["imported"], 0)
        self.assertEqual(first_status["result"]["batch_id"], first)
        self.assertEqual(second_status["result"]["batch_id"], second)


class ApiCdpUnavailableTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.publish_case(job_id="job")
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="api_cdp_down_")
        self.addCleanup(self.directory.cleanup)
        self.source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=free_port(),
            user_data_dir=str(Path(self.directory.name) / "profile"),
            headless=True, auto_launch=False)
        self.runtime = BrowserRuntime(self.source)
        self.addCleanup(self.runtime.close)
        self.app = create_app(
            self.repo, mode="deterministic", browser_source=self.source,
            browser_runtime=self.runtime,
            compiler_factory=lambda: JobPolicyCompiler(
                client=RecordedCompilerClient(offline_policy("java_backend")),
                model_name="offline-api-compiler"),
            pipeline_factory=lambda repository: PolicyScoringPipeline(
                repository, semantic_service=RecordingSemantic(repository),
                review_service=RecordingReview(repository)))
        self.client = TestClient(self.app)
        self.addCleanup(lambda: self.app.state.executor.shutdown(wait=True))
        self.headers = {"X-Org-Id": "org", "X-User-Id": "owner"}

    def test_unavailable_browser_is_a_structured_error(self):
        batch = self.client.post("/api/jobs/job/batches", headers=self.headers).json()
        response = self.client.post(
            f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
            headers=self.headers, json={"pages": 1, "filters_confirmed": True})
        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()
        self.assertEqual(body["error_type"], "BrowserError")
        self.assertEqual(body["code"], "BROWSER_UNAVAILABLE")

        status = self.client.get(
            f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
            headers=self.headers)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "FAILED")
        self.assertEqual(status.json()["error"]["code"], "BROWSER_UNAVAILABLE")

        listing = self.client.get("/api/jobs/job/candidates", headers=self.headers).json()
        self.assertEqual(listing["total"], 0)


@unittest.skipUnless(EDGE_AVAILABLE, "Playwright/Edge CDP unavailable")
class ApiCdpSessionIsolationTests(PipelineCase):
    """Two HR accounts get separate Edge instances (port + profile) over HTTP."""

    def setUp(self):
        super().setUp()
        self.publish_case(job_id="job")
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="api_cdp_sessions_")
        self.addCleanup(self.directory.cleanup)
        self.source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=free_port(),
            user_data_dir=str(Path(self.directory.name) / "base"),
            headless=True, auto_launch=True)
        self.registry = BrowserSessionRegistry(
            self.source, profile_root=str(Path(self.directory.name) / "sessions"))
        self.addCleanup(self.registry.close)
        self.app = create_app(
            self.repo, mode="deterministic", browser_source=self.source,
            browser_session_registry=self.registry,
            compiler_factory=lambda: JobPolicyCompiler(
                client=RecordedCompilerClient(offline_policy("java_backend")),
                model_name="offline-api-compiler"),
            pipeline_factory=lambda repository: PolicyScoringPipeline(
                repository, semantic_service=RecordingSemantic(repository),
                review_service=RecordingReview(repository)))
        self.client = TestClient(self.app)
        self.addCleanup(lambda: self.app.state.executor.shutdown(wait=True))
        self.owner_headers = {"X-Org-Id": "org", "X-User-Id": "owner"}
        self.repo.create_user(self.system, "test:cdp-hr2", user_id="hr2")
        self.repo.add_organization_member(self.ctx, "hr2", org_role="ORG_ADMIN")
        self.hr2_headers = {"X-Org-Id": "org", "X-User-Id": "hr2"}

    def test_two_hr_accounts_get_isolated_cdp_sessions(self):
        batch = self.client.post("/api/jobs/job/batches",
                                 headers=self.owner_headers).json()
        owner_scan = self.client.post(
            f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
            headers=self.owner_headers, json={"pages": 1, "page_size": 5,
                                             "filters_confirmed": True})
        self.assertEqual(owner_scan.status_code, 200, owner_scan.text)
        hr_scan = self.client.post(
            f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
            headers=self.hr2_headers, json={"pages": 1, "page_size": 5,
                                            "filters_confirmed": True})
        self.assertEqual(hr_scan.status_code, 200, hr_scan.text)

        owner_body = owner_scan.json()
        hr_body = hr_scan.json()
        self.assertTrue(owner_body["browser_account_id"].startswith("mock-account-"))
        self.assertTrue(hr_body["browser_account_id"].startswith("mock-account-"))
        self.assertNotEqual(owner_body["browser_account_id"],
                            hr_body["browser_account_id"])
        self.assertNotEqual(owner_body["page_url"], hr_body["page_url"])
        self.assertEqual(owner_body["imported"], 3)
        self.assertEqual(hr_body["imported"], 0)
        self.assertEqual(hr_body["reused"], 3)

        sessions = self.registry.describe()
        self.assertEqual(len(sessions), 2)
        self.assertEqual(len({item["cdp_port"] for item in sessions}), 2)
        self.assertEqual(len({item["profile_id"] for item in sessions}), 2)
        self.assertTrue(all(item["profile_verified"] for item in sessions))
        for item in sessions:
            self.assertNotIn("profile", item)
            self.assertNotIn("user_data_dir", item)

        # Anonymous runtime-mode: global summary only, no sessions or paths.
        anonymous = self.client.get("/api/runtime-mode")
        self.assertEqual(anonymous.status_code, 200)
        anonymous_body = anonymous.json()
        self.assertNotIn("sessions", anonymous_body["browser_source"])
        self.assertIsNone(anonymous_body["browser_source"]["session"])
        self.assertNotIn(self.directory.name, anonymous.text)
        self.assertNotIn("user_data_dir", anonymous.text)
        self.assertNotIn("actor_user_id", anonymous.text)

        # Authenticated HR only sees their own session summary.
        owner_mode = self.client.get("/api/runtime-mode",
                                     headers=self.owner_headers).json()
        owner_session = owner_mode["browser_source"]["session"]
        self.assertEqual(owner_session["actor_user_id"], "owner")
        self.assertEqual(owner_session["org_id"], "org")
        self.assertNotIn("profile", owner_session)
        self.assertNotIn(self.directory.name, self.client.get(
            "/api/runtime-mode", headers=self.owner_headers).text)

        hr_mode = self.client.get("/api/runtime-mode",
                                  headers=self.hr2_headers).json()
        hr_session = hr_mode["browser_source"]["session"]
        self.assertEqual(hr_session["actor_user_id"], "hr2")
        self.assertNotEqual(hr_session["browser_account_id"],
                            owner_session["browser_account_id"])


if __name__ == "__main__":
    unittest.main()
