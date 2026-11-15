import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from engine.semantic_profiles import load_semantic_profiles
from schemas.semantic import EmbeddingProfile, SemanticExecutionProfile


class SemanticProfileTests(unittest.TestCase):
    def test_default_profile_is_strict_pinned_and_fully_hashed(self):
        loaded = load_semantic_profiles()
        self.assertEqual(loaded.embedding.revision_status, "PINNED")
        self.assertEqual(loaded.embedding.pooling_strategy, "CLS")
        self.assertEqual(loaded.embedding.embedding_dimension, 1024)
        self.assertEqual(len(loaded.embedding_hash), 64)
        self.assertEqual(len(loaded.chunker_hash), 64)
        self.assertEqual(len(loaded.semantic_hash), 64)
        self.assertNotIn("embedding_profile_id", loaded.embedding_storage_payload)
        self.assertNotIn("profile_version", loaded.embedding_storage_payload)

    def test_extra_or_nonfinite_profile_values_are_rejected(self):
        value = load_semantic_profiles().embedding.model_dump(mode="json")
        with self.assertRaises(ValidationError):
            EmbeddingProfile.model_validate({**value, "unknown": True})
        semantic = load_semantic_profiles().semantic.model_dump()
        semantic["similarity_min"] = float("nan")
        with self.assertRaises(ValidationError):
            SemanticExecutionProfile.model_validate(semantic)

    def test_unpinned_is_explicit_but_pinned_requires_both_revisions(self):
        value = load_semantic_profiles().embedding.model_dump(mode="json")
        value.update(revision_status="UNPINNED", model_revision=None, tokenizer_revision=None)
        self.assertEqual(EmbeddingProfile.model_validate(value).revision_status, "UNPINNED")
        value["revision_status"] = "PINNED"
        with self.assertRaisesRegex(ValidationError, "concrete model and tokenizer"):
            EmbeddingProfile.model_validate(value)

    def test_outer_profile_file_rejects_unknown_sections(self):
        source = json.loads(Path("config/semantic_profile.json").read_text(encoding="utf-8"))
        source["job_specific_route"] = "forbidden"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "invalid.json"
            target.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly"):
                load_semantic_profiles(target)


if __name__ == "__main__":
    unittest.main()
