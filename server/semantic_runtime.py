"""Semantic runtime modes for the HTTP service.

- ``deterministic`` (default): no models are loaded.  Tier 2 returns a valid,
  explicitly mock-labelled payload so the whole HTTP flow can be demonstrated
  and tested without BGE/Qwen.  Semantic preparation is a declared no-op.
- ``live``: loads the local BGE model (once, lazily) and prepares immutable
  embedding assets for the current published Policy and the candidate resume
  before Tier 2 runs; Tier 3 then uses the configured real Qwen client.
"""
import json

from engine.policy_interpreter import PolicyInterpreter


RUNTIME_MODES = ("deterministic", "live")
DETERMINISTIC_SEMANTIC_SCORE = 80.0


class SemanticRuntimeError(RuntimeError):
    pass


class DeterministicSemanticRuntime:
    mode = "deterministic"

    def describe(self):
        return {"mode": self.mode, "models_required": False,
                "semantic": "fixed deterministic payload (demo only)",
                "notes": "No BGE model loaded; Tier 2 scores are demo data, not quality claims."}

    def prepare(self, context, job_id, job_candidate_id=None):
        return {"mode": self.mode, "status": "DETERMINISTIC_NO_ASSETS_REQUIRED",
                "policy": None, "candidate": None}

    def semantic_service(self, repository):
        return _DeterministicSemanticService(repository)


class _DeterministicSemanticService:
    """Valid Tier 2 payload assembled from database identity, without BGE."""

    def __init__(self, repository):
        self.repository = repository

    def evaluate(self, context, job_id, job_candidate_id, *, preliminary=None):
        candidate = self.repository.get_job_candidate(context, job_id, job_candidate_id)
        sealed = self.repository.get_sealed_input(context, job_id, job_candidate_id)
        snapshot = self.repository.get_resume_snapshot(
            context, job_id, job_candidate_id, sealed["resume_id"])
        plan = PolicyInterpreter(self.repository).load_for_candidate(
            context, job_id, job_candidate_id).plan
        return {
            "org_id": context.org_id, "job_id": job_id,
            "job_candidate_id": job_candidate_id, "candidate_id": candidate["candidate_id"],
            "policy_id": plan.policy_id, "policy_version": plan.policy_version,
            "policy_hash": plan.policy_hash, "resume_id": sealed["resume_id"],
            "resume_content_hash": snapshot["content_hash"],
            "scoring_status": "SEMANTIC_SCORED",
            "semantic_score": DETERMINISTIC_SEMANTIC_SCORE,
            "semantic_coverage": 1.0,
            "dimension_scores": [
                {"dimension_id": dimension.dimension_id, "scoring_status": "SCORED",
                 "semantic_score": DETERMINISTIC_SEMANTIC_SCORE}
                for dimension in plan.policy.scoring_dimensions],
            "review_reasons": ["DETERMINISTIC_MODE: 语义分为演示用固定值，不代表真实模型质量"],
            "debug_trace": ["semantic-runtime=deterministic"],
        }


class LiveSemanticRuntime:
    mode = "live"

    def __init__(self, repository):
        self.repository = repository
        self._model = None

    def describe(self):
        from config.settings import load_runtime_settings
        settings = load_runtime_settings()
        return {"mode": self.mode, "models_required": True,
                "embedding_model_path": settings.embedding_model_path,
                "llm_model_name": settings.llm_model_name}

    def model(self):
        if self._model is None:
            from config.settings import load_runtime_settings
            from engine.semantic_profiles import load_semantic_profiles
            from engine.semantic_verification import assert_embedding_model_matches
            from sentence_transformers import SentenceTransformer
            path = load_runtime_settings().embedding_model_path
            try:
                model = SentenceTransformer(path)
                assert_embedding_model_matches(model, load_semantic_profiles().embedding)
            except Exception as exc:  # pragma: no cover - environment dependent
                raise SemanticRuntimeError(
                    f"Local BGE model could not be loaded/verified at {path!r}: {exc}") from exc
            self._model = model
        return self._model

    def prepare(self, context, job_id, job_candidate_id=None):
        """Prepare immutable embedding assets for the correct Policy version.

        With a candidate, the Policy identity always comes from the candidate's
        first-bound batch version (never the job's current publication): a batch
        locked on v1 must keep preparing and scoring v1 after v2 is published.
        Without a candidate, the currently published Policy is allowed.
        """
        from engine.embedding_service import PolicyEmbeddingService, ResumeEmbeddingService
        if job_candidate_id is not None:
            candidate = self.repository.get_job_candidate(context, job_id, job_candidate_id)
            version = self.repository.get_policy_version(
                context, job_id, policy_id=candidate["policy_id"])
            if version["policy_version"] != candidate["policy_version"]:
                raise SemanticRuntimeError(
                    "Candidate-bound Policy version does not match its immutable version row")
            policy_id = candidate["policy_id"]
            policy_version = candidate["policy_version"]
        else:
            current = self.repository.get_current_policy(context, job_id)
            policy_id = current["policy_id"]
            policy_version = current["policy_version"]
        service = PolicyEmbeddingService(self.repository, self.model())
        result = {"mode": self.mode, "status": "PREPARED",
                  "policy": service.prepare_policy(context, job_id, policy_id, policy_version)}
        if job_candidate_id is not None:
            result["candidate"] = ResumeEmbeddingService(
                self.repository, self.model()).prepare_candidate(
                context, job_id, job_candidate_id)
        return result

    def semantic_service(self, repository):
        return _PreparingSemanticService(repository, self)


class _PreparingSemanticService:
    """Prepare policy/candidate assets, then run the real Tier 2 service."""

    def __init__(self, repository, runtime):
        self.repository = repository
        self.runtime = runtime
        self._service = None

    def evaluate(self, context, job_id, job_candidate_id, *, preliminary=None):
        from engine.semantic_scoring_service import SemanticScoringService
        self.runtime.prepare(context, job_id, job_candidate_id)
        if self._service is None:
            self._service = SemanticScoringService(self.repository)
        return self._service.evaluate(context, job_id, job_candidate_id,
                                      preliminary=preliminary)
