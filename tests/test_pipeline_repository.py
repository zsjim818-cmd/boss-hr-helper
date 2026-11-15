"""Pipeline persistence: shared evidence rules, immutability, review linkage."""
import json
import sqlite3
import unittest

from storage.models import AccessContext
from storage.repositories import PermissionDenied
from tests.pipeline_support import PipelineCase, RecordingReview, RecordingSemantic


class PipelinePersistenceTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.version = self.publish_case()
        acquired = self.import_resume(key="cand")
        self.jcid = acquired["job_candidate"]["job_candidate_id"]
        self.tier3_service = RecordingReview(self.repo)
        self.runner = self.pipeline(semantic=RecordingSemantic(self.repo),
                                    review=self.tier3_service)
        self.deep = self.runner.run(self.ctx, "job", self.jcid)
        self.assertEqual(self.deep.status, "DEEP_SCORED")
        self.stored = self.repo.save_pipeline_result(self.ctx, "job", self.deep)

    # ------------------------------------------------------------- positives
    def test_valid_result_is_stored_once_per_binding(self):
        self.assertEqual(self.stored["scoring_status"], "DEEP_SCORED")
        self.assertEqual(self.stored["policy_hash"], self.version["policy_hash"])
        again = self.repo.save_pipeline_result(self.ctx, "job", self.deep)
        self.assertEqual(again["pipeline_evaluation_id"], self.stored["pipeline_evaluation_id"])
        self.assertEqual(len(self.repo.list_pipeline_results(self.ctx, "job", self.jcid)), 1)
        latest = self.repo.latest_pipeline_result(self.ctx, "job", self.jcid)
        self.assertEqual(latest["pipeline_evaluation_id"], self.stored["pipeline_evaluation_id"])

    def test_needs_review_result_is_formal_without_tier3(self):
        acquired = self.import_resume(key="partial")
        jcid = acquired["job_candidate"]["job_candidate_id"]
        partial = self.pipeline(
            semantic=RecordingSemantic(self.repo, status="PARTIAL_SEMANTIC", coverage=0.5),
            review=RecordingReview(self.repo)).run(self.ctx, "job", jcid)
        self.assertEqual(partial.status, "NEEDS_REVIEW")
        stored = self.repo.save_pipeline_result(self.ctx, "job", partial)
        self.assertIsNone(stored["tier3_json"])
        self.assertIsNone(stored["routing_suggestion"])

    # --------------------------------------------------------- forged payloads
    def _tier3(self):
        return json.loads(json.dumps(self.deep.tier3))

    def test_forged_total_score_is_rejected(self):
        forged = self.deep.model_copy(update={"total_score": round(self.deep.total_score + 5, 2)})
        with self.assertRaisesRegex(ValueError, "Tier3 payload"):
            self.repo.save_pipeline_result(self.ctx, "job", forged)

    def test_forged_routing_is_rejected(self):
        forged = self.deep.model_copy(update={"routing_suggestion": "DEEP_RECOMMEND"})
        with self.assertRaisesRegex(ValueError, "Tier3|totals|routing"):
            self.repo.save_pipeline_result(self.ctx, "job", forged)

    def test_short_evidence_quote_is_rejected(self):
        tier3 = self._tier3()
        tier3["dimensions"][0]["evidence"][0]["quote"] = "SQL"
        forged = self.deep.model_copy(update={"tier3": tier3})
        with self.assertRaisesRegex(ValueError, "过短|grounded"):
            self.repo.save_pipeline_result(self.ctx, "job", forged)

    def test_overconfident_missing_evidence_is_rejected(self):
        tier3 = self._tier3()
        tier3["dimensions"][0]["missing_evidence"] = ["缺少性能证据"]
        tier3["dimensions"][0]["confidence"] = 0.95
        forged = self.deep.model_copy(update={"tier3": tier3})
        with self.assertRaisesRegex(ValueError, "缺失证据"):
            self.repo.save_pipeline_result(self.ctx, "job", forged)

    def test_wrong_evidence_path_is_rejected(self):
        tier3 = self._tier3()
        evidence = tier3["dimensions"][0]["evidence"][0]
        evidence["source_path"] = "skills"
        forged = self.deep.model_copy(update={"tier3": tier3})
        with self.assertRaisesRegex(ValueError, "path|grounded"):
            self.repo.save_pipeline_result(self.ctx, "job", forged)

    def test_hard_filtered_cannot_carry_semantic(self):
        acquired = self.import_resume(key="junior", degree="大专",
                                      education=[{"school": "S", "degree": "大专"}])
        jcid = acquired["job_candidate"]["job_candidate_id"]
        result = self.pipeline(semantic=RecordingSemantic(self.repo),
                               review=RecordingReview(self.repo)).run(self.ctx, "job", jcid)
        self.assertEqual(result.status, "HARD_FILTERED")
        forged = result.model_copy(update={"semantic": {"scoring_status": "SEMANTIC_SCORED"}})
        with self.assertRaisesRegex(ValueError, "semantic|Hard"):
            self.repo.save_pipeline_result(self.ctx, "job", forged)

    def test_deep_scored_requires_tier3(self):
        forged = self.deep.model_copy(update={"tier3": None})
        with self.assertRaisesRegex(ValueError, "DEEP_SCORED requires"):
            self.repo.save_pipeline_result(self.ctx, "job", forged)

    # ----------------------------------------------------------- immutability
    def test_formal_result_and_attempts_are_immutable(self):
        self.repo.append_pipeline_result_attempt(self.ctx, "job", self.deep, status="SUCCEEDED")
        connection = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE pipeline_evaluations SET total_score=1")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM pipeline_evaluations")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE pipeline_attempts SET status='TECHNICAL_FAILED'")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM pipeline_attempts")
        finally:
            connection.close()

    # ---------------------------------------------------------- task lifecycle
    def test_task_lifecycle_is_monotonic(self):
        task = self.repo.create_pipeline_task(self.ctx, "job", self.jcid, "deep_mode")
        connection = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE pipeline_tasks SET status='SUCCEEDED' WHERE pipeline_task_id=?",
                                   (task["pipeline_task_id"],))
        finally:
            connection.close()
        running = self.repo.update_pipeline_task(self.ctx, "job", task["pipeline_task_id"],
                                                 status="RUNNING")
        self.assertEqual(running["status"], "RUNNING")

    # ------------------------------------------------------------- HR review
    def test_review_requires_existing_evaluation(self):
        with self.assertRaises(LookupError):
            self.repo.append_pipeline_review(self.ctx, "job", "ghost-eval", "APPROVE")

    def test_review_history_and_contact_are_separate(self):
        evaluation_id = self.stored["pipeline_evaluation_id"]
        first = self.repo.append_pipeline_review(self.ctx, "job", evaluation_id, "REQUEST_REVIEW",
                                                 "需要复核项目贡献")
        second = self.repo.append_pipeline_review(self.ctx, "job", evaluation_id, "APPROVE", "通过")
        self.repo.append_pipeline_review(self.ctx, "job", evaluation_id, "NOTE", "仅备注")
        reviews = self.repo.list_pipeline_reviews(self.ctx, "job", evaluation_id)
        self.assertEqual([item["action"] for item in reviews],
                         ["REQUEST_REVIEW", "APPROVE", "NOTE"])
        self.assertEqual(first["actor_id"], "owner")
        self.assertEqual(second["actor_id"], "owner")
        contact = self.repo.append_pipeline_contact_event(
            self.ctx, "job", evaluation_id, "CONTACTED", "手工私信")
        self.assertEqual(contact["action"], "CONTACTED")
        self.assertEqual(self.repo.list_pipeline_contact_events(
            self.ctx, "job", evaluation_id)[0]["contact_event_id"],
            contact["contact_event_id"])
        connection = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE pipeline_hr_reviews SET action='REJECT'")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM pipeline_contact_events")
        finally:
            connection.close()

    def test_review_permission_is_enforced(self):
        self.repo.create_user(self.system, "test:hr-no-grant", user_id="hr1")
        self.repo.add_organization_member(self.ctx, "hr1", org_role="HR")
        with self.assertRaises(PermissionDenied):
            self.repo.append_pipeline_review(
                AccessContext("org", "hr1"), "job",
                self.stored["pipeline_evaluation_id"], "APPROVE")

    # -------------------------------------------------------- listing/ranking
    def test_listing_filter_pagination_and_ranking(self):
        second = self.import_resume(key="cand2")
        jcid2 = second["job_candidate"]["job_candidate_id"]
        partial = self.pipeline(
            semantic=RecordingSemantic(self.repo, status="PARTIAL_SEMANTIC", coverage=0.5),
            review=RecordingReview(self.repo)).run(self.ctx, "job", jcid2)
        self.repo.save_pipeline_result(self.ctx, "job", partial)
        page = self.repo.list_job_candidates(self.ctx, "job", limit=1, offset=0)
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 1)
        filtered = self.repo.list_job_candidates(self.ctx, "job",
                                                 status="DEEP_SCORED")
        self.assertEqual([item["job_candidate"]["job_candidate_id"]
                          for item in filtered["items"]], [self.jcid])
        ranked = self.repo.job_candidate_ranking(self.ctx, "job")
        self.assertEqual(ranked[0]["job_candidate_id"], self.jcid)
        self.assertEqual(ranked[0]["total_score"], self.stored["total_score"])
        self.assertIn(ranked[1]["scoring_status"], ("NEEDS_REVIEW",))


if __name__ == "__main__":
    unittest.main()
