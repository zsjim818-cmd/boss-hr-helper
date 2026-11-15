from copy import deepcopy
import hashlib
from contextlib import closing
import unittest

import numpy as np

from engine.embedding_service import PolicyEmbeddingService
from engine.embedding_vectors import embedding_profile_hash
from engine.semantic_profiles import LoadedSemanticProfiles, load_semantic_profiles
from schemas.semantic import EmbeddingProfile
from storage.repositories import NotFound
from tests.policy_fixtures import offline_policy
from tests.prescreen_support import PrescreenDatabaseCase


class RecordingEmbedder:
    """Deterministic test double; these vectors are not BGE quality evidence."""
    def __init__(self, *, bad_dimension=False):
        self.calls = []
        self.bad_dimension = bad_dimension

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), dict(kwargs)))
        dimension = 2 if self.bad_dimension else 3
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            rows.append([float(digest[index] + 1) for index in range(dimension)])
        return np.asarray(rows)

    @property
    def encoded_count(self):
        return sum(len(texts) for texts, _ in self.calls)


def profiles(profile_id="mock-embedding-v1", revision="fixture-r1"):
    base = load_semantic_profiles()
    embedding = EmbeddingProfile(
        embedding_profile_id=profile_id,
        profile_version="embedding-profile-v1",
        model_identifier="DETERMINISTIC_MOCK_NOT_BGE",
        model_revision=revision,
        revision_status="PINNED",
        tokenizer_identifier="mock-tokenizer",
        tokenizer_revision=revision,
        pooling_strategy="MEAN",
        text_normalization_version="nfkc-whitespace-v1",
        input_prefix="",
        max_length=64,
        truncation_strategy="LONGEST_FIRST",
        l2_normalize=True,
        embedding_dimension=3,
        dtype="float32",
        byte_order="little",
    )
    return LoadedSemanticProfiles(
        embedding=embedding,
        chunker=base.chunker,
        semantic=base.semantic,
        embedding_hash=embedding_profile_hash(embedding),
        chunker_hash=base.chunker_hash,
        semantic_hash=base.semantic_hash,
    )


class PolicyEmbeddingTests(PrescreenDatabaseCase):
    def setUp(self):
        super().setUp()
        self.v1 = self.publish("java_backend", "job")
        self.embedder = RecordingEmbedder()
        self.service = PolicyEmbeddingService(self.repo, self.embedder, profiles=profiles())

    def test_all_confirmed_policy_anchors_are_persisted_and_restart_validates(self):
        result = self.service.prepare_policy(
            self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"])
        expected = len(offline_policy("java_backend")["semantic_anchors"])
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["anchor_count"], expected)
        self.assertEqual(result["encoded_count"], expected)
        self.assertEqual(self.embedder.encoded_count, expected)
        restarted = type(self.repo)(type(self.db)(self.path))
        rows = restarted.get_policy_embeddings(
            self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"],
            profiles().embedding.embedding_profile_id)
        self.assertEqual(len(rows), expected)
        self.assertTrue(all(len(row["vector_blob"]) == 12 for row in rows))

    def test_second_prepare_is_manifest_cache_hit_without_model_call(self):
        self.service.prepare_policy(self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"])
        before = self.embedder.encoded_count
        again = self.service.prepare_policy(
            self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"])
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["encoded_count"], 0)
        self.assertEqual(self.embedder.encoded_count, before)

    def test_weight_only_change_reuses_vectors_and_text_change_encodes_one(self):
        first = self.service.prepare_policy(
            self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"])
        weighted = deepcopy(offline_policy("java_backend"))
        weighted["scoring_dimensions"][0]["weight"] = 0.25
        weighted["scoring_dimensions"][1]["weight"] = 0.35
        v2 = self.publish("java_backend", "job", policy=weighted)
        second = self.service.prepare_policy(self.ctx, "job", v2["policy_id"], v2["policy_version"])
        self.assertEqual(second["encoded_count"], 0)
        self.assertEqual(second["reused_asset_count"], first["anchor_count"])

        changed = deepcopy(weighted)
        changed["semantic_anchors"][0]["text"] += "并记录可复核的边界处理。"
        v3 = self.publish("java_backend", "job", policy=changed)
        third = self.service.prepare_policy(self.ctx, "job", v3["policy_id"], v3["policy_version"])
        self.assertEqual(third["encoded_count"], 1)
        self.assertEqual(third["reused_asset_count"], first["anchor_count"] - 1)

    def test_model_revision_change_does_not_reuse_incompatible_assets(self):
        self.service.prepare_policy(self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"])
        other_embedder = RecordingEmbedder()
        other = PolicyEmbeddingService(
            self.repo, other_embedder, profiles=profiles("mock-embedding-v2", "fixture-r2"))
        result = other.prepare_policy(self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"])
        self.assertEqual(result["encoded_count"], result["anchor_count"])
        self.assertEqual(result["reused_asset_count"], 0)

    def test_model_failure_records_failed_attempt_without_ready_manifest(self):
        broken_profiles = profiles("mock-broken-dimension", "broken-r1")
        broken = PolicyEmbeddingService(
            self.repo, RecordingEmbedder(bad_dimension=True), profiles=broken_profiles)
        with self.assertRaisesRegex(ValueError, "batch shape|size 3"):
            broken.prepare_policy(self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"])
        with self.assertRaises(NotFound):
            self.repo.get_policy_embedding_manifest(
                self.ctx, "job", self.v1["policy_id"], self.v1["policy_version"],
                broken_profiles.embedding.embedding_profile_id)
        with closing(self.db.connect()) as connection:
            attempts = connection.execute("""SELECT status,error_type FROM embedding_build_attempts
                WHERE org_id=? AND job_id=? AND policy_id=? AND embedding_profile_id=?""",
                (self.ctx.org_id, "job", self.v1["policy_id"],
                 broken_profiles.embedding.embedding_profile_id)).fetchall()
        self.assertEqual([(row["status"], row["error_type"]) for row in attempts],
                         [("FAILED", "ValueError")])


if __name__ == "__main__":
    unittest.main()
