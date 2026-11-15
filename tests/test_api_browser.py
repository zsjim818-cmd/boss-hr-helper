"""HTTP E2E: local Mock browser scan -> import -> pipeline -> review -> dry-run.

The browser routes are mounted whenever BROWSER_SOURCE is enabled (mock or
cdp_mock); no real BOSS session is touched and no message is ever sent.
"""
import unittest

from fastapi.testclient import TestClient

from server.app import create_app
from tests.test_api_pipeline import ApiCase


class ApiBrowserCase(ApiCase):
    def create_batch(self, job_id="job"):
        response = self.client.post(f"/api/jobs/{job_id}/batches", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["batch_id"]

    def scan(self, batch_id, *, job_id="job", pages=2, page_size=5):
        return self.client.post(
            f"/api/jobs/{job_id}/batches/{batch_id}/browser/scan", headers=self.headers,
            json={"pages": pages, "page_size": page_size})

    def scan_status(self, batch_id, *, job_id="job", headers=None):
        return self.client.get(
            f"/api/jobs/{job_id}/batches/{batch_id}/browser/scan",
            headers=headers or self.headers)

    def score(self, jcid, mode="deep_mode", *, job_id="job"):
        started = self.client.post(
            f"/api/jobs/{job_id}/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": mode, "idempotency_key": f"browser-{mode}-{jcid}"})
        self.assertEqual(started.status_code, 200, started.text)
        body = started.json()
        if body.get("reused"):
            return body["evaluation"]
        finished = self.wait_task(body["task"]["pipeline_task_id"], job_id=job_id)
        self.assertEqual(finished["task"]["status"], "SUCCEEDED", finished)
        return finished["evaluation"]

    def append_review(self, evaluation_id, action, *, job_id="job"):
        response = self.client.post(
            f"/api/jobs/{job_id}/evaluations/{evaluation_id}/reviews",
            headers=self.headers, json={"action": action, "reason": "browser acceptance"})
        self.assertEqual(response.status_code, 200, response.text)

    def dry_run(self, evaluation_id, *, job_id="job", message=None):
        payload = {"message": message} if message else {}
        return self.client.post(
            f"/api/jobs/{job_id}/evaluations/{evaluation_id}/greeting-dry-run",
            headers=self.headers, json=payload)

    def contact_events(self, evaluation_id, *, job_id="job"):
        response = self.client.get(
            f"/api/jobs/{job_id}/evaluations/{evaluation_id}/contact", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["events"]


class ApiBrowserScanTests(ApiBrowserCase):
    def test_scan_status_is_404_before_the_first_run(self):
        batch_id = self.create_batch()
        response = self.scan_status(batch_id)
        self.assertEqual(response.status_code, 404, response.text)

    def test_scan_imports_mock_candidates_into_the_locked_batch(self):
        batch_id = self.create_batch()
        response = self.scan(batch_id)
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["batch_id"], batch_id)
        self.assertEqual(report["collected"], 3)
        self.assertEqual(report["imported"], 3)
        self.assertEqual(report["reused"], 0)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["source"], "mock")
        for item in report["imported_candidates"]:
            self.assertEqual(item["policy_id"], self.job_version["policy_id"])
            self.assertEqual(item["policy_version"], 1)
        listed = self.client.get("/api/jobs/job/candidates", headers=self.headers).json()
        self.assertEqual(listed["total"], 3)

        status = self.scan_status(batch_id)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "COMPLETED")
        self.assertEqual(status.json()["batch_id"], batch_id)
        self.assertEqual(status.json()["result"]["batch_id"], batch_id)

        # Re-scan is idempotent: the same stable identities are reused.
        again = self.scan(batch_id)
        self.assertEqual(again.json()["imported"], 0)
        self.assertEqual(again.json()["reused"], 3)
        self.assertEqual(
            self.client.get("/api/jobs/job/candidates", headers=self.headers).json()["total"], 3)

    def test_scan_status_is_isolated_per_actor_in_one_batch(self):
        batch_id = self.create_batch()
        owner_scan = self.scan(batch_id, pages=1, page_size=1).json()
        self.assertEqual(owner_scan["collected"], 1)

        self.repo.create_user(self.system, "test:scan-hr2", user_id="hr2")
        self.repo.add_organization_member(self.ctx, "hr2", org_role="ORG_ADMIN")
        hr_headers = {"X-Org-Id": "org", "X-User-Id": "hr2"}
        hr_scan = self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/scan", headers=hr_headers,
            json={"pages": 1, "page_size": 5})
        self.assertEqual(hr_scan.status_code, 200, hr_scan.text)
        self.assertEqual(hr_scan.json()["collected"], 3)

        owner_status = self.scan_status(batch_id).json()
        hr_status = self.scan_status(batch_id, headers=hr_headers).json()
        self.assertEqual(owner_status["status"], "COMPLETED")
        self.assertEqual(owner_status["result"]["collected"], 1)
        self.assertEqual(hr_status["result"]["collected"], 3)
        self.assertNotEqual(owner_status["result"]["collected"],
                            hr_status["result"]["collected"])

    def test_scan_status_is_isolated_between_batches_of_one_job(self):
        first = self.create_batch()
        first_report = self.scan(first).json()
        self.assertEqual(first_report["imported"], 3)
        second = self.create_batch()
        second_report = self.scan(second).json()
        self.assertEqual(second_report["imported"], 0)
        self.assertEqual(second_report["reused"], 3)
        self.assertNotEqual(first, second)

        first_status = self.scan_status(first).json()
        second_status = self.scan_status(second).json()
        self.assertEqual(first_status["batch_id"], first)
        self.assertEqual(first_status["result"]["batch_id"], first)
        self.assertEqual(first_status["result"]["imported"], 3)
        self.assertEqual(second_status["batch_id"], second)
        self.assertEqual(second_status["result"]["batch_id"], second)
        self.assertEqual(second_status["result"]["imported"], 0)

    def test_scan_status_survives_application_restart(self):
        batch_id = self.create_batch()
        first = self.scan(batch_id).json()
        self.assertEqual(first["imported"], 3)

        restarted = create_app(self.repo, browser_source="mock")
        try:
            client = TestClient(restarted)
            status = client.get(
                f"/api/jobs/job/batches/{batch_id}/browser/scan",
                headers=self.headers)
            self.assertEqual(status.status_code, 200, status.text)
            body = status.json()
            self.assertEqual(body["status"], "COMPLETED")
            self.assertEqual(body["result"]["imported"], 3)
            self.assertEqual(body["result"]["batch_id"], batch_id)
        finally:
            restarted.state.executor.shutdown(wait=True)

    def test_scan_rejects_unknown_batch_and_unauthorised_identity(self):
        unknown = self.scan("ghost-batch")
        self.assertEqual(unknown.status_code, 404, unknown.text)

        batch_id = self.create_batch()
        self.assertEqual(self.scan(batch_id).status_code, 200)

        self.repo.create_user(self.system, "test:browser-hr", user_id="hr2")
        self.repo.add_organization_member(self.ctx, "hr2", org_role="HR")
        outsider = {"X-Org-Id": "org", "X-User-Id": "hr2"}
        denied = self.client.post(
            f"/api/jobs/job/batches/{batch_id}/browser/scan", headers=outsider,
            json={"pages": 1})
        self.assertEqual(denied.status_code, 403, denied.text)
        denied_status = self.scan_status(batch_id, headers=outsider)
        self.assertEqual(denied_status.status_code, 403, denied_status.text)

    def test_browser_routes_follow_the_browser_source_not_the_model_mode(self):
        # Live model mode + explicit mock source: routes are mounted.
        live_mock = create_app(self.repo, mode="live", browser_source="mock",
                               allow_trusted_headers=True)
        try:
            client = TestClient(live_mock)
            response = client.post(
                "/api/jobs/job/batches/ghost/browser/scan", headers=self.headers,
                json={"pages": 1})
            self.assertEqual(response.status_code, 404)  # unknown batch, route exists
        finally:
            live_mock.state.executor.shutdown(wait=True)

        # Live model mode with no browser source: disabled by default.
        live_default = create_app(self.repo, mode="live")
        try:
            client = TestClient(live_default)
            self.assertEqual(client.get("/api/runtime-mode").json()["browser_source"]["source"],
                             "disabled")
            self.assertEqual(client.post(
                "/api/jobs/job/batches/any/browser/scan", json={"pages": 1}).status_code, 404)
            self.assertEqual(client.post(
                "/api/jobs/job/evaluations/any/greeting-dry-run",
                json={"message": "hi"}).status_code, 404)
        finally:
            live_default.state.executor.shutdown(wait=True)


class ApiGreetingDryRunTests(ApiBrowserCase):
    def _scan_and_score(self, mode="deep_mode"):
        batch_id = self.create_batch()
        report = self.scan(batch_id).json()
        self.assertEqual(report["imported"], 3)
        first = report["imported_candidates"][0]
        evaluation = self.score(first["job_candidate_id"], mode)
        return first, evaluation

    def test_full_chain_scan_score_approve_dry_run(self):
        first, evaluation = self._scan_and_score()
        evaluation_id = evaluation["pipeline_evaluation_id"]

        # 1. Approval is mandatory.
        refused = self.dry_run(evaluation_id)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["state"], "NOT_APPROVED")
        self.assertEqual(self.contact_events(evaluation_id), [])

        # 2. Approved -> rehearsal succeeds and persists only a PENDING event.
        self.append_review(evaluation_id, "APPROVE")
        allowed = self.dry_run(evaluation_id)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        body = allowed.json()
        self.assertEqual(body["state"], "DRY_RUN_READY")
        self.assertEqual(body["identity"], first["identity"])
        self.assertTrue(body["dry_run"])
        self.assertFalse(body["recorded"])
        self.assertFalse(body["idempotent"])
        event = body["contact_event"]
        self.assertEqual(event["action"], "PENDING")
        self.assertTrue(event["note"].startswith("DRY_RUN: "))

        events = self.contact_events(evaluation_id)
        self.assertEqual([item["action"] for item in events], ["PENDING"])
        self.assertEqual(events[0]["mode"], "DRY_RUN")
        self.assertIsNone(events[0]["browser_account_id"])

        # 3. Repeated rehearsal is idempotent: reuse, never duplicate PENDING.
        second = self.dry_run(evaluation_id)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["idempotent"])
        self.assertEqual(second.json()["contact_event"]["contact_event_id"],
                         event["contact_event_id"])
        events = self.contact_events(evaluation_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "PENDING")
        self.assertNotIn("CONTACTED", [item["action"] for item in events])

        # 4. A different HR account gets its own idempotent rehearsal row.
        self.repo.create_user(self.system, "test:browser-hr3", user_id="hr3")
        self.repo.add_organization_member(self.ctx, "hr3", org_role="ORG_ADMIN")
        hr_headers = {"X-Org-Id": "org", "X-User-Id": "hr3"}
        hr_dry = self.client.post(
            f"/api/jobs/job/evaluations/{evaluation_id}/greeting-dry-run",
            headers=hr_headers, json={"message": "hello"})
        self.assertEqual(hr_dry.status_code, 200, hr_dry.text)
        self.assertFalse(hr_dry.json()["idempotent"])
        self.assertEqual(len(self.contact_events(evaluation_id)), 2)

        # 5. Revoked approval blocks further rehearsals.
        self.append_review(evaluation_id, "REJECT")
        revoked = self.dry_run(evaluation_id)
        self.assertEqual(revoked.status_code, 409, revoked.text)
        self.assertEqual(revoked.json()["state"], "NOT_APPROVED")

    def test_cross_mode_contact_blocks_dry_run_over_http(self):
        batch_id = self.create_batch()
        report = self.scan(batch_id).json()
        jcid = report["imported_candidates"][0]["job_candidate_id"]

        fast = self.score(jcid, "fast_mode")
        self.append_review(fast["pipeline_evaluation_id"], "APPROVE")
        contact = self.client.post(
            f"/api/jobs/job/evaluations/{fast['pipeline_evaluation_id']}/contact",
            headers=self.headers, json={"action": "CONTACTED", "note": "contacted via fast flow"})
        self.assertEqual(contact.status_code, 200, contact.text)

        deep = self.score(jcid, "deep_mode")
        self.append_review(deep["pipeline_evaluation_id"], "APPROVE")
        blocked = self.dry_run(deep["pipeline_evaluation_id"])
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["state"], "ALREADY_SENT")
        self.assertEqual(self.contact_events(deep["pipeline_evaluation_id"]), [])

    def test_manual_candidate_without_browser_identity_is_unsupported(self):
        imported = self.import_candidate()
        jcid = imported["job_candidate"]["job_candidate_id"]
        evaluation = self.score(jcid)
        self.append_review(evaluation["pipeline_evaluation_id"], "APPROVE")
        response = self.dry_run(evaluation["pipeline_evaluation_id"])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["state"], "UNSUPPORTED")

    def test_concurrent_dry_runs_create_exactly_one_pending_row(self):
        from concurrent.futures import ThreadPoolExecutor

        _, evaluation = self._scan_and_score()
        evaluation_id = evaluation["pipeline_evaluation_id"]
        self.append_review(evaluation_id, "APPROVE")

        def rehearse(_):
            return self.dry_run(evaluation_id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(rehearse, range(8)))
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["state"], "DRY_RUN_READY")
        events = self.contact_events(evaluation_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "PENDING")
        self.assertTrue(events[0]["note"].startswith("DRY_RUN: "))
        ids = {response.json()["contact_event"]["contact_event_id"]
               for response in responses}
        self.assertEqual(len(ids), 1)
        self.assertEqual(sum(1 for response in responses
                             if response.json()["idempotent"] is False), 1)

    def test_dom_failure_returns_a_structured_browser_error(self):
        from browser.adapter import BrowserAdapter
        from browser.contracts import BrowserError, BrowserHealth

        class BrokenPageAdapter(BrowserAdapter):
            def connect(self):
                return BrowserHealth(connected=True, browser="edge-cdp")

            def close(self):
                pass

            def health(self):
                return BrowserHealth(connected=True, browser="edge-cdp")

            def list_cards(self, page=0, page_size=20):
                raise RuntimeError("renderer crashed while reading cards")

            def open_detail(self, identity):
                raise BrowserError("PAGE_UNAVAILABLE", "renderer crashed")

            def send_greeting(self, identity, message, *, dry_run=True):
                raise BrowserError("PAGE_UNAVAILABLE", "renderer crashed")

        app = create_app(self.repo, browser_source="mock",
                         browser_adapter_factory=lambda *args: BrokenPageAdapter())
        try:
            client = TestClient(app)
            batch = client.post("/api/jobs/job/batches", headers=self.headers).json()
            response = client.post(
                f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
                headers=self.headers, json={"pages": 1})
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["error_type"], "BrowserError")
            self.assertEqual(response.json()["code"], "UNEXPECTED_STATE")
            status = client.get(
                f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
                headers=self.headers).json()
            self.assertEqual(status["status"], "FAILED")
            self.assertEqual(status["error"]["code"], "UNEXPECTED_STATE")
        finally:
            app.state.executor.shutdown(wait=True)

    def test_import_rejects_cross_job_candidate_key(self):
        batch = self.client.post("/api/jobs/job/batches", headers=self.headers).json()
        response = self.client.post(
            f"/api/jobs/job/batches/{batch['batch_id']}/candidates",
            headers=self.headers,
            json={"candidate_key": "other-job:geek-1", "source": "browser",
                  "resume": {"name": "x", "skills": "Java",
                             "_meta": {"candidate_key": "other-job:geek-1",
                                       "encrypt_geek_id": "geek-1",
                                       "encrypt_job_id": "other-job"}}})
        self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main()
