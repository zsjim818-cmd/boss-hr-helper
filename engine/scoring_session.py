"""Input auditing, versioned evaluation cache and per-batch config snapshots."""
from engine.resume_text import get_source_texts
from copy import deepcopy
from threading import RLock

from pydantic import ValidationError

from engine.llm_reviewer import PROMPT_VERSION
from engine.rule_matcher import evidence_sources
from schemas import ResumeData, CandidateCardData, EvaluationOutput, DataIssue, Evidence, LLMStructuredEvaluation
from engine.hard_filter import check_hard_filters
from storage.scoring_cache import stable_hash, ScoringCache

ENGINE_VERSION = "hybrid-v2.1"


class ScoringSessionMixin:
    @property
    def config(self):
        """A detached snapshot. Change the file and reload explicitly between batches."""
        return deepcopy(self._config)

    def _init_session(self, cache, injected):
        self._session_lock = RLock()
        self._batch_depth = 0
        cache_cfg = self._config.get("cache", {})
        directory = cache_cfg.get("directory")
        if directory and not injected:
            directory = self.config_path.parent / directory
        else:
            directory = None
        self._score_cache = cache if cache is not None else ScoringCache(directory)

    def _versions(self):
        configured = self._config["model_versions"]
        return {
            "engine_version": ENGINE_VERSION, "prompt_version": PROMPT_VERSION,
            "config_label": self._config["config_version"],
            "job_config_version": stable_hash(self._config),
            "embedding_model_version": f"{configured['embedding']}:{getattr(self.embedder, 'version', self._embedding_identity)}",
            "llm_model_version": f"{configured['llm']}:{self.llm_model_name}:{getattr(self.llm_client, 'version', self._provider_identity)}",
        }

    def evaluate_batch(self, resumes, force_mode=None):
        """The reload lock is held for the entire batch, including lazy inputs."""
        with self._session_lock:
            self._batch_depth += 1
            try:
                return [self.evaluate(resume, force_mode) for resume in resumes]
            finally:
                self._batch_depth -= 1

    def evaluate(self, resume_input, force_mode=None):
        with self._session_lock:
            mode = force_mode or self._config["quotas_and_switches"]["scoring_mode"]
            if mode not in ("fast_mode", "deep_mode"):
                raise ValueError(f"Unsupported scoring mode: {mode}")
            try:
                if isinstance(resume_input, CandidateCardData):
                    raise ValueError("CandidateCardData is a preview, not a full ResumeData")
                # Revalidation catches objects mutated after construction too.
                data = resume_input.model_dump(by_alias=True) if isinstance(resume_input, ResumeData) else resume_input
                resume = ResumeData.model_validate(data)
            except (ValidationError, ValueError, TypeError) as exc:
                raw = resume_input if isinstance(resume_input, dict) else {}
                meta = raw.get("_meta", {})
                meta = meta if isinstance(meta, dict) else {}
                issues = [DataIssue(path=".".join(map(str, e["loc"])), kind="MAPPING_ERROR", message=e["type"])
                          for e in exc.errors(include_input=False)] if isinstance(exc, ValidationError) else [DataIssue(path="$", kind="MAPPING_ERROR", message=str(exc))]
                out = EvaluationOutput(candidate_key=str(meta.get("candidate_key") or "unidentified"),
                                       name=raw.get("name") if isinstance(raw.get("name"), str) else "未知候选人",
                                       scoring_mode=mode, scoring_status="SCORING_FAILED", routing_decision="PENDING_REVIEW",
                                       data_issues=issues, version_info=self._versions(), risk_point="字段映射失败，需修复数据后重评")
                out.debug_trace.route_reason = out.risk_point
                return out
            content = resume.model_dump(mode="json", by_alias=True, exclude={"meta"})
            content_hash = stable_hash(content)
            versions = self._versions()
            key = {**versions, "candidate_key": resume.meta.candidate_key,
                   "resume_content_hash": content_hash, "scoring_mode": mode}
            enabled = self._config.get("cache", {}).get("enabled", True)
            if enabled:
                stored = self._score_cache.get(key)
                if stored:
                    try:
                        out = EvaluationOutput.model_validate(stored)
                        if out.cache_key != stable_hash(key) or out.version_info != versions or out.candidate_key != resume.meta.candidate_key or out.resume_content_hash != content_hash or out.scoring_mode != mode:
                            raise ValueError("cache identity mismatch")
                        if out.scoring_status not in ("DEEP_SCORED", "COARSE_ONLY", "HARD_FILTERED"):
                            raise ValueError("Uncacheable scoring status")
                        if out.scoring_status == "DEEP_SCORED":
                            evaluation = LLMStructuredEvaluation.model_validate({
                                **{k: v.model_dump() for k, v in out.debug_trace.tier3_dimension_evidence.items()},
                                "highlights": out.highlights, "risk_point": out.risk_point})
                            self._validate_llm_evaluation(evaluation, resume)
                            total, dims = self._compute_weighted_dimensions(evaluation)
                            state, _ = check_hard_filters(resume, self._config)
                            expected_route = self._deep_routing(total, evaluation, resume, state)
                            if total != out.total_score or dims != out.dimension_scores or expected_route != out.routing_decision:
                                raise ValueError("Cached deep score or route mismatch")
                        out.cache_hit = True
                        return out
                    except (ValueError, TypeError):
                        self._score_cache.issues.append("cache_output_validation_failed")
            out = self._evaluate_resume(resume, mode)
            out.data_completeness = resume.data_completeness
            out.data_issues = resume.data_issues
            out.version_info, out.resume_content_hash, out.cache_key = versions, content_hash, stable_hash(key)
            out.evidence_details = [Evidence(stage="input", source=source, source_path=path, snippet=text,
                                            evaluation="原始自述，真实性需HR核验")
                                    for source, path, text in evidence_sources(resume, self._config["scoring_config"])]
            for chunk in out.debug_trace.tier2_chunk_details:
                out.evidence_details.append(Evidence(stage="tier2", source=chunk.source, source_path=chunk.source_path,
                                                    snippet=chunk.text, evaluation=f"Top-K入选路线:{chunk.selected_routes}",
                                                    metrics={**chunk.route_scores, **{f"penalty_{k}": v for k, v in chunk.route_penalties.items()}}))
            sources = {p: s for s, p, _ in get_source_texts(resume)}
            for dim, assessment in out.debug_trace.tier3_dimension_evidence.items():
                for citation in assessment.evidence:
                    out.evidence_details.append(Evidence(stage="tier3", source=sources.get(citation.source_path, "objective"),
                                                        source_path=citation.source_path, snippet=citation.quote, confidence=assessment.confidence,
                                                        evaluation=f"{dim}:{assessment.score}; 缺失:{assessment.missing_evidence}"))
            if enabled and out.scoring_status in ("DEEP_SCORED", "COARSE_ONLY", "HARD_FILTERED"):
                self._score_cache.put(key, out.model_dump(mode="json"))
            return out
