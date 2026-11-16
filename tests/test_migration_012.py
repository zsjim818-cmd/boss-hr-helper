"""v11 -> v12: batch confirmation, scan counters, input versions, credential guards."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage.database import Database, MigrationError
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository
from storage.repositories.candidates import prepare_resume
from storage.repositories.base import uid
from tests.pipeline_support import resume_for
from tests.test_migration_010 import _migration_dir, _publish


class Migration012AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="migration_012_")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def _v11_seed(self, name="legacy.sqlite3"):
        path = self.root / name
        legacy_dir = _migration_dir(self.root, 11)
        database = Database(path, migration_directory=legacy_dir)
        self.assertEqual(database.migrate()["schema_version"], 11)
        repo = RecruitmentRepository(database)
        system = ProvisioningContext("migration 012 seed")
        repo.create_user(system, "mig:owner", user_id="owner")
        repo.create_organization(system, "Migration org", "owner", org_id="org")
        ctx = AccessContext("org", "owner")
        _publish(repo, ctx, "job")
        batch = repo.create_scan_batch(ctx, "job")
        acquired = repo.acquire_candidate_for_job(
            ctx, "job", batch["batch_id"], "job:geek-1",
            resume=resume_for("job:geek-1"))
        # v11 rows intentionally use only the v11 column set.
        connection = sqlite3.connect(path)
        try:
            connection.execute("""INSERT INTO browser_scan_runs
                (org_id,job_id,batch_id,actor_user_id,status,source,collected,
                 imported,reused,failed)
                VALUES('org','job',?,'owner','COMPLETED','cdp_mock',3,2,1,1)""",
                (batch["batch_id"],))
            connection.commit()
        finally:
            connection.close()
        return {"path": path, "ctx": ctx, "repo": repo, "batch": batch,
                "jcid": acquired["job_candidate"]["job_candidate_id"],
                "resume_id": acquired["sealed_input"]["resume_id"],
                "legacy_dir": legacy_dir}

    @staticmethod
    def _rows(path, sql, params=()):
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(sql, params)]
        finally:
            connection.close()

    def test_v11_to_v12_backfills_and_enforces_guards(self):
        seed = self._v11_seed()
        path, ctx, jcid = seed["path"], seed["ctx"], seed["jcid"]
        legacy_before = self._rows(path, "SELECT * FROM job_candidate_inputs")
        evaluations_before = self._rows(path, "SELECT * FROM pipeline_evaluations")

        upgraded = Database(path)
        result = upgraded.migrate()
        self.assertEqual(result["schema_version"], 12)
        self.assertEqual(result["applied"], ["012_workbench_persistence.sql"])
        check = upgraded.check()
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["foreign_key_violations"], [])
        self.assertEqual(check["integrity_check"], ["ok"])

        # Legacy input becomes version 1/current; legacy tables are untouched.
        self.assertEqual(self._rows(path, "SELECT * FROM job_candidate_inputs"),
                         legacy_before)
        self.assertEqual(self._rows(path, "SELECT * FROM pipeline_evaluations"),
                         evaluations_before)
        versions = self._rows(path, "SELECT * FROM job_candidate_input_versions")
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["input_version"], 1)
        self.assertEqual(versions[0]["resume_id"], seed["resume_id"])
        self.assertEqual(versions[0]["is_current"], 1)
        self.assertIsNone(versions[0]["superseded_at"])

        # Old scan runs keep counts; new counters/token default safely.
        run = self._rows(path, "SELECT * FROM browser_scan_runs")[0]
        self.assertEqual((run["collected"], run["imported"], run["reused"],
                          run["failed"]), (3, 2, 1, 1))
        for column in ("pages_read", "cards_seen", "cards_deduped",
                       "details_opened", "details_completed",
                       "details_partial", "prescreen_skipped"):
            self.assertEqual(run[column], 0)
        self.assertEqual(run["run_token"], "")
        batch = self._rows(path, "SELECT * FROM scan_batches WHERE batch_id=?",
                           (seed["batch"]["batch_id"],))[0]
        self.assertIsNone(batch["platform_job_id"])
        self.assertEqual(batch["filter_summary_json"], "[]")
        self.assertIsNone(batch["filters_confirmed_at"])
        self.assertIsNone(batch["confirmed_by"])

        # Re-collection seals version 2 and preserves version 1 + its evaluation.
        repo = RecruitmentRepository(upgraded)
        changed = resume_for("job:geek-1", advantage="补充可核验项目")
        appended = repo.append_resume_snapshot(
            ctx, "job", jcid, changed, batch_id=seed["batch"]["batch_id"])
        current = repo.get_sealed_input(ctx, "job", jcid)
        self.assertEqual(current["input_version"], 2)
        self.assertEqual(current["resume_id"], appended["resume_id"])
        history = repo.list_input_versions(ctx, "job", jcid)
        self.assertEqual([item["input_version"] for item in history], [1, 2])
        self.assertEqual(history[0]["is_current"], 0)
        self.assertIsNotNone(history[0]["superseded_at"])

        connection = sqlite3.connect(path)
        try:
            # Only one current version may exist.
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO job_candidate_input_versions
                    (org_id,job_id,job_candidate_id,input_version,candidate_id,
                     policy_id,policy_version,resume_id,sealed_by,is_current)
                    SELECT org_id,job_id,job_candidate_id,3,candidate_id,policy_id,
                           policy_version,resume_id,sealed_by,1
                    FROM job_candidate_input_versions WHERE input_version=1""")
            connection.rollback()
            # Identity/history is immutable; deletion is forbidden.
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""UPDATE job_candidate_input_versions
                    SET resume_id='other' WHERE input_version=1""")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM job_candidate_input_versions")
            connection.rollback()

            # Cookie/token/security_id material is rejected at the DB boundary.
            cookie_resume = resume_for("job:geek-cookie")
            cookie_resume["_meta"]["cookie"] = "sessionid=secret"
            payload = prepare_resume(cookie_resume)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO resume_snapshots
                    (org_id,candidate_id,resume_id,raw_resume_json,
                     normalized_resume_json,content_hash,raw_hash,completeness,
                     is_scorable,captured_by)
                    VALUES('org',? ,?,?,?,?,?,?,?, 'owner')""",
                    ("cand-cookie", uid(), payload["raw_resume_json"],
                     payload["normalized_resume_json"], payload["content_hash"],
                     payload["raw_hash"], payload["completeness"],
                     payload["is_scorable"]))
            connection.rollback()
            secret_resume = resume_for("job:geek-secret")
            secret_resume["_meta"]["security_id"] = "live-session-secret"
            secret_json = json.dumps(secret_resume, ensure_ascii=False)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO resume_snapshots
                    (org_id,candidate_id,resume_id,raw_resume_json,
                     normalized_resume_json,content_hash,raw_hash,completeness,
                     is_scorable,captured_by)
                    VALUES('org',? ,?,?,?,?,?,?,?, 'owner')""",
                    ("cand-secret", uid(), secret_json, secret_json,
                     "hash-secret", uid(), "COMPLETE", 1))
            connection.rollback()
        finally:
            connection.close()

        # Fresh v12 schema is valid and seeds no phantom versions.
        fresh = Database(self.root / "fresh.sqlite3")
        self.assertEqual(fresh.migrate()["schema_version"], 12)
        self.assertTrue(fresh.check()["ok"])

    def test_failed_013_rolls_back_without_partial_012(self):
        seed = self._v11_seed("broken.sqlite3")
        path = seed["path"]
        before = self._rows(path, "SELECT * FROM job_candidate_inputs")
        broken_dir = _migration_dir(
            self.root, 12,
            extra=("013_broken.sql",
                   "CREATE TABLE broken_013(x INTEGER); SELECT * FROM missing_table;"))
        broken = Database(path, migration_directory=broken_dir)
        with self.assertRaises(MigrationError):
            broken.migrate()
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            batch_columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(scan_batches)")}
        finally:
            connection.close()
        self.assertEqual(version, 11)
        self.assertNotIn("broken_013", tables)
        self.assertNotIn("job_candidate_input_versions", tables)
        self.assertNotIn("platform_job_id", batch_columns)
        self.assertEqual(self._rows(path, "SELECT * FROM job_candidate_inputs"),
                         before)
        recovered = Database(path).migrate()
        self.assertEqual(recovered["schema_version"], 12)
        self.assertEqual(recovered["applied"], ["012_workbench_persistence.sql"])


if __name__ == "__main__":
    unittest.main()
