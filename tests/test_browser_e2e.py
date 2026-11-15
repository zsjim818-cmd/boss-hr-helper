"""Playwright Mock browser E2E: collect -> import -> score -> review -> dry-run.

Skipped automatically when Playwright or the Edge channel is unavailable.
"""
import unittest
from pathlib import Path

from browser.collector import CandidateListCollector
from browser.contracts import BrowserError
from browser.greeting import GreetingExecutor
from browser.mock_data import mock_cards
from browser.playwright_adapter import PlaywrightBossAdapter
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from tests.pipeline_support import PipelineCase, RecordingReview, RecordingSemantic

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def _playwright_available():
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return EDGE.is_file()


@unittest.skipUnless(_playwright_available(), "Playwright/Edge channel unavailable")
class PlaywrightMockBrowserE2ETests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.publish_case(job_id="job")
        self.batch = self.repo.create_scan_batch(self.ctx, "job")
        self.cards = mock_cards("job", "java", 2)
        self.adapters = []

    def tearDown(self):
        for adapter in self.adapters:
            adapter.close()
        super().tearDown()

    def _adapter(self, **kwargs):
        adapter = PlaywrightBossAdapter(self.cards, **kwargs)
        adapter.connect()
        self.adapters.append(adapter)
        return adapter

    def test_full_mock_browser_flow_and_failure_modes(self):
        adapter = self._adapter()
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"], pages=2, page_size=5)
        self.assertEqual(report.imported, 2)
        self.assertEqual(report.failed, 0)
        jcid = report.imported_candidates[0]["job_candidate_id"]

        pipeline = PolicyScoringPipeline(
            self.repo, semantic_service=RecordingSemantic(self.repo),
            review_service=RecordingReview(self.repo))
        outcome = pipeline.run_and_persist(self.ctx, "job", jcid)
        evaluation_id = outcome["evaluation"]["pipeline_evaluation_id"]
        self.repo.append_pipeline_review(self.ctx, "job", evaluation_id, "APPROVE", "ok")

        executor = GreetingExecutor(self.repo, adapter)
        greeting = executor.execute(self.ctx, "job", evaluation_id,
                                    message="您好，方便聊聊吗？",
                                    identity=report.imported_candidates[0]["identity"])
        self.assertEqual(greeting.state, "DRY_RUN_READY")
        self.assertFalse(greeting.recorded)

        # Page failure surfaces a structured error, never an exception leak.
        broken = self._adapter(page_error=True)
        broken_report = CandidateListCollector(self.repo, broken).collect(
            self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(broken_report.imported, 0)
        self.assertEqual([issue.code for issue in broken_report.issues], ["PAGE_UNAVAILABLE"])

        # Login-required is non-recoverable and reported.
        login = self._adapter(login_required=True)
        with self.assertRaises(BrowserError) as captured:
            login.list_cards()
        self.assertEqual(captured.exception.code, "LOGIN_REQUIRED")

        # Candidate no longer visible -> NOT_FOUND (no send assumption).
        from browser.mock_page import build_html
        empty = self._adapter()
        empty._page.set_content(build_html([]))
        empty_executor = GreetingExecutor(self.repo, empty)
        outcome = empty_executor.execute(self.ctx, "job", evaluation_id,
                                         message="hello",
                                         identity=report.imported_candidates[0]["identity"])
        self.assertEqual(outcome.state, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
