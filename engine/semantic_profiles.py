"""Load the versioned Stage 5 execution profiles without loading any model."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from schemas.semantic import EmbeddingProfile, ResumeChunkerProfile, SemanticExecutionProfile
from engine.embedding_vectors import canonical_json, embedding_profile_hash, sha256_text


DEFAULT_SEMANTIC_PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "semantic_profile.json"


def _hash_model(model) -> str:
    return sha256_text(canonical_json(model.model_dump(mode="json")))


@dataclass(frozen=True)
class LoadedSemanticProfiles:
    embedding: EmbeddingProfile
    chunker: ResumeChunkerProfile
    semantic: SemanticExecutionProfile
    embedding_hash: str
    chunker_hash: str
    semantic_hash: str

    @property
    def embedding_storage_payload(self) -> dict:
        payload = self.embedding.model_dump(mode="json")
        payload.pop("embedding_profile_id")
        payload.pop("profile_version")
        return payload

    @property
    def chunker_storage_payload(self) -> dict:
        return self.chunker.model_dump(mode="json")


def load_semantic_profiles(path=None) -> LoadedSemanticProfiles:
    target = Path(path) if path is not None else DEFAULT_SEMANTIC_PROFILE_PATH
    raw = json.loads(target.read_text(encoding="utf-8"))
    expected = {"embedding_profile", "chunker", "semantic_scoring"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("Semantic profile must contain exactly embedding_profile, chunker and semantic_scoring")
    # JSON arrays are the wire representation of immutable tuple fields.  Strict
    # JSON validation preserves the contract without weakening Python inputs.
    embedding = EmbeddingProfile.model_validate_json(json.dumps(raw["embedding_profile"], ensure_ascii=False))
    chunker = ResumeChunkerProfile.model_validate_json(json.dumps(raw["chunker"], ensure_ascii=False))
    semantic = SemanticExecutionProfile.model_validate_json(json.dumps(raw["semantic_scoring"], ensure_ascii=False))
    return LoadedSemanticProfiles(
        embedding=embedding,
        chunker=chunker,
        semantic=semantic,
        embedding_hash=embedding_profile_hash(embedding),
        chunker_hash=_hash_model(chunker),
        semantic_hash=_hash_model(semantic),
    )
