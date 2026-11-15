"""Generic policy-driven Tier 3 result contracts (Stage 6).

The LLM's wire protocol is ``schemas.job_policy.DynamicPolicyAssessment``
(already defined and tested); these models describe the *validated, weighted*
engine result that binds the assessment to one immutable candidate/policy
binding, so it can be persisted immutably and displayed by an HR client.

Nothing here is job-specific: dimensions, weights, labels and thresholds all
come from the bound ``JobScoringPolicy``.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.job_policy import PolicyThresholds


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


PipelineStatus = Literal[
    "HARD_FILTERED", "DATA_INCOMPLETE", "NEEDS_REVIEW", "COARSE_ONLY",
    "DEEP_SCORED", "TECHNICAL_FAILED",
]
PipelineRoutingSuggestion = Literal["DEEP_RECOMMEND", "HUMAN_REVIEW", "NOT_RECOMMENDED"]


class PipelineEvidence(PipelineModel):
    """One grounded quote: exact substring of a sealed resume field."""

    source_path: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=2000)


class PipelineDimensionReview(PipelineModel):
    dimension_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    weight: float = Field(gt=0, le=1)
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    evidence: list[PipelineEvidence] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class PolicyTier3Evaluation(PipelineModel):
    """Validated Tier 3 result bound to the candidate's immutable Policy version.

    ``routing_suggestion`` never authorises contacting anyone: it only records
    which policy threshold band the weighted score falls into, for HR review.
    """

    org_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    job_candidate_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    candidate_key: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    policy_hash: str = Field(min_length=1)
    resume_id: str = Field(min_length=1)
    resume_content_hash: str = Field(min_length=1)
    reviewer_version: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=200)
    llm_model: str | None = None
    dimensions: list[PipelineDimensionReview] = Field(min_length=1)
    total_score: float = Field(ge=0, le=100)
    routing_suggestion: Literal["DEEP_RECOMMEND", "HUMAN_REVIEW", "NOT_RECOMMENDED"]
    thresholds: PolicyThresholds
    review_reasons: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    llm_metrics: dict[str, Any] = Field(default_factory=dict)


class PolicyPipelineResult(PipelineModel):
    """One executed Tier 0->3 pipeline result bound to one immutable binding.

    ``status`` semantics:

    - HARD_FILTERED: explicit Tier 0 failure; no semantic/Tier 3 model work ran.
    - DATA_INCOMPLETE: unsealed/incomplete input; no scoring models ran.
    - COARSE_ONLY: Tier 1 below the policy's coarse threshold (or fast_mode).
    - NEEDS_REVIEW: hard facts unknown, or Tier 2 evidence/coverage insufficient,
      so no Tier 3 call was made; a human must resolve the gap.
    - DEEP_SCORED: Tier 0/1 passed and Tier 2 coverage was complete; Tier 3 ran
      and produced a routing suggestion (never a contact authorisation).
    - TECHNICAL_FAILED: an unexpected technical failure; nothing formal is stored.
    """

    org_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    job_candidate_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    candidate_key: str = Field(min_length=1)
    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    policy_hash: str = Field(min_length=1)
    resume_id: str = Field(min_length=1)
    resume_content_hash: str = Field(min_length=1)
    pipeline_version: str = Field(min_length=1, max_length=200)
    scoring_mode: Literal["fast_mode", "deep_mode"]
    status: PipelineStatus
    hard_filter_status: Literal["PASS", "FAIL", "UNKNOWN"]
    preliminary: dict[str, Any] | None = None
    semantic: dict[str, Any] | None = None
    tier3: dict[str, Any] | None = None
    total_score: float | None = Field(default=None, ge=0, le=100)
    routing_suggestion: PipelineRoutingSuggestion | None = None
    review_reasons: list[str] = Field(default_factory=list)
    technical_failure: dict[str, str] | None = None
