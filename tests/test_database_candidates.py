"""Actual migration/repository integration for batch locking and candidate inputs."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from config.settings import DEFAULT_CONFIG_PATH
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository
from storage.repositories.base import PermissionDenied, NotFound
from tests.mock_data import BENCHMARK_CANDIDATES


class DatabaseCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "business.sqlite"
        self.db = Database(self.path)
        self.db.migrate()
        self.repo = RecruitmentRepository(self.db)
        system = ProvisioningContext("candidate integration test setup")
        self.repo.create_user(system, "owner", user_id="owner")
        self.repo.create_user(system, "other-hr", user_id="hr")
        self.repo.create_organization(system, "Organization", "owner", org_id="org")
        self.ctx = AccessContext("org", "owner")
        self.repo.add_organization_member(self.ctx, "hr")
        self.repo.create_job(self.ctx, "Job A", job_id="A")
        self.repo.create_job(self.ctx, "Job B", job_id="B")
        self.profile = self.repo.create_execution_profile(self.ctx, {}, profile_id="profile")
        self.config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        self.p1 = self.publish("A", "p1")
        self.pb = self.publish("B", "pb")
        self.batch1 = self.repo.create_scan_batch(self.ctx, "A", batch_id="batch1")

    def publish(self, job_id, policy_id):
        policy = self.repo.create_policy_version(self.ctx, job_id, self.config,
                                                  execution_profile_id="profile", policy_id=policy_id)
        self.repo.publish_policy(self.ctx, job_id, policy_id, confirmation_hash=policy["policy_hash"])
        return policy

    def resume(self, *, complete=True, key="candidate-A"):
        data = deepcopy(BENCHMARK_CANDIDATES[0])
        data["_meta"]["candidate_key"] = key
        if not complete:
            data.pop("skills")
        return data

    def acquire(self, key="candidate-A", batch="batch1", resume=None, **options):
        return self.repo.acquire_candidate_for_job(self.ctx, "A", batch, key, resume=resume, **options)

    def sql(self, query, args=()):
        with closing(self.db.connect()) as conn:
            return [dict(row) for row in conn.execute(query, args)]

    def reject_sql(self, query, args=()):
        with closing(self.db.connect()) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute(query, args)

    def test_batches_lock_versions_and_existing_candidates_keep_original(self):
        a = self.acquire(resume=self.resume(complete=False))
        self.publish("A", "p2")
        late = self.acquire("candidate-late", resume=self.resume(key="candidate-late"))
        self.assertEqual(late["job_candidate"]["policy_id"], "p1")
        self.assertEqual(late["job_candidate"]["policy_version"], 1)
        batch2 = self.repo.create_scan_batch(self.ctx, "A", batch_id="batch2")
        self.assertEqual(batch2["policy_id"], "p2")
        b = self.acquire("candidate-B", "batch2", self.resume(key="candidate-B"))
        repeated = self.acquire(batch="batch2", resume=self.resume())
        self.assertEqual(b["job_candidate"]["policy_id"], "p2")
        self.assertFalse(repeated["is_new"])
        self.assertEqual(a["job_candidate"]["job_candidate_id"], repeated["job_candidate"]["job_candidate_id"])
        self.assertEqual(repeated["job_candidate"]["policy_id"], "p1")
        self.assertEqual(repeated["sealed_input"]["policy_id"], "p1")
        self.assertEqual(repeated["acquisition"]["batch_id"], "batch2")

    def test_partial_completion_is_sealed_once_without_changing_bound_policy(self):
        first = self.acquire(resume=self.resume(complete=False))
        self.assertIsNone(first["sealed_input"])
        self.publish("A", "p2")
        self.repo.create_scan_batch(self.ctx, "A", batch_id="batch2")
        completed = self.acquire(batch="batch2", resume=self.resume())
        self.assertEqual(completed["sealed_input"]["policy_id"], "p1")
        original_id = completed["sealed_input"]["resume_id"]
        changed = self.resume()
        changed["skills"] += "; new acquisition content"
        repeated = self.acquire(batch="batch2", resume=changed)
        self.assertNotEqual(repeated["resume_snapshot"]["resume_id"], original_id)
        self.assertEqual(repeated["sealed_input"]["resume_id"], original_id)
        sealed = self.repo.seal_candidate_input(self.ctx, "A", first["job_candidate"]["job_candidate_id"], repeated["resume_snapshot"]["resume_id"])
        self.assertEqual(sealed["resume_id"], original_id)

    def test_restart_and_resume_batch_keep_original_policy(self):
        self.repo.transition_scan_batch(self.ctx, "A", "batch1", "PAUSED")
        self.publish("A", "p2")
        reopened = RecruitmentRepository(Database(self.path))
        batch = reopened.transition_scan_batch(self.ctx, "A", "batch1", "RUNNING")
        self.assertEqual(batch["policy_id"], "p1")
        row = reopened.acquire_candidate_for_job(self.ctx, "A", "batch1", "new-after-restart", resume=self.resume())
        self.assertEqual(row["job_candidate"]["policy_id"], "p1")

    def test_batch_lifecycle_blocks_terminal_resume_and_inactive_acquisition(self):
        self.repo.transition_scan_batch(self.ctx, "A", "batch1", "PAUSED")
        with self.assertRaises(ValueError):
            self.acquire()
        self.repo.transition_scan_batch(self.ctx, "A", "batch1", "COMPLETED")
        with self.assertRaises(ValueError):
            self.repo.transition_scan_batch(self.ctx, "A", "batch1", "RUNNING")
        finished = self.repo.get_scan_batch(self.ctx, "A", "batch1")
        self.assertIsNotNone(finished["completed_at"])

    def test_database_rejects_batch_rebinding_and_cross_job_policy(self):
        self.publish("A", "p2")
        self.reject_sql("UPDATE scan_batches SET policy_id='p2',policy_version=2 WHERE batch_id='batch1'")
        self.reject_sql("INSERT INTO scan_batches(org_id,job_id,batch_id,policy_id,policy_version,created_by) VALUES('org','B','bad','p1',1,'owner')")
        self.reject_sql("INSERT OR REPLACE INTO scan_batches(org_id,job_id,batch_id,policy_id,policy_version,created_by) VALUES('org','A','batch1','p2',2,'owner')")
        self.assertEqual(self.repo.get_scan_batch(self.ctx, "A", "batch1")["policy_id"], "p1")

    def test_database_rejects_candidate_policy_not_matching_batch(self):
        self.publish("A", "p2")
        self.repo.create_candidate(self.ctx, "A", candidate_id="direct-candidate")
        self.reject_sql("INSERT INTO job_candidates(org_id,job_id,job_candidate_id,candidate_id,candidate_key,policy_id,policy_version,first_batch_id) "
                        "VALUES('org','A','bad','direct-candidate','direct','p2',2,'batch1')")

    def test_same_candidate_can_have_independent_job_associations(self):
        a = self.acquire(external_id="same-platform-person", resume=self.resume())
        batch_b = self.repo.create_scan_batch(self.ctx, "B", batch_id="batch-b")
        b = self.repo.acquire_candidate_for_job(self.ctx, "B", batch_b["batch_id"], "job-B:person",
                                               external_id="same-platform-person", resume=self.resume(key="job-B:person"))
        self.assertEqual(a["job_candidate"]["candidate_id"], b["job_candidate"]["candidate_id"])
        self.assertNotEqual(a["job_candidate"]["job_candidate_id"], b["job_candidate"]["job_candidate_id"])
        self.assertEqual(b["job_candidate"]["policy_id"], "pb")
        with self.assertRaises(NotFound):
            self.repo.get_job_candidate(self.ctx, "A", b["job_candidate"]["job_candidate_id"])

    def test_identity_does_not_merge_by_name_and_conflicts_roll_back(self):
        a = self.acquire(resume=self.resume(), external_id="identity-A")
        b = self.acquire("candidate-B", resume=self.resume(), external_id="identity-B")
        self.assertNotEqual(a["job_candidate"]["candidate_id"], b["job_candidate"]["candidate_id"])
        with self.assertRaises(ValueError):
            self.acquire(resume=self.resume(), external_id="identity-B")
        self.assertEqual(len(self.sql("SELECT * FROM candidate_acquisitions")), 2)

    def test_authorization_checked_for_batch_candidate_and_raw_resume_reads(self):
        row = self.acquire(resume=self.resume())
        hr = AccessContext("org", "hr")
        with self.assertRaises(PermissionDenied):
            self.repo.get_scan_batch(hr, "A", "batch1")
        with self.assertRaises(PermissionDenied):
            self.repo.get_job_candidate(hr, "A", row["job_candidate"]["job_candidate_id"])
        self.repo.grant_job_access(self.ctx, "A", "hr", permissions=("view",))
        self.assertEqual(self.repo.get_job_candidate(hr, "A", row["job_candidate"]["job_candidate_id"])["policy_id"], "p1")
        with self.assertRaises(PermissionDenied):
            self.repo.append_resume_snapshot(hr, "A", row["job_candidate"]["job_candidate_id"], self.resume(), batch_id="batch1")

    def test_raw_mapping_failures_and_unknown_fields_are_not_sealed(self):
        invalid = self.resume()
        invalid["work_experience"][0]["responsibility"] = {"wrong": "mapping"}
        row = self.acquire(resume=invalid)
        snapshot = row["resume_snapshot"]
        self.assertEqual(json.loads(snapshot["raw_resume_json"]), invalid)
        self.assertEqual(snapshot["is_scorable"], 0)
        self.assertEqual(snapshot["completeness"], "PARTIAL")
        self.assertTrue(json.loads(snapshot["data_issues_json"]))
        self.assertIsNone(row["sealed_input"])
        with self.assertRaises(ValueError):
            self.repo.seal_candidate_input(self.ctx, "A", row["job_candidate"]["job_candidate_id"], snapshot["resume_id"])
        unknown = self.resume()
        unknown["unmapped"] = "preserved"
        other = self.acquire("candidate-unknown", resume=unknown)
        self.assertEqual(other["resume_snapshot"]["is_scorable"], 0)
        self.assertIsNone(other["sealed_input"])

    def test_raw_snapshots_and_original_input_are_database_immutable(self):
        acquired = self.acquire(resume=self.resume())
        snapshot = acquired["resume_snapshot"]
        self.reject_sql("UPDATE resume_snapshots SET raw_resume_json='{}' WHERE resume_id=?", (snapshot["resume_id"],))
        self.reject_sql("DELETE FROM resume_snapshots WHERE resume_id=?", (snapshot["resume_id"],))
        self.reject_sql("UPDATE job_candidate_inputs SET policy_id='wrong' WHERE job_candidate_id=?", (acquired["job_candidate"]["job_candidate_id"],))
        self.reject_sql("UPDATE job_candidates SET policy_id='wrong' WHERE job_candidate_id=?", (acquired["job_candidate"]["job_candidate_id"],))
        self.assertEqual(self.sql("PRAGMA foreign_key_check"), [])

    def test_acquisition_capture_id_is_idempotent_without_replacing_snapshot(self):
        first = self.acquire(resume=self.resume(), capture_id="event1")
        repeated = self.acquire(resume=self.resume(), capture_id="event1")
        self.assertEqual(first["resume_snapshot"]["resume_id"], repeated["resume_snapshot"]["resume_id"])
        self.assertEqual(len(self.sql("SELECT * FROM candidate_acquisitions")), 1)
        changed = self.resume()
        changed["skills"] += "; changed"
        with self.assertRaises(ValueError):
            self.acquire(resume=changed, capture_id="event1")
        self.assertEqual(len(self.sql("SELECT * FROM resume_snapshots")), 1)

    def test_cross_organization_candidate_and_batch_references_fail(self):
        system = ProvisioningContext("second organization for isolation tests")
        self.repo.create_organization(system, "Other organization", "owner", org_id="other")
        other = AccessContext("other", "owner")
        self.repo.create_job(other, "Other A", job_id="A")
        self.repo.create_execution_profile(other, {}, profile_id="profile")
        policy = self.repo.create_policy_version(other, "A", self.config, execution_profile_id="profile", policy_id="outside")
        self.repo.publish_policy(other, "A", "outside", confirmation_hash=policy["policy_hash"])
        self.repo.create_candidate(other, "A", candidate_id="foreign-candidate")
        self.reject_sql("INSERT INTO scan_batches(org_id,job_id,batch_id,policy_id,policy_version,created_by) VALUES('org','A','wrong-org','outside',1,'owner')")
        self.reject_sql("INSERT INTO job_candidates(org_id,job_id,job_candidate_id,candidate_id,candidate_key,policy_id,policy_version,first_batch_id) "
                        "VALUES('org','A','wrong-person','foreign-candidate','foreign','p1',1,'batch1')")

    def _ordered_concurrency(self, first_kind):
        policy = self.repo.create_policy_version(self.ctx, "A", self.config,
                                                 execution_profile_id="profile", policy_id="p2")
        locked, release, second_started, second_done = (threading.Event() for _ in range(4))
        errors, values = [], {}
        original_connect = self.db.connect
        prefix = "INSERT INTO POLICY_PUBLICATIONS" if first_kind == "publish" else "INSERT INTO SCAN_BATCHES"

        def instrumented_connect():
            conn = original_connect()
            def trace(sql):
                if threading.current_thread().name == "first-db-writer" and sql.lstrip().upper().startswith(prefix) and not locked.is_set():
                    locked.set()
                    if not release.wait(5):
                        errors.append(AssertionError("Timed out releasing the first writer"))
            conn.set_trace_callback(trace)
            return conn

        self.db.connect = instrumented_connect
        def publish():
            values["publication"] = self.repo.publish_policy(self.ctx, "A", "p2", confirmation_hash=policy["policy_hash"])
        def batch():
            values["batch"] = self.repo.create_scan_batch(self.ctx, "A", batch_id="concurrent-batch")
        def run(operation, second=False):
            if second:
                second_started.set()
            try:
                operation()
            except BaseException as exc:
                errors.append(exc)
            finally:
                if second:
                    second_done.set()
        operations = (publish, batch) if first_kind == "publish" else (batch, publish)
        first = threading.Thread(target=run, args=(operations[0],), name="first-db-writer")
        second = threading.Thread(target=run, args=(operations[1], True), name="second-db-writer")
        try:
            first.start()
            self.assertTrue(locked.wait(5), errors)
            second.start()
            self.assertTrue(second_started.wait(2))
            self.assertFalse(second_done.wait(.05), "Second writer ran before the first committed")
        finally:
            release.set()
            first.join(8)
            if second.ident is not None:
                second.join(8)
            self.db.connect = original_connect
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        return values["batch"]

    def test_concurrent_publish_before_batch_binds_new_version(self):
        self.assertEqual(self._ordered_concurrency("publish")["policy_id"], "p2")

    def test_concurrent_batch_before_publish_keeps_old_version(self):
        batch = self._ordered_concurrency("batch")
        self.assertEqual(batch["policy_id"], "p1")
        result = self.acquire("after-concurrent-publish", "concurrent-batch", self.resume())
        self.assertEqual(result["job_candidate"]["policy_id"], "p1")

    def test_resume_reads_require_acquisition_by_the_requested_job(self):
        a = self.acquire(external_id="shared-person", resume=self.resume())
        batch_b = self.repo.create_scan_batch(self.ctx, "B", batch_id="batch-b-reads")
        b = self.repo.acquire_candidate_for_job(self.ctx, "B", batch_b["batch_id"], "candidate-B",
                                                 external_id="shared-person")
        a_id, b_id = a["job_candidate"]["job_candidate_id"], b["job_candidate"]["job_candidate_id"]
        self.assertEqual(a["job_candidate"]["candidate_id"], b["job_candidate"]["candidate_id"])
        a_resume = a["resume_snapshot"]["resume_id"]
        self.assertEqual(self.repo.get_resume_snapshot(self.ctx, "A", a_id, a_resume)["resume_id"], a_resume)
        with self.assertRaises(NotFound):
            self.repo.get_resume_snapshot(self.ctx, "B", b_id, a_resume)
        own_resume = self.repo.append_resume_snapshot(self.ctx, "B", b_id, self.resume(key="candidate-B"),
                                                       batch_id=batch_b["batch_id"])
        self.assertEqual(self.repo.get_resume_snapshot(self.ctx, "B", b_id, own_resume["resume_id"]), own_resume)
        with self.assertRaises(NotFound):
            self.repo.get_resume_snapshot(self.ctx, "A", a_id, own_resume["resume_id"])

    def test_acquisition_reads_recheck_revoked_access(self):
        candidate = self.acquire(resume=self.resume())["job_candidate"]["job_candidate_id"]
        hr = AccessContext("org", "hr")
        self.repo.grant_job_access(self.ctx, "A", "hr", permissions=("view",))
        self.assertEqual(len(self.repo.list_candidate_acquisitions(hr, "A", candidate)), 1)
        self.repo.revoke_job_access(self.ctx, "A", "hr")
        with self.assertRaises(PermissionDenied):
            self.repo.list_candidate_acquisitions(hr, "A", candidate)
