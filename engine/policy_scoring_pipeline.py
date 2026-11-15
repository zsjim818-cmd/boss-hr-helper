"""Generic Tier 0->3 orchestration; Stage 6 pipeline application service.

Decision matrix (all thresholds and dimensions come from the candidate's one
immutable bound Policy version):

| Tier 0/1 preliminary state | fast_mode | deep_mode |
| --- | --- | --- |
| HARD_FILTERED | HARD_FILTERED | HARD_FILTERED (no BGE, no Qwen) |
| DATA_INCOMPLETE / SCORING_FAILED | DATA_INCOMPLETE | DATA_INCOMPLETE (no BGE/Qwen) |
| hard UNKNOWN | NEEDS_REVIEW | NEEDS_REVIEW (Tier 2 runs for evidence, no Qwen) |
| PASS, below coarse threshold | COARSE_ONLY | COARSE_ONLY (no BGE/Qwen) |
| PASS, coarse pass, Tier 2 incomplete (PARTIAL / POLICY_ANCHOR_MISSING / coverage<1) | — | NEEDS_REVIEW (no Qwen) |
| PASS, coarse pass, Tier 2 fully scored | — | DEEP_SCORED via Tier 3 |

Any unexpected exception in Tier 2/Tier 3 becomes TECHNICAL_FAILED (attempt is
recorded, no formal result).  Incomplete evidence never counts as "does not
meet the requirement"; routing suggestions never authorise contacting anyone.
"""
from __future__ import annotations

import json

from engine.policy_interpreter import PolicyInterpreter
from engine.policy_llm_reviewer import PolicyReviewService
from engine.prescreen_engine import PreliminaryScreeningEngine
from schemas.pipeline import PolicyPipelineResult
from storage.repositories.pipeline import PipelineConflict


PIPELINE_VERSION = "policy-scoring-pipeline-v1"

FORMAL_STATUSES = ("HARD_FILTERED", "DATA_INCOMPLETE", "NEEDS_REVIEW",
                   "COARSE_ONLY", "DEEP_SCORED")


class PipelineError(ValueError):
    """Invalid pipeline request (not a technical failure)."""


def _dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


class PolicyScoringPipeline:
    def __init__(self, repository, *, interpreter=None, semantic_service=None,
                 review_service=None, pipeline_version=PIPELINE_VERSION):
        if interpreter is not None and interpreter.repository is not repository:
            raise ValueError("An injected interpreter must use this service's repository")
        self.repository = repository
        self.interpreter = interpreter or PolicyInterpreter(repository)
        self._semantic = semantic_service
        self._review = review_service
        self.pipeline_version = pipeline_version
        self.last_metrics = {}

    # ------------------------------------------------------------- factories
    def _semantic_service(self):
        if self._semantic is None:
            from engine.semantic_scoring_service import SemanticScoringService
            self._semantic = SemanticScoringService(self.repository, interpreter=self.interpreter)
        return self._semantic

    def _review_service(self):
        if self._review is None:
            self._review = PolicyReviewService(self.repository, interpreter=self.interpreter)
        return self._review

    # -------------------------------------------------------------- pipeline
    def _result(self, preliminary, *, status, semantic=None, tier3=None,
                total_score=None, routing=None, review_reasons=(), technical_failure=None):
        return PolicyPipelineResult(
            org_id=preliminary.org_id, job_id=preliminary.job_id,
            job_candidate_id=preliminary.job_candidate_id,
            candidate_id=preliminary.candidate_id,
            candidate_key=self._candidate_key,
            policy_id=preliminary.policy_id, policy_version=preliminary.policy_version,
            policy_hash=preliminary.policy_hash, resume_id=preliminary.resume_id,
            resume_content_hash=preliminary.resume_content_hash,
            pipeline_version=self.pipeline_version,
            scoring_mode=self._scoring_mode,
            status=status,
            hard_filter_status=preliminary.hard_filter_status,
            preliminary=preliminary.model_dump(mode="json"),
            semantic=_dump(semantic) if semantic is not None else None,
            tier3=_dump(tier3) if tier3 is not None else None,
            total_score=total_score, routing_suggestion=routing,
            review_reasons=list(review_reasons), technical_failure=technical_failure)

    def _semantic_fully_scored(self, semantic: dict) -> tuple[bool, list[str]]:
        reasons = []
        if semantic.get("scoring_status") != "SEMANTIC_SCORED":
            reasons.append(f"Tier2状态为{semantic.get('scoring_status')}，未完成语义评分")
        coverage = semantic.get("semantic_coverage")
        if coverage != 1.0:
            reasons.append(f"Tier2语义覆盖率仅{coverage}，存在锚点缺失或局部覆盖")
        for item in semantic.get("dimension_scores", []):
            if item.get("scoring_status") != "SCORED":
                reasons.append(f"维度{item.get('dimension_id')}状态为{item.get('scoring_status')}，"
                               "不能以其他维度高分掩盖")
        return (not reasons), reasons

    def run(self, context, job_id, job_candidate_id, *, scoring_mode="deep_mode",
            batch_id=None):
        if scoring_mode not in ("fast_mode", "deep_mode"):
            raise PipelineError("Unsupported scoring mode")
        self._scoring_mode = scoring_mode
        candidate = self.repository.get_job_candidate(context, job_id, job_candidate_id)
        self._candidate_key = candidate["candidate_key"]
        preliminary = PreliminaryScreeningEngine(
            self.repository, interpreter=self.interpreter).evaluate(
            context, job_id, job_candidate_id, batch_id=batch_id)
        if scoring_mode == "fast_mode":
            return self._fast_mode(preliminary)
        return self._deep_mode(context, job_id, job_candidate_id, preliminary,
                               batch_id=batch_id)

    def _fast_mode(self, preliminary):
        if preliminary.scoring_status == "HARD_FILTERED":
            return self._result(preliminary, status="HARD_FILTERED",
                                review_reasons=["Tier0硬条件明确不满足；未调用语义/终审模型"])
        if preliminary.scoring_status in ("DATA_INCOMPLETE", "SCORING_FAILED"):
            return self._result(preliminary, status="DATA_INCOMPLETE",
                                review_reasons=["评分输入不完整；未调用语义/终审模型"])
        if preliminary.scoring_status == "HARD_UNKNOWN":
            return self._result(preliminary, status="NEEDS_REVIEW",
                                review_reasons=["Tier0存在未知硬条件；需人工核实，未调用终审模型"])
        return self._result(preliminary, status="COARSE_ONLY",
                            total_score=preliminary.preliminary_score,
                            review_reasons=["fast_mode仅完成Tier0/Tier1；未调用语义/终审模型"])

    def _deep_mode(self, context, job_id, job_candidate_id, preliminary,
                   *, batch_id=None):
        if preliminary.scoring_status == "HARD_FILTERED":
            return self._result(preliminary, status="HARD_FILTERED",
                                review_reasons=["Tier0硬条件明确不满足；未调用语义/终审模型"])
        if preliminary.scoring_status in ("DATA_INCOMPLETE", "SCORING_FAILED"):
            return self._result(preliminary, status="DATA_INCOMPLETE",
                                review_reasons=["评分输入不完整；未调用语义/终审模型"])
        if preliminary.scoring_status == "HARD_UNKNOWN":
            # Objective facts are unknown: gather Tier 2 evidence for HR, but
            # never spend a Tier 3 call on an unresolved hard condition.
            try:
                semantic = _dump(self._semantic_service().evaluate(
                    context, job_id, job_candidate_id, preliminary=preliminary,
                    batch_id=batch_id))
            except Exception as exc:  # technical failure of the semantic stage
                return self._technical(preliminary, exc, stage="tier2")
            return self._result(preliminary, status="NEEDS_REVIEW", semantic=semantic,
                                review_reasons=["Tier0存在未知硬条件；保留语义证据供人工核实",
                                                "未调用终审模型"])
        if not preliminary.coarse_passed:
            return self._result(preliminary, status="COARSE_ONLY",
                                total_score=preliminary.preliminary_score,
                                review_reasons=["Tier1规则初筛分低于本岗位粗排门槛；未调用语义/终审模型"])
        try:
            semantic = _dump(self._semantic_service().evaluate(
                context, job_id, job_candidate_id, preliminary=preliminary,
                batch_id=batch_id))
        except Exception as exc:
            return self._technical(preliminary, exc, stage="tier2")
        if semantic.get("scoring_status") in ("DATA_INCOMPLETE", "ASSET_INCOMPLETE", "SCORING_FAILED"):
            return self._result(preliminary, status="NEEDS_REVIEW", semantic=semantic,
                                review_reasons=[f"Tier2未能完成({semantic.get('scoring_status')})；"
                                                "不生成终审分数"])
        fully_scored, reasons = self._semantic_fully_scored(semantic)
        if not fully_scored:
            return self._result(preliminary, status="NEEDS_REVIEW", semantic=semantic,
                                review_reasons=[*reasons, "语义证据不完整；未调用终审模型"])
        try:
            tier3 = self._review_service().evaluate(
                context, job_id, job_candidate_id, batch_id=batch_id)
        except Exception as exc:
            return self._technical(preliminary, exc, stage="tier3", semantic=semantic)
        return self._result(preliminary, status="DEEP_SCORED", semantic=semantic,
                            tier3=tier3, total_score=tier3.total_score,
                            routing=tier3.routing_suggestion,
                            review_reasons=["Tier0/1/2通过，Tier3完成；路由仅建议，不授权发送",
                                            *tier3.review_reasons])

    def _technical(self, preliminary, exc, *, stage, semantic=None):
        failure = {"error_type": type(exc).__name__, "message": str(exc)[:2000], "stage": stage}
        self.last_metrics = {"stage": stage, "error_type": failure["error_type"]}
        return self._result(preliminary, status="TECHNICAL_FAILED", semantic=semantic,
                            review_reasons=[f"{stage}阶段技术失败；未写入正式评估"],
                            technical_failure=failure)

    # -------------------------------------------------------- persistence API
    def _current_binding(self, context, job_id, job_candidate_id, batch_id=None):
        return self.repository.expected_candidate_policy(
            context, job_id, job_candidate_id, batch_id)

    def reusable_result(self, context, job_id, job_candidate_id, scoring_mode,
                        batch_id=None):
        """Reuse only results of the same scoring context (batch Policy)."""
        if scoring_mode not in ("fast_mode", "deep_mode"):
            raise PipelineError("Unsupported scoring mode")
        expected = self._current_binding(context, job_id, job_candidate_id, batch_id)
        try:
            sealed = self.repository.get_sealed_input(context, job_id, job_candidate_id)
            resume_id = sealed["resume_id"]
        except Exception:
            resume_id = None
        deep = self.repository.pipeline_result_for_mode(
            context, job_id, job_candidate_id, "deep_mode",
            policy_id=expected["policy_id"],
            policy_version=expected["policy_version"],
            resume_id=resume_id, pipeline_version=self.pipeline_version)
        if deep is not None:
            return deep
        if scoring_mode == "fast_mode":
            return self.repository.pipeline_result_for_mode(
                context, job_id, job_candidate_id, "fast_mode",
                policy_id=expected["policy_id"],
                policy_version=expected["policy_version"],
                resume_id=resume_id, pipeline_version=self.pipeline_version)
        return None

    def run_and_persist(self, context, job_id, job_candidate_id, *, scoring_mode="deep_mode",
                        idempotency_key=None, task_id=None, batch_id=None):
        """Idempotent execution: existing formal result/task wins, no re-scoring."""
        candidate = self._current_binding(context, job_id, job_candidate_id, batch_id)
        existing = self.reusable_result(context, job_id, job_candidate_id, scoring_mode,
                                        batch_id=batch_id)
        if (existing is not None and existing["policy_id"] == candidate["policy_id"]
                and existing["policy_version"] == candidate["policy_version"]):
            return {"task": None, "evaluation": existing, "reused": True}
        if task_id is None:
            task = self.repository.create_pipeline_task(
                context, job_id, job_candidate_id, scoring_mode,
                idempotency_key=idempotency_key, batch_id=batch_id)
        else:
            task = self.repository.get_pipeline_task(context, job_id, task_id)
        # Identity binding is re-verified for reused tasks as well: a task may
        # never be executed for a different candidate or a different stage.
        if (task["job_candidate_id"], task["scoring_mode"]) != (job_candidate_id, scoring_mode):
            raise PipelineConflict(
                "Pipeline task identity does not match the requested candidate/scoring mode")
        if task["status"] == "SUCCEEDED" and task["pipeline_evaluation_id"]:
            evaluation = self.repository.get_pipeline_result(
                context, job_id, task["pipeline_evaluation_id"])
            if (evaluation["policy_id"], evaluation["policy_version"]) != (
                    candidate["policy_id"], candidate["policy_version"]):
                raise PipelineConflict(
                    "Task is bound to another Policy version than the scoring batch")
            return {"task": task, "evaluation": evaluation, "reused": True}
        if task["status"] == "RUNNING":
            return {"task": task, "evaluation": None, "reused": True, "in_progress": True}
        claimed = self._claim_task(context, job_id, task)
        if claimed is None:
            current = self.repository.get_pipeline_task(context, job_id, task["pipeline_task_id"])
            return {"task": current, "evaluation": None, "reused": True, "in_progress": True}
        task = claimed
        try:
            result = self.run(context, job_id, job_candidate_id,
                              scoring_mode=scoring_mode, batch_id=batch_id)
        except Exception as exc:  # identity/sealed-input/authorization errors
            task = self.repository.update_pipeline_task(
                context, job_id, task["pipeline_task_id"], status="FAILED",
                error_type=type(exc).__name__, error_message=str(exc)[:2000])
            return {"task": task, "evaluation": None, "reused": False}
        metrics = {"pipeline_version": self.pipeline_version}
        if result.status == "TECHNICAL_FAILED":
            self.repository.append_pipeline_result_attempt(
                context, job_id, result, status="TECHNICAL_FAILED", metrics=metrics)
            task = self.repository.update_pipeline_task(
                context, job_id, task["pipeline_task_id"], status="FAILED",
                error_type=(result.technical_failure or {}).get("error_type", "TechnicalFailure"),
                error_message=(result.technical_failure or {}).get("message"))
            return {"task": task, "evaluation": None, "reused": False}
        saved = self.repository.save_pipeline_result(
            context, job_id, result, batch_id=batch_id)
        self.repository.append_pipeline_result_attempt(
            context, job_id, result, status="SUCCEEDED", metrics=metrics)
        task = self.repository.update_pipeline_task(
            context, job_id, task["pipeline_task_id"], status="SUCCEEDED",
            pipeline_evaluation_id=saved["pipeline_evaluation_id"])
        return {"task": task, "evaluation": saved, "reused": False}

    def _claim_task(self, context, job_id, task):
        """Atomically claim a PENDING/FAILED task; a lost race means in-progress."""
        import sqlite3
        try:
            return self.repository.update_pipeline_task(
                context, job_id, task["pipeline_task_id"], status="RUNNING")
        except sqlite3.IntegrityError:
            return None
