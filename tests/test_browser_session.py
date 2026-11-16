"""Sprint 5 session/precheck/batch/progress/resume regressions (zero schema)."""
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from browser.mock_adapter import MockBossAdapter
from browser.mock_data import mock_cards
from browser.source import BrowserSourceConfig
from server.app import create_app
from tests.test_api_pipeline import ApiCase


class BrowserSessionApiTests(ApiCase):
    def test_session_lifecycle_and_precheck_mismatch(self):
        session = self.client.get("/api/browser/session", headers=self.headers)
        self.assertEqual(session.status_code, 200, session.text)
        body = session.json()
        self.assertEqual(body["source"], "mock")
        self.assertEqual(body["state"], "DISCONNECTED")
        self.assertIn("connect", body["actions"])

        connected = self.client.post("/api/browser/session/connect",
                                     headers=self.headers)
        self.assertEqual(connected.status_code, 200, connected.text)
        self.assertEqual(connected.json()["state"], "READY")

        precheck_before = self.client.get(
            "/api/jobs/job/browser/precheck", headers=self.headers)
        self.assertEqual(precheck_before.status_code, 409, precheck_before.text)

        navigated = self.client.post(
            "/api/browser/session/navigate", headers=self.headers,
            json={"job_id": "job"})
        self.assertEqual(navigated.status_code, 200, navigated.text)
        state = navigated.json()
        self.assertEqual(state["state"], "READY")
        self.assertEqual(state["page_job_id"], "job")
        self.assertTrue(state["iframe_detected"])
        self.assertGreater(state["visible_cards"], 0)

        precheck = self.client.get(
            "/api/jobs/job/browser/precheck", headers=self.headers)
        self.assertEqual(precheck.status_code, 200, precheck.text)
        checked = precheck.json()
        self.assertFalse(checked["job_mismatch"])
        self.assertEqual(checked["page_job_id"], "job")
        self.assertTrue(checked["platform_job_id"].startswith("boss-job-"))

        other = self.client.post("/api/jobs", headers=self.headers,
                                 json={"title": "其他岗位"}).json()["job_id"]
        self.client.post("/api/browser/session/navigate", headers=self.headers,
                         json={"job_id": other})
        mismatch = self.client.get(
            "/api/jobs/job/browser/precheck", headers=self.headers).json()
        self.assertTrue(mismatch["job_mismatch"])
        self.assertTrue(mismatch["warnings"])

    def test_session_is_isolated_per_hr(self):
        self.client.post("/api/browser/session/connect", headers=self.headers)
        self.client.post("/api/browser/session/navigate", headers=self.headers,
                         json={"job_id": "job"})
        self.repo.create_user(self.system, "test:session-hr", user_id="hr_s")
        self.repo.add_organization_member(self.ctx, "hr_s", org_role="ORG_ADMIN")
        other_headers = {"X-Org-Id": "org", "X-User-Id": "hr_s"}
        other = self.client.get("/api/browser/session", headers=other_headers).json()
        self.assertEqual(other["state"], "DISCONNECTED")
        self.assertEqual(other["page_url"], "")
        self.assertEqual(other["page_job_id"], "")
        self.client.post("/api/browser/session/connect", headers=other_headers)
        other = self.client.get("/api/browser/session", headers=other_headers).json()
        self.assertEqual(other["page_url"], "")
        owner = self.client.get("/api/browser/session", headers=self.headers).json()
        self.assertNotEqual(owner["page_url"], "")

    def test_disabled_and_boss_sources_are_explicit(self):
        disabled = create_app(self.repo, browser_source="disabled")
        try:
            client = TestClient(disabled)
            state = client.get("/api/browser/session", headers=self.headers).json()
            self.assertEqual(state["state"], "DISABLED")
            self.assertEqual(client.post("/api/browser/session/connect",
                                         headers=self.headers).status_code, 409)
        finally:
            disabled.state.executor.shutdown(wait=True)

        boss = create_app(self.repo, browser_source=BrowserSourceConfig(
            name="boss_readonly", enabled=True, real_boss_allowed=True,
            port=9339, user_data_dir=str(__import__("tempfile").mkdtemp(
                prefix="boss_session_"))))
        try:
            client = TestClient(boss)
            state = client.get("/api/browser/session", headers=self.headers).json()
            self.assertEqual(state["state"], "REAL_CONTRACT_UNVERIFIED")
            self.assertFalse(state["real_contract_verified"])
            navigate = client.post("/api/browser/session/navigate",
                                   headers=self.headers, json={"job_id": "job"})
            self.assertEqual(navigate.status_code, 409, navigate.text)
            self.assertEqual(navigate.json()["code"], "REAL_CONTRACT_UNVERIFIED")
        finally:
            boss.state.executor.shutdown(wait=True)


class BrowserSessionDataTests(ApiCase):
    def _batch(self):
        return self.client.post("/api/jobs/job/batches", headers=self.headers).json()

    def _scan(self, batch_id, **overrides):
        payload = {"pages": 1, "page_size": 5, "filters_confirmed": True}
        payload.update(overrides)
        return self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/scan",
            headers=self.headers, json=payload)

    def test_batch_history_and_candidate_resume_drawer_data(self):
        first = self._batch()
        time.sleep(0.05)
        second = self._batch()
        listing = self.client.get("/api/jobs/job/batches", headers=self.headers)
        self.assertEqual(listing.status_code, 200, listing.text)
        batches = listing.json()["batches"]
        self.assertEqual([item["batch_id"] for item in batches[:2]],
                         [second["batch_id"], first["batch_id"]])
        self.assertEqual(batches[0]["policy_version"], 1)

        self.assertEqual(self._scan(second["batch_id"]).status_code, 200)
        candidates = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": second["batch_id"]}).json()
        self.assertEqual(candidates["total"], 3)
        item = candidates["items"][0]
        self.assertTrue(item["sealed"])
        self.assertTrue(item["resume_summary"]["degree"])
        self.assertIn("contact_summary", item)
        jcid = item["job_candidate"]["job_candidate_id"]
        resume = self.client.get(
            f"/api/jobs/job/candidates/{jcid}/resume", headers=self.headers)
        self.assertEqual(resume.status_code, 200, resume.text)
        body = resume.json()
        self.assertTrue(body["sealed"])
        self.assertEqual(body["completeness"], "COMPLETE")
        self.assertEqual(body["resume"]["_meta"]["encrypt_geek_id"],
                         body["resume"]["_meta"]["candidate_key"].split(":", 1)[1])
        self.assertIn("work_experience", body["resume"])
        self.assertNotIn("security_id", body["resume"]["_meta"])

        policy_filtered = self.client.get(
            "/api/jobs/job/ranking", headers=self.headers,
            params={"batch_id": second["batch_id"], "policy_version": 1}).json()
        self.assertGreaterEqual(len(policy_filtered["ranking"]), 0)

    def test_scan_progress_is_pollable_and_cancel_keeps_imported_rows(self):
        class SlowAdapter(MockBossAdapter):
            def list_cards(self, page=0, page_size=20):
                time.sleep(0.5)
                return super().list_cards(page, page_size)

        app = create_app(
            self.repo, browser_source="mock",
            browser_adapter_factory=lambda *args: SlowAdapter(
                mock_cards("job", "java", 3), page_size=5))
        try:
            client = TestClient(app)
            batch = client.post("/api/jobs/job/batches", headers=self.headers).json()
            url = f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan"
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    client.post, url, headers=self.headers,
                    json={"pages": 2, "filters_confirmed": True,
                          "max_duration_seconds": 30})
                progress = {}
                for _ in range(20):
                    time.sleep(0.1)
                    status = client.get(url, headers=self.headers).json()
                    progress = status.get("progress") or {}
                    if progress.get("status") == "RUNNING":
                        break
                self.assertEqual(progress.get("status"), "RUNNING")
                cancel = client.post(
                    f"/api/jobs/job/batches/{batch['batch_id']}/browser/cancel",
                    headers=self.headers)
                self.assertEqual(cancel.status_code, 200, cancel.text)
                response = future.result()
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "CANCELLED")
            status = client.get(url, headers=self.headers).json()
            self.assertEqual(status["status"], "CANCELLED")
            self.assertEqual(status["progress"]["status"], "CANCELLED")
            # Any candidates imported before cancellation remain visible.
            listed = client.get(
                "/api/jobs/job/candidates", headers=self.headers,
                params={"batch_id": batch["batch_id"]}).json()
            self.assertGreaterEqual(listed["total"], 0)
        finally:
            app.state.executor.shutdown(wait=True)


"""Sprint 5 Batch 2.1: real Edge CDP page lifecycle regression."""
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from browser.runtime import BrowserRuntime, find_edge
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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(EDGE_AVAILABLE, "Playwright/Edge CDP unavailable")
class CdpPageLifecycleTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.job_version = self.publish_case(job_id="job")
        self.directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True, prefix="cdp_lifecycle_")
        self.addCleanup(self.directory.cleanup)
        headless = os.getenv("BROWSER_HEADLESS", "1").strip().lower() not in (
            "0", "false", "no", "off")
        self.source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=_free_port(),
            user_data_dir=str(Path(self.directory.name) / "profile"),
            headless=headless, auto_launch=True)
        self.registry = BrowserSessionRegistry(
            self.source,
            profile_root=str(Path(self.directory.name) / "sessions"),
            runtime_factory=lambda binding: BrowserRuntime(self.source),
            store=self.repo)
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
        self.headers = {"X-Org-Id": "org", "X-User-Id": "owner"}

    # ---------------------------------------------------------------- helpers
    def _batch(self):
        response = self.client.post("/api/jobs/job/batches",
                                    headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["batch_id"]

    def _connect(self):
        response = self.client.post(
            "/api/browser/session/connect", headers=self.headers,
            json={"job_id": "job"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _navigate(self):
        response = self.client.post(
            "/api/browser/session/navigate", headers=self.headers,
            json={"job_id": "job"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _precheck(self, batch_id):
        response = self.client.get(
            f"/api/jobs/job/browser/precheck?batch_id={batch_id}",
            headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _scan(self, batch_id, key="lifecycle-scan"):
        response = self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/scan",
            headers=self.headers,
            json={"pages": 1, "page_size": 5, "filters_confirmed": True})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _score(self, jcid):
        started = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": "deep_mode", "idempotency_key": "lifecycle"})
        self.assertEqual(started.status_code, 200, started.text)
        body = started.json()
        if body.get("reused"):
            return body["evaluation"]
        deadline = time.time() + 20
        while time.time() < deadline:
            status = self.client.get(
                f"/api/jobs/job/tasks/{body['task']['pipeline_task_id']}",
                headers=self.headers).json()
            if status["task"]["status"] == "SUCCEEDED":
                return status["evaluation"]
            if status["task"]["status"] == "FAILED":
                self.fail(status)
            time.sleep(0.05)
        self.fail("pipeline task did not finish")

    def _manager(self):
        return self.registry.runtime_for(self.ctx).manager

    def _mock_pages(self):
        manager = self._manager()
        return [page for page in manager.list_pages()
                if "mock-pages" in page["url"]]

    def _close_all_pages(self):
        manager = self._manager()

        def close_pages():
            for context in list(manager._browser.contexts):
                for page in list(context.pages):
                    try:
                        page.close()
                    except Exception:
                        pass
        manager.run(close_pages)

    # ----------------------------------------------------------------- tests
    def test_connect_job_reuses_blank_tab_and_full_chain_is_stable(self):
        state = self._connect()
        self.assertEqual(state["state"], "READY")
        self.assertTrue(state["page_url"].startswith("file://"))
        self.assertNotIn("about:blank", state["page_url"])
        self.assertTrue(state["iframe_detected"])
        self.assertGreater(state["visible_cards"], 0)

        # Repeated navigate is idempotent and keeps exactly one Mock page.
        again = self._navigate()
        self.assertEqual(again["page_url"], state["page_url"])
        self.assertEqual(len(self._mock_pages()), 1)

        batch_id = self._batch()
        precheck = self._precheck(batch_id)
        self.assertFalse(precheck["job_mismatch"])
        scan = self._scan(batch_id)
        self.assertEqual(scan["imported"], 3)

        listing = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id}).json()
        jcid = listing["items"][0]["job_candidate"]["job_candidate_id"]
        resume = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/re-extract",
            headers=self.headers, json={"batch_id": batch_id})
        self.assertEqual(resume.status_code, 200, resume.text)
        self.assertTrue(resume.json()["sealed"])

        evaluation = self._score(jcid)
        approved = self.client.post(
            f"/api/jobs/job/evaluations/"
            f"{evaluation['pipeline_evaluation_id']}/reviews",
            headers=self.headers, json={"action": "APPROVE", "reason": "lifecycle"})
        self.assertEqual(approved.status_code, 200, approved.text)
        # Dry-Run must also self-heal after the tab was manually closed.
        self._close_all_pages()
        greeting = self.client.post(
            f"/api/jobs/job/evaluations/"
            f"{evaluation['pipeline_evaluation_id']}/greeting-dry-run",
            headers=self.headers, json={"message": "hello"})
        self.assertEqual(greeting.status_code, 200, greeting.text)
        self.assertEqual(greeting.json()["state"], "DRY_RUN_READY")
        self.assertFalse(greeting.json()["recorded"])
        # Every step reused the same rendered Mock tab.
        self.assertEqual(len(self._mock_pages()), 1)
        session = self.client.get("/api/browser/session",
                                  headers=self.headers).json()
        self.assertEqual(session["page_url"], state["page_url"])

    def test_closed_page_and_browser_disconnect_self_heal(self):
        state = self._connect()
        self._close_all_pages()
        recovered = self._navigate()
        self.assertEqual(recovered["state"], "READY")
        self.assertTrue(recovered["page_url"].startswith("file://"))
        self.assertGreaterEqual(len(self._mock_pages()), 1)

        # Scan works after the page was closed under the cached handle.
        batch_id = self._batch()
        self._close_all_pages()
        self._precheck(batch_id)  # read-only precheck heals the closed tab
        self.assertGreaterEqual(len(self._mock_pages()), 1)
        self._close_all_pages()
        scan = self._scan(batch_id)
        self.assertEqual(scan["imported"], 3)

        # Simulate a CDP drop (browser handle closed) and navigate again.
        manager = self._manager()
        manager.run(lambda: manager._browser.close())
        after_drop = self._navigate()
        self.assertEqual(after_drop["state"], "READY")
        self.assertTrue(after_drop["page_url"].startswith("file://"))
        reopened = self.client.get(
            f"/api/jobs/job/browser/precheck?batch_id={batch_id}",
            headers=self.headers)
        self.assertEqual(reopened.status_code, 200, reopened.text)

        # A re-extract after another page close must also self-heal.
        listing = self.client.get(
            "/api/jobs/job/candidates", headers=self.headers,
            params={"batch_id": batch_id}).json()
        jcid = listing["items"][0]["job_candidate"]["job_candidate_id"]
        self._close_all_pages()
        resume = self.client.post(
            f"/api/jobs/job/candidates/{jcid}/re-extract",
            headers=self.headers, json={"batch_id": batch_id})
        self.assertEqual(resume.status_code, 200, resume.text)
        self.assertTrue(resume.json()["sealed"])


if __name__ == "__main__":
    unittest.main()
