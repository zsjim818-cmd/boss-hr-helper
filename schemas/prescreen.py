"""Strict job-independent Tier 0/1 results, separate from formal V2 scores."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.job_policy import PolicyThresholds


class ScreenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class SourceWeights(ScreenModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    work: float = Field(default=1.2, gt=0)
    project: float = Field(default=1.2, gt=0)
    thesis: float = Field(default=1.0, gt=0)
    work_tags: float = Field(default=.35, gt=0)
    skills: float = Field(default=.5, gt=0)
    advantage: float = Field(default=.4, gt=0)


class PrescreenExecutionProfile(ScreenModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)
    profile_version: str = Field(default="prescreen-rules-v1", min_length=1)
    formula_version: Literal["coverage-exp-quality-v1"] = "coverage-exp-quality-v1"
    source_weights: SourceWeights = Field(default_factory=SourceWeights)
    city_basis: Literal["expected", "current"] = "expected"
    salary_implicit_period: Literal["month", "year", "day", "hour"] | None = None
    practice_actions: tuple[str, ...] = ("负责", "独立", "主导", "完成", "交付", "实现", "设计", "执行", "组织", "管理", "推进", "处理", "维护", "优化", "搭建", "编写", "部署", "开发", "implemented", "delivered", "designed", "managed", "led", "built")
    learning_markers: tuple[str, ...] = ("正在学习", "学习中", "自学", "入门", "计划学习", "准备学习", "仅了解", "learning", "studying", "plan to learn")
    assertion_markers: tuple[str, ...] = ("熟悉", "掌握", "精通", "熟练", "具备", "proficient", "experienced", "skilled")
    label_prefixes: tuple[str, ...] = ("技术栈", "技术标签", "技能", "关键词", "熟悉", "了解", "精通", "熟练掌握", "skills", "tech stack")

    @property
    def maximum_source_weight(self):
        return max(self.source_weights.model_dump().values())

    @model_validator(mode="after")
    def keep_auxiliary_below_practice(self):
        weights = self.source_weights
        if max(weights.work_tags, weights.skills, weights.advantage) >= max(weights.work, weights.project, weights.thesis):
            raise ValueError("Auxiliary sources must remain weaker than practical evidence")
        return self


class PrescreenEvidence(ScreenModel):
    source_path: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    source_kind: str
    context: Literal["FACT", "PRACTICE", "ASSERTED", "LEARNING", "NEGATED", "NO_PRACTICE", "MENTION", "AMBIGUOUS"]
    canonical: str | None = None
    matched_variant: str | None = None
    requirement_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    source_weight: float = Field(default=0.0, ge=0)
    selected: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_span(self):
        if self.end - self.start != len(self.quote):
            raise ValueError("Evidence offsets must delimit the exact original quote")
        return self


class ObjectiveFact(ScreenModel):
    value: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class ObjectiveFacts(ScreenModel):
    """Only explicit values from the same raw snapshot, never inferred attributes."""
    work_authorization: ObjectiveFact | None = None
    current_city: ObjectiveFact | None = None


class RequirementCheck(ScreenModel):
    requirement_id: str
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    reason: str
    filter_ids: list[str] = Field(default_factory=list)
    evidence: list[PrescreenEvidence] = Field(default_factory=list)
    hard_eligible: bool = True


class GroupCheck(ScreenModel):
    group_id: str
    operator: Literal["ALL", "ANY"]
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    members: list[str]
    reason: str


class Tier0Result(ScreenModel):
    hard_filter_status: Literal["PASS", "FAIL", "UNKNOWN"]
    matched_requirements: list[str] = Field(default_factory=list)
    failed_requirements: list[str] = Field(default_factory=list)
    unknown_requirements: list[str] = Field(default_factory=list)
    atomic_results: list[RequirementCheck] = Field(default_factory=list)
    group_results: list[GroupCheck] = Field(default_factory=list)
    evidence: list[PrescreenEvidence] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    skipped_requirements: list[str] = Field(default_factory=list)


class GroupSelection(ScreenModel):
    group_id: str
    operator: Literal["ALL", "ANY"]
    dimension_id: str | None = None
    selected_members: list[str]
    member_scores: dict[str, float]
    reason: str


class PreliminaryDimensionScore(ScreenModel):
    dimension_id: str
    dimension_label: str
    dimension_weight: float = Field(gt=0, le=1)
    raw_score: float = Field(ge=0)
    normalized_score: float = Field(ge=0, le=100)
    matched_requirements: list[str] = Field(default_factory=list)
    evidence: list[PrescreenEvidence] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    keyword_budget: float = Field(default=0.0, ge=0)
    matched_budget: float = Field(default=0.0, ge=0)
    evidence_quality: float = Field(default=0.0, ge=0, le=1)


class Tier1Result(ScreenModel):
    preliminary_score: float = Field(ge=0, le=100)
    dimension_scores: list[PreliminaryDimensionScore]
    matched_requirements: list[str] = Field(default_factory=list)
    unknown_requirements: list[str] = Field(default_factory=list)
    keyword_evidence: list[PrescreenEvidence] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    group_selections: list[GroupSelection] = Field(default_factory=list)
    canonical_count: int = Field(default=0, ge=0)
    matched_canonical_count: int = Field(default=0, ge=0)


class PreliminaryEvaluation(ScreenModel):
    org_id: str
    candidate_id: str
    job_id: str
    job_candidate_id: str
    policy_id: str
    policy_version: int = Field(gt=0)
    policy_hash: str
    resume_id: str
    resume_content_hash: str
    input_kind: Literal["SEALED", "PARTIAL_PREVIEW"]
    data_completeness: Literal["COMPLETE", "PARTIAL", "COLLECTION_FAILED"]
    input_has_issues: bool = False
    score_kind: Literal["POLICY_TIER1_PRELIMINARY"] = "POLICY_TIER1_PRELIMINARY"
    assessment_scope: Literal["TIER0_TIER1_ONLY"] = "TIER0_TIER1_ONLY"
    interpreter_version: str
    execution_profile_version: str
    execution_profile_hash: str
    hard_filter_status: Literal["PASS", "FAIL", "UNKNOWN"]
    preliminary_score: float | None = Field(default=None, ge=0, le=100)
    dimension_scores: list[PreliminaryDimensionScore] = Field(default_factory=list)
    matched_requirements: list[str] = Field(default_factory=list)
    failed_requirements: list[str] = Field(default_factory=list)
    unknown_requirements: list[str] = Field(default_factory=list)
    keyword_evidence: list[PrescreenEvidence] = Field(default_factory=list)
    evidence: list[PrescreenEvidence] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    scoring_status: Literal["SCORING_FAILED", "HARD_FILTERED", "DATA_INCOMPLETE", "HARD_UNKNOWN", "BELOW_COARSE", "READY_FOR_DEEP"]
    thresholds: PolicyThresholds
    coarse_passed: bool = False
    deep_thresholds_applied: Literal[False] = False
    deep_score: None = None
    tier0: Tier0Result | None = None
    tier1: Tier1Result | None = None
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_consistency(self):
        if len({item.dimension_id for item in self.dimension_scores}) != len(self.dimension_scores):
            raise ValueError("Duplicate dynamic dimension ID")
        if self.scoring_status == "HARD_FILTERED" and self.hard_filter_status != "FAIL":
            raise ValueError("Hard-filtered status requires explicit failure")
        if self.hard_filter_status == "FAIL" and self.scoring_status != "HARD_FILTERED":
            raise ValueError("A preliminary score cannot override hard-filter failure")
        if self.scoring_status == "SCORING_FAILED" and (self.preliminary_score is not None or self.dimension_scores or self.coarse_passed):
            raise ValueError("Failed screening cannot fabricate rule scores")
        if self.scoring_status != "SCORING_FAILED" and self.preliminary_score is None:
            raise ValueError("Completed screening requires a preliminary rule score")
        if self.preliminary_score is not None:
            if abs(sum(item.dimension_weight for item in self.dimension_scores) - 1) > 1e-6:
                raise ValueError("Dynamic dimension weights must sum to one")
            score = round(sum(item.dimension_weight * item.normalized_score for item in self.dimension_scores), 2)
            if abs(score - self.preliminary_score) > .011:
                raise ValueError("Preliminary score must equal the dynamic weighted sum")
            if self.coarse_passed != (self.preliminary_score >= self.thresholds.coarse_pass):
                raise ValueError("Coarse decision must use this policy's coarse_pass threshold")
        if self.tier0 is not None and self.tier0.hard_filter_status != self.hard_filter_status:
            raise ValueError("Unified hard status must preserve Tier 0")
        if self.tier1 is not None and (self.preliminary_score != self.tier1.preliminary_score
                or self.dimension_scores != self.tier1.dimension_scores
                or self.keyword_evidence != self.tier1.keyword_evidence):
            raise ValueError("Unified scores/dimensions/evidence must preserve Tier 1")
        if self.scoring_status == "HARD_UNKNOWN" and self.hard_filter_status != "UNKNOWN":
            raise ValueError("Hard-unknown status requires an unknown hard result")
        if self.scoring_status == "BELOW_COARSE" and not (
                self.hard_filter_status == "PASS" and not self.coarse_passed):
            raise ValueError("Below-coarse status cannot replace a hard failure or a passing score")
        if self.scoring_status == "DATA_INCOMPLETE" and not (
                self.data_completeness != "COMPLETE" or self.input_has_issues or self.input_kind == "PARTIAL_PREVIEW"):
            raise ValueError("Data-incomplete status requires a visible input limitation")
        if self.scoring_status == "READY_FOR_DEEP" and not (
                self.hard_filter_status == "PASS" and self.data_completeness == "COMPLETE" and self.coarse_passed
                and self.input_kind == "SEALED" and not self.input_has_issues):
            raise ValueError("Ready-for-deep requires sealed complete input and passing objective/coarse gates")
        return self
