"""Integration checks of actual migrations and repositories using offline results."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from config.settings import DEFAULT_CONFIG_PATH
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository
from storage.repositories.base import NotFound, PermissionDenied
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import make_engine


class DatabaseEvaluationTests(unittest.TestCase):
    """Mock scores exercise persistence only; no real Qwen or BGE is called."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "recruitment.sqlite"
        self.database = Database(self.path)
        self.database.migrate()
        self.repo = RecruitmentRepository(self.database)
        provision = ProvisioningContext("database evaluation integration tests")
        self.repo.create_user(provision, "test-owner", user_id="owner")
        self.repo.create_organization(provision, "Test Organization", "owner", org_id="org")
        self.context = AccessContext("org", "owner")
        self.repo.create_job(self.context, "AI role", job_id="job")
        self.config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        self.repo.create_execution_profile(self.context, {}, profile_id="profile")
        self.policy = self.repo.create_policy_version(self.context, "job", self.config,
                                                      execution_profile_id="profile", policy_id="policy")
        self.repo.publish_policy(self.context, "job", "policy", confirmation_hash=self.policy["policy_hash"])
        self.batch = self.repo.create_scan_batch(self.context, "job")
        self.resume = deepcopy(BENCHMARK_CANDIDATES[0])
        acquired = self.repo.acquire_candidate_for_job(
            self.context, "job", self.batch["batch_id"], self.resume["_meta"]["candidate_key"],
            external_id="geek-1", resume=self.resume)
        self.association = acquired["job_candidate"]

    def result(self, mode="deep_mode"):
        # A new offline call is intentional: cache imports are rejected by the repository.
        return make_engine().evaluate(self.resume, mode)

    def task(self, mode="deep_mode"):
        return self.repo.create_evaluation_task(self.context, "job", self.association["job_candidate_id"], mode)

    def save(self, mode="deep_mode"):
        task, result = self.task(mode), self.result(mode)
        attempt = self.repo.append_evaluation_attempt(self.context, "job", task["task_id"], "SUCCEEDED", response=result)
        row = self.repo.save_evaluation(self.context, "job", task["task_id"], result, attempt_id=attempt["attempt_id"])
        return task, result, attempt, row

    def test_fast_and_deep_records_are_separate_and_survive_restart(self):
        _, fast, _, fast_row = self.save("fast_mode")
        _, deep, _, deep_row = self.save("deep_mode")
        self.assertIsNone(fast_row["final_score"])
        self.assertEqual(deep_row["final_score"], deep.total_score)
        self.assertNotEqual(fast_row["evaluation_id"], deep_row["evaluation_id"])
        restarted = Database(self.path)
        restarted.migrate()
        rows = RecruitmentRepository(restarted).get_candidate_evaluations(
            self.context, "job", self.association["job_candidate_id"])
        self.assertEqual({r["scoring_mode"] for r in rows}, {"fast_mode", "deep_mode"})
        self.assertTrue(all(r["policy_id"] == self.policy["policy_id"] for r in rows))
        self.assertEqual(json.loads(deep_row["result_json"]), deep.model_dump(mode="json"))
        with closing(restarted.connect()) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_attempt_has_no_official_result_and_can_retry_same_input(self):
        task = self.task()
        failed = self.result().model_copy(deep=True)
        failed.scoring_status, failed.total_score = "SCORING_FAILED", None
        failed.routing_decision = "PENDING_REVIEW"
        self.repo.append_evaluation_attempt(self.context, "job", task["task_id"], "FAILED",
                                            error_type="APITimeoutError", response=failed)
        self.assertEqual(self.repo.get_candidate_evaluations(self.context, "job", self.association["job_candidate_id"]), [])
        with self.assertRaises(ValueError):
            self.repo.save_evaluation(self.context, "job", task["task_id"], failed)
        result = self.result()
        attempt = self.repo.append_evaluation_attempt(self.context, "job", task["task_id"], "SUCCEEDED", response=result)
        saved = self.repo.save_evaluation(self.context, "job", task["task_id"], result, attempt_id=attempt["attempt_id"])
        self.assertEqual(saved["policy_id"], task["policy_id"])
        attempts = self.repo.list_evaluation_attempts(self.context, "job", task["task_id"])
        self.assertEqual([a["status"] for a in attempts], ["FAILED", "SUCCEEDED"])
        self.assertEqual([a["attempt_no"] for a in attempts], [1, 2])

    def test_task_created_after_new_publication_still_uses_first_bound_policy(self):
        new_config = deepcopy(self.config)
        new_config["scoring_config"]["thresholds"]["auto_greet_threshold"] = 95
        second = self.repo.create_policy_version(self.context, "job", new_config,
                                                  execution_profile_id="profile", policy_id="policy-v2")
        self.repo.publish_policy(self.context, "job", second["policy_id"], confirmation_hash=second["policy_hash"])
        task, result, _, row = self.save()
        self.assertEqual(task["policy_id"], "policy")
        self.assertEqual(row["policy_version"], 1)
        self.assertEqual(json.loads(row["result_json"])["version_info"]["job_config_version"], self.policy["policy_hash"])
        self.assertEqual(row["final_score"], result.total_score)

    def test_database_refuses_formal_score_attached_to_failed_attempt(self):
        task, result = self.task(), self.result()
        attempt = self.repo.append_evaluation_attempt(self.context, "job", task["task_id"], "FAILED", error_type="APITimeoutError")
        columns = ("org_id", "job_id", "evaluation_id", "task_id", "attempt_id", "job_candidate_id",
                   "candidate_id", "policy_id", "policy_version", "resume_id", "execution_profile_id",
                   "scoring_mode", "scoring_status", "final_score", "tier0_json", "tier1_score", "tier2_score",
                   "dimensions_json", "evidence_json", "result_json", "created_by")
        encoded = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        values = ("org", "job", "forged-result", task["task_id"], attempt["attempt_id"], task["job_candidate_id"],
                  task["candidate_id"], task["policy_id"], task["policy_version"], task["resume_id"],
                  task["execution_profile_id"], "deep_mode", "DEEP_SCORED", result.total_score,
                  encoded(result.debug_trace.hard_filter_checks), result.debug_trace.tier1_capped_score,
                  result.debug_trace.tier2_semantic_score, encoded(result.dimension_scores),
                  encoded([item.model_dump(mode="json") for item in result.evidence_details]),
                  encoded(result.model_dump(mode="json")), "owner")
        with closing(self.database.connect()) as connection, self.assertRaises(sqlite3.IntegrityError):
            connection.execute(f"INSERT INTO evaluations({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
        self.assertEqual(self.repo.get_candidate_evaluations(self.context, "job", self.association["job_candidate_id"]), [])

    def test_direct_sql_cannot_update_delete_or_replace_official_result(self):
        _, result, _, saved = self.save()
        with closing(self.database.connect()) as connection:
            for sql in ["UPDATE evaluations SET final_score=99 WHERE evaluation_id=?",
                        "DELETE FROM evaluations WHERE evaluation_id=?"]:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql, (saved["evaluation_id"],))
            columns = list(saved)
            for recursive in (0, 1):
                connection.execute(f"PRAGMA recursive_triggers={recursive}")
                with self.subTest(recursive=recursive), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"INSERT OR REPLACE INTO evaluations({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                                       tuple(saved[k] for k in columns))
            self.assertEqual(connection.execute("SELECT final_score FROM evaluations WHERE evaluation_id=?", (saved["evaluation_id"],)).fetchone()[0], result.total_score)

    def test_second_official_result_cannot_use_another_id(self):
        task, result, attempt, first = self.save()
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.save_evaluation(self.context, "job", task["task_id"], result,
                                      attempt_id=attempt["attempt_id"], evaluation_id="replacement")
        self.assertEqual(len(self.repo.get_candidate_evaluations(self.context, "job", self.association["job_candidate_id"])), 1)
        self.assertEqual(self.task()["task_id"], task["task_id"])

    def test_result_must_match_mode_policy_hash_resume_and_full_scope(self):
        task = self.task()
        for change in (lambda r: setattr(r, "resume_content_hash", "wrong"),
                       lambda r: r.version_info.update(job_config_version="wrong"),
                       lambda r: setattr(r, "candidate_key", "wrong-person"),
                       lambda r: setattr(r, "cache_hit", True),
                       lambda r: r.version_info.update(assessment_scope="tier3_only_diagnostic")):
            result = self.result().model_copy(deep=True)
            change(result)
            with self.assertRaises(ValueError):
                self.repo.append_evaluation_attempt(self.context, "job", task["task_id"], "SUCCEEDED", response=result)
        with self.assertRaises(ValueError):
            self.repo.append_evaluation_attempt(self.context, "job", task["task_id"], "SUCCEEDED", response=self.result("fast_mode"))
        self.assertEqual(self.repo.list_evaluation_attempts(self.context, "job", task["task_id"]), [])

    def test_successful_attempt_cannot_be_replaced_by_different_result(self):
        task, result = self.task(), self.result()
        attempt = self.repo.append_evaluation_attempt(self.context, "job", task["task_id"], "SUCCEEDED", response=result)
        changed = result.model_copy(deep=True)
        changed.risk_point = "different, unaccepted answer"
        with self.assertRaises(ValueError):
            self.repo.save_evaluation(self.context, "job", task["task_id"], changed, attempt_id=attempt["attempt_id"])
        self.assertEqual(self.repo.get_candidate_evaluations(self.context, "job", self.association["job_candidate_id"]), [])
        self.repo.save_evaluation(self.context, "job", task["task_id"], result, attempt_id=attempt["attempt_id"])

    def test_invalid_source_quote_and_weighted_score_are_rejected(self):
        task = self.task()
        for change in (lambda r: setattr(r, "total_score", 99),
                       lambda r: setattr(r.debug_trace.tier3_dimension_evidence["ai_automation"].evidence[0], "quote", "invented evidence")):
            result = self.result().model_copy(deep=True)
            change(result)
            with self.assertRaises(ValueError):
                self.repo.append_evaluation_attempt(self.context, "job", task["task_id"], "SUCCEEDED", response=result)

    def test_hr_review_is_separate_from_immutable_model_result(self):
        _, result, _, saved = self.save()
        review = self.repo.append_hr_review(self.context, "job", saved["evaluation_id"], "APPROVE", "HR checked original evidence")
        self.assertEqual(review["evaluation_id"], saved["evaluation_id"])
        row = self.repo.get_candidate_evaluations(self.context, "job", self.association["job_candidate_id"])[0]
        self.assertEqual(row["final_score"], result.total_score)
        self.assertEqual(json.loads(row["result_json"]), result.model_dump(mode="json"))

    def test_unauthorized_hr_cannot_read_or_write_job_evaluations(self):
        provision = ProvisioningContext("add restricted HR")
        self.repo.create_user(provision, "restricted-hr", user_id="hr")
        self.repo.add_organization_member(self.context, "hr")
        restricted = AccessContext("org", "hr")
        for action in [lambda: self.repo.get_candidate_evaluations(restricted, "job", self.association["job_candidate_id"]),
                       lambda: self.repo.create_evaluation_task(restricted, "job", self.association["job_candidate_id"], "fast_mode")]:
            with self.assertRaises(PermissionDenied):
                action()
        self.repo.grant_job_access(self.context, "job", "hr", permissions=("view",))
        self.assertEqual(self.repo.get_candidate_evaluations(restricted, "job", self.association["job_candidate_id"]), [])
        with self.assertRaises(PermissionDenied):
            self.repo.create_evaluation_task(restricted, "job", self.association["job_candidate_id"], "fast_mode")
