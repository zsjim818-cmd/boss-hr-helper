"""Business routes: jobs, policies, candidates/batches, scoring, HR review.

All business logic is delegated to existing services and repositories; this
module only translates HTTP to the trusted AccessContext and serialises results.
"""
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from config.policy_compiler import JobPolicyCompiler
from config.policy_service import PolicyCompilerService, PolicyQualityBlocked
from engine.policy_validator import validate_policy_quality
from schemas.job_policy import JobScoringPolicy
from server.auth import get_context
from server.responses import (BatchOut, CandidateDetailOut, CandidateListOut,
                              CompileOut, ConfirmOut, ContactsOut, DraftOut,
                              EvaluationsOut, ImportOut, JobOut, JobsOut,
                              PolicyRowOut, PublishOut, QualityOut, RankingOut,
                              ReviewOut, ReviewsOut, RowOut, RuntimeModeOut,
                              ScoreStartOut, SemanticPrepareOut, TaskOut,
                              VersionOut)
from server.schemas import (CompilePolicyRequest, ConfirmDraftRequest,
                            ContactRequest, CreateJobRequest,
                            CreateVersionRequest, ImportCandidateRequest,
                            PublishPolicyRequest, ReviewRequest, ScoreRequest,
                            SemanticPrepareRequest, UpdateDraftRequest)


jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])
policies_router = APIRouter(prefix="/api/jobs", tags=["policies"])
candidates_router = APIRouter(prefix="/api/jobs", tags=["candidates"])
scoring_router = APIRouter(prefix="/api/jobs", tags=["scoring"])
reviews_router = APIRouter(prefix="/api/jobs", tags=["reviews"])

JSON_FIELDS = ("policy_json", "source_json", "diagnostics_json", "source_snapshot_json",
               "policy", "draft", "tier0_json", "tier1_json", "tier2_json", "tier3_json",
               "review_reasons_json", "result_json", "metrics_json", "result_summary_json",
               "checks_json")


def _row(value: Any) -> Any:
    if isinstance(value, list):
        return [_row(item) for item in value]
    if not isinstance(value, dict):
        return value
    parsed = {}
    for key, item in value.items():
        if key in JSON_FIELDS and isinstance(item, str):
            try:
                parsed[key] = json.loads(item)
            except ValueError:
                parsed[key] = item
        else:
            parsed[key] = _row(item)
    return parsed


def _repo(request: Request):
    return request.app.state.repository


def _compiler(request: Request) -> JobPolicyCompiler:
    factory = request.app.state.compiler_factory
    return factory()


# --------------------------------------------------------------------- jobs
@jobs_router.post("", response_model=JobOut)
def create_job(body: CreateJobRequest, request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).create_job(ctx, body.title))


@jobs_router.get("", response_model=JobsOut)
def list_jobs(request: Request, ctx=Depends(get_context)):
    return {"jobs": _row(_repo(request).list_jobs(ctx))}


@jobs_router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).get_job(ctx, job_id))


# ----------------------------------------------------------------- policies
def _service(request: Request) -> PolicyCompilerService:
    return PolicyCompilerService(_repo(request), _compiler(request))


@policies_router.post("/{job_id}/policies/compile", response_model=CompileOut)
def compile_policy(job_id: str, body: CompilePolicyRequest, request: Request,
                   ctx=Depends(get_context)):
    result = _service(request).compile_draft(
        ctx, job_id, {"job_title": body.job_title, "jd_text": body.jd_text,
                      "hr_requirements": body.hr_requirements})
    return _row(result)


@policies_router.get("/{job_id}/policies/drafts/{draft_id}", response_model=DraftOut)
def get_draft(job_id: str, draft_id: str, request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).get_policy_draft(ctx, job_id, draft_id))


@policies_router.patch("/{job_id}/policies/drafts/{draft_id}", response_model=CompileOut)
def update_draft(job_id: str, draft_id: str, body: UpdateDraftRequest, request: Request,
                 ctx=Depends(get_context)):
    return _row(_service(request).update_draft(
        ctx, job_id, draft_id, body.policy, expected_revision=body.expected_revision,
        parent_run_id=body.parent_run_id, source_input=body.source_input))


@policies_router.get("/{job_id}/policies/drafts/{draft_id}/quality", response_model=QualityOut)
def draft_quality(job_id: str, draft_id: str, request: Request, ctx=Depends(get_context)):
    draft = _repo(request).get_policy_draft(ctx, job_id, draft_id)
    policy = JobScoringPolicy.model_validate(json.loads(draft["policy_json"]))
    return validate_policy_quality(policy).model_dump(mode="json")


@policies_router.post("/{job_id}/policies/drafts/{draft_id}/confirm", response_model=ConfirmOut)
def confirm_draft(job_id: str, draft_id: str, body: ConfirmDraftRequest,
                  request: Request, ctx=Depends(get_context)):
    return _row(_service(request).confirm_draft(
        ctx, job_id, draft_id, body.run_id, confirmation_hash=body.confirmation_hash))


@policies_router.post("/{job_id}/policies/drafts/{draft_id}/versions", response_model=VersionOut)
def create_version(job_id: str, draft_id: str, body: CreateVersionRequest,
                   request: Request, ctx=Depends(get_context)):
    repo = _repo(request)
    profile = repo.create_execution_profile(
        ctx, body.execution_profile or {"assessment_scope": "generic_pipeline",
                                        "pipeline_protocol": "job_scoring_policy_v1"})
    return _row(_service(request).create_version(
        ctx, job_id, draft_id, body.run_id, body.confirmation_id,
        execution_profile_id=profile["profile_id"]))


@policies_router.post("/{job_id}/policies/{policy_id}/publish", response_model=PublishOut)
def publish_policy(job_id: str, policy_id: str, body: PublishPolicyRequest,
                   request: Request, ctx=Depends(get_context)):
    return _row(_service(request).publish_version(
        ctx, job_id, policy_id, confirmation_hash=body.confirmation_hash,
        quality_override_reason=body.quality_override_reason))


@policies_router.get("/{job_id}/policies/current", response_model=PolicyRowOut)
def current_policy(job_id: str, request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).get_current_policy(ctx, job_id))


@policies_router.get("/{job_id}/policies/history", response_model=PolicyRowOut)
def policy_history(job_id: str, version: int, request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).get_policy_version(ctx, job_id, policy_version=version))


# ------------------------------------------------------ candidates and batches
@candidates_router.post("/{job_id}/batches", response_model=BatchOut)
def create_batch(job_id: str, request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).create_scan_batch(ctx, job_id))


@candidates_router.get("/{job_id}/batches/{batch_id}", response_model=BatchOut)
def get_batch(job_id: str, batch_id: str, request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).get_scan_batch(ctx, job_id, batch_id))


@candidates_router.post("/{job_id}/batches/{batch_id}/candidates", response_model=ImportOut)
def import_candidate(job_id: str, batch_id: str, body: ImportCandidateRequest,
                     request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).acquire_candidate_for_job(
        ctx, job_id, batch_id, body.candidate_key, source=body.source,
        external_id=body.external_id, resume=body.resume))


@candidates_router.get("/{job_id}/candidates", response_model=CandidateListOut)
def list_candidates(job_id: str, request: Request, ctx=Depends(get_context),
                    limit: int = 50, offset: int = 0, status: str | None = None,
                    min_score: float | None = None):
    result = _repo(request).list_job_candidates(
        ctx, job_id, limit=limit, offset=offset, status=status, min_score=min_score)
    return {"total": result["total"], "items": _row(result["items"])}


@candidates_router.get("/{job_id}/candidates/{job_candidate_id}", response_model=CandidateDetailOut)
def candidate_detail(job_id: str, job_candidate_id: str, request: Request,
                     ctx=Depends(get_context)):
    repo = _repo(request)
    candidate = repo.get_job_candidate(ctx, job_id, job_candidate_id)
    latest = repo.latest_pipeline_result(ctx, job_id, job_candidate_id)
    history = repo.list_pipeline_results(ctx, job_id, job_candidate_id)
    attempts = repo.list_pipeline_attempts(ctx, job_id, job_candidate_id)
    return {"job_candidate": _row(candidate), "latest_evaluation": _row(latest),
            "evaluations": _row(history), "attempts": _row(attempts)}


# -------------------------------------------------------------------- scoring
@scoring_router.post("/{job_id}/candidates/{job_candidate_id}/score", response_model=ScoreStartOut)
def start_scoring(job_id: str, job_candidate_id: str, body: ScoreRequest,
                  request: Request, ctx=Depends(get_context)):
    repo = _repo(request)
    pipeline_factory = request.app.state.pipeline_factory
    candidate = repo.get_job_candidate(ctx, job_id, job_candidate_id)
    try:
        repo.get_sealed_input(ctx, job_id, job_candidate_id)
    except LookupError:
        raise HTTPException(
            status_code=409,
            detail="Candidate has no sealed scoring input; re-collect the resume first "
                   "(incomplete information is never scored)") from None
    pipeline = pipeline_factory(repo)
    existing = pipeline.reusable_result(ctx, job_id, job_candidate_id, body.scoring_mode)
    if (existing is not None and existing["policy_id"] == candidate["policy_id"]
            and existing["policy_version"] == candidate["policy_version"]):
        return {"reused": True, "task": None, "evaluation": _row(existing)}
    task = repo.create_pipeline_task(ctx, job_id, job_candidate_id, body.scoring_mode,
                                     idempotency_key=body.idempotency_key)
    if task["status"] == "SUCCEEDED" and task["pipeline_evaluation_id"]:
        return {"reused": True, "task": _row(task),
                "evaluation": _row(repo.get_pipeline_result(ctx, job_id,
                                                            task["pipeline_evaluation_id"]))}
    if task["status"] == "RUNNING":
        return {"reused": True, "in_progress": True, "task": _row(task)}
    request.app.state.executor.submit(
        pipeline.run_and_persist, ctx, job_id, job_candidate_id,
        scoring_mode=body.scoring_mode, idempotency_key=body.idempotency_key,
        task_id=task["pipeline_task_id"])
    return {"reused": False, "in_progress": True, "task": _row(task)}


@scoring_router.get("/{job_id}/tasks/{task_id}", response_model=TaskOut)
def get_task(job_id: str, task_id: str, request: Request, ctx=Depends(get_context)):
    repo = _repo(request)
    task = repo.get_pipeline_task(ctx, job_id, task_id)
    evaluation = None
    if task["pipeline_evaluation_id"]:
        evaluation = repo.get_pipeline_result(ctx, job_id, task["pipeline_evaluation_id"])
    return {"task": _row(task), "evaluation": _row(evaluation)}


@scoring_router.get("/{job_id}/candidates/{job_candidate_id}/evaluations", response_model=EvaluationsOut)
def candidate_evaluations(job_id: str, job_candidate_id: str, request: Request,
                          ctx=Depends(get_context)):
    repo = _repo(request)
    return {"latest": _row(repo.latest_pipeline_result(ctx, job_id, job_candidate_id)),
            "history": _row(repo.list_pipeline_results(ctx, job_id, job_candidate_id))}


@scoring_router.get("/{job_id}/ranking", response_model=RankingOut)
def ranking(job_id: str, request: Request, ctx=Depends(get_context), limit: int = 100):
    return {"ranking": _row(_repo(request).job_candidate_ranking(ctx, job_id, limit=limit))}


@scoring_router.post("/{job_id}/semantic-prepare", response_model=SemanticPrepareOut)
def semantic_prepare(job_id: str, request: Request, ctx=Depends(get_context),
                     body: SemanticPrepareRequest | None = None):
    """Prepare immutable Policy/candidate embedding assets (live mode).

    In deterministic mode this is an explicit no-op; the response says so
    instead of pretending assets exist.
    """
    repo = _repo(request)
    # Authorization: reading the current policy requires job view permission.
    repo.get_current_policy(ctx, job_id)
    if body and body.job_candidate_id:
        repo.get_job_candidate(ctx, job_id, body.job_candidate_id)
    return _row(request.app.state.semantic_runtime.prepare(
        ctx, job_id, body.job_candidate_id if body else None))


# -------------------------------------------------------------------- reviews
@reviews_router.post("/{job_id}/evaluations/{evaluation_id}/reviews", response_model=ReviewOut)
def append_review(job_id: str, evaluation_id: str, body: ReviewRequest,
                  request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).append_pipeline_review(
        ctx, job_id, evaluation_id, body.action, body.reason))


@reviews_router.get("/{job_id}/evaluations/{evaluation_id}/reviews", response_model=ReviewsOut)
def list_reviews(job_id: str, evaluation_id: str, request: Request,
                 ctx=Depends(get_context)):
    return {"reviews": _row(_repo(request).list_pipeline_reviews(ctx, job_id, evaluation_id))}


@reviews_router.post("/{job_id}/evaluations/{evaluation_id}/contact", response_model=RowOut)
def append_contact(job_id: str, evaluation_id: str, body: ContactRequest,
                   request: Request, ctx=Depends(get_context)):
    return _row(_repo(request).append_pipeline_contact_event(
        ctx, job_id, evaluation_id, body.action, body.note))


@reviews_router.get("/{job_id}/evaluations/{evaluation_id}/contact", response_model=ContactsOut)
def list_contact(job_id: str, evaluation_id: str, request: Request,
                 ctx=Depends(get_context)):
    return {"events": _row(_repo(request).list_pipeline_contact_events(
        ctx, job_id, evaluation_id))}
