from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3

from schemas.semantic import EmbeddingProfile
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import NotFound
from tests.prescreen_support import PrescreenDatabaseCase


def test_profile(profile_id="fixture-embedding-v1", revision="model-r1", *, dimension=3,
                 revision_status="PINNED"):
    value = EmbeddingProfile(
        embedding_profile_id=profile_id,
        profile_version="embedding-profile-v1",
        model_identifier="deterministic-test-only",
        model_revision=revision if revision_status == "PINNED" else None,
        revision_status=revision_status,
        tokenizer_identifier="deterministic-tokenizer",
        tokenizer_revision=revision if revision_status == "PINNED" else None,
        pooling_strategy="MEAN",
        text_normalization_version="nfkc-whitespace-v1",
        input_prefix="",
        max_length=64,
        truncation_strategy="LONGEST_FIRST",
        l2_normalize=True,
        embedding_dimension=dimension,
        dtype="float32",
        byte_order="little",
    )
    payload = value.model_dump(mode="json", exclude={"embedding_profile_id", "profile_version"})
    return value, payload


class SemanticStorageTests(PrescreenDatabaseCase):
    def setUp(self):
        super().setUp()
        self.publish("java_backend", "job")
        self.acquire("job", "candidate")

    def create_profile(self, profile_id="fixture-embedding-v1", revision="model-r1", **changes):
        model, payload = test_profile(profile_id, revision, **changes)
        row = self.repo.create_embedding_profile(
            self.ctx, "job", payload, embedding_profile_id=model.embedding_profile_id)
        return model, payload, row

    def test_migration_is_v8_idempotent_and_survives_restart(self):
        self.assertEqual(self.db.migrate()["schema_version"], 8)
        self.assertTrue(self.db.migrate()["already_current"])
        self.assertTrue(self.db.check()["ok"])
        restarted = type(self.db)(self.path)
        check = restarted.check()
        self.assertTrue(check["ok"])
        self.assertEqual(check["schema_version"], 8)

    def test_profile_and_asset_are_persistent_and_content_addressed(self):
        model, payload, profile = self.create_profile()
        first = self.repo.store_or_reuse_embedding_asset(
            self.ctx, "job", model.embedding_profile_id, "相同编码文本", [1, 0, 0])
        second = self.repo.store_or_reuse_embedding_asset(
            self.ctx, "job", model.embedding_profile_id, "相同编码文本", [1, 0, 0])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["asset"]["asset_id"], second["asset"]["asset_id"])
        restarted = type(self.repo)(type(self.db)(self.path))
        loaded = restarted.get_embedding_asset(self.ctx, "job", first["asset"]["asset_id"])
        self.assertEqual(loaded["vector_hash"], first["asset"]["vector_hash"])
        self.assertEqual(restarted.get_embedding_profile(self.ctx, "job", model.embedding_profile_id)["profile_hash"],
                         profile["profile_hash"])

    def test_same_physical_key_with_conflicting_vector_is_rejected(self):
        model, _, _ = self.create_profile()
        self.repo.store_or_reuse_embedding_asset(
            self.ctx, "job", model.embedding_profile_id, "immutable text", [1, 0, 0])
        with self.assertRaisesRegex(ValueError, "conflicting vector content"):
            self.repo.store_or_reuse_embedding_asset(
                self.ctx, "job", model.embedding_profile_id, "immutable text", [0, 1, 0])

    def test_model_revision_change_uses_incompatible_profile(self):
        first, _, row1 = self.create_profile("profile-r1", "model-r1")
        second, _, row2 = self.create_profile("profile-r2", "model-r2")
        asset1 = self.repo.store_or_reuse_embedding_asset(
            self.ctx, "job", first.embedding_profile_id, "same text", [1, 0, 0])["asset"]
        asset2 = self.repo.store_or_reuse_embedding_asset(
            self.ctx, "job", second.embedding_profile_id, "same text", [1, 0, 0])["asset"]
        self.assertNotEqual(row1["profile_hash"], row2["profile_hash"])
        self.assertNotEqual(asset1["asset_id"], asset2["asset_id"])

    def test_unpinned_profile_is_explicit_and_does_not_claim_revision(self):
        model, payload = test_profile("profile-unpinned", None, revision_status="UNPINNED")
        row = self.repo.create_embedding_profile(
            self.ctx, "job", payload, embedding_profile_id=model.embedding_profile_id)
        self.assertEqual(row["revision_state"], "UNPINNED")
        self.assertIsNone(row["model_revision"])
        self.assertIsNone(row["tokenizer_revision"])

    def test_append_only_guards_reject_update_delete_and_replace(self):
        model, _, profile = self.create_profile()
        asset = self.repo.store_or_reuse_embedding_asset(
            self.ctx, "job", model.embedding_profile_id, "guarded", [1, 0, 0])["asset"]
        with closing(self.db.connect()) as connection:
            for sql, args in [
                ("UPDATE embedding_profiles SET model_name='changed' WHERE org_id=? AND embedding_profile_id=?",
                 (self.ctx.org_id, model.embedding_profile_id)),
                ("DELETE FROM embedding_assets WHERE org_id=? AND asset_id=?",
                 (self.ctx.org_id, asset["asset_id"])),
                ("INSERT OR REPLACE INTO embedding_assets SELECT * FROM embedding_assets WHERE org_id=? AND asset_id=?",
                 (self.ctx.org_id, asset["asset_id"])),
            ]:
                with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql, args)

    def test_concurrent_same_vector_creates_one_asset(self):
        model, _, _ = self.create_profile()

        def store(_):
            repo = type(self.repo)(type(self.db)(self.path))
            return repo.store_or_reuse_embedding_asset(
                self.ctx, "job", model.embedding_profile_id, "concurrent", [1, 0, 0])

        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(store, range(12)))
        self.assertEqual(len({row["asset"]["asset_id"] for row in rows}), 1)
        with closing(self.db.connect()) as connection:
            count = connection.execute("SELECT count(*) FROM embedding_assets WHERE org_id=? "
                                       "AND embedding_profile_id=?", (self.ctx.org_id,
                                       model.embedding_profile_id)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_different_organizations_never_share_assets(self):
        model, payload, _ = self.create_profile()
        first = self.repo.store_or_reuse_embedding_asset(
            self.ctx, "job", model.embedding_profile_id, "private candidate text", [1, 0, 0])["asset"]
        system = ProvisioningContext("second isolated tenant")
        self.repo.create_user(system, "semantic:owner2", user_id="owner2")
        self.repo.create_organization(system, "Other org", "owner2", org_id="org2")
        other = AccessContext("org2", "owner2")
        self.repo.create_job(other, "Other job", job_id="job2")
        self.repo.create_embedding_profile(other, "job2", payload,
                                           embedding_profile_id=model.embedding_profile_id)
        second = self.repo.store_or_reuse_embedding_asset(
            other, "job2", model.embedding_profile_id, "private candidate text", [1, 0, 0])["asset"]
        self.assertNotEqual(first["asset_id"], second["asset_id"])
        with self.assertRaises(NotFound):
            self.repo.get_embedding_asset(other, "job2", first["asset_id"])


if __name__ == "__main__":
    import unittest
    unittest.main()
