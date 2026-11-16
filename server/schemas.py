"""Explicit request/response contracts for the HR console API."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class CreateJobRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)


class CompilePolicyRequest(ApiModel):
    job_title: str = Field(min_length=1, max_length=200)
    jd_text: str = Field(min_length=1, max_length=30000)
    hr_requirements: str = Field(max_length=15000)


class UpdateDraftRequest(ApiModel):
    policy: dict[str, Any]
    expected_revision: int = Field(ge=1)
    parent_run_id: str = Field(min_length=1)
    source_input: dict[str, Any] | None = None


class ConfirmDraftRequest(ApiModel):
    run_id: str = Field(min_length=1)
    confirmation_hash: str = Field(min_length=1, max_length=128)


class CreateVersionRequest(ApiModel):
    run_id: str = Field(min_length=1)
    confirmation_id: str = Field(min_length=1)
    execution_profile: dict[str, Any] | None = None


class PublishPolicyRequest(ApiModel):
    confirmation_hash: str = Field(min_length=1, max_length=128)
    quality_override_reason: str | None = Field(default=None, max_length=2000)


class ImportCandidateRequest(ApiModel):
    candidate_key: str = Field(min_length=1, max_length=200)
    resume: dict[str, Any]
    source: str = Field(default="manual", min_length=1, max_length=50)
    external_id: str | None = Field(default=None, max_length=200)


class ScoreRequest(ApiModel):
    scoring_mode: Literal["fast_mode", "deep_mode"] = "deep_mode"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    batch_id: str | None = Field(default=None, min_length=1, max_length=200)


class ReviewRequest(ApiModel):
    action: Literal["APPROVE", "REJECT", "REQUEST_REVIEW", "NOTE"]
    reason: str = Field(default="", max_length=2000)


class ContactRequest(ApiModel):
    action: Literal["CONTACTED", "DECLINED", "PENDING"]
    note: str = Field(default="", max_length=2000)


class SemanticPrepareRequest(ApiModel):
    job_candidate_id: str | None = Field(default=None, min_length=1, max_length=200)
    batch_id: str | None = Field(default=None, min_length=1, max_length=200)


class BrowserScanRequest(ApiModel):
    # Batch is bound by the path; the body only tunes pagination.  A conflicting
    # body batch_id is rejected instead of silently scanning another batch.
    batch_id: str | None = Field(default=None, max_length=200)
    pages: int = Field(default=3, ge=1, le=20)
    page_size: int = Field(default=20, ge=1, le=100)
    platform_job_id: str | None = Field(default=None, max_length=200)
    filters_confirmed: bool = False
    filter_summary: str | None = Field(default=None, max_length=500)
    max_details: int = Field(default=50, ge=1, le=500)
    max_duration_seconds: int = Field(default=300, ge=1, le=3600)


class GreetingDryRunRequest(ApiModel):
    message: str = Field(default="您好，方便聊聊岗位机会吗？", min_length=1, max_length=500)


class BrowserSessionNavigateRequest(ApiModel):
    job_id: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)


class PrecheckConfirmRequest(ApiModel):
    platform_job_id: str = Field(default="", max_length=200)
    filter_summary: str = Field(default="", max_length=500)
    visible_cards: int = Field(default=0, ge=0, le=10000)


class ReextractRequest(ApiModel):
    batch_id: str | None = Field(default=None, max_length=200)
