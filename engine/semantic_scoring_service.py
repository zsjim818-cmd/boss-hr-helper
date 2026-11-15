"""Repository-bound Tier 2 semantic scoring assembly.

This service performs no model inference and writes nothing: it re-reads the
candidate-bound Policy, the sealed resume snapshot and the already validated
embedding assets, runs the deterministic Stage 5 ranker, and returns a strict
``SemanticEvaluation`` that is explicitly TIER2-only.  It never substitutes
``jobs.current_policy_id`` for the candidate's first-bound policy version and
never overrides a Tier 0 decision.
"""
from __future__ import annotations

import json

from engine.policy_interpreter import PolicyInterpreter
from engine.policy_semantic_ranker import evaluate_policy_semantics
from engine.resume_chunker import resolve_chunk_source
from engine.semantic_profiles import LoadedSemanticProfiles, load_semantic_profiles
from schemas.semantic import SemanticEvaluation
from storage.repositories import NotFound


SEMANTIC_SERVICE_VERSION = "semantic-scoring-service-v1"


class SemanticScoringService:
    def __init__(self, repository, *, profiles: LoadedSemanticProfiles | None = None,
                 profile_path=None, interpreter: PolicyInterpreter | None = None):
        if interpreter is not None and (profiles is not None or profile_path is not None):
            raise ValueError("An injected interpreter owns its semantic execution profiles")
        if interpreter is not None and interpreter.repository is not repository:
            raise ValueError("An injected interpreter must use this service's repository")
        self.repository = repository
        self.profiles = profiles or load_semantic_profiles(profile_path)
        self.interpreter = interpreter or PolicyInterpreter(repository)

    def evaluate(self, context, job_id, job_candidate_id, *, preliminary=None) -> SemanticEvaluation:
        # Authorization failure is an access error, never a fabricated score.
        with self.repository.read(context, job_id, "evaluate"):
            pass
        candidate = self.repository.get_job_candidate(context, job_id, job_candidate_id)
        bound = self.interpreter.load_for_candidate(context, job_id, job_candidate_id)
        plan = bound.plan
        if ((plan.org_id, plan.job_id, bound.job_candidate_id, bound.candidate_id,
             plan.policy_id, plan.policy_version) !=
                (context.org_id, job_id, job_candidate_id, candidate["candidate_id"],
                 candidate["policy_id"], candidate["policy_version"])):
            raise ValueError("Interpreter binding does not match the authorized candidate association")

        # Semantic scoring requires the immutable first-complete scoring input.
        sealed = self.repository.get_sealed_input(context, job_id, job_candidate_id)
        resume_id = sealed["resume_id"]
        snapshot = self.repository.get_resume_snapshot(context, job_id, job_candidate_id, resume_id)
        if (snapshot["org_id"], snapshot["candidate_id"], snapshot["resume_id"]) != (
                context.org_id, candidate["candidate_id"], resume_id):
            raise ValueError("Resume snapshot identity does not match the authorized association")

        identity = dict(
            org_id=context.org_id, job_id=job_id, job_candidate_id=job_candidate_id,
            candidate_id=candidate["candidate_id"], policy_id=plan.policy_id,
            policy_version=plan.policy_version, policy_hash=plan.policy_hash,
            resume_id=resume_id, resume_content_hash=snapshot["content_hash"],
            input_kind="SEALED", data_completeness=snapshot["completeness"],
            input_has_issues=bool(json.loads(snapshot["data_issues_json"])
                                  or json.loads(snapshot["collection_errors_json"])),
            embedding_profile_id=self.profiles.embedding.embedding_profile_id,
            embedding_profile_hash=self.profiles.embedding_hash,
            chunker_version=self.profiles.chunker.chunker_version,
            chunker_profile_hash=self.profiles.chunker_hash,
            semantic_profile_version=self.profiles.semantic.semantic_profile_version,
            semantic_profile_hash=self.profiles.semantic_hash,
            upstream_preliminary_status=(preliminary.scoring_status if preliminary is not None else None),
        )

        if snapshot["completeness"] != "COMPLETE" or identity["input_has_issues"]:
            return SemanticEvaluation(
                **identity, scoring_status="DATA_INCOMPLETE", semantic_coverage=0.0,
                review_reasons=["DATA_INCOMPLETE:封存评分输入不完整或含数据问题，不参与语义深评"],
                debug_trace=[f"service={SEMANTIC_SERVICE_VERSION}"])

        manifests = dict(identity)
        try:
            profile_row = self.repository.get_embedding_profile(
                context, job_id, self.profiles.embedding.embedding_profile_id)
            if profile_row["profile_hash"] != self.profiles.embedding_hash:
                raise ValueError("Persisted embedding profile hash differs from the effective encoding contract")
            anchor_manifest = self.repository.get_policy_embedding_manifest(
                context, job_id, plan.policy_id, plan.policy_version,
                self.profiles.embedding.embedding_profile_id)
            chunk_manifest = self.repository.get_resume_chunk_manifest(
                context, job_id, job_candidate_id, resume_id, self.profiles.chunker.chunker_version)
            embedding_manifest = self.repository.get_resume_embedding_manifest(
                context, job_id, job_candidate_id, resume_id, self.profiles.chunker.chunker_version,
                self.profiles.embedding.embedding_profile_id)
            anchor_rows = self.repository.get_policy_embeddings(
                context, job_id, plan.policy_id, plan.policy_version,
                self.profiles.embedding.embedding_profile_id)
            candidate_rows = self.repository.get_candidate_embeddings(
                context, job_id, job_candidate_id, resume_id, self.profiles.chunker.chunker_version,
                self.profiles.embedding.embedding_profile_id)
            raw = json.loads(snapshot["raw_resume_json"])
            self._validate_chunk_sources(raw, candidate_rows)
        except NotFound as exc:
            return SemanticEvaluation(
                **manifests, scoring_status="ASSET_INCOMPLETE", semantic_coverage=0.0,
                review_reasons=[f"ASSET_INCOMPLETE:语义资产尚未准备或不完整:{exc}"],
                debug_trace=[f"service={SEMANTIC_SERVICE_VERSION}"])
        except ValueError as exc:
            return SemanticEvaluation(
                **manifests, scoring_status="ASSET_INCOMPLETE", semantic_coverage=0.0,
                review_reasons=[f"ASSET_INTEGRITY:已存语义资产与绑定身份不一致:{exc}"],
                debug_trace=[f"service={SEMANTIC_SERVICE_VERSION}"])

        manifests.update(anchor_manifest_hash=anchor_manifest["manifest_hash"],
                         chunk_manifest_hash=chunk_manifest["manifest_hash"],
                         candidate_embedding_manifest_hash=embedding_manifest["manifest_hash"])
        try:
            computation = evaluate_policy_semantics(plan, candidate_rows, anchor_rows, self.profiles.semantic)
        except (ValueError, TypeError, KeyError) as exc:
            return SemanticEvaluation(
                **manifests, scoring_status="SCORING_FAILED", semantic_coverage=0.0,
                review_reasons=[f"SCORING_FAILED:{type(exc).__name__}:语义执行失败，不生成伪0分"],
                debug_trace=[f"service={SEMANTIC_SERVICE_VERSION}", type(exc).__name__])

        all_scored = bool(computation.dimension_scores) and all(
            item.scoring_status == "SCORED" for item in computation.dimension_scores)
        status = "SEMANTIC_SCORED" if (computation.semantic_coverage == 1 and all_scored) else "PARTIAL_SEMANTIC"
        return SemanticEvaluation(
            **manifests, scoring_status=status,
            semantic_score=computation.semantic_score, semantic_coverage=computation.semantic_coverage,
            dimension_scores=list(computation.dimension_scores),
            requirement_scores=list(computation.requirement_scores),
            group_traces=list(computation.group_traces),
            selected_evidence=list(computation.selected_evidence),
            excluded_evidence=list(computation.excluded_evidence),
            negative_evidence=list(computation.negative_evidence),
            missing_evidence=list(computation.missing_evidence),
            review_reasons=list(computation.review_reasons),
            debug_trace=[*computation.debug_trace, f"service={SEMANTIC_SERVICE_VERSION}"])

    @staticmethod
    def _validate_chunk_sources(raw, candidate_rows):
        """Re-prove every stored chunk quote resolves to the sealed snapshot."""
        for row in candidate_rows:
            source = resolve_chunk_source(raw, row["source_path"])
            if (not isinstance(source, str)
                    or source[row["start_offset"]:row["end_offset"]] != row["chunk_text"]):
                raise ValueError("Stored resume chunk no longer resolves to the sealed snapshot")
