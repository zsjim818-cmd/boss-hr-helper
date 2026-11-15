"""Real Edge CDP E2E: dedicated profile, multi-tab targeting, DOM collection.

Skipped automatically when Playwright or Microsoft Edge is unavailable.  The
browser is launched headless on a free CDP port with a temporary profile; the
user's daily Edge profile and any existing 9222 session are never touched.
"""
import socket
import tempfile
import unittest
from pathlib import Path

from browser.cdp_adapter import CdpMockBossAdapter
from browser.collector import CandidateListCollector
from browser.contracts import BrowserError
from browser.greeting import GreetingExecutor
from browser.mock_data import mock_cards
from browser.runtime import CDPConnectionManager, _default_edge_profile, find_edge
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from tests.pipeline_support import PipelineCase, RecordingReview, RecordingSemantic

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


@unittest.skipUnless(EDGE_AVAILABLE, "Playwright/Edge CDP unavailable")
class CdpRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cdp_e2e_")
        self.addCleanup(self.directory.cleanup)
        self.profile = str(Path(self.directory.name) / "profile")
        self.port = free_port()
        self.managers: list[CDPConnectionManager] = []

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        for manager in self.managers:
            self.assertFalse(manager.owned, "close() must release ownership")

    def manager(self, **kwargs):
        options = {"port": self.port, "user_data_dir": self.profile,
                   "headless": True, "auto_launch": True}
        options.update(kwargs)
        manager = CDPConnectionManager(**options)
        self.managers.append(manager)
        return manager

    def test_connect_health_pages_multi_tab_and_profile_isolation(self):
        manager = self.manager()
        health = manager.connect()
        self.assertTrue(health.connected)
        self.assertIn(str(self.port), health.message)
        self.assertTrue(manager.owned)
        # Dedicated profile actually used, never the daily Edge profile.
        self.assertTrue(Path(self.profile, "Default").is_dir()) or \
            self.assertTrue(any(Path(self.profile).iterdir()))
        daily = _default_edge_profile()
        if daily is not None:
            self.assertNotEqual(Path(self.profile).resolve(), daily.resolve())

        # Open a decoy tab first: target selection must pick the Mock page.
        manager.open_page("data:text/html,<title>decoy</title><h1>decoy</h1>")
        adapter = CdpMockBossAdapter(manager, mock_cards("job", "java", 2))
        health = adapter.connect()
        self.assertTrue(health.connected)
        self.assertEqual(health.source, "cdp_mock")
        self.assertTrue(health.page_url.startswith("file://"))
        self.assertGreaterEqual(health.pages, 2)

        pages = manager.list_pages()
        urls = " ".join(page["url"] for page in pages)
        self.assertIn("data:text/html", urls)
        self.assertIn("mock-", urls)
        # Cookies/tokens are never part of the page listing contract.
        for page in pages:
            self.assertEqual(set(page), {"context", "url", "title"})

        cards = adapter.list_cards(page=0, page_size=10)
        self.assertEqual([card.identity for card in cards],
                         ["geek-java-1", "geek-java-2"])
        detail = adapter.open_detail("geek-java-1")
        self.assertEqual(detail.identity, "geek-java-1")
        self.assertEqual(detail.raw["_meta"]["candidate_key"], "job:geek-java-1")
        outcome = adapter.send_greeting("geek-java-1", "hello", dry_run=True)
        self.assertEqual(outcome.state, "DRY_RUN_READY")
        self.assertFalse(outcome.recorded)
        adapter.close()

    def test_disconnect_is_reported_and_reconnect_recovers(self):
        manager = self.manager()
        manager.connect()
        self.assertTrue(manager.health().connected)
        manager.close()
        self.assertFalse(manager.health().connected)

        # Reconnect on the same endpoint/profile after a clean disconnect.
        second = self.manager()
        health = second.connect()
        self.assertTrue(health.connected)
        second.open_page("data:text/html,<title>after-reconnect</title>")
        pages = second.list_pages()
        self.assertTrue(pages)
        self.assertTrue(any(page["title"] == "after-reconnect" for page in pages))

    def test_external_connection_closes_without_killing_the_browser(self):
        owner = self.manager()
        owner.connect()
        external = self.manager(auto_launch=False)
        health = external.connect()
        self.assertTrue(health.connected)
        self.assertFalse(external.owned)
        pages_before = len(external.list_pages())

        external.close()
        self.assertFalse(external.health().connected)
        # The browser launched by the first manager is still alive.
        self.assertTrue(owner.health().connected)
        self.assertGreaterEqual(len(owner.list_pages()), pages_before)

    def test_dom_failure_modes_are_structured(self):
        manager = self.manager()
        broken = CdpMockBossAdapter(manager, mock_cards("job", "java", 1),
                                    page_error=True)
        broken.connect()
        with self.assertRaises(BrowserError) as captured:
            broken.list_cards()
        self.assertEqual(captured.exception.code, "PAGE_UNAVAILABLE")

        login = CdpMockBossAdapter(manager, mock_cards("job", "java", 1),
                                   login_required=True)
        login.connect()
        with self.assertRaises(BrowserError) as captured:
            login.list_cards()
        self.assertEqual(captured.exception.code, "LOGIN_REQUIRED")
        self.assertFalse(captured.exception.recoverable)

    def test_target_page_missing_is_not_a_silent_success(self):
        manager = self.manager()
        adapter = CdpMockBossAdapter(manager, mock_cards("job", "java", 1))
        adapter.connect()
        manager.close()
        with self.assertRaises(BrowserError) as captured:
            adapter.list_cards()
        self.assertEqual(captured.exception.code, "BROWSER_CLOSED")

    def test_greeting_scope_rejects_another_candidates_open_panel(self):
        manager = self.manager()
        adapter = CdpMockBossAdapter(manager, mock_cards("job", "java", 2))
        adapter.connect()

        first = adapter.send_greeting("geek-java-1", "hello", dry_run=True)
        self.assertEqual(first.state, "DRY_RUN_READY")
        # Panel A is still open; a direct rehearsal for B must be refused.
        mismatched = adapter.send_greeting("geek-java-2", "hello", dry_run=True)
        self.assertEqual(mismatched.state, "IDENTITY_MISMATCH")
        self.assertIn("geek-java-1", mismatched.message)

        # Through the executor the detail panel is re-opened for B and verified.
        adapter.open_detail("geek-java-2")
        allowed = adapter.send_greeting("geek-java-2", "hello", dry_run=True)
        self.assertEqual(allowed.state, "DRY_RUN_READY")

        # A corrupted panel identity is never accepted as B's controls.
        manager.run(lambda: adapter._page.evaluate(
            "document.getElementById('detail-panel').setAttribute("
            "'data-geek-id', 'geek-java-1')"))
        corrupted = adapter.send_greeting("geek-java-2", "hello", dry_run=True)
        self.assertEqual(corrupted.state, "IDENTITY_MISMATCH")

        # Missing greet control inside the verified panel is PAGE_UNCERTAIN.
        manager.run(lambda: adapter._page.evaluate(
            "document.getElementById('detail-panel').setAttribute("
            "'data-geek-id', 'geek-java-2');"
            "document.getElementById('greet-button').remove()"))
        uncertain = adapter.send_greeting("geek-java-2", "hello", dry_run=True)
        self.assertEqual(uncertain.state, "PAGE_UNCERTAIN")

    def test_external_profile_must_match_or_connection_is_refused(self):
        owner = self.manager()
        owner.connect()
        other_profile = str(Path(self.directory.name) / "other-profile")
        foreign = CDPConnectionManager(port=self.port, user_data_dir=other_profile,
                                       auto_launch=False, launch_timeout=5)
        self.managers.append(foreign)
        with self.assertRaises(BrowserError) as captured:
            foreign.connect()
        self.assertEqual(captured.exception.code, "CDP_PROFILE_MISMATCH")
        self.assertTrue(owner.health().connected)

        matching = CDPConnectionManager(port=self.port, user_data_dir=self.profile,
                                        auto_launch=False, launch_timeout=5)
        self.managers.append(matching)
        health = matching.connect()
        self.assertTrue(health.connected)
        self.assertTrue(health.profile_verified)
        self.assertFalse(matching.owned)
        matching.close()
        self.assertTrue(owner.health().connected)

    def test_owned_process_survives_a_cdp_reconnect_and_is_closed(self):
        manager = self.manager()
        manager.connect()
        self.assertTrue(manager.owned)
        manager.run(lambda: manager._browser.close())  # simulate CDP drop
        health = manager.connect()
        self.assertTrue(health.connected)
        self.assertTrue(manager.owned)
        process = manager._process
        self.assertIsNotNone(process)
        manager.close()
        self.assertIsNotNone(process.poll())

    def test_two_hr_sessions_use_isolated_browsers(self):
        from browser.sessions import BrowserSessionRegistry
        from browser.source import BrowserSourceConfig
        from storage.models import AccessContext

        source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=self.port,
            user_data_dir=str(Path(self.directory.name) / "base"), headless=True)
        registry = BrowserSessionRegistry(
            source, profile_root=str(Path(self.directory.name) / "sessions"))
        self.addCleanup(registry.close)
        owner_ctx = AccessContext("org", "owner")
        hr_ctx = AccessContext("org", "hr2")
        owner_runtime = registry.runtime_for(owner_ctx)
        hr_runtime = registry.runtime_for(hr_ctx)
        owner_binding = registry.binding_for(owner_ctx)
        hr_binding = registry.binding_for(hr_ctx)
        self.assertNotEqual(owner_binding.cdp_port, hr_binding.cdp_port)
        self.assertNotEqual(owner_binding.user_data_dir, hr_binding.user_data_dir)
        self.assertNotEqual(owner_binding.browser_account_id,
                            hr_binding.browser_account_id)

        owner_adapter = owner_runtime.adapter(mock_cards("job", "java", 1))
        hr_adapter = hr_runtime.adapter(mock_cards("job", "sales", 1))
        owner_adapter.connect()
        hr_adapter.connect()
        self.assertNotEqual(owner_adapter.page_url, hr_adapter.page_url)
        self.assertNotEqual(owner_adapter.health().profile,
                            hr_adapter.health().profile)
        owner_pages = owner_runtime.manager.list_pages()
        hr_pages = hr_runtime.manager.list_pages()
        self.assertTrue(any(page["url"] == owner_adapter.page_url for page in owner_pages))
        self.assertTrue(any(page["url"] == hr_adapter.page_url for page in hr_pages))
        self.assertFalse(any(page["url"] == hr_adapter.page_url for page in owner_pages))
        owner_process = owner_runtime.manager._process
        hr_process = hr_runtime.manager._process
        registry.close()
        self.assertIsNotNone(owner_process.poll())
        self.assertIsNotNone(hr_process.poll())


@unittest.skipUnless(EDGE_AVAILABLE, "Playwright/Edge CDP unavailable")
class CdpCollectorAndGreetingTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory(prefix="cdp_collect_")
        self.addCleanup(self.directory.cleanup)
        self.manager = CDPConnectionManager(
            port=free_port(), user_data_dir=str(Path(self.directory.name) / "profile"),
            headless=True, auto_launch=True)
        self.addCleanup(self.manager.close)
        self.publish_case(job_id="job")
        self.batch = self.repo.create_scan_batch(self.ctx, "job")

    def test_real_dom_collection_reaches_the_repository(self):
        adapter = CdpMockBossAdapter(self.manager, mock_cards("job", "java", 2))
        adapter.connect()
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"], pages=2, page_size=5)
        self.assertEqual(report.imported, 2)
        self.assertEqual(report.failed, 0)
        self.assertEqual(adapter.health().source, "cdp_mock")
        listed = self.repo.list_job_candidates(self.ctx, "job", limit=10)
        self.assertEqual(listed["total"], 2)
        self.assertEqual({item["job_candidate"]["candidate_key"] for item in listed["items"]},
                         {"job:geek-java-1", "job:geek-java-2"})
        # Re-scan through the same CDP tab is deduplicated.
        again = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"], pages=2, page_size=5)
        self.assertEqual(again.imported, 0)
        self.assertEqual(again.reused, 2)

    def test_dry_run_uses_real_dom_after_approval(self):
        adapter = CdpMockBossAdapter(self.manager, mock_cards("job", "java", 1))
        adapter.connect()
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"], pages=1, page_size=5)
        jcid = report.imported_candidates[0]["job_candidate_id"]
        pipeline = PolicyScoringPipeline(
            self.repo, semantic_service=RecordingSemantic(self.repo),
            review_service=RecordingReview(self.repo))
        outcome = pipeline.run_and_persist(self.ctx, "job", jcid)
        evaluation_id = outcome["evaluation"]["pipeline_evaluation_id"]
        self.repo.append_pipeline_review(self.ctx, "job", evaluation_id, "APPROVE", "ok")

        greeting = GreetingExecutor(self.repo, adapter).execute(
            self.ctx, "job", evaluation_id, message="hello")
        self.assertEqual(greeting.state, "DRY_RUN_READY")
        self.assertIn("verified candidate panel", greeting.message)
        self.assertFalse(greeting.recorded)


if __name__ == "__main__":
    unittest.main()
