"""Synthetic fixtures on actual SQLite repositories, with no model calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from schemas.job_policy import JobScoringPolicy
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository, NotFound
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.policy_fixtures import offline_policy


class PrescreenDatabaseCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="prescreen_test_")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "business.sqlite3"
        self.db = Database(self.path)
        self.db.migrate()
        self.repo = RecruitmentRepository(self.db)
        self.system = ProvisioningContext("isolated synthetic prescreen fixture")
        self.repo.create_user(self.system, "prescreen:owner", user_id="owner")
        self.repo.create_organization(self.system, "Synthetic prescreen organization", "owner", org_id="org")
        self.ctx = AccessContext("org", "owner")

    def publish(self, case_id="java_backend", job_id="job", *, policy=None, policy_id=None):
        value = JobScoringPolicy.model_validate(policy or offline_policy(case_id)).model_dump(mode="json")
        try:
            self.repo.get_job(self.ctx, job_id)
        except NotFound:
            self.repo.create_job(self.ctx, value["job"]["title"], job_id=job_id)
        source = {"job_title": value["job"]["title"], **value["sources"]}
        draft = self.repo.create_policy_draft(self.ctx, job_id, value, source_json=source)
        run = self.repo.record_policy_compilation(self.ctx, job_id, source, status="SUCCEEDED",
            provenance=value["provenance"], policy_json=value, draft_id=draft["draft_id"], draft_revision=1,
            metrics={"test_fixture": True, "model_calls": False})
        confirmation = self.repo.confirm_policy_draft(self.ctx, job_id, draft["draft_id"], run["run_id"], draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"assessment_scope": "synthetic-prescreen-fixture"})
        version = self.repo.create_policy_version(self.ctx, job_id, value, policy_id=policy_id,
            execution_profile_id=profile["profile_id"], policy_schema_version="job_scoring_policy_v1",
            source_draft_id=draft["draft_id"], expected_draft_revision=1,
            compilation_run_id=run["run_id"], confirmation_id=confirmation["confirmation_id"])
        self.repo.publish_policy(self.ctx, job_id, version["policy_id"], confirmation_hash=draft["draft_hash"])
        return version

    def resume(self, key="synthetic-candidate", **changes):
        value = deepcopy(BENCHMARK_CANDIDATES[0])
        value.update(name="Synthetic candidate", degree="本科", work_years="5年",
                     job_status="离职-随时到岗", active_status="今日活跃",
                     expectation={"city": "北京", "salary": "12K/月"},
                     skills="Java, SQL, Spring Boot, Quarkus, 接口自动化测试, Kafka, Kubernetes",
                     work_experience=[{"company": "Synthetic company", "position": "Engineer",
                         "responsibility": "负责Java与SQL事务设计。独立完成Spring Boot服务与接口自动化测试。", "keywords": []}],
                     project_experience=[], education=[{"school": "Synthetic school", "degree": "本科"}],
                     advantage="", certifications=[])
        value["_meta"]["candidate_key"] = key
        value.update(changes)
        return value

    def acquire(self, job_id="job", key="synthetic-candidate", *, resume=None, batch_id=None):
        batch = self.repo.create_scan_batch(self.ctx, job_id, batch_id=batch_id)
        return self.repo.acquire_candidate_for_job(self.ctx, job_id, batch["batch_id"], key,
                                                   resume=resume if resume is not None else self.resume(key))
