"""P1-3: v9 -> v10 data migration keeps results, reviews, contacts and guards."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage.database import Database, MigrationError
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository
from tests.pipeline_support import RecordingReview, RecordingSemantic, resume_for
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from schemas.job_policy import JobScoringPolicy
from tests.policy_fixtures import offline_policy

PROJECT_MIGRATIONS = Path(__file__).resolve().parents[1] / "storage" / "database" / "migrations"


def _migration_dir(parent: Path, upto: int, extra: tuple[str, str] | None = None) -> Path:
    target = Path(tempfile.mkdtemp(prefix=f"migrations_v{upto}_", dir=parent))
    for path in sorted(PROJECT_MIGRATIONS.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version <= upto:
            shutil.copyfile(path, target / path.name)
    if extra is not None:
        name, sql = extra
        (target / name).write_text(sql, encoding="utf-8")
    return target


def _publish(repo, ctx, job_id):
    value = JobScoringPolicy.model_validate(offline_policy("java_backend")).model_dump(mode="json")
    repo.create_job(ctx, value["job"]["title"], job_id=job_id)
    source = {"job_title": value["job"]["title"], **value["sources"]}
    draft = repo.create_policy_draft(ctx, job_id, value, source_json=source)
    run = repo.record_policy_compilation(ctx, job_id, source, status="SUCCEEDED",
        provenance=value["provenance"], policy_json=value, draft_id=draft["draft_id"],
        draft_revision=1, metrics={"migration_acceptance": True})
    confirmation = repo.confirm_policy_draft(ctx, job_id, draft["draft_id"],
                                             run["run_id"], draft["draft_hash"])
    profile = repo.create_execution_profile(ctx, {"assessment_scope": "migration-010"})
    version = repo.create_policy_version(ctx, job_id, value,
        execution_profile_id=profile["profile_id"],
        policy_schema_version="job_scoring_policy_v1",
        source_draft_id=draft["draft_id"], expected_draft_revision=1,
        compilation_run_id=run["run_id"], confirmation_id=confirmation["confirmation_id"])
    repo.publish_policy(ctx, job_id, version["policy_id"],
                        confirmation_hash=draft["draft_hash"])
    return version


class Migration010AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="migration_010_")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def _v9_database(self, name="legacy.sqlite3"):
        path = self.root / name
        database = Database(path, migration_directory=_migration_dir(self.root, 9))
        self.assertEqual(database.migrate()["schema_version"], 9)
        return path

    def _seed(self, path):
        database = Database(path, migration_directory=_migration_dir(self.root, 9))
        repo = RecruitmentRepository(database)
        system = ProvisioningContext("migration 010 acceptance seed")
        repo.create_user(system, "mig:owner", user_id="owner")
        repo.create_organization(system, "Migration org", "owner", org_id="org")
        ctx = AccessContext("org", "owner")
        version = _publish(repo, ctx, "job")
        batch = repo.create_scan_batch(ctx, "job")
        acquired = repo.acquire_candidate_for_job(
            ctx, "job", batch["batch_id"], "cand", resume=resume_for("cand"))
        jcid = acquired["job_candidate"]["job_candidate_id"]
        pipeline = PolicyScoringPipeline(
            repo, semantic_service=RecordingSemantic(repo),
            review_service=RecordingReview(repo))
        outcome = pipeline.run_and_persist(ctx, "job", jcid, scoring_mode="deep_mode")
        evaluation = outcome["evaluation"]
        review = repo.append_pipeline_review(ctx, "job",
                                             evaluation["pipeline_evaluation_id"],
                                             "APPROVE", "迁移验收")
        contact = repo.append_pipeline_contact_event(
            ctx, "job", evaluation["pipeline_evaluation_id"], "CONTACTED", "迁移验收")
        return {"ctx": ctx, "version": version, "jcid": jcid,
                "evaluation": evaluation, "review": review, "contact": contact}

    def _snapshot(self, path):
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return {
                "evaluations": [dict(row) for row in connection.execute(
                    "SELECT * FROM pipeline_evaluations ORDER BY pipeline_evaluation_id")],
                "attempts": [dict(row) for row in connection.execute(
                    "SELECT * FROM pipeline_attempts ORDER BY attempt_id")],
                "reviews": [dict(row) for row in connection.execute(
                    "SELECT * FROM pipeline_hr_reviews ORDER BY review_event_id")],
                "contacts": [dict(row) for row in connection.execute(
                    "SELECT * FROM pipeline_contact_events ORDER BY contact_event_id")],
            }
        finally:
            connection.close()

    def test_v9_to_v10_preserves_all_pipeline_data_and_guards(self):
        path = self._v9_database()
        seed = self._seed(path)
        before = self._snapshot(path)
        self.assertEqual(len(before["evaluations"]), 1)
        self.assertEqual(before["evaluations"][0]["scoring_mode"], "deep_mode")

        upgraded = Database(path)
        result = upgraded.migrate()
        self.assertEqual(result["schema_version"], 10)
        self.assertEqual(result["applied"], ["010_pipeline_scoring_mode.sql"])
        check = upgraded.check()
        self.assertTrue(check["ok"], check)
        self.assertEqual(check["foreign_key_violations"], [])
        self.assertEqual(check["integrity_check"], ["ok"])

        after = self._snapshot(path)
        for key in ("evaluations", "attempts", "reviews", "contacts"):
            self.assertEqual(before[key], after[key], f"{key} must survive migration")
        # Reviews/contacts still point at the migrated formal result.
        self.assertEqual(after["reviews"][0]["pipeline_evaluation_id"],
                         seed["evaluation"]["pipeline_evaluation_id"])
        self.assertEqual(after["contacts"][0]["pipeline_evaluation_id"],
                         seed["evaluation"]["pipeline_evaluation_id"])

        # Immutability guards still fire after the rebuild.
        connection = sqlite3.connect(path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE pipeline_evaluations SET total_score=1")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM pipeline_evaluations")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE pipeline_hr_reviews SET action='REJECT'")
            connection.rollback()
        finally:
            connection.close()
        with self.assertRaises(sqlite3.IntegrityError):
            # Guard: reviews must reference an existing evaluation.
            connection = sqlite3.connect(path)
            try:
                connection.execute("""INSERT INTO pipeline_hr_reviews
                    (org_id,job_id,review_event_id,pipeline_evaluation_id,action,reason,actor_id)
                    VALUES('org','job','ghost-review','ghost-eval','NOTE','x','owner')""")
            finally:
                connection.close()

        # The unique key now allows one fast + one deep result for the binding,
        # and still rejects a duplicate of the same mode.
        repo = RecruitmentRepository(Database(path))
        ctx = seed["ctx"]
        pipeline = PolicyScoringPipeline(
            repo, semantic_service=RecordingSemantic(repo),
            review_service=RecordingReview(repo))
        fast = repo.create_pipeline_task(ctx, "job", seed["jcid"], "fast_mode")
        repo.update_pipeline_task(ctx, "job", fast["pipeline_task_id"], status="RUNNING")
        fast_result = pipeline.run(ctx, "job", seed["jcid"], scoring_mode="fast_mode")
        repo.save_pipeline_result(ctx, "job", fast_result)
        rows = repo.list_pipeline_results(ctx, "job", seed["jcid"])
        self.assertEqual({item["scoring_mode"] for item in rows},
                         {"fast_mode", "deep_mode"})
        duplicate = repo.save_pipeline_result(ctx, "job", fast_result)
        self.assertEqual(duplicate["scoring_mode"], "fast_mode")
        self.assertEqual(len(repo.list_pipeline_results(ctx, "job", seed["jcid"])), 2)

    def test_failed_migration_rolls_back_without_partial_state(self):
        path = self._v9_database("broken.sqlite3")
        seed = self._seed(path)
        before = self._snapshot(path)
        broken_dir = _migration_dir(
            self.root, 10,
            extra=("011_broken.sql", "CREATE TABLE broken_partial(x INTEGER); SELECT * FROM missing_table;"))
        broken = Database(path, migration_directory=broken_dir)
        with self.assertRaises(MigrationError):
            broken.migrate()
        # Nothing from the failed attempt survives: still v9, original rows intact.
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        self.assertEqual(version, 9)
        self.assertNotIn("broken_partial", tables)
        self.assertEqual(self._snapshot(path), before)
        # Recovery: the correct migration set still upgrades the same file.
        recovered = Database(path).migrate()
        self.assertEqual(recovered["schema_version"], 10)
        self.assertEqual(self._snapshot(path), before)


if __name__ == "__main__":
    unittest.main()
