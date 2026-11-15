"""BossReadonlyCdpAdapter E2E against local BOSS-like fixtures (real Edge CDP)."""
import tempfile
import unittest
from pathlib import Path

from browser.boss_fixture import build_boss_html
from browser.boss_readonly_adapter import BossReadonlyCdpAdapter
from browser.collector import CandidateListCollector
from browser.contracts import BrowserError
from browser.identity import CandidateDetailParser
from browser.runtime import CDPConnectionManager, find_edge
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from tests.pipeline_support import PipelineCase, RecordingReview, RecordingSemantic

EDGE_AVAILABLE = True
try:
    find_edge()
    import playwright.sync_api  # noqa: F401
except Exception:  # pragma: no cover - environment dependent
    EDGE_AVAILABLE = False


def free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(EDGE_AVAILABLE, "Playwright/Edge CDP unavailable")
class BossReadonlyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True, prefix="boss_readonly_")
        self.addCleanup(self.directory.cleanup)
        self.manager = CDPConnectionManager(
            port=free_port(), user_data_dir=str(Path(self.directory.name) / "profile"),
            headless=True, auto_launch=True)
        self.addCleanup(self.manager.close)
        self.adapters = []

    def adapter(self, **kwargs):
        fixture = build_boss_html(**kwargs)
        adapter = BossReadonlyCdpAdapter(self.manager, fixture_html=fixture)
        adapter.connect()
        self.adapters.append(adapter)
        return adapter

    def test_read_only_collection_and_scoped_dry_run(self):
        adapter = self.adapter()
        self.assertTrue(adapter._url.startswith("file://"))
        cards = adapter.list_cards(page=0, page_size=10)
        self.assertEqual([card.identity for card in cards],
                         ["geek-boss-1", "geek-boss-2"])
        detail = adapter.open_detail("geek-boss-1")
        resume = CandidateDetailParser.parse(detail, job_id="job")
        self.assertEqual(resume.meta.candidate_key, "job:geek-boss-1")
        self.assertEqual(resume.data_completeness, "COMPLETE")

        outcome = adapter.send_greeting("geek-boss-1", "hello", dry_run=True)
        self.assertEqual(outcome.state, "DRY_RUN_READY")
        self.assertFalse(outcome.recorded)
        self.assertFalse(self.manager.run(
            lambda: adapter._page.evaluate("() => window.__BOSS_GREET_CLICKED__ === true")))

    def test_login_and_risk_control_preflight(self):
        with self.assertRaises(BrowserError) as captured:
            self.adapter(login=True)
        self.assertEqual(captured.exception.code, "AUTH_REQUIRED")

        with self.assertRaises(BrowserError) as captured:
            self.adapter(captcha=True)
        self.assertEqual(captured.exception.code, "CAPTCHA_BLOCKED")

        with self.assertRaises(BrowserError) as captured:
            self.adapter(slider=True)
        self.assertEqual(captured.exception.code, "CAPTCHA_BLOCKED")

    def test_partial_and_failed_resume_states(self):
        partial = self.adapter(partial=True)
        resume = CandidateDetailParser.parse(
            partial.open_detail("geek-boss-1"), job_id="job")
        self.assertEqual(resume.data_completeness, "PARTIAL")

        failed = self.adapter(failed=True)
        resume = CandidateDetailParser.parse(
            failed.open_detail("geek-boss-1"), job_id="job")
        self.assertEqual(resume.data_completeness, "COLLECTION_FAILED")

    def test_panel_scope_identity_and_button_state(self):
        adapter = self.adapter()
        first = adapter.send_greeting("geek-boss-1", "hello", dry_run=True)
        self.assertEqual(first.state, "DRY_RUN_READY")
        mismatched = adapter.send_greeting("geek-boss-2", "hello", dry_run=True)
        self.assertEqual(mismatched.state, "IDENTITY_MISMATCH")

        mismatch_adapter = self.adapter(panel_mismatch=True)
        with self.assertRaises(BrowserError) as captured:
            mismatch_adapter.open_detail("geek-boss-1")
        self.assertEqual(captured.exception.code, "IDENTITY_MISMATCH")

        disabled = self.adapter(greet_disabled=True)
        limited = disabled.send_greeting("geek-boss-1", "hello", dry_run=True)
        self.assertEqual(limited.state, "PAGE_UNCERTAIN")
        self.assertFalse(self.manager.run(
            lambda: disabled._page.evaluate("() => window.__BOSS_GREET_CLICKED__ === true")))

    def test_real_send_is_physically_disabled(self):
        adapter = self.adapter()
        outcome = adapter.send_greeting("geek-boss-1", "hello", dry_run=False)
        self.assertEqual(outcome.state, "UNSUPPORTED")
        self.assertFalse(self.manager.run(
            lambda: adapter._page.evaluate("() => window.__BOSS_GREET_CLICKED__ === true")))


@unittest.skipUnless(EDGE_AVAILABLE, "Playwright/Edge CDP unavailable")
class BossReadonlyCollectorTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True, prefix="boss_collect_")
        self.addCleanup(self.directory.cleanup)
        self.manager = CDPConnectionManager(
            port=free_port(), user_data_dir=str(Path(self.directory.name) / "profile"),
            headless=True, auto_launch=True)
        self.addCleanup(self.manager.close)
        self.publish_case(job_id="job")
        self.batch = self.repo.create_scan_batch(self.ctx, "job")

    def test_fixture_collection_reaches_repository_and_dry_run(self):
        adapter = BossReadonlyCdpAdapter(
            self.manager, fixture_html=build_boss_html(["geek-boss-1", "geek-boss-2"]),
            delay_ms=0)
        adapter.connect()
        self.addCleanup(adapter.close)
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"], pages=1, page_size=10)
        self.assertEqual(report.imported, 2)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.issues, [])
        jcid = report.imported_candidates[0]["job_candidate_id"]
        self.assertEqual(
            self.repo.get_job_candidate(self.ctx, "job", jcid)["candidate_key"],
            "job:geek-boss-1")

        pipeline = PolicyScoringPipeline(
            self.repo, semantic_service=RecordingSemantic(self.repo),
            review_service=RecordingReview(self.repo))
        outcome = pipeline.run_and_persist(
            self.ctx, "job", jcid, scoring_mode="deep_mode",
            batch_id=self.batch["batch_id"])
        evaluation_id = outcome["evaluation"]["pipeline_evaluation_id"]
        self.repo.append_pipeline_review(self.ctx, "job", evaluation_id, "APPROVE", "ok")
        from browser.greeting import GreetingExecutor
        greeting = GreetingExecutor(self.repo, adapter).execute(
            self.ctx, "job", evaluation_id, message="hello")
        self.assertEqual(greeting.state, "DRY_RUN_READY")


if __name__ == "__main__":
    unittest.main()
