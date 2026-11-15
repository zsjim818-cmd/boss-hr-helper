"""Actual migration/connection integration checks on independent file databases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest

from config.settings import PROJECT_ROOT
from storage.database import Database, MigrationError
from storage.database.transaction import transaction


class DatabaseInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="recruitment_db_test_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "business.sqlite3"
        self.database = Database(self.path)
        self.migrations = self.database._migrations()
        self.latest_version = self.migrations[-1].version
        self.migration_count = len(self.migrations)

    def copy_migrations(self):
        target = self.root / "migrations"
        shutil.copytree(self.database.migration_directory, target)
        return target

    def test_first_initialization_and_required_business_entities(self):
        result = self.database.migrate()
        self.assertEqual(result["schema_version"], self.latest_version)
        self.assertEqual(len(result["applied"]), self.migration_count)
        with closing(self.database.connect()) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"organizations", "users", "organization_memberships", "jobs", "job_access_grants",
                         "policy_drafts", "job_policy_versions", "policy_publications", "policy_activation_events",
                         "execution_profiles", "candidates", "candidate_identity_aliases", "resume_snapshots",
                         "scan_batches", "job_candidates", "candidate_acquisitions", "job_candidate_inputs",
                         "evaluation_tasks", "evaluation_attempts", "evaluations", "hr_review_events"} <= tables)
        self.assertTrue(self.database.check()["ok"])

    def test_every_connection_enforces_required_pragmas(self):
        self.database.migrate()
        for _ in range(2):
            with closing(self.database.connect()) as connection:
                self.assertEqual(connection.isolation_level, None)
                self.assertIs(connection.row_factory, sqlite3.Row)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA recursive_triggers").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_repeat_migration_is_idempotent(self):
        self.database.migrate()
        result = self.database.migrate()
        self.assertEqual(result["applied"], [])
        self.assertTrue(result["already_current"])
        with closing(self.database.connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], self.migration_count)
        self.assertTrue(self.database.check()["ok"])

    def test_restart_preserves_committed_business_data(self):
        self.database.migrate()
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO organizations(org_id,name) VALUES('org','Persisted organization')")
        restarted = Database(self.path)
        restarted.migrate()
        with closing(restarted.connect()) as connection:
            self.assertEqual(connection.execute("SELECT name FROM organizations WHERE org_id='org'").fetchone()[0],
                             "Persisted organization")
        self.assertTrue(restarted.check()["ok"])

    def test_check_missing_database_does_not_create_one(self):
        result = self.database.check()
        self.assertFalse(result["ok"])
        self.assertFalse(self.path.exists())

    def test_initialization_failure_rolls_back_all_pending_ddl_and_ledger(self):
        directory = self.copy_migrations()
        (directory / f"{self.latest_version + 1:03d}_failing.sql").write_text(
            "CREATE TABLE must_not_survive(value TEXT);\nINSERT INTO missing_table VALUES(1);\n", encoding="utf-8")
        with self.assertRaises(MigrationError):
            Database(self.path, migration_directory=directory).migrate()
        with closing(sqlite3.connect(self.path)) as connection:
            tables = list(connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))
            self.assertEqual(tables, [])
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_upgrade_failure_preserves_existing_schema_and_data(self):
        self.database.migrate()
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO organizations(org_id,name) VALUES('org','Still here')")
        directory = self.copy_migrations()
        (directory / f"{self.latest_version + 1:03d}_failing.sql").write_text(
            "CREATE TABLE must_not_survive(value TEXT);\nINSERT INTO missing_table VALUES(1);\n", encoding="utf-8")
        with self.assertRaises(MigrationError):
            Database(self.path, migration_directory=directory).migrate()
        with closing(self.database.connect()) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='must_not_survive'").fetchone())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], self.migration_count)
            self.assertEqual(connection.execute("SELECT name FROM organizations WHERE org_id='org'").fetchone()[0], "Still here")
        self.assertTrue(self.database.check()["ok"])

    def test_changed_applied_migration_is_not_silently_accepted(self):
        directory = self.copy_migrations()
        database = Database(self.path, migration_directory=directory)
        database.migrate()
        first = directory / "001_identity_policy.sql"
        first.write_text(first.read_text(encoding="utf-8") + "\n-- changed after deployment\n", encoding="utf-8")
        with self.assertRaises(MigrationError):
            database.migrate()
        self.assertFalse(database.check()["ok"])

    def test_schema_drift_is_detected_without_repairing_it(self):
        self.database.migrate()
        with closing(self.database.connect()) as connection:
            connection.execute("DROP TRIGGER no_update_evaluations")
        self.assertFalse(self.database.check()["ok"])
        with self.assertRaises(MigrationError):
            self.database.migrate()

    def test_migration_ledger_itself_cannot_be_overwritten(self):
        self.database.migrate()
        with closing(self.database.connect()) as connection:
            for sql in ["UPDATE schema_migrations SET checksum='forged' WHERE version=1",
                        "DELETE FROM schema_migrations WHERE version=1",
                        "INSERT OR REPLACE INTO schema_migrations SELECT * FROM schema_migrations WHERE version=1"]:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql)
        self.assertTrue(self.database.check()["ok"])

    def test_parallel_initialization_produces_one_consistent_history(self):
        barrier = Barrier(2)
        def initialize():
            barrier.wait()
            return Database(self.path).migrate()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: initialize(), range(2)))
        self.assertEqual(sum(len(result["applied"]) for result in results), self.migration_count)
        self.assertTrue(self.database.check()["ok"])

    def test_transaction_failure_and_nested_savepoint_have_distinct_scopes(self):
        self.database.migrate()
        with self.assertRaises(RuntimeError):
            with self.database.transaction() as connection:
                connection.execute("INSERT INTO organizations(org_id,name) VALUES('rollback','Rollback')")
                raise RuntimeError("abort")
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO organizations(org_id,name) VALUES('outer','Outer')")
            with self.assertRaises(RuntimeError):
                with transaction(connection):
                    connection.execute("INSERT INTO organizations(org_id,name) VALUES('inner','Inner')")
                    raise RuntimeError("abort nested")
        with closing(self.database.connect()) as connection:
            rows = [row[0] for row in connection.execute("SELECT org_id FROM organizations ORDER BY org_id")]
            self.assertEqual(rows, ["outer"])

    def test_database_module_does_not_load_model_clients_or_bge(self):
        result = subprocess.run([sys.executable, "-c",
                                 "import sys; import storage.database; assert 'openai' not in sys.modules; "
                                 "assert 'sentence_transformers' not in sys.modules; assert 'torch' not in sys.modules"],
                                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_refuse_unversioned_nonempty_database_and_memory_database(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
            connection.commit()
        with self.assertRaises(MigrationError):
            self.database.migrate()
        with self.assertRaises(ValueError):
            Database(":memory:")
