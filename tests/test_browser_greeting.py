"""Greeting executor: approval gating, duplicate protection, dry-run semantics."""
import unittest

from browser.contracts import BrowserError, CandidateDetail
from browser.greeting import GreetingExecutor
from browser.mock_adapter import MockBossAdapter
from browser.mock_data import mock_cards
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from tests.pipeline_support import PipelineCase, RecordingReview, RecordingSemantic


class GreetingExecutorTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.publish_case(job_id="job")
        self.batch = self.repo.create_scan_batch(self.ctx, "job")
        self.adapter = MockBossAdapter(mock_cards("job", "java", 1), page_size=5)
        self.adapter.connect()
        from browser.collector import CandidateListCollector
        report = CandidateListCollector(self.repo, self.adapter).collect(
            self.ctx, "job", self.batch["batch_id"])
        self.assertEqual(report.imported, 1)
        self.jcid = report.imported_candidates[0]["job_candidate_id"]
        pipeline = PolicyScoringPipeline(
            self.repo, semantic_service=RecordingSemantic(self.repo),
            review_service=RecordingReview(self.repo))
        outcome = pipeline.run_and_persist(self.ctx, "job", self.jcid)
        self.evaluation_id = outcome["evaluation"]["pipeline_evaluation_id"]
        self.identity = "geek-java-1"
        self.executor = GreetingExecutor(self.repo, self.adapter)

    def _execute(self, **kwargs):
        kwargs.setdefault("message", "您好，方便聊聊岗位机会吗？")
        return self.executor.execute(self.ctx, "job", self.evaluation_id, **kwargs)

    def test_approval_is_required_and_note_does_not_count(self):
        self.assertEqual(self._execute().state, "NOT_APPROVED")
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id,
                                         "NOTE", "just a note")
        self.assertEqual(self._execute().state, "NOT_APPROVED")
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id,
                                         "REQUEST_REVIEW", "check evidence")
        self.assertEqual(self._execute().state, "NOT_APPROVED")
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id,
                                         "APPROVE", "ok")
        self.assertEqual(self._execute().state, "DRY_RUN_READY")

    def test_dry_run_persists_nothing_and_uses_dry_run_mode(self):
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        outcome = self._execute()
        self.assertEqual(outcome.state, "DRY_RUN_READY")
        self.assertTrue(outcome.dry_run)
        self.assertFalse(outcome.recorded)
        self.assertEqual(self.repo.list_pipeline_contact_events(
            self.ctx, "job", self.evaluation_id), [])
        self.assertEqual(self.adapter.send_calls, 0)

    def test_duplicate_contact_is_blocked(self):
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        self.repo.append_pipeline_contact_event(
            self.ctx, "job", self.evaluation_id, "CONTACTED", "earlier manual contact")
        self.assertEqual(self._execute().state, "ALREADY_SENT")

    def test_explicit_identity_cannot_override_the_approved_candidate(self):
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        outcome = self._execute(identity="some-other-geek")
        self.assertEqual(outcome.state, "IDENTITY_MISMATCH")
        self.assertEqual(self.adapter.send_calls, 0)
        self.assertEqual(self.repo.list_pipeline_contact_events(
            self.ctx, "job", self.evaluation_id), [])

    def test_cross_mode_contact_blocks_send(self):
        # fast evaluation contacted first, then deep result approved.
        from engine.policy_scoring_pipeline import PolicyScoringPipeline
        from tests.pipeline_support import RecordingReview, RecordingSemantic
        pipeline = PolicyScoringPipeline(
            self.repo, semantic_service=RecordingSemantic(self.repo),
            review_service=RecordingReview(self.repo))
        fast = pipeline.run_and_persist(self.ctx, "job", self.jcid, scoring_mode="fast_mode")
        fast_eval = fast["evaluation"]["pipeline_evaluation_id"]
        self.repo.append_pipeline_review(self.ctx, "job", fast_eval, "APPROVE", "ok")
        self.repo.append_pipeline_contact_event(self.ctx, "job", fast_eval,
                                                "CONTACTED", "contacted via fast flow")
        deep = pipeline.run_and_persist(self.ctx, "job", self.jcid, scoring_mode="deep_mode")
        deep_eval = deep["evaluation"]["pipeline_evaluation_id"]
        self.repo.append_pipeline_review(self.ctx, "job", deep_eval, "APPROVE", "ok")
        outcome = self._execute()
        self.assertEqual(outcome.state, "ALREADY_SENT")
        self.assertEqual(self.adapter.send_calls, 0)

    def test_older_policy_version_contact_cannot_be_bypassed(self):
        # A CONTACTED event recorded against an evaluation with another Policy
        # version still blocks this candidate (dedupe is candidate-scoped).
        import sqlite3
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("""INSERT INTO pipeline_evaluations
                (org_id,job_id,pipeline_evaluation_id,job_candidate_id,candidate_id,candidate_key,
                 policy_id,policy_version,policy_hash,resume_id,resume_content_hash,pipeline_version,
                 scoring_mode,scoring_status,created_by)
                SELECT org_id,job_id,'eval-v2-contact',job_candidate_id,candidate_id,candidate_key,
                 policy_id,2,policy_hash,resume_id,resume_content_hash,pipeline_version,
                 'fast_mode','COARSE_ONLY',created_by
                FROM pipeline_evaluations WHERE pipeline_evaluation_id=?""",
                               (self.evaluation_id,))
            connection.commit()
        finally:
            connection.close()
        self.repo.append_pipeline_contact_event(self.ctx, "job", "eval-v2-contact",
                                                "CONTACTED", "contacted under v2")
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        outcome = self._execute()
        self.assertEqual(outcome.state, "ALREADY_SENT")

    def test_revoked_approval_blocks_dry_run(self):
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        self.assertEqual(self._execute().state, "DRY_RUN_READY")
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "REJECT",
                                         "approval revoked")
        self.assertEqual(self._execute().state, "NOT_APPROVED")
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "REQUEST_REVIEW",
                                         "needs re-review")
        self.assertEqual(self._execute().state, "NOT_APPROVED")

    def test_missing_candidate_returns_not_found(self):
        class RemovedAdapter(MockBossAdapter):
            def open_detail(self, identity):
                raise BrowserError("NOT_FOUND", "candidate disappeared")

        executor = GreetingExecutor(self.repo, RemovedAdapter(mock_cards("job", "java", 1)))
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        outcome = executor.execute(self.ctx, "job", self.evaluation_id,
                                   message="hello", identity=self.identity)
        self.assertEqual(outcome.state, "NOT_FOUND")

    def test_identity_mismatch_stops_before_any_send(self):
        class MismatchAdapter(MockBossAdapter):
            def open_detail(self, identity):
                detail = super().open_detail(identity)
                return CandidateDetail(identity="other-geek", name=detail.name,
                                       job_id=detail.job_id, raw=detail.raw)

        executor = GreetingExecutor(self.repo, MismatchAdapter(mock_cards("job", "java", 1)))
        executor.adapter.connect()
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        outcome = executor.execute(self.ctx, "job", self.evaluation_id,
                                   message="hello", identity=self.identity)
        self.assertEqual(outcome.state, "IDENTITY_MISMATCH")

    def test_uncertain_page_state_is_not_reported_as_success(self):
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        self.adapter.greeting_confirmed = False
        outcome = self._execute()
        self.assertEqual(outcome.state, "PAGE_UNCERTAIN")
        self.assertFalse(outcome.recorded)
        self.assertEqual(self.repo.list_pipeline_contact_events(
            self.ctx, "job", self.evaluation_id), [])

    def test_real_sending_is_disabled_in_this_batch(self):
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        outcome = self._execute(dry_run=False)
        self.assertEqual(outcome.state, "UNSUPPORTED")
        self.assertEqual(self.adapter.send_calls, 0)
        self.assertEqual(self.adapter.sent_identities(), set())
        self.assertEqual(self.repo.list_pipeline_contact_events(
            self.ctx, "job", self.evaluation_id), [])

    def test_review_and_contact_status_are_separate(self):
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        self.repo.append_pipeline_contact_event(
            self.ctx, "job", self.evaluation_id, "PENDING", "to contact later")
        reviews = self.repo.list_pipeline_reviews(self.ctx, "job", self.evaluation_id)
        contacts = self.repo.list_pipeline_contact_events(self.ctx, "job", self.evaluation_id)
        self.assertEqual([item["action"] for item in reviews], ["APPROVE"])
        self.assertEqual([item["action"] for item in contacts], ["PENDING"])
        # PENDING is not CONTACTED, so a dry-run is still allowed.
        self.assertEqual(self._execute().state, "DRY_RUN_READY")

    def test_blank_message_is_uncertain_and_not_sent(self):
        self.repo.append_pipeline_review(self.ctx, "job", self.evaluation_id, "APPROVE", "ok")
        outcome = self._execute(message="   ")
        self.assertEqual(outcome.state, "PAGE_UNCERTAIN")


if __name__ == "__main__":
    unittest.main()
