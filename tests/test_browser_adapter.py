"""Browser adapter / collector unit tests (in-memory Mock site, no real browser)."""
import unittest

from browser.collector import CandidateListCollector
from browser.contracts import BrowserError, CandidateDetail
from browser.identity import CandidateIdentityResolver, CandidateDetailParser
from browser.mock_adapter import MockBossAdapter
from browser.mock_data import mock_cards, resume_template
from tests.pipeline_support import PipelineCase


class BrowserCollectorTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.v1 = self.publish_case(job_id="job")
        self.batch = self.repo.create_scan_batch(self.ctx, "job")

    def _adapter(self, **kwargs):
        cards = mock_cards("job", "java", 3)
        adapter = MockBossAdapter(cards, page_size=2, **kwargs)
        adapter.connect()
        return adapter

    def test_collect_paginates_and_imports_into_the_locked_batch(self):
        adapter = self._adapter()
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"], pages=3, page_size=2)
        self.assertEqual(report.collected, 3)
        self.assertEqual(report.imported, 3)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.issues, [])
        for item in report.imported_candidates:
            self.assertEqual(item["policy_id"], self.v1["policy_id"])
            self.assertEqual(item["policy_version"], 1)

    def test_repeated_collection_reuses_candidates_without_duplicates(self):
        adapter = self._adapter()
        collector = CandidateListCollector(self.repo, adapter)
        first = collector.collect(self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(first.imported, 3)
        adapter.reorder([item["identity"] for item in mock_cards("job", "java", 3)][::-1])
        second = collector.collect(self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.reused, 3)
        listed = self.repo.list_job_candidates(self.ctx, "job", limit=20)
        self.assertEqual(listed["total"], 3)
        self.assertEqual(len({item["job_candidate"]["candidate_key"]
                              for item in listed["items"]}), 3)

    def test_page_failures_are_structured_and_stop_collection(self):
        for flag, expected in (("fail_list", "PAGE_UNAVAILABLE"),
                               ("timeout_list", "TIMEOUT"),
                               ("login_required", "LOGIN_REQUIRED")):
            with self.subTest(flag=flag):
                adapter = self._adapter()
                setattr(adapter, flag, True)
                report = CandidateListCollector(self.repo, adapter).collect(
                    self.ctx, "job", self.batch["batch_id"])
                self.assertEqual(report.imported, 0)
                self.assertEqual([issue.code for issue in report.issues], [expected])

    def test_detail_failures_keep_reason_and_other_cards_continue(self):
        adapter = self._adapter()
        adapter.fail_detail = True
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(report.imported, 0)
        self.assertEqual(report.failed, 3)
        self.assertTrue(all(issue.code == "UNEXPECTED_STATE" for issue in report.issues))

    def test_removed_candidate_reports_not_found(self):
        class RemovedAdapter(MockBossAdapter):
            def open_detail(self, identity):
                if identity == "geek-java-2":
                    raise BrowserError("NOT_FOUND", "candidate is no longer visible")
                return super().open_detail(identity)

        adapter = RemovedAdapter(mock_cards("job", "java", 3), page_size=5)
        adapter.connect()
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(report.imported, 2)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.issues[0].code, "NOT_FOUND")

    def test_invalid_resume_is_rejected_without_import(self):
        adapter = self._adapter()
        original = adapter._cards["geek-java-1"]["resume"]
        adapter._cards["geek-java-1"]["resume"] = {
            "name": "broken", "work_experience": "not-a-list"}
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(report.imported, 2)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.issues[0].code, "UNEXPECTED_STATE")
        adapter._cards["geek-java-1"]["resume"] = original

    def test_identity_mismatch_aborts_that_candidate(self):
        class MismatchAdapter(MockBossAdapter):
            def open_detail(self, identity):
                detail = super().open_detail(identity)
                return CandidateDetail(identity="other-geek", name=detail.name,
                                       job_id=detail.job_id, raw=detail.raw)

        adapter = MismatchAdapter(mock_cards("job", "java", 1), page_size=2)
        adapter.connect()
        report = CandidateListCollector(self.repo, adapter).collect(
            self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(report.imported, 0)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.issues[0].code, "IDENTITY_MISMATCH")

    def test_cross_job_isolation_and_batch_policy_stays_locked(self):
        self.publish_case("b2b_sales", job_id="job-sales")
        collector = CandidateListCollector(self.repo, self._adapter())
        first = collector.collect(self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(first.imported, 3)
        # Publish v2 for the java job; batch A must keep v1.
        self.publish("java_backend", job_id="job")
        sales_adapter = MockBossAdapter(mock_cards("job-sales", "sales", 2), page_size=5)
        sales_adapter.connect()
        sales_batch = self.repo.create_scan_batch(self.ctx, "job-sales")
        sales = CandidateListCollector(self.repo, sales_adapter).collect(
            self.ctx, "job-sales", sales_batch["batch_id"])
        self.assertEqual(sales.imported, 2)
        java_items = self.repo.list_job_candidates(self.ctx, "job", limit=20)["items"]
        sales_items = self.repo.list_job_candidates(self.ctx, "job-sales", limit=20)["items"]
        self.assertTrue(all(item["job_candidate"]["policy_version"] == 1
                            for item in java_items))
        self.assertTrue(all(item["job_candidate"]["policy_version"] == 1
                            for item in sales_items))
        java_keys = {item["job_candidate"]["candidate_key"] for item in java_items}
        sales_keys = {item["job_candidate"]["candidate_key"] for item in sales_items}
        self.assertFalse(java_keys & sales_keys)


class CdpDomErrorTests(unittest.TestCase):
    """Raw Playwright/DOM failures must become structured BrowserError values."""

    class BrokenPage:
        def locator(self, *args, **kwargs):
            raise RuntimeError("renderer crashed")

    def test_list_cards_wraps_dom_failures(self):
        from browser.cdp_adapter import CdpMockBossAdapter
        adapter = CdpMockBossAdapter(manager=None, cards=[])
        adapter._page = self.BrokenPage()
        with self.assertRaises(BrowserError) as captured:
            adapter._list_cards_impl(0, 5)
        self.assertEqual(captured.exception.code, "PAGE_UNAVAILABLE")

    def test_open_detail_wraps_dom_failures(self):
        from browser.cdp_adapter import CdpMockBossAdapter
        adapter = CdpMockBossAdapter(manager=None, cards=[])
        adapter._page = self.BrokenPage()
        with self.assertRaises(BrowserError) as captured:
            adapter._open_detail_impl("geek-1")
        self.assertEqual(captured.exception.code, "PAGE_CHANGED")

    def test_greeting_wraps_dom_failures(self):
        from browser.cdp_adapter import CdpMockBossAdapter
        adapter = CdpMockBossAdapter(manager=None, cards=[])
        adapter._page = self.BrokenPage()
        with self.assertRaises(BrowserError) as captured:
            adapter._send_greeting_impl("geek-1", "hello", True)
        self.assertEqual(captured.exception.code, "PAGE_CHANGED")


class ParserContractTests(unittest.TestCase):
    def test_parser_requires_stable_identity_and_resume_fields(self):
        parser = CandidateDetailParser()
        good = CandidateDetail(identity="geek-1", name="n", job_id="job",
                               raw=resume_template("java", "geek-1", "job"))
        resume = parser.parse(good, job_id="job")
        self.assertEqual(resume.meta.encrypt_geek_id, "geek-1")
        self.assertEqual(resume.meta.candidate_key, "job:geek-1")

        with self.assertRaises(BrowserError):
            parser.parse(CandidateDetail(identity="geek-1", name="n", job_id="job",
                                         raw={"name": "broken",
                                              "work_experience": "not-a-list"}),
                         job_id="job")

    def test_candidate_key_must_carry_the_identity(self):
        self.assertEqual(CandidateIdentityResolver.candidate_key("job", "g1"), "job:g1")
        self.assertEqual(CandidateIdentityResolver.from_candidate_key("job:g1"), "g1")
        self.assertIsNone(CandidateIdentityResolver.from_candidate_key("plain-key"))
        with self.assertRaises(BrowserError):
            CandidateIdentityResolver.assert_match("g1", "g2")


if __name__ == "__main__":
    unittest.main()
