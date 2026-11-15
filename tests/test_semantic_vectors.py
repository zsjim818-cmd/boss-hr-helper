import unittest

import numpy as np

from engine.embedding_vectors import (
    cosine_matrix,
    deserialize_vector,
    embedding_profile_hash,
    encode_texts,
    encoded_text,
    encoded_text_hash,
    normalize_embedding_text,
    serialize_vector,
)


def profile(**changes):
    value = {
        "embedding_profile_id": "test-profile",
        "profile_version": "embedding-profile-v1",
        "model_name": "deterministic-test-only",
        "model_revision": "fixture-v1",
        "revision_state": "PINNED",
        "tokenizer_revision": "fixture-v1",
        "pooling": "MEAN",
        "normalizer_version": "nfkc-whitespace-v1",
        "input_prefix": "passage: ",
        "max_length": 32,
        "truncation": "LONGEST_FIRST",
        "l2_normalize": True,
        "dimension": 3,
        "dtype": "float32",
        "byte_order": "little",
    }
    value.update(changes)
    return value


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), dict(kwargs)))
        return np.asarray([[3.0, 4.0, 0.0] for _ in texts])


class SemanticVectorTests(unittest.TestCase):
    def test_text_preparation_hashes_exact_sent_to_model_content(self):
        p = profile()
        self.assertEqual(normalize_embedding_text("  Ｐｙｔｈｏｎ\r\n开发  "), "Python 开发")
        self.assertEqual(encoded_text(p, "  Ｐｙｔｈｏｎ\r\n开发  "), "passage: Python 开发")
        self.assertEqual(encoded_text_hash(p, "Ｐｙｔｈｏｎ  开发"),
                         encoded_text_hash(p, "Python\n开发"))
        self.assertNotEqual(encoded_text_hash(p, "Python开发"),
                            encoded_text_hash(p, "Python 开发"))

    def test_profile_hash_changes_with_real_encoding_parameter(self):
        base = embedding_profile_hash(profile())
        self.assertEqual(base, embedding_profile_hash(profile(
            embedding_profile_id="another-label", profile_version="label-v2")))
        self.assertNotEqual(base, embedding_profile_hash(profile(input_prefix="query: ")))
        self.assertNotEqual(base, embedding_profile_hash(profile(model_revision="fixture-v2")))
        self.assertNotEqual(base, embedding_profile_hash(profile(max_length=64)))

    def test_float32_round_trip_is_normalized_and_hash_checked(self):
        p = profile()
        blob, digest, norm = serialize_vector([3, 4, 0], p)
        self.assertEqual(len(blob), 12)
        self.assertAlmostEqual(norm, 1.0, places=6)
        decoded = deserialize_vector(blob, p, digest)
        np.testing.assert_allclose(decoded, np.asarray([0.6, 0.8, 0], dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            deserialize_vector(blob, p, "0" * 64)
        with self.assertRaisesRegex(ValueError, "length"):
            deserialize_vector(blob[:-1], p, None)

    def test_invalid_dimension_nan_inf_and_zero_are_rejected(self):
        p = profile()
        for value, message in [
            ([1, 2], "size 3"),
            ([np.nan, 0, 1], "NaN or infinity"),
            ([np.inf, 0, 1], "NaN or infinity"),
            ([0, 0, 0], "zero or invalid norm"),
        ]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                serialize_vector(value, p)

    def test_encode_contract_does_not_delegate_normalization_trust(self):
        embedder = FakeEmbedder()
        prepared, vectors = encode_texts(embedder, ["  A  ", "B"], profile())
        self.assertEqual(prepared, ["passage: A", "passage: B"])
        self.assertEqual(embedder.calls[0][1]["normalize_embeddings"], False)
        self.assertTrue(embedder.calls[0][1]["convert_to_numpy"])
        self.assertTrue(all(abs(float(np.linalg.norm(item)) - 1) < 1e-5 for item in vectors))

    def test_cosine_matrix_validates_shape_and_zero_vectors(self):
        result = cosine_matrix([np.asarray([1.0, 0.0])],
                               [np.asarray([1.0, 0.0]), np.asarray([0.0, 1.0])])
        np.testing.assert_allclose(result, np.asarray([[1.0, 0.0]]))
        with self.assertRaisesRegex(ValueError, "equal dimensions"):
            cosine_matrix([np.asarray([1.0, 0.0])], [np.asarray([1.0])])
        with self.assertRaisesRegex(ValueError, "zero vector"):
            cosine_matrix([np.asarray([0.0, 0.0])], [np.asarray([1.0, 0.0])])


if __name__ == "__main__":
    unittest.main()
