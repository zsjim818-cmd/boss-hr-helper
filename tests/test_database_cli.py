"""Real CLI processes and a cold-process Repository persistence check."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from config.settings import PROJECT_ROOT
from storage.database import Database
from tests.database_support import DatabaseTestCase


class DatabaseCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="recruitment_cli_test_")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "business.sqlite3"

    def run_cli(self, command, *args, env=None):
        environment = os.environ.copy()
        if env:
            environment.update(env)
        result = subprocess.run([sys.executable, "-X", "utf8", str(PROJECT_ROOT / "main.py"),
                                 command, *args], cwd=self.directory.name, env=environment,
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        return result, json.loads(result.stdout)

    def test_init_check_and_repeated_migrate_in_separate_processes(self):
        init, initialized = self.run_cli("db-init", "--database", str(self.path))
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertEqual(len(initialized["result"]["applied"]), len(Database(self.path)._migrations()))
        repeat, migrated = self.run_cli("db-migrate", "--database", str(self.path))
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertTrue(migrated["result"]["already_current"])
        check, checked = self.run_cli("db-check", "--database", str(self.path))
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertTrue(checked["result"]["ok"])
        self.assertEqual(checked["result"]["foreign_key_violations"], [])
        self.assertEqual(checked["result"]["pragmas"], {
            "foreign_keys": 1, "recursive_triggers": 1, "journal_mode": "wal", "busy_timeout": 5000})

    def test_environment_path_and_busy_timeout_are_used(self):
        result, output = self.run_cli("db-init", env={"RECRUITMENT_DB_PATH": str(self.path),
                                                      "RECRUITMENT_DB_BUSY_TIMEOUT_MS": "1234"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(output["database"]), self.path)
        result, output = self.run_cli("db-check", env={"RECRUITMENT_DB_PATH": str(self.path),
                                                       "RECRUITMENT_DB_BUSY_TIMEOUT_MS": "1234"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["result"]["pragmas"]["busy_timeout"], 1234)

    def test_check_missing_database_does_not_create_it(self):
        result, output = self.run_cli("db-check", "--database", str(self.path))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output["error_type"], "FileNotFoundError")
        self.assertFalse(self.path.exists())

    def test_corrupt_database_and_invalid_timeout_return_explicit_failure(self):
        self.path.write_bytes(b"This is not a SQLite database")
        result, output = self.run_cli("db-migrate", "--database", str(self.path))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(output["ok"])
        self.assertIn(output["error_type"], ("DatabaseError", "MigrationError"))
        result, output = self.run_cli("db-init", "--database", str(self.path), "--busy-timeout-ms", "0")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output["error_type"], "ValueError")


class ColdProcessPersistenceTests(DatabaseTestCase):
    def test_new_process_repository_reads_original_batch_and_candidate_policy(self):
        self.create_job_policy()
        self.repo.create_scan_batch(self.ctx, "job1", batch_id="batch1")
        acquired = self.repo.acquire_candidate_for_job(self.ctx, "job1", "batch1", "candidate-A")
        jcid = acquired["job_candidate"]["job_candidate_id"]
        v2 = self.repo.create_policy_version(self.ctx, "job1", self.config,
            execution_profile_id="profile_job1", policy_id="policy2")
        self.repo.publish_policy(self.ctx, "job1", "policy2", confirmation_hash=v2["policy_hash"])
        program = """
import json, sys
from storage.database import Database
from storage.models import AccessContext
from storage.repositories import RecruitmentRepository
db = Database(sys.argv[1])
repo = RecruitmentRepository(db)
ctx = AccessContext('org1', 'owner')
print(json.dumps({'current': repo.get_current_policy(ctx, 'job1')['policy_id'],
                  'batch': repo.get_scan_batch(ctx, 'job1', 'batch1')['policy_id'],
                  'candidate': repo.get_job_candidate(ctx, 'job1', sys.argv[2])['policy_id'],
                  'check': db.check()['ok']}))
"""
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", program, str(self.path), jcid],
                                cwd=PROJECT_ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "current": "policy2", "batch": "policy1", "candidate": "policy1", "check": True})
