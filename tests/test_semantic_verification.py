"""Regression tests for runtime EmbeddingProfile ↔ loaded-model consistency.

These tests prove the immutable profile hash is not merely a written claim: the
actually loaded sentence-transformers model must expose the pooling mode,
dimension and max length that the profile declares.
"""
import unittest
from pathlib import Path

from config.settings import load_runtime_settings
from engine.embedding_vectors import embedding_profile_hash
from engine.semantic_profiles import load_semantic_profiles
from engine.semantic_verification import (
    assert_embedding_model_matches,
    detect_embedding_dimension,
    detect_max_seq_length,
    detect_pooling_strategy,
    verify_embedding_model,
)


class _ModernPooling:
    """Mirrors sentence-transformers releases exposing pooling_mode as a string."""

    def __init__(self, mode):
        self.pooling_mode = mode


class _LegacyPooling:
    """Mirrors older releases exposing boolean pooling flags."""

    def __init__(self, cls=False, mean=False, lasttoken=False):
        self.pooling_mode_cls_token = cls
        self.pooling_mode_mean_tokens = mean
        self.pooling_mode_lasttoken = lasttoken


class _FakeModel:
    def __init__(self, modules, dimension=None, max_seq_length=None):
        self._modules = modules
        self._dimension = dimension
        self.max_seq_length = max_seq_length

    def __iter__(self):
        return iter(self._modules)

    def get_sentence_embedding_dimension(self):
        return self._dimension


class PoolingDetectionTests(unittest.TestCase):
    def test_string_and_boolean_pooling_forms_are_both_understood(self):
        self.assertEqual(detect_pooling_strategy(_FakeModel([_ModernPooling("cls")])), "CLS")
        self.assertEqual(detect_pooling_strategy(_FakeModel([_ModernPooling("mean")])), "MEAN")
        self.assertEqual(detect_pooling_strategy(_FakeModel([_ModernPooling("lasttoken")])), "LAST_TOKEN")
        self.assertEqual(detect_pooling_strategy(_FakeModel([_LegacyPooling(cls=True)])), "CLS")
        self.assertEqual(detect_pooling_strategy(_FakeModel([_LegacyPooling(mean=True)])), "MEAN")

    def test_ambiguous_or_absent_pooling_is_rejected_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "multiple pooling modes"):
            detect_pooling_strategy(_FakeModel([_LegacyPooling(cls=True, mean=True)]))
        with self.assertRaisesRegex(ValueError, "no recognisable pooling module"):
            detect_pooling_strategy(_FakeModel([object()]))
        with self.assertRaisesRegex(ValueError, "no recognisable pooling module"):
            detect_pooling_strategy(_FakeModel([]))

    def test_dimension_falls_back_to_module_attribute(self):
        class _Dimensioned:
            embedding_dimension = 768

        self.assertEqual(detect_embedding_dimension(_FakeModel([_Dimensioned()])), 768)
        self.assertEqual(detect_embedding_dimension(_FakeModel([], dimension=384)), 384)
        with self.assertRaisesRegex(ValueError, "no verifiable embedding dimension"):
            detect_embedding_dimension(_FakeModel([]))

    def test_max_length_is_only_read_when_reported(self):
        self.assertEqual(detect_max_seq_length(_FakeModel([], max_seq_length=512)), 512)
        self.assertIsNone(detect_max_seq_length(_FakeModel([])))


class ProfileModelConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_semantic_profiles().embedding

    def test_matching_model_is_accepted(self):
        model = _FakeModel([_ModernPooling("cls")], dimension=1024, max_seq_length=512)
        report = verify_embedding_model(model, self.profile)
        self.assertTrue(report["ok"])
        self.assertEqual(report["checks"]["pooling"],
                         {"declared": "CLS", "actual": "CLS", "ok": True})
        self.assertIn("max_length", report["checks"])

    def test_pooling_mismatch_fails_the_check_and_raises(self):
        model = _FakeModel([_ModernPooling("mean")], dimension=1024, max_seq_length=512)
        report = verify_embedding_model(model, self.profile)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["pooling"]["ok"])
        with self.assertRaisesRegex(ValueError, "does not match the declared profile"):
            assert_embedding_model_matches(model, self.profile)

    def test_dimension_mismatch_fails_the_check(self):
        model = _FakeModel([_ModernPooling("cls")], dimension=768, max_seq_length=512)
        report = verify_embedding_model(model, self.profile)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["dimension"]["ok"])

    def test_absent_max_length_does_not_fabricate_a_mismatch(self):
        model = _FakeModel([_ModernPooling("cls")], dimension=1024, max_seq_length=None)
        report = verify_embedding_model(model, self.profile)
        self.assertTrue(report["ok"])
        self.assertNotIn("max_length", report["checks"])


class ProfileHashSensitivityTests(unittest.TestCase):
    def test_effective_encoding_parameters_change_the_hash_but_labels_do_not(self):
        payload = load_semantic_profiles().embedding.model_dump(mode="json")
        base = embedding_profile_hash(payload)
        labels_only = dict(payload, embedding_profile_id="relabelled", profile_version="v9")
        self.assertEqual(base, embedding_profile_hash(labels_only))
        for change in ({"pooling_strategy": "MEAN"},
                       {"embedding_dimension": 768},
                       {"max_length": 256},
                       {"l2_normalize": False}):
            self.assertNotEqual(base, embedding_profile_hash(dict(payload, **change)),
                                f"hash ignored effective parameter change {change}")


def _load_local_model():
    path = Path(load_runtime_settings().embedding_model_path)
    if not path.exists():
        raise unittest.SkipTest(f"local embedding model path is absent: {path}")
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # pragma: no cover - environment dependent
        raise unittest.SkipTest(f"sentence-transformers unavailable: {exc}")
    try:
        return SentenceTransformer(str(path))
    except Exception as exc:  # pragma: no cover - environment dependent
        raise unittest.SkipTest(f"could not load local embedding model: {exc}")


class RealModelConsistencyTests(unittest.TestCase):
    """Cross-check the declared profile against the real pinned BGE snapshot."""

    @classmethod
    def setUpClass(cls):
        cls.model = _load_local_model()
        cls.profile = load_semantic_profiles().embedding

    def test_runtime_pooling_matches_declared_profile(self):
        self.assertEqual(detect_pooling_strategy(self.model), self.profile.pooling_strategy)

    def test_runtime_dimension_and_length_match_declared_profile(self):
        self.assertEqual(detect_embedding_dimension(self.model), self.profile.embedding_dimension)
        report = assert_embedding_model_matches(self.model, self.profile)
        self.assertTrue(report["ok"])

    def test_real_vector_is_finite_normalized_and_correctly_sized(self):
        import numpy as np

        matrix = self.model.encode(["Python 后端开发工程师"],
                                   normalize_embeddings=False,
                                   convert_to_numpy=True,
                                   show_progress_bar=False)
        array = np.asarray(matrix)
        self.assertEqual(array.ndim, 2)
        self.assertEqual(array.shape[0], 1)
        self.assertEqual(array.shape[1], self.profile.embedding_dimension)
        self.assertTrue(np.isfinite(array).all())
        norm = float(np.linalg.norm(array[0].astype(np.float64)))
        self.assertAlmostEqual(norm, 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
