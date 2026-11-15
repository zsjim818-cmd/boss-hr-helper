"""Permissive response models: documented OpenAPI contract for core routes.

Fields are optional and extras are preserved so the API can expose repository
columns without a second parallel schema layer, while the frontend still gets a
typed contract for the fields that matter.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict


class Out(BaseModel):
    model_config = ConfigDict(extra="allow")


class RuntimeModeOut(Out):
    mode: str | None = None
    models_required: bool | None = None
    notes: str | None = None
    embedding_model_path: str | None = None
    llm_model_name: str | None = None
    auth_mode: str | None = None
    local_only: bool | None = None


class JobOut(Out):
    job_id: str | None = None
    org_id: str | None = None
    title: str | None = None
    state: str | None = None
    current_policy_id: str | None = None
    current_policy_version: int | None = None
    next_policy_version: int | None = None


class JobsOut(Out):
    jobs: list[dict[str, Any]] = []


class CompileOut(Out):
    draft: dict[str, Any] | None = None
    compilation_run: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] = []
    publishable: bool | None = None
    quality: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None


class DraftOut(Out):
    draft_id: str | None = None
    revision: int | None = None
    draft_hash: str | None = None
    policy_json: dict[str, Any] | str | None = None
    diagnostics_json: list[dict[str, Any]] | None = None


class QualityOut(Out):
    status: str | None = None
    coverage_status: str | None = None
    semantic_requirement_count: int | None = None
    directly_covered_count: int | None = None
    broad_only_count: int | None = None
    missing_positive_count: int | None = None
    unclassified_requirement_count: int | None = None
    orphan_group_count: int | None = None
    positive_coverage_rate: float | None = None
    dimension_coverage: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []


class VersionOut(Out):
    version: dict[str, Any] | None = None


class PublishOut(Out):
    policy_id: str | None = None
    policy_version: int | None = None
    execution_profile_id: str | None = None
    confirmation_hash: str | None = None
    published_by: str | None = None


class PolicyRowOut(Out):
    policy_id: str | None = None
    policy_version: int | None = None
    policy_hash: str | None = None
    policy_json: dict[str, Any] | str | None = None
    policy_schema_version: str | None = None


class CandidateListOut(Out):
    total: int = 0
    items: list[dict[str, Any]] = []


class CandidateDetailOut(Out):
    job_candidate: dict[str, Any] | None = None
    latest_evaluation: dict[str, Any] | None = None
    evaluations: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []


class ScoreStartOut(Out):
    reused: bool | None = None
    in_progress: bool | None = None
    task: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None


class TaskOut(Out):
    task: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None


class EvaluationsOut(Out):
    latest: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []


class RankingOut(Out):
    ranking: list[dict[str, Any]] = []


class ReviewOut(Out):
    review_event_id: str | None = None
    pipeline_evaluation_id: str | None = None
    action: str | None = None
    reason: str | None = None
    actor_id: str | None = None
    created_at: str | None = None


class ReviewsOut(Out):
    reviews: list[dict[str, Any]] = []


class ContactsOut(Out):
    events: list[dict[str, Any]] = []


class SemanticPrepareOut(Out):
    mode: str | None = None
    status: str | None = None
    policy: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None


class BrowserScanOut(Out):
    job_id: str | None = None
    batch_id: str | None = None
    collected: int | None = None
    imported: int | None = None
    reused: int | None = None
    failed: int | None = None
    issues: list[dict[str, Any]] = []
    imported_candidates: list[dict[str, Any]] = []
    source: str | None = None
    browser: str | None = None
    page_url: str | None = None
    browser_account_id: str | None = None


class BrowserScanStatusOut(Out):
    status: str | None = None
    batch_id: str | None = None
    source: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class GreetingDryRunOut(Out):
    state: str | None = None
    identity: str | None = None
    message: str | None = None
    dry_run: bool | None = None
    recorded: bool | None = None
    idempotent: bool | None = None
    contact_event: dict[str, Any] | None = None


class ContactSummaryOut(Out):
    job_candidate_id: str | None = None
    contacted: bool | None = None
    contacted_by: list[str] = []
    current_user_contacted: bool | None = None
    latest_contact: dict[str, Any] | None = None
    latest_pending_dry_run: dict[str, Any] | None = None
    current_user_pending_dry_run: dict[str, Any] | None = None
    pending_dry_run_count: int | None = None
    events: list[dict[str, Any]] = []


class ConfirmOut(Out):
    confirmation_id: str | None = None
    draft_id: str | None = None
    run_id: str | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None


class RowOut(Out):
    pass


class BatchOut(Out):
    batch_id: str | None = None
    job_id: str | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    status: str | None = None


class ImportOut(Out):
    job_candidate: dict[str, Any] | None = None
    acquisition: dict[str, Any] | None = None
    resume_snapshot: dict[str, Any] | None = None
    sealed_input: dict[str, Any] | None = None
    is_new: bool | None = None
