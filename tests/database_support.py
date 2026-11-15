"""Offline fixtures for integration tests against real SQLite migrations/repos."""
import json
from pathlib import Path
import tempfile
import unittest

from config.settings import PROJECT_ROOT
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="recruitment_db_test_")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "business.sqlite3"
        self.db = Database(self.path)
        self.db.migrate()
        self.repo = RecruitmentRepository(self.db)
        self.system = ProvisioningContext("isolated integration test bootstrap")
        self.repo.create_user(self.system, "test:owner", user_id="owner")
        self.repo.create_organization(self.system, "Test organization", "owner", org_id="org1")
        self.ctx = AccessContext("org1", "owner")
        self.config = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))

    def create_job_policy(self, job_id="job1", policy_id="policy1"):
        self.repo.create_job(self.ctx, "Test job", job_id=job_id)
        profile = self.repo.create_execution_profile(self.ctx, {}, profile_id="profile_" + job_id)
        policy = self.repo.create_policy_version(self.ctx, job_id, self.config,
                                                execution_profile_id=profile["profile_id"], policy_id=policy_id)
        self.repo.publish_policy(self.ctx, job_id, policy["policy_id"], confirmation_hash=policy["policy_hash"])
        return policy
