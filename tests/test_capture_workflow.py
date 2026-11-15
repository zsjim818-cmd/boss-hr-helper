"""P0/P1 workflow regressions: gates, preview, counters, idempotency, reseal."""
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from browser.collector import CandidateListCollector
from browser.contracts import BrowserHealth, CandidateDetail
from browser.identity import CandidateDetailParser
from browser.mock_adapter import MockBossAdapter
from browser.mock_data import mock_cards, resume_template
from browser.preview import decide_preview
from browser.source import BrowserSourceConfig
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from server.app import create_app
from storage.models import AccessContext
from tests.pipeline_support import (PipelineCase, RecordingReview,
                                    RecordingSemantic, resume_for)
from tests.test_api_pipeline import ApiCase


class PreviewTests(unittest.TestCase):
    def card(self, degree="", years=""):
        return mock_cards("job", "java", 1)[0] | {"degree": degree, "work_years": years}

    def test_only_explicit_list_mismatch_skips(self):
        from browser.contracts import CandidateCard
        base = self.card(degree="大专", years="5年")
        card = CandidateCard(identity=base["identity"], name=base["name"],
                             degree="大专", work_years="5年")
        self.assertEqual(decide_preview(card, required_degree="本科").decision,
                         "SKIP_CONFIRMED")
        self.assertEqual(decide_preview(card, minimum_work_years=8).decision,
                         "SKIP_CONFIRMED")
        missing = CandidateCard(identity="g", name="n")
        self.assertEqual(decide_preview(missing, required_degree="本科").decision,
                         "FETCH_DETAIL")
        ok = CandidateCard(identity="g", name="n", degree="本科", work_years="5")
        self.assertEqual(decide_preview(ok, required_degree="本科",
                                        minimum_work_years=3).decision,
                         "FETCH_DETAIL")


class SpyAdapter(MockBossAdapter):
    def __init__(self, cards, **kwargs):
        super().__init__(cards, **kwargs)
        self.detail_calls = []
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        return super().connect()

    def open_detail(self, identity):
        self.detail_calls.append(identity)
        return super().open_detail(identity)


class CollectorWorkflowTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.publish_case(job_id="job")
        self.batch = self.repo.create_scan_batch(self.ctx, "job")

    def test_preview_skips_confirmed_mismatch_without_detail_call(self):
        cards = mock_cards("job", "java", 3)
        cards[0]["degree"] = "大专"
        adapter = SpyAdapter(cards, page_size=5)
        adapter.connect()
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"], pages=1, page_size=5,
            preview=lambda card: decide_preview(card, required_degree="本科"))
        self.assertEqual(report.listed, 3)
        self.assertEqual(report.preview_skipped, 1)
        self.assertEqual(report.detail_attempted, 2)
        self.assertEqual(report.imported, 2)
        self.assertEqual(len(adapter.detail_calls), 2)
        self.assertNotIn("geek-java-1", adapter.detail_calls)
        self.assertTrue(report.stop_reason)

    def test_max_details_stops_with_reason(self):
        adapter = SpyAdapter(mock_cards("job", "java", 3), page_size=5)
        adapter.connect()
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"], pages=1, page_size=5,
            max_details=1)
        self.assertEqual(report.detail_attempted, 1)
        self.assertEqual(report.imported, 1)
        self.assertEqual(report.stop_reason, "MAX_DETAILS")

    def test_new_resume_snapshot_versioning_is_blocked_without_012(self):
        resume = resume_for("job:geek-java-1")
        resume["_meta"]["encrypt_geek_id"] = "geek-java-1"
        resume["_meta"]["encrypt_job_id"] = "boss-job-job"
        acquired = self.repo.acquire_candidate_for_job(
            self.ctx, "job", self.batch["batch_id"], "job:geek-java-1",
            source="browser", resume=resume)
        jcid = acquired["job_candidate"]["job_candidate_id"]
        pipeline = PolicyScoringPipeline(
            self.repo, semantic_service=RecordingSemantic(self.repo),
            review_service=RecordingReview(self.repo))
        first = pipeline.run_and_persist(
            self.ctx, "job", jcid, scoring_mode="deep_mode",
            batch_id=self.batch["batch_id"])
        first_id = first["evaluation"]["pipeline_evaluation_id"]

        changed = dict(resume)
        changed["advantage"] = "新增可核验项目证据"
        snapshot = self.repo.append_resume_snapshot(
            self.ctx, "job", jcid, changed, batch_id=self.batch["batch_id"])
        with self.assertRaises(ValueError) as captured:
            self.repo.reseal_candidate_input(
                self.ctx, "job", jcid, snapshot["resume_id"])
        self.assertIn("INPUT_VERSIONING_REQUIRES_012", str(captured.exception))
        self.assertEqual(
            self.repo.get_sealed_input(self.ctx, "job", jcid)["resume_id"],
            acquired["sealed_input"]["resume_id"])
        self.assertEqual(len(self.repo.list_pipeline_results(self.ctx, "job", jcid)), 1)
        self.assertEqual(
            self.repo.get_pipeline_result(self.ctx, "job", first_id)["resume_id"],
            acquired["sealed_input"]["resume_id"])

    def test_parser_separates_platform_job_id_and_drops_security_id(self):
        raw = resume_template("java", "geek-1", "job")
        raw["_meta"]["security_id"] = "short-lived-secret"
        resume = CandidateDetailParser.parse(
            CandidateDetail(identity="geek-1", name="n", job_id="",
                            platform_job_id="boss-job-xyz", raw=raw),
            job_id="job")
        self.assertEqual(resume.meta.encrypt_job_id, "boss-job-xyz")
        self.assertEqual(resume.meta.candidate_key, "job:geek-1")
        self.assertIsNone(resume.meta.security_id)


class ScanControlApiTests(ApiCase):
    def create_batch(self, job_id="job"):
        response = self.client.post(f"/api/jobs/{job_id}/batches", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_empty_page_is_unverified_not_success(self):
        app = create_app(
            self.repo, browser_source="mock",
            browser_adapter_factory=lambda *args: MockBossAdapter([], page_size=5))
        try:
            client = TestClient(app)
            batch = client.post("/api/jobs/job/batches", headers=self.headers).json()
            response = client.post(
                f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
                headers=self.headers, json={"pages": 1, "filters_confirmed": True})
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["code"], "EMPTY_UNVERIFIED")
            status = client.get(
                f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
                headers=self.headers).json()
            self.assertEqual(status["status"], "FAILED")
            self.assertEqual(status["error"]["code"], "EMPTY_UNVERIFIED")
        finally:
            app.state.executor.shutdown(wait=True)

    def test_unconfirmed_filters_never_start_a_scan(self):
        batch = self.create_batch()
        response = self.client.post(
            f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
            headers=self.headers,
            json={"pages": 1, "filters_confirmed": False})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("FILTERS_UNCONFIRMED", response.text)

    def test_boss_readonly_remains_hard_disabled_even_when_allowed(self):
        import tempfile
        directory = tempfile.TemporaryDirectory(prefix="boss_gate_")
        self.addCleanup(directory.cleanup)
        source = BrowserSourceConfig(
            name="boss_readonly", enabled=True, real_boss_allowed=True,
            port=9339, user_data_dir=str(Path(directory.name) / "profile"))
        app = create_app(self.repo, browser_source=source)
        try:
            client = TestClient(app)
            batch = client.post("/api/jobs/job/batches", headers=self.headers).json()
            response = client.post(
                f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
                headers=self.headers,
                json={"pages": 1, "filters_confirmed": True})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "REAL_CONTRACT_UNVERIFIED")
            candidates = client.get("/api/jobs/job/candidates",
                                    headers=self.headers).json()
            self.assertEqual(candidates["total"], 0)
        finally:
            app.state.executor.shutdown(wait=True)

    def test_one_connect_per_http_scan(self):
        adapter_holder = {}

        def factory(repository, context, job_id):
            adapter = SpyAdapter(mock_cards(job_id, "java", 2), page_size=5)
            adapter_holder["adapter"] = adapter
            return adapter

        app = create_app(self.repo, browser_source="mock",
                         browser_adapter_factory=factory)
        try:
            client = TestClient(app)
            batch = client.post("/api/jobs/job/batches", headers=self.headers).json()
            response = client.post(
                f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan",
                headers=self.headers, json={"pages": 1, "filters_confirmed": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(adapter_holder["adapter"].connect_calls, 1)
        finally:
            app.state.executor.shutdown(wait=True)

    def test_cancel_request_stops_scan_with_structured_status(self):
        class SlowAdapter(SpyAdapter):
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
                    json={"pages": 2, "filters_confirmed": True})
                time.sleep(0.15)
                cancel = client.post(f"{url}/cancel", headers=self.headers)
                self.assertEqual(cancel.status_code, 200, cancel.text)
                response = future.result()
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "CANCELLED")
            status = client.get(url, headers=self.headers).json()
            self.assertEqual(status["status"], "CANCELLED")
            self.assertEqual(status["error"]["code"], "CANCELLED")
        finally:
            app.state.executor.shutdown(wait=True)

    def test_concurrent_scans_are_rejected_not_overwritten(self):
        class SlowAdapter(SpyAdapter):
            def list_cards(self, page=0, page_size=20):
                time.sleep(0.4)
                return super().list_cards(page, page_size)

        app = create_app(
            self.repo, browser_source="mock",
            browser_adapter_factory=lambda *args: SlowAdapter(
                mock_cards("job", "java", 2), page_size=5))
        try:
            client = TestClient(app)
            batch = client.post("/api/jobs/job/batches", headers=self.headers).json()
            url = f"/api/jobs/job/batches/{batch['batch_id']}/browser/scan"

            def run():
                return client.post(url, headers=self.headers,
                                   json={"pages": 1, "filters_confirmed": True})

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: run(), range(2)))
            codes = sorted(item.status_code for item in responses)
            self.assertEqual(codes, [200, 409], [item.text for item in responses])
            rejected = next(item for item in responses if item.status_code == 409)
            self.assertEqual(rejected.json()["code"], "SCAN_ALREADY_RUNNING")
        finally:
            app.state.executor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
