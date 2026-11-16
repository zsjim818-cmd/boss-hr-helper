"""v10 -> v11: bindings, scan runs, batch tasks and structured contacts survive."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from engine.policy_scoring_pipeline import PolicyScoringPipeline
from storage.database import Database, MigrationError
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository
from tests.pipeline_support import RecordingReview, RecordingSemantic, resume_for
from tests.test_migration_010 import _migration_dir, _publish


class Migration011AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="migration_011_")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def _v10_seed(self, name="legacy.sqlite3"):
        path = self.root / name
        legacy_dir = _migration_dir(self.root, 10)
        database = Database(path, migration_directory=legacy_dir)
        self.assertEqual(database.migrate()["schema_version"], 10)
        repo = RecruitmentRepository(database)
        system = ProvisioningContext("migration 011 seed")
        repo.create_user(system, "mig:owner", user_id="owner")
        repo.create_organization(system, "Migration org", "owner", org_id="org")
        ctx = AccessContext("org", "owner")
        version = _publish(repo, ctx, "job")
        batch = repo.create_scan_batch(ctx, "job")
        acquired = repo.acquire_candidate_for_job(
            ctx, "job", batch["batch_id"], "job:geek-1",
            resume=resume_for("job:geek-1"))
        jcid = acquired["job_candidate"]["job_candidate_id"]
        pipeline = PolicyScoringPipeline(
            repo, semantic_service=RecordingSemantic(repo),
            review_service=RecordingReview(repo))
        evaluation = pipeline.run(ctx, "job", jcid, scoring_mode="deep_mode")
        saved = repo.save_pipeline_result(ctx, "job", evaluation)
        evaluation_id = saved["pipeline_evaluation_id"]
        review = repo.append_pipeline_review(ctx, "job", evaluation_id, "APPROVE", "ok")
        # v10 contact rows are inserted with the v10 column set.
        connection = sqlite3.connect(path)
        try:
            connection.execute("""INSERT INTO pipeline_contact_events
                (org_id,job_id,contact_event_id,pipeline_evaluation_id,action,note,actor_id)
                VALUES('org','job','contact-manual',?,'CONTACTED','manual contact','owner')""",
                               (evaluation_id,))
            connection.execute("""INSERT INTO pipeline_contact_events
                (org_id,job_id,contact_event_id,pipeline_evaluation_id,action,note,actor_id)
                VALUES('org','job','contact-dry',?,'PENDING','DRY_RUN: rehearsal','owner')""",
                               (evaluation_id,))
            connection.commit()
        finally:
            connection.close()
        return {"path": path, "ctx": ctx, "version": version, "batch": batch,
                "jcid": jcid, "evaluation_id": evaluation_id,
                "review": review, "legacy_dir": legacy_dir}

    def _rows(self, path, table, order):
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY {order}")]
        finally:
            connection.close()

    def test_v10_to_v11_preserves_history_and_adds_constraints(self):
        seed = self._v10_seed()
        path = seed["path"]
        evaluations_before = self._rows(path, "pipeline_evaluations", "pipeline_evaluation_id")
        reviews_before = self._rows(path, "pipeline_hr_reviews", "review_event_id")
        tasks_before = self._rows(path, "pipeline_tasks", "pipeline_task_id")

        upgraded = Database(path)
        result = upgraded.migrate()
        self.assertEqual(result["schema_version"], 12)
        self.assertEqual(result["applied"], ["011_browser_persistence.sql",
                                             "012_workbench_persistence.sql"])
        check = upgraded.check()
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["foreign_key_violations"], [])
        self.assertEqual(check["integrity_check"], ["ok"])

        self.assertEqual(self._rows(path, "pipeline_evaluations", "pipeline_evaluation_id"),
                         evaluations_before)
        self.assertEqual(self._rows(path, "pipeline_hr_reviews", "review_event_id"),
                         reviews_before)
        tasks_after = self._rows(path, "pipeline_tasks", "pipeline_task_id")
        for before, after in zip(tasks_before, tasks_after, strict=True):
            for key, value in before.items():
                self.assertEqual(after[key], value)
            self.assertIsNone(after["batch_id"])

        contacts = {row["contact_event_id"]: row
                    for row in self._rows(path, "pipeline_contact_events", "contact_event_id")}
        self.assertEqual(contacts["contact-manual"]["job_candidate_id"], seed["jcid"])
        self.assertEqual(contacts["contact-manual"]["mode"], "LEGACY")
        self.assertIsNone(contacts["contact-manual"]["browser_account_id"])
        self.assertEqual(contacts["contact-dry"]["mode"], "DRY_RUN")
        self.assertEqual(contacts["contact-dry"]["note"], "DRY_RUN: rehearsal")

        connection = sqlite3.connect(path)
        try:
            # Bindings: unique user/account/port; scan runs: status transitions.
            connection.execute("""INSERT INTO browser_account_bindings
                (org_id,user_id,browser_account_id,cdp_port,profile_id)
                VALUES('org','owner','boss-account-1',9333,'profile-owner')""")
            connection.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO browser_account_bindings
                    (org_id,user_id,browser_account_id,cdp_port,profile_id)
                    VALUES('org','owner','boss-account-2',9334,'profile-x')""")
            connection.rollback()
            connection.execute("""INSERT INTO browser_scan_runs
                (org_id,job_id,batch_id,actor_user_id,status,source,collected,imported)
                VALUES('org','job',?,'owner','RUNNING','boss_readonly',0,0)""",
                               (seed["batch"]["batch_id"],))
            connection.execute("""UPDATE browser_scan_runs SET status='COMPLETED',
                collected=2, imported=2, finished_at='2026-11-15T00:00:00.000Z'
                WHERE org_id='org' AND job_id='job' AND batch_id=? AND actor_user_id='owner'""",
                               (seed["batch"]["batch_id"],))
            connection.commit()

            # Task batch binding is immutable and can be set once at creation.
            connection.execute("""INSERT INTO pipeline_tasks
                (org_id,job_id,pipeline_task_id,job_candidate_id,scoring_mode,
                 idempotency_key,status,created_by,batch_id)
                VALUES('org','job','task-batch',?,'deep_mode','batch-key','PENDING','owner',?)""",
                               (seed["jcid"], seed["batch"]["batch_id"]))
            connection.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE pipeline_tasks SET batch_id=NULL WHERE pipeline_task_id='task-batch'")
            connection.rollback()

            # Real-send uniqueness applies per job/candidate/HR only.
            connection.execute("""INSERT INTO pipeline_contact_events
                (org_id,job_id,contact_event_id,pipeline_evaluation_id,job_candidate_id,
                 action,mode,browser_account_id,note,actor_id)
                VALUES('org','job','real-1',? ,?,'CONTACTED','REAL_SEND','boss-account-1','sent','owner')""",
                               (seed["evaluation_id"], seed["jcid"]))
            connection.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO pipeline_contact_events
                    (org_id,job_id,contact_event_id,pipeline_evaluation_id,job_candidate_id,
                     action,mode,browser_account_id,note,actor_id)
                    VALUES('org','job','real-2',?,?,'CONTACTED','REAL_SEND','boss-account-1','sent','owner')""",
                                   (seed["evaluation_id"], seed["jcid"]))
            connection.rollback()

            # A different HR may contact the same candidate in the same job.
            connection.execute("""INSERT INTO pipeline_contact_events
                (org_id,job_id,contact_event_id,pipeline_evaluation_id,job_candidate_id,
                 action,mode,browser_account_id,note,actor_id)
                VALUES('org','job','real-hr2',?,?,'CONTACTED','REAL_SEND','boss-account-2','sent','hr2')""",
                               (seed["evaluation_id"], seed["jcid"]))
            connection.commit()

            # Candidate/evaluation mismatch is rejected.
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO pipeline_contact_events
                    (org_id,job_id,contact_event_id,pipeline_evaluation_id,job_candidate_id,
                     action,mode,note,actor_id)
                    VALUES('org','job','mismatch',?,'other-candidate','PENDING','DRY_RUN','DRY_RUN: x','owner')""",
                                   (seed["evaluation_id"],))
            connection.rollback()

            # Immutable contact history still rejects UPDATE/DELETE.
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE pipeline_contact_events SET mode='LEGACY'")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM pipeline_contact_events")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE pipeline_evaluations SET total_score=1")
            connection.rollback()
        finally:
            connection.close()

        # Fresh v12 build reproduces a valid schema.
        fresh = Database(self.root / "fresh.sqlite3")
        self.assertEqual(fresh.migrate()["schema_version"], 12)
        self.assertTrue(fresh.check()["ok"])

    def test_failed_012_rolls_back_without_partial_state(self):
        seed = self._v10_seed("broken.sqlite3")
        path = seed["path"]
        before = self._rows(path, "pipeline_evaluations", "pipeline_evaluation_id")
        broken_dir = _migration_dir(
            self.root, 11,
            extra=("012_broken.sql",
                   "CREATE TABLE broken_011(x INTEGER); SELECT * FROM missing_table;"))
        broken = Database(path, migration_directory=broken_dir)
        with self.assertRaises(MigrationError):
            broken.migrate()
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        self.assertEqual(version, 10)
        self.assertNotIn("broken_011", tables)
        self.assertNotIn("browser_account_bindings", tables)
        self.assertEqual(self._rows(path, "pipeline_evaluations", "pipeline_evaluation_id"),
                         before)
        recovered = Database(path).migrate()
        self.assertEqual(recovered["schema_version"], 12)


if __name__ == "__main__":
    unittest.main()
