"""Actual migration/repository authorization and immutable policy integration."""
from contextlib import closing
import json
import sqlite3

from storage.models import AccessContext
from storage.repositories import NotFound, PermissionDenied
from tests.database_support import DatabaseTestCase


class IdentityPolicyTests(DatabaseTestCase):
    def test_hr_manages_multiple_private_jobs_with_explicit_grants(self):
        self.repo.create_user(self.system, "test:hr", user_id="hr")
        self.repo.create_user(self.system, "test:colleague", user_id="colleague")
        self.repo.add_organization_member(self.ctx, "hr")
        self.repo.add_organization_member(self.ctx, "colleague")
        hr = AccessContext("org1", "hr")
        colleague = AccessContext("org1", "colleague")
        self.repo.create_job(hr, "Java backend", job_id="java")
        self.repo.create_job(hr, "Sales", job_id="sales")
        self.assertEqual({j["job_id"] for j in self.repo.list_jobs(hr)}, {"java", "sales"})
        self.assertEqual(self.repo.list_jobs(colleague), [])
        with self.assertRaises(PermissionDenied):
            self.repo.get_job(colleague, "java")
        self.repo.grant_job_access(hr, "java", "colleague", permissions=("view",))
        self.assertEqual(self.repo.get_job(colleague, "java")["title"], "Java backend")
        with self.assertRaises(PermissionDenied):
            self.repo.create_policy_version(colleague, "java", {}, execution_profile_id="missing")
        self.repo.revoke_job_access(hr, "java", "colleague")
        with self.assertRaises(PermissionDenied):
            self.repo.get_job(colleague, "java")

    def test_tenant_context_and_foreign_membership_denied(self):
        self.create_job_policy()
        self.repo.create_user(self.system, "test:foreign", user_id="foreign")
        self.repo.create_organization(self.system, "Another organization", "foreign", org_id="org2")
        foreign = AccessContext("org2", "foreign")
        with self.assertRaises(NotFound):
            self.repo.get_job(foreign, "job1")
        with self.assertRaises(PermissionDenied):
            self.repo.get_job(AccessContext("org1", "foreign"), "job1")
        with self.assertRaises(sqlite3.IntegrityError):
            with closing(self.db.connect()) as conn:
                conn.execute("""INSERT INTO job_access_grants(org_id,job_id,user_id,granted_by)
                    VALUES('org1','job1','foreign','owner')""")
        with self.assertRaises(PermissionDenied):
            self.repo.list_jobs({"org_id": "org1", "user_id": "owner"})

    def test_policy_counter_publication_hash_and_activation_are_atomic(self):
        p1 = self.create_job_policy()
        p2 = self.repo.create_policy_version(self.ctx, "job1", {"changed": True},
                                              execution_profile_id="profile_job1", policy_id="policy2")
        self.assertEqual((p1["policy_version"], p2["policy_version"]), (1, 2))
        with self.assertRaises(ValueError):
            self.repo.publish_policy(self.ctx, "job1", "policy2", confirmation_hash="wrong")
        self.assertEqual(self.repo.get_current_policy(self.ctx, "job1")["policy_id"], "policy1")
        self.repo.publish_policy(self.ctx, "job1", "policy2", confirmation_hash=p2["policy_hash"])
        self.assertEqual(self.repo.get_current_policy(self.ctx, "job1")["policy_id"], "policy2")
        self.assertEqual(self.repo.get_policy_version(self.ctx, "job1", policy_version=1)["policy_hash"], p1["policy_hash"])
        with closing(self.db.connect()) as conn:
            rows = conn.execute("SELECT from_policy_id,to_policy_id FROM policy_activation_events ORDER BY rowid").fetchall()
            self.assertEqual([tuple(row) for row in rows], [(None, "policy1"), ("policy1", "policy2")])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_policy_update_delete_and_replace_cannot_mutate_history(self):
        self.create_job_policy()
        with closing(self.db.connect()) as conn:
            original = dict(conn.execute("SELECT * FROM job_policy_versions").fetchone())
            for sql in ["UPDATE job_policy_versions SET policy_json='{}'", "DELETE FROM job_policy_versions"]:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql)
            for recursive in (0, 1):
                conn.execute(f"PRAGMA recursive_triggers={recursive}")
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("""INSERT OR REPLACE INTO job_policy_versions
                        (org_id,job_id,policy_id,policy_version,policy_json,policy_hash,execution_profile_id,created_by)
                        VALUES('org1','job1','policy1',1,'{}','changed','profile_job1','owner')""")
            self.assertEqual(dict(conn.execute("SELECT * FROM job_policy_versions").fetchone()), original)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE jobs SET current_policy_id='unpublished',current_policy_version=2")

    def test_job_versions_are_independent_and_cross_job_publication_rejected(self):
        a = self.create_job_policy("A", "pa")
        b = self.create_job_policy("B", "pb")
        self.assertEqual(a["policy_version"], b["policy_version"])
        with self.assertRaises(NotFound):
            self.repo.publish_policy(self.ctx, "A", "pb", confirmation_hash=b["policy_hash"])
        with closing(self.db.connect()) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""INSERT INTO policy_publications
                (org_id,job_id,policy_id,policy_version,execution_profile_id,confirmation_hash,published_by)
                VALUES('org1','A','pb',1,'profile_B',?,'owner')""", (b["policy_hash"],))

    def test_draft_revision_change_invalidates_publication(self):
        self.repo.create_job(self.ctx, "Draft job", job_id="draft-job")
        self.repo.create_execution_profile(self.ctx, {}, profile_id="profile")
        draft = self.repo.create_policy_draft(self.ctx, "draft-job", {"title": "original"}, draft_id="draft")
        policy = self.repo.create_policy_version(self.ctx, "draft-job", {"title": "original"},
                                                 execution_profile_id="profile", source_draft_id="draft",
                                                 expected_draft_revision=draft["revision"])
        self.repo.update_policy_draft(self.ctx, "draft-job", "draft", {"title": "changed"}, expected_revision=1)
        with self.assertRaises(ValueError):
            self.repo.publish_policy(self.ctx, "draft-job", policy["policy_id"], confirmation_hash=policy["source_draft_hash"])
        with self.assertRaises(NotFound):
            self.repo.get_current_policy(self.ctx, "draft-job")

    def test_disabled_members_and_inactive_context_are_rejected(self):
        self.repo.create_user(self.system, "test:worker", user_id="worker")
        self.repo.add_organization_member(self.ctx, "worker")
        self.create_job_policy()
        worker = AccessContext("org1", "worker")
        self.repo.grant_job_access(self.ctx, "job1", "worker", permissions=("view", "collect"))
        self.repo.set_member_state(self.ctx, "worker", "DISABLED")
        with self.assertRaises(PermissionDenied):
            self.repo.get_job(worker, "job1")

    def test_execution_profile_cannot_change_and_credentials_not_stored(self):
        with self.assertRaises(ValueError):
            self.repo.create_execution_profile(self.ctx, {"api_key": "synthetic-secret"})
        profile = self.repo.create_execution_profile(self.ctx, {"prompt_version": "frozen"})
        with closing(self.db.connect()) as conn, self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE execution_profiles SET profile_json='{}' WHERE profile_id=?", (profile["profile_id"],))
        self.assertEqual(json.loads(self.repo.get_execution_profile(self.ctx, profile["profile_id"])["profile_json"]), {"prompt_version": "frozen"})

    def test_unchanged_confirmed_draft_can_publish_using_its_full_source_hash(self):
        self.repo.create_job(self.ctx, "Draft publication", job_id="draft")
        self.repo.create_execution_profile(self.ctx, {}, profile_id="profile")
        draft = self.repo.create_policy_draft(self.ctx, "draft", {"title": "A"},
                                              source_json={"original_jd": "Actual source text"})
        policy = self.repo.create_policy_version(self.ctx, "draft", {"title": "A"},
            execution_profile_id="profile", source_draft_id=draft["draft_id"],
            expected_draft_revision=draft["revision"])
        self.assertNotEqual(draft["draft_hash"], policy["policy_hash"])
        self.assertEqual(policy["source_draft_hash"], draft["draft_hash"])
        self.repo.publish_policy(self.ctx, "draft", policy["policy_id"], confirmation_hash=draft["draft_hash"])
        self.assertEqual(self.repo.get_current_policy(self.ctx, "draft")["policy_id"], policy["policy_id"])

    def test_confirmation_cannot_be_reused_for_a_different_source_with_same_policy(self):
        self.repo.create_job(self.ctx, "Confirm original source", job_id="draft")
        self.repo.create_execution_profile(self.ctx, {}, profile_id="profile")
        policies = []
        for source in ("Original HR requirement", "Changed HR requirement"):
            draft = self.repo.create_policy_draft(self.ctx, "draft", {"title": "same policy"},
                                                  source_json={"original_jd": source})
            policies.append(self.repo.create_policy_version(self.ctx, "draft", {"title": "same policy"},
                execution_profile_id="profile", source_draft_id=draft["draft_id"], expected_draft_revision=1))
        first, second = policies
        self.assertEqual(first["policy_hash"], second["policy_hash"])
        self.assertNotEqual(first["source_draft_hash"], second["source_draft_hash"])
        self.repo.publish_policy(self.ctx, "draft", first["policy_id"], confirmation_hash=first["source_draft_hash"])
        for stale_hash in (first["source_draft_hash"], second["policy_hash"]):
            with self.subTest(confirmation_hash=stale_hash), self.assertRaises(ValueError):
                self.repo.publish_policy(self.ctx, "draft", second["policy_id"], confirmation_hash=stale_hash)
            with closing(self.db.connect()) as conn, self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO policy_publications
                    (org_id,job_id,policy_id,policy_version,execution_profile_id,confirmation_hash,published_by)
                    VALUES('org1','draft',?,2,'profile',?,'owner')""", (second["policy_id"], stale_hash))
        self.assertEqual(self.repo.get_current_policy(self.ctx, "draft")["policy_id"], first["policy_id"])
        self.repo.publish_policy(self.ctx, "draft", second["policy_id"], confirmation_hash=second["source_draft_hash"])

    def test_invalid_falsey_draft_sources_are_not_silently_converted_to_empty_objects(self):
        self.repo.create_job(self.ctx, "Validate source", job_id="draft")
        for source in ([], 0, False, ""):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.repo.create_policy_draft(self.ctx, "draft", {}, source_json=source)

    def test_published_version_retains_original_source_after_draft_changes(self):
        self.repo.create_job(self.ctx, "Preserve source", job_id="draft")
        self.repo.create_execution_profile(self.ctx, {}, profile_id="profile")
        original = {"original_jd": "Original JD text", "hr_requirements": "Original HR notes",
                    "source_refs": ["document:original"]}
        draft = self.repo.create_policy_draft(self.ctx, "draft", {"title": "same policy"}, source_json=original)
        policy = self.repo.create_policy_version(self.ctx, "draft", {"title": "same policy"},
            execution_profile_id="profile", source_draft_id=draft["draft_id"], expected_draft_revision=1)
        self.repo.publish_policy(self.ctx, "draft", policy["policy_id"], confirmation_hash=draft["draft_hash"])
        self.repo.update_policy_draft(self.ctx, "draft", draft["draft_id"], {"title": "new draft"},
                                     source_json={"original_jd": "Edited JD"}, expected_revision=1)
        stored = self.repo.get_policy_version(self.ctx, "draft", policy["policy_id"])
        self.assertEqual(json.loads(stored["source_snapshot_json"]), original)
        self.assertEqual((stored["original_jd"], stored["hr_requirements"]),
                         (original["original_jd"], original["hr_requirements"]))
