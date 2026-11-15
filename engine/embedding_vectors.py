"""Deterministic text preparation and checked little-endian float32 vectors.

The SQLite layer can check BLOB length, but it cannot prove that float payloads
are finite, non-zero, normalized, or that their digest is correct.  Every write
and read therefore passes through this module.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Iterable, Sequence

import numpy as np


VECTOR_VALIDATOR_VERSION = "float32-vector-validator-v1"


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Text hashing requires a string")
    return sha256_bytes(value.encode("utf-8"))


def profile_payload(profile) -> dict:
    if hasattr(profile, "model_dump"):
        payload = profile.model_dump(mode="json")
    elif isinstance(profile, dict):
        payload = dict(profile)
    else:
        raise TypeError("Embedding profile must be a Pydantic model or object mapping")
    return payload


def embedding_profile_hash(profile) -> str:
    payload = profile_payload(profile)
    # Human-readable identity/version labels do not change the encoded vector.
    # The physical cache fingerprint covers only effective encoding parameters.
    payload.pop("embedding_profile_id", None)
    payload.pop("profile_version", None)
    return sha256_text(canonical_json(payload))


def normalize_embedding_text(text: str, version: str = "nfkc-whitespace-v1") -> str:
    if not isinstance(text, str):
        raise TypeError("Embedding text must be a string")
    if version != "nfkc-whitespace-v1":
        raise ValueError(f"Unsupported embedding normalizer: {version}")
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        raise ValueError("Embedding text is empty after normalization")
    return normalized


def encoded_text(profile, text: str) -> str:
    payload = profile_payload(profile)
    normalizer = payload.get("text_normalization_version", payload.get("normalizer_version"))
    normalized = normalize_embedding_text(text, normalizer)
    prefix = payload.get("input_prefix", "")
    if not isinstance(prefix, str):
        raise TypeError("Embedding input prefix must be a string")
    result = prefix + normalized
    if not result:
        raise ValueError("Encoded text is empty")
    return result


def encoded_text_hash(profile, text: str) -> str:
    return sha256_text(encoded_text(profile, text))


def _profile_value(profile, name):
    payload = profile_payload(profile)
    if name not in payload:
        raise ValueError(f"Embedding profile is missing {name}")
    return payload[name]


def prepare_vector(vector, profile) -> np.ndarray:
    """Validate one vector and return canonical little-endian float32 bytes view."""
    payload = profile_payload(profile)
    dimension = payload.get("embedding_dimension", payload.get("dimension"))
    dtype = _profile_value(profile, "dtype")
    byte_order = _profile_value(profile, "byte_order")
    if dtype != "float32" or byte_order != "little":
        raise ValueError("Only little-endian float32 assets are supported")
    array64 = np.asarray(vector, dtype=np.float64)
    if array64.ndim != 1 or array64.shape[0] != dimension:
        raise ValueError(f"Embedding vector must be one-dimensional with size {dimension}")
    if not np.isfinite(array64).all():
        raise ValueError("Embedding vector contains NaN or infinity")
    norm = float(np.linalg.norm(array64))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Embedding vector has zero or invalid norm")
    if bool(_profile_value(profile, "l2_normalize")):
        array64 = array64 / norm
    array = np.ascontiguousarray(array64, dtype=np.dtype("<f4"))
    if not np.isfinite(array).all():
        raise ValueError("Embedding vector became non-finite during float32 conversion")
    stored_norm = float(np.linalg.norm(array.astype(np.float64)))
    if stored_norm <= 1e-12:
        raise ValueError("Stored embedding vector has zero norm")
    if bool(_profile_value(profile, "l2_normalize")) and abs(stored_norm - 1.0) > 1e-5:
        raise ValueError("Stored embedding vector is not L2 normalized")
    return array


def serialize_vector(vector, profile) -> tuple[bytes, str, float]:
    array = prepare_vector(vector, profile)
    blob = array.tobytes(order="C")
    return blob, sha256_bytes(blob), float(np.linalg.norm(array.astype(np.float64)))


def deserialize_vector(blob: bytes, profile, expected_hash: str | None = None) -> np.ndarray:
    if not isinstance(blob, bytes):
        raise TypeError("Vector payload must be bytes")
    payload = profile_payload(profile)
    dimension = payload.get("embedding_dimension", payload.get("dimension"))
    if len(blob) != dimension * 4:
        raise ValueError("Vector BLOB length does not match profile dimension")
    actual_hash = sha256_bytes(blob)
    if expected_hash is not None and actual_hash != expected_hash:
        raise ValueError("Vector content hash mismatch")
    # Copy detaches the returned array from an untrusted database buffer.
    array = np.frombuffer(blob, dtype=np.dtype("<f4")).copy()
    checked = prepare_vector(array, profile)
    # Normalized assets must already be canonical.  Never silently repair DB bytes.
    if checked.tobytes(order="C") != blob:
        raise ValueError("Stored vector bytes are not canonical for the profile")
    return checked


def encode_texts(embedder, texts: Sequence[str], profile, *, inputs_prepared=False) -> tuple[list[str], list[np.ndarray]]:
    """Encode exact prepared strings, then independently validate every vector."""
    if inputs_prepared:
        prepared = list(texts)
        if any(not isinstance(item, str) or not item for item in prepared):
            raise ValueError("Prepared embedding inputs must be non-empty strings")
    else:
        prepared = [encoded_text(profile, item) for item in texts]
    if not prepared:
        return [], []
    matrix = embedder.encode(prepared, normalize_embeddings=False,
                             convert_to_numpy=True, show_progress_bar=False)
    array = np.asarray(matrix)
    if array.ndim != 2 or array.shape[0] != len(prepared):
        raise ValueError("Embedding model returned an invalid batch shape")
    return prepared, [prepare_vector(row, profile) for row in array]


def cosine_matrix(candidate_vectors: Iterable[np.ndarray], anchor_vectors: Iterable[np.ndarray]) -> np.ndarray:
    left = np.asarray(list(candidate_vectors), dtype=np.float64)
    right = np.asarray(list(anchor_vectors), dtype=np.float64)
    if left.ndim != 2 or right.ndim != 2 or left.shape[1] != right.shape[1]:
        raise ValueError("Cosine matrix requires two nonempty matrices with equal dimensions")
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("Cosine matrix input is non-finite")
    left_norm = np.linalg.norm(left, axis=1)
    right_norm = np.linalg.norm(right, axis=1)
    if np.any(left_norm <= 1e-12) or np.any(right_norm <= 1e-12):
        raise ValueError("Cosine matrix input contains a zero vector")
    result = (left / left_norm[:, None]) @ (right / right_norm[:, None]).T
    if not np.isfinite(result).all():
        raise ValueError("Cosine matrix is non-finite")
    return result
