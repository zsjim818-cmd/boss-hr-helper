"""Compiler facts/publication constraints on actual migrations and repositories.

Policy fixtures are explicitly human-authored offline data, not Qwen output.
"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from storage.database import Database
from storage.models import AccessContext
from storage.repositories import NotFound, PermissionDenied, RecruitmentRepository
from storage.repositories.base import json_dump
from tests.database_support import DatabaseTestCase
from tests.policy_fixtures import offline_policy, policy_input


class CompilerPersistenceTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.repo.create_job(self.ctx, "Generic compiler job", job_id="generic")
        self.repo.create_execution_profile(self.ctx, {"protocol": "policy-only-v1"}, profile_id="profile")
        self.source = policy_input("java_backend")
        self.policy = offline_policy("java_backend")
        self.provenance = self.policy["provenance"]

    def compiled_draft(self):
        draft = self.repo.create_policy_draft(self.ctx, "generic", self.policy, source_json=self.source)
        run = self.repo.record_policy_compilation(self.ctx, "generic", self.source, status="SUCCEEDED",
            provenance=self.provenance, policy_json=self.policy, draft_id=draft["draft_id"],
            draft_revision=draft["revision"], metrics={"usage": {"total_tokens": 123, "reasoning_tokens": 0}})
        return draft, run

    def sealed_version(self):
        draft, run = self.compiled_draft()
        confirmation = self.repo.confirm_policy_draft(self.ctx, "generic", draft["draft_id"], run["run_id"], draft["draft_hash"])
        version = self.repo.create_policy_version(self.ctx, "generic", self.policy,
            execution_profile_id="profile", policy_schema_version="job_scoring_policy_v1",
            source_draft_id=draft["draft_id"], expected_draft_revision=draft["revision"])
        return draft, run, confirmation, version

    def test_validated_run_confirmation_and_attestation_publish_exact_generic_version(self):
        draft, run, confirmation, version = self.sealed_version()
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.publish_policy(self.ctx, "generic", version["policy_id"], confirmation_hash=draft["draft_hash"])
        self.repo.attest_policy_version(self.ctx, "generic", version["policy_id"], run["run_id"], confirmation["confirmation_id"])
        self.repo.publish_policy(self.ctx, "generic", version["policy_id"], confirmation_hash=draft["draft_hash"])
        self.assertEqual(self.repo.get_current_policy(self.ctx, "generic")["policy_id"], version["policy_id"])
        stored = self.repo.get_policy_compilation(self.ctx, "generic", run["run_id"])
        self.assertEqual(stored["model_version"], self.provenance["model_version"])
        self.assertEqual(json.loads(stored["metrics_json"])["usage"]["total_tokens"], 123)
        self.assertEqual(json.loads(version["source_snapshot_json"]), self.source)
        self.assertEqual(version["original_jd"], self.source["jd_text"])
        self.assertEqual(self.repo.get_policy_confirmation(self.ctx, "generic", confirmation["confirmation_id"])["draft_hash"], draft["draft_hash"])
        self.assertTrue(self.db.check()["ok"])

    def test_model_failure_is_audited_without_draft_or_published_version(self):
        run = self.repo.record_policy_compilation(self.ctx, "generic", self.source,
            status="FAILED", provenance=self.provenance, error_type="APITimeoutError",
            metrics={"elapsed_seconds": 45.01, "reasoning_tokens": None})
        self.assertEqual(run["publishable"], 0)
        self.assertIsNone(run["draft_id"])
        self.assertIsNone(run["policy_json"])
        self.assertEqual(self.repo.list_policy_compilations(self.ctx, "generic")[0]["error_type"], "APITimeoutError")
        with self.assertRaises(NotFound):
            self.repo.get_current_policy(self.ctx, "generic")

    def test_invalid_weights_cannot_be_validated_confirmed_or_versioned(self):
        policy = deepcopy(self.policy)
        policy["scoring_dimensions"][0]["weight"] = 0.99
        draft = self.repo.create_policy_draft(self.ctx, "generic", policy, source_json=self.source)
        with self.assertRaises(ValueError):
            self.repo.record_policy_compilation(self.ctx, "generic", self.source, status="SUCCEEDED",
                provenance=self.provenance, policy_json=policy, draft_id=draft["draft_id"], draft_revision=1)
        failed = self.repo.record_policy_compilation(self.ctx, "generic", self.source, status="FAILED",
            provenance=self.provenance, policy_json=policy, draft_id=draft["draft_id"], draft_revision=1,
            error_type="PolicyNotPublishable", diagnostics=[{"code": "INVALID_WEIGHT_SUM", "severity": "ERROR"}])
        with self.assertRaises(ValueError):
            self.repo.confirm_policy_draft(self.ctx, "generic", draft["draft_id"], failed["run_id"], draft["draft_hash"])
        with self.assertRaises(ValueError):
            self.repo.create_policy_version(self.ctx, "generic", policy,
                execution_profile_id="profile", policy_schema_version="job_scoring_policy_v1",
                source_draft_id=draft["draft_id"], expected_draft_revision=1)

    def test_old_confirmation_and_validation_are_invalid_after_draft_revision(self):
        draft, run, confirmation, version = self.sealed_version()
        self.repo.attest_policy_version(self.ctx, "generic", version["policy_id"], run["run_id"], confirmation["confirmation_id"])
        changed = deepcopy(self.policy)
        changed["job"]["summary"] += " (HR reviewed edit)"
        updated = self.repo.update_policy_draft(self.ctx, "generic", draft["draft_id"], changed, expected_revision=1)
        self.assertNotEqual(updated["draft_hash"], draft["draft_hash"])
        with self.assertRaises(ValueError):
            self.repo.confirm_policy_draft(self.ctx, "generic", draft["draft_id"], run["run_id"], draft["draft_hash"])
        with self.assertRaises(ValueError):
            self.repo.publish_policy(self.ctx, "generic", version["policy_id"], confirmation_hash=draft["draft_hash"])
        self.assertEqual(self.repo.get_policy_confirmation(self.ctx, "generic", confirmation["confirmation_id"])["draft_revision"], 1)

    def test_revalidation_preserves_original_model_call_lineage(self):
        draft, original = self.compiled_draft()
        changed = deepcopy(self.policy)
        changed["job"]["summary"] += " (revised draft)"
        updated = self.repo.update_policy_draft(self.ctx, "generic", draft["draft_id"], changed, expected_revision=1)
        validated = self.repo.record_policy_compilation(self.ctx, "generic", self.source, status="SUCCEEDED",
            provenance=self.provenance, policy_json=changed, draft_id=draft["draft_id"], draft_revision=2,
            run_kind="VALIDATION", parent_run_id=original["run_id"], metrics={"model_called": False})
        self.assertEqual(validated["parent_run_id"], original["run_id"])
        self.assertEqual(validated["run_kind"], "VALIDATION")
        self.assertNotEqual(validated["draft_hash"], original["draft_hash"])
        confirmation = self.repo.confirm_policy_draft(self.ctx, "generic", draft["draft_id"], validated["run_id"], updated["draft_hash"])
        self.assertEqual(confirmation["draft_revision"], 2)

    def test_revalidation_cannot_claim_another_drafts_compilation_as_its_parent(self):
        draft, run = self.compiled_draft()
        unrelated, unrelated_run = self.compiled_draft()
        updated = self.repo.update_policy_draft(self.ctx, "generic", draft["draft_id"], self.policy, expected_revision=1)
        with self.assertRaises(ValueError):
            self.repo.record_policy_compilation(self.ctx, "generic", self.source, status="SUCCEEDED",
                provenance=self.provenance, policy_json=self.policy, draft_id=draft["draft_id"], draft_revision=2,
                run_kind="VALIDATION", parent_run_id=unrelated_run["run_id"])
        forged = dict(run)
        forged.update(run_id="forged-lineage", run_kind="VALIDATION", parent_run_id=unrelated_run["run_id"],
                      draft_revision=2, draft_hash=updated["draft_hash"])
        with closing(self.db.connect()) as connection, self.assertRaises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO policy_compilation_runs(" + ",".join(forged) + ") VALUES(" +
                               ",".join("?" for _ in forged) + ")", tuple(forged.values()))

    def test_atomic_version_allocation_rolls_back_if_confirmation_cannot_be_attested(self):
        draft, run = self.compiled_draft()
        confirmation = self.repo.confirm_policy_draft(self.ctx, "generic", draft["draft_id"], run["run_id"], draft["draft_hash"])
        with self.assertRaises(NotFound):
            self.repo.create_policy_version(self.ctx, "generic", self.policy,
                execution_profile_id="profile", policy_schema_version="job_scoring_policy_v1",
                source_draft_id=draft["draft_id"], expected_draft_revision=1,
                compilation_run_id=run["run_id"], confirmation_id="missing-confirmation")
        with self.assertRaises(NotFound):
            self.repo.get_policy_version(self.ctx, "generic", policy_version=1)
        self.assertEqual(self.repo.get_job(self.ctx, "generic")["next_policy_version"], 1)
        version = self.repo.create_policy_version(self.ctx, "generic", self.policy,
            execution_profile_id="profile", policy_schema_version="job_scoring_policy_v1",
            source_draft_id=draft["draft_id"], expected_draft_revision=1,
            compilation_run_id=run["run_id"], confirmation_id=confirmation["confirmation_id"])
        self.assertEqual(version["policy_version"], 1)
        self.repo.publish_policy(self.ctx, "generic", version["policy_id"], confirmation_hash=draft["draft_hash"])

    def test_compiler_facts_confirmations_and_links_are_immutable_even_with_recursive_off(self):
        draft, run, confirmation, version = self.sealed_version()
        self.repo.attest_policy_version(self.ctx, "generic", version["policy_id"], run["run_id"], confirmation["confirmation_id"])
        with closing(self.db.connect()) as connection:
            for table in ("policy_compilation_runs", "policy_confirmations", "policy_version_compilations"):
                for recursive in (0, 1):
                    connection.execute(f"PRAGMA recursive_triggers={recursive}")
                    for sql in (f"UPDATE {table} SET org_id=org_id", f"DELETE FROM {table}",
                                f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}"):
                        with self.subTest(table=table, recursive=recursive, sql=sql), self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(sql)

    def test_compiler_and_confirmation_permissions_are_checked(self):
        draft, run = self.compiled_draft()
        self.repo.create_user(self.system, "test:reader", user_id="reader")
        self.repo.add_organization_member(self.ctx, "reader")
        self.repo.grant_job_access(self.ctx, "generic", "reader", permissions=("view",))
        reader = AccessContext("org1", "reader")
        self.assertEqual(self.repo.get_policy_compilation(reader, "generic", run["run_id"])["run_id"], run["run_id"])
        with self.assertRaises(PermissionDenied):
            self.repo.record_policy_compilation(reader, "generic", self.source, status="FAILED",
                provenance=self.provenance, error_type="Failure")
        with self.assertRaises(PermissionDenied):
            self.repo.confirm_policy_draft(reader, "generic", draft["draft_id"], run["run_id"], draft["draft_hash"])
        self.repo.revoke_job_access(self.ctx, "generic", "reader")
        with self.assertRaises(PermissionDenied):
            self.repo.list_policy_compilations(reader, "generic")

    def test_database_cannot_confirm_a_failed_compilation_or_an_unauthorized_hr(self):
        draft, valid = self.compiled_draft()
        failed = self.repo.record_policy_compilation(self.ctx, "generic", self.source,
            status="FAILED", provenance=self.provenance, policy_json=self.policy,
            draft_id=draft["draft_id"], draft_revision=1, error_type="PolicyDiagnosticsBlocked")
        self.repo.create_user(self.system, "test:untrusted", user_id="untrusted")
        self.repo.add_organization_member(self.ctx, "untrusted")
        with closing(self.db.connect()) as connection:
            for run, actor in ((failed, "owner"), (valid, "untrusted")):
                with self.subTest(status=run["status"], actor=actor), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("""INSERT INTO policy_confirmations
                        (org_id,job_id,confirmation_id,draft_id,draft_revision,draft_hash,policy_hash,run_id,confirmed_by)
                        VALUES('org1','generic','forged',?,1,?,?,?,?)""",
                        (draft["draft_id"], draft["draft_hash"], run["policy_hash"], run["run_id"], actor))

    def test_compilation_and_confirmation_cannot_cross_job_boundaries(self):
        draft, run, confirmation, version = self.sealed_version()
        self.repo.create_job(self.ctx, "Other job", job_id="other")
        other_draft = self.repo.create_policy_draft(self.ctx, "other", self.policy, source_json=self.source)
        other_run = self.repo.record_policy_compilation(self.ctx, "other", self.source,
            status="SUCCEEDED", provenance=self.provenance, policy_json=self.policy,
            draft_id=other_draft["draft_id"], draft_revision=1)
        other_confirmation = self.repo.confirm_policy_draft(self.ctx, "other", other_draft["draft_id"],
            other_run["run_id"], other_draft["draft_hash"])
        with self.assertRaises(NotFound):
            self.repo.attest_policy_version(self.ctx, "generic", version["policy_id"],
                other_run["run_id"], other_confirmation["confirmation_id"])
        with closing(self.db.connect()) as connection, self.assertRaises(sqlite3.IntegrityError):
            connection.execute("""INSERT INTO policy_version_compilations
                (org_id,job_id,policy_id,run_id,confirmation_id,created_by)
                VALUES('org1','generic',?,?,?,'owner')""",
                (version["policy_id"], other_run["run_id"], confirmation["confirmation_id"]))

    def test_database_rejects_generic_protocol_downgrade_duplicate_ids_and_bad_weights(self):
        rows = [(deepcopy(self.policy), "storage_snapshot_v1")]
        duplicate = deepcopy(self.policy)
        duplicate["scoring_dimensions"][1]["dimension_id"] = duplicate["scoring_dimensions"][0]["dimension_id"]
        rows.append((duplicate, "job_scoring_policy_v1"))
        missing_weight = deepcopy(self.policy)
        del missing_weight["scoring_dimensions"][0]["weight"]
        rows.append((missing_weight, "job_scoring_policy_v1"))
        invalid_sum = deepcopy(self.policy)
        invalid_sum["scoring_dimensions"][0]["weight"] = 0.99
        rows.append((invalid_sum, "job_scoring_policy_v1"))
        with closing(self.db.connect()) as connection:
            for policy, protocol in rows:
                with self.subTest(protocol=protocol), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("""INSERT INTO job_policy_versions
                        (org_id,job_id,policy_id,policy_version,policy_json,policy_hash,execution_profile_id,
                         policy_schema_version,created_by)
                        VALUES('org1','generic','bad',1,?,'hash','profile',?,'owner')""",
                        (json_dump(policy), protocol))

    def test_migration_upgrade_preserves_stage2_history(self):
        with tempfile.TemporaryDirectory(prefix="stage2_to_stage3_") as temporary:
            root = Path(temporary)
            old_migrations = root / "old_migrations"
            old_migrations.mkdir()
            for migration in self.db.migration_directory.glob("00[1-5]_*.sql"):
                shutil.copyfile(migration, old_migrations / migration.name)
            old_db = Database(root / "business.sqlite3", migration_directory=old_migrations)
            self.assertEqual(old_db.migrate()["schema_version"], 5)
            old_repo = RecruitmentRepository(old_db)
            old_repo.create_user(self.system, "migration-owner", user_id="owner")
            old_repo.create_organization(self.system, "Old organization", "owner", org_id="org1")
            old_repo.create_job(self.ctx, "Previously published job", job_id="old")
            old_repo.create_execution_profile(self.ctx, {}, profile_id="old-profile")
            version = old_repo.create_policy_version(self.ctx, "old", self.config, execution_profile_id="old-profile")
            old_repo.publish_policy(self.ctx, "old", version["policy_id"], confirmation_hash=version["policy_hash"])
            old_repo.create_scan_batch(self.ctx, "old", batch_id="old-batch")
            candidate = old_repo.acquire_candidate_for_job(self.ctx, "old", "old-batch", "old-candidate")
            new_db = Database(old_db.path)
            expected = [migration.name for migration in new_db._migrations() if migration.version > 5]
            self.assertEqual(new_db.migrate()["applied"], expected)
            new_repo = RecruitmentRepository(new_db)
            self.assertEqual(new_repo.get_policy_version(self.ctx, "old", version["policy_id"]), version)
            self.assertEqual(new_repo.get_job_candidate(self.ctx, "old", candidate["job_candidate"]["job_candidate_id"]), candidate["job_candidate"])
            self.assertTrue(new_db.check()["ok"])
