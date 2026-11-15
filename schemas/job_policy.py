"""Job-independent policy and dynamic-assessment data contracts.

The policy is a finite declarative program.  Its structure is stable across jobs;
business graph/source validation lives in config.policy_validation.  No scoring
engine imports or job-specific defaults belong here.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


POLICY_SCHEMA_VERSION = "job_scoring_policy_v1"


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class PolicyCompileInput(PolicyModel):
    job_title: str = Field(min_length=1, max_length=200)
    jd_text: str = Field(min_length=1, max_length=30000)
    hr_requirements: str = Field(max_length=15000)


class PolicyJob(PolicyModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)


class PolicySources(PolicyModel):
    jd_text: str = Field(min_length=1, max_length=30000)
    hr_requirements: str = Field(max_length=15000)


Priority = Literal["REQUIRED", "PREFERRED", "OPTIONAL"]
SourceType = Literal["JD", "HR", "INFERENCE"]


class PolicyRequirement(PolicyModel):
    requirement_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=500)
    source_type: SourceType
    source_text: str = Field(max_length=1500)
    priority: Priority
    requirement_type: Literal["SKILL", "EXPERIENCE", "EDUCATION", "LOCATION", "COMPENSATION", "RESPONSIBILITY", "OTHER"]
    needs_confirmation: bool


class RequirementGroup(PolicyModel):
    group_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=300)
    operator: Literal["ALL", "ANY"]
    priority: Priority
    members: list[str] = Field(min_length=1, max_length=40)
    source_type: SourceType
    source_text: str = Field(max_length=1500)
    needs_confirmation: bool


class HardFilter(PolicyModel):
    filter_id: str = Field(min_length=1, max_length=80)
    requirement_id: str = Field(min_length=1, max_length=80)
    field: Literal["education_level", "work_years", "expected_salary", "city", "work_authorization", "job_status"]
    operator: Literal["EQ", "NE", "GTE", "LTE", "IN", "EXISTS"]
    value_text: str | None
    value_number: float | None
    value_list: list[str]
    unit: Literal["year", "month", "day", "hour", "level"] | None
    on_unknown: Literal["REVIEW"]


class PolicyKeyword(PolicyModel):
    canonical: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(max_length=12)
    weight: float = Field(gt=0, le=100)


class KeywordGroup(PolicyModel):
    group_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=300)
    requirement_ids: list[str] = Field(min_length=1, max_length=40)
    keywords: list[PolicyKeyword] = Field(min_length=1, max_length=30)


class SemanticAnchor(PolicyModel):
    anchor_id: str = Field(min_length=1, max_length=80)
    requirement_ids: list[str] = Field(min_length=1, max_length=40)
    polarity: Literal["POSITIVE", "NEGATIVE"]
    text: str = Field(min_length=8, max_length=1000)


class RubricBand(PolicyModel):
    min_score: int = Field(ge=0, le=100)
    max_score: int = Field(ge=0, le=100)
    description: str = Field(min_length=8, max_length=1000)


class ScoringDimension(PolicyModel):
    dimension_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=8, max_length=1500)
    requirement_ids: list[str] = Field(min_length=1, max_length=40)
    weight: float = Field(gt=0, le=1)
    rubric: list[RubricBand] = Field(min_length=2, max_length=8)


class PolicyThresholds(PolicyModel):
    coarse_pass: float = Field(ge=0, le=100)
    deep_review: float = Field(ge=0, le=100)
    deep_recommend: float = Field(ge=0, le=100)


class PolicyDiagnostic(PolicyModel):
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["ERROR", "CONFIRM", "WARNING"]
    message: str = Field(min_length=1, max_length=2000)
    requirement_ids: list[str]


class PolicyProvenance(PolicyModel):
    compiler_version: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=200)
    validator_version: str = Field(min_length=1, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)


class JobScoringPolicy(PolicyModel):
    schema_version: Literal["job_scoring_policy_v1"]
    job: PolicyJob
    sources: PolicySources
    requirements: list[PolicyRequirement] = Field(min_length=1, max_length=60)
    requirement_groups: list[RequirementGroup] = Field(min_length=1, max_length=30)
    hard_filters: list[HardFilter] = Field(max_length=20)
    keyword_groups: list[KeywordGroup] = Field(min_length=1, max_length=20)
    semantic_anchors: list[SemanticAnchor] = Field(min_length=1, max_length=30)
    scoring_dimensions: list[ScoringDimension] = Field(min_length=2, max_length=8)
    thresholds: PolicyThresholds
    diagnostics: list[PolicyDiagnostic] = Field(max_length=60)
    provenance: PolicyProvenance


class DynamicDimensionAssessment(PolicyModel):
    dimension_id: str
    score: float = Field(ge=0, le=100)
    evidence_quotes: list[str]
    missing_evidence: list[str]
    confidence: float = Field(ge=0, le=1)


class DynamicPolicyAssessment(PolicyModel):
    """Future engine output protocol; dimensions are values, never class fields."""
    dimensions: list[DynamicDimensionAssessment] = Field(min_length=1)
