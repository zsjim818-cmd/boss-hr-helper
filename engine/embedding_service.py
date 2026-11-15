"""Explicit, idempotent preparation of immutable policy embedding assets."""
from __future__ import annotations

import json
from time import perf_counter

from config.policy_validation import validate_publishable_policy
from schemas import ResumeData
from schemas.job_policy import JobScoringPolicy
from storage.repositories import NotFound
from storage.repositories.embeddings import unpack_float32
from storage.repositories.candidates import prepare_resume

from engine.embedding_vectors import encode_texts, encoded_text, encoded_text_hash
from engine.semantic_profiles import LoadedSemanticProfiles, load_semantic_profiles
from engine.resume_chunker import chunk_resume, chunks_for_storage, validate_chunk_sources


POLICY_EMBEDDING_BUILDER_VERSION = "policy-embedding-builder-v1"


class PolicyEmbeddingService:
    """Build Policy anchors outside DB locks, then atomically seal a READY manifest."""

    def __init__(self, repository, embedder, *, profiles: LoadedSemanticProfiles | None = None,
                 profile_path=None):
        self.repository = repository
        self.embedder = embedder
        self.profiles = profiles or load_semantic_profiles(profile_path)

    def ensure_profile(self, context, job_id):
        profile = self.profiles.embedding
        row = self.repository.create_embedding_profile(
            context, job_id, self.profiles.embedding_storage_payload,
            embedding_profile_id=profile.embedding_profile_id)
        if row["profile_hash"] != self.profiles.embedding_hash:
            raise ValueError("Persisted embedding profile hash differs from the effective encoding contract")
        return row

    def _load_policy(self, context, job_id, policy_id, policy_version):
        row = self.repository.get_policy_version(context, job_id, policy_id)
        if row["policy_version"] != policy_version:
            raise ValueError("Requested Policy version does not match the immutable Policy identifier")
        if row["policy_schema_version"] != "job_scoring_policy_v1":
            raise ValueError("Stage 5 only supports confirmed generic JobScoringPolicy versions")
        policy = JobScoringPolicy.model_validate(json.loads(row["policy_json"]))
        validate_publishable_policy(policy, json.loads(row["source_snapshot_json"]))
        return row, policy

    def prepare_policy(self, context, job_id, policy_id, policy_version):
        started = perf_counter()
        profile_row = self.ensure_profile(context, job_id)
        policy_row, policy = self._load_policy(context, job_id, policy_id, policy_version)
        profile = self.profiles.embedding
        try:
            existing = self.repository.get_policy_embedding_manifest(
                context, job_id, policy_id, policy_version, profile.embedding_profile_id)
        except NotFound:
            existing = None
        if existing is not None:
            # Reading all rows revalidates vector hashes, dimensions, norms and manifest membership.
            rows = self.repository.get_policy_embeddings(
                context, job_id, policy_id, policy_version, profile.embedding_profile_id)
            return {
                "status": "READY", "cache_hit": True, "policy_id": policy_id,
                "policy_version": policy_version, "policy_hash": policy_row["policy_hash"],
                "embedding_profile_id": profile.embedding_profile_id,
                "embedding_profile_hash": profile_row["profile_hash"],
                "anchor_count": len(rows), "encoded_count": 0,
                "reused_asset_count": len(rows), "manifest_hash": existing["manifest_hash"],
                "elapsed_seconds": round(perf_counter() - started, 6),
            }

        prepared_by_hash = {}
        anchors = []
        for anchor in policy.semantic_anchors:
            actual = encoded_text(profile, anchor.text)
            encoded_hash = encoded_text_hash(profile, anchor.text)
            prepared_by_hash.setdefault(encoded_hash, actual)
            anchors.append((anchor, encoded_hash, actual))
        cached = self.repository.find_embedding_assets(
            context, job_id, profile.embedding_profile_id, prepared_by_hash.values())
        missing_hashes = [key for key in sorted(prepared_by_hash) if key not in cached]
        encoded_vectors = {}
        inference_started = perf_counter()
        if missing_hashes:
            missing_texts = [prepared_by_hash[key] for key in missing_hashes]
            try:
                prepared, vectors = encode_texts(
                    self.embedder, missing_texts, profile, inputs_prepared=True)
            except Exception as exc:
                inference_seconds = perf_counter() - inference_started
                try:
                    self.repository.append_embedding_build_attempt(
                        context, job_id, profile.embedding_profile_id, target_kind="POLICY",
                        status="FAILED", policy_id=policy_id, policy_version=policy_version,
                        error_type=type(exc).__name__, error_message=str(exc)[:2000],
                        metrics={"stage": "model_encode", "encoded_count": len(missing_hashes),
                                 "inference_seconds": round(inference_seconds, 6)})
                except Exception:
                    pass
                raise
            if prepared != missing_texts:
                raise ValueError("Embedding preparation changed an already prepared model input")
            encoded_vectors.update(zip(missing_hashes, vectors))
        inference_seconds = perf_counter() - inference_started
        for key, row in cached.items():
            encoded_vectors[key] = unpack_float32(row["vector_blob"], row["dimension"])

        supplied = [{
            "anchor_id": anchor.anchor_id,
            "requirement_ids": list(anchor.requirement_ids),
            "polarity": anchor.polarity,
            "text": anchor.text,
            "encoded_text": actual,
            "vector": encoded_vectors[encoded_hash],
        } for anchor, encoded_hash, actual in anchors]
        try:
            manifest = self.repository.persist_policy_embeddings(
                context, job_id, policy_id, policy_version, profile.embedding_profile_id,
                supplied, builder_version=POLICY_EMBEDDING_BUILDER_VERSION)
            metrics = {
                "anchor_count": len(anchors), "unique_text_count": len(prepared_by_hash),
                "encoded_count": len(missing_hashes), "reused_asset_count": len(cached),
                "inference_seconds": round(inference_seconds, 6),
            }
            self.repository.append_embedding_build_attempt(
                context, job_id, profile.embedding_profile_id, target_kind="POLICY",
                status="SUCCEEDED", policy_id=policy_id, policy_version=policy_version,
                metrics=metrics)
            return {
                "status": "READY", "cache_hit": False, "policy_id": policy_id,
                "policy_version": policy_version, "policy_hash": policy_row["policy_hash"],
                "embedding_profile_id": profile.embedding_profile_id,
                "embedding_profile_hash": profile_row["profile_hash"],
                **metrics, "manifest_hash": manifest["manifest_hash"],
                "elapsed_seconds": round(perf_counter() - started, 6),
            }
        except Exception as exc:
            try:
                self.repository.append_embedding_build_attempt(
                    context, job_id, profile.embedding_profile_id, target_kind="POLICY",
                    status="FAILED", policy_id=policy_id, policy_version=policy_version,
                    error_type=type(exc).__name__, error_message=str(exc)[:2000],
                    metrics={"encoded_count": len(missing_hashes),
                             "inference_seconds": round(inference_seconds, 6)})
            except Exception:
                pass
            raise


class ResumeEmbeddingService(PolicyEmbeddingService):
    """Prepare one candidate's sealed resume chunks and reusable vectors."""

    def prepare_candidate(self, context, job_id, job_candidate_id):
        started = perf_counter()
        profile_row = self.ensure_profile(context, job_id)
        candidate = self.repository.get_job_candidate(context, job_id, job_candidate_id)
        sealed = self.repository.get_sealed_input(context, job_id, job_candidate_id)
        snapshot = self.repository.get_resume_snapshot(
            context, job_id, job_candidate_id, sealed["resume_id"])
        if (sealed["candidate_id"], sealed["policy_id"], sealed["policy_version"]) != (
                candidate["candidate_id"], candidate["policy_id"], candidate["policy_version"]):
            raise ValueError("Sealed resume identity no longer matches the immutable job candidate binding")
        if snapshot["completeness"] != "COMPLETE" or not snapshot["is_scorable"]:
            raise ValueError("Semantic vectors require the complete sealed scoring input")
        raw = json.loads(snapshot["raw_resume_json"])
        ResumeData.model_validate(raw)
        if prepare_resume(raw)["content_hash"] != snapshot["content_hash"]:
            raise ValueError("Sealed resume content hash no longer matches its parsed content")

        chunker = self.profiles.chunker
        chunks = chunk_resume(
            raw, org_id=context.org_id, candidate_id=candidate["candidate_id"],
            resume_id=snapshot["resume_id"], resume_content_hash=snapshot["content_hash"],
            profile=chunker)
        if not chunks:
            raise ValueError("The complete resume contains no traceable semantic text chunks")
        validate_chunk_sources(raw, chunks)
        chunk_manifest = self.repository.persist_resume_chunks(
            context, job_id, job_candidate_id, snapshot["resume_id"], chunker.chunker_version,
            self.profiles.chunker_storage_payload, chunks_for_storage(chunks))

        embedding = self.profiles.embedding
        try:
            existing = self.repository.get_resume_embedding_manifest(
                context, job_id, job_candidate_id, snapshot["resume_id"],
                chunker.chunker_version, embedding.embedding_profile_id)
        except NotFound:
            existing = None
        if existing is not None:
            rows = self.repository.get_candidate_embeddings(
                context, job_id, job_candidate_id, snapshot["resume_id"],
                chunker.chunker_version, embedding.embedding_profile_id)
            return {
                "status": "READY", "cache_hit": True,
                "job_candidate_id": job_candidate_id, "candidate_id": candidate["candidate_id"],
                "resume_id": snapshot["resume_id"], "resume_content_hash": snapshot["content_hash"],
                "embedding_profile_id": embedding.embedding_profile_id,
                "embedding_profile_hash": profile_row["profile_hash"],
                "chunker_version": chunker.chunker_version,
                "chunker_profile_hash": self.profiles.chunker_hash,
                "chunk_count": len(rows), "encoded_count": 0,
                "reused_asset_count": len(rows),
                "chunk_manifest_hash": chunk_manifest["manifest_hash"],
                "embedding_manifest_hash": existing["manifest_hash"],
                "elapsed_seconds": round(perf_counter() - started, 6),
            }

        prepared_by_hash = {}
        chunk_inputs = []
        for chunk in chunks:
            actual = encoded_text(embedding, chunk.text)
            encoded_hash = encoded_text_hash(embedding, chunk.text)
            prepared_by_hash.setdefault(encoded_hash, actual)
            chunk_inputs.append((chunk, encoded_hash, actual))
        cached = self.repository.find_embedding_assets(
            context, job_id, embedding.embedding_profile_id, prepared_by_hash.values())
        missing_hashes = [key for key in sorted(prepared_by_hash) if key not in cached]
        vectors = {}
        inference_started = perf_counter()
        try:
            if missing_hashes:
                missing_texts = [prepared_by_hash[key] for key in missing_hashes]
                prepared, encoded = encode_texts(
                    self.embedder, missing_texts, embedding, inputs_prepared=True)
                if prepared != missing_texts:
                    raise ValueError("Embedding preparation changed an already prepared resume input")
                vectors.update(zip(missing_hashes, encoded))
            for key, row in cached.items():
                vectors[key] = unpack_float32(row["vector_blob"], row["dimension"])
            inference_seconds = perf_counter() - inference_started
            supplied = [{"chunk_id": chunk.chunk_id, "encoded_text": actual,
                         "vector": vectors[encoded_hash]}
                        for chunk, encoded_hash, actual in chunk_inputs]
            manifest = self.repository.persist_candidate_embeddings(
                context, job_id, job_candidate_id, snapshot["resume_id"],
                chunker.chunker_version, embedding.embedding_profile_id, supplied)
            metrics = {
                "chunk_count": len(chunks), "unique_text_count": len(prepared_by_hash),
                "encoded_count": len(missing_hashes), "reused_asset_count": len(cached),
                "inference_seconds": round(inference_seconds, 6),
            }
            self.repository.append_embedding_build_attempt(
                context, job_id, embedding.embedding_profile_id, target_kind="RESUME",
                status="SUCCEEDED", candidate_id=candidate["candidate_id"],
                resume_id=snapshot["resume_id"], chunker_version=chunker.chunker_version,
                metrics=metrics)
            return {
                "status": "READY", "cache_hit": False,
                "job_candidate_id": job_candidate_id, "candidate_id": candidate["candidate_id"],
                "resume_id": snapshot["resume_id"], "resume_content_hash": snapshot["content_hash"],
                "embedding_profile_id": embedding.embedding_profile_id,
                "embedding_profile_hash": profile_row["profile_hash"],
                "chunker_version": chunker.chunker_version,
                "chunker_profile_hash": self.profiles.chunker_hash,
                **metrics, "chunk_manifest_hash": chunk_manifest["manifest_hash"],
                "embedding_manifest_hash": manifest["manifest_hash"],
                "elapsed_seconds": round(perf_counter() - started, 6),
            }
        except Exception as exc:
            inference_seconds = perf_counter() - inference_started
            try:
                self.repository.append_embedding_build_attempt(
                    context, job_id, embedding.embedding_profile_id, target_kind="RESUME",
                    status="FAILED", candidate_id=candidate["candidate_id"],
                    resume_id=snapshot["resume_id"], chunker_version=chunker.chunker_version,
                    error_type=type(exc).__name__, error_message=str(exc)[:2000],
                    metrics={"stage": "vector_prepare", "encoded_count": len(missing_hashes),
                             "inference_seconds": round(inference_seconds, 6)})
            except Exception:
                pass
            raise
