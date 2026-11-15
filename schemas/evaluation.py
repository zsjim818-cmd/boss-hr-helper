"""Scoring output contracts, model evidence and routing invariants."""
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import DataIssue, Evidence


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_path: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class DimensionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    score: float = Field(ge=0, le=100, allow_inf_nan=False)
    evidence: List[EvidenceCitation]
    missing_evidence: List[str]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_missing_reason(self):
        if not self.evidence and not self.missing_evidence:
            raise ValueError("缺乏证据时必须记录证据缺失项")
        return self


class LLMStructuredEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ai_automation: DimensionAssessment
    engineering: DimensionAssessment
    efficiency: DimensionAssessment
    job_fit: DimensionAssessment
    highlights: List[str] = Field(max_length=3)
    risk_point: str


class ChunkSimDetail(BaseModel):
    chunk_preview: str
    best_pos_sim: float
    best_pos_anchor: str
    best_neg_sim: float
    source: str = ""
    source_path: str = ""
    text: str = ""
    route_scores: Dict[str, float] = Field(default_factory=dict)
    route_penalties: Dict[str, float] = Field(default_factory=dict)
    route_positive_similarities: Dict[str, float] = Field(default_factory=dict)
    source_weight: float = 1.0
    selected_routes: List[str] = Field(default_factory=list)


class DebugTrace(BaseModel):
    hard_filter_status: Literal["PASS", "FAIL", "UNKNOWN"] = "PASS"
    hard_filter_checks: Dict[str, List[str]] = Field(default_factory=dict)
    route_reason: str = ""
    hard_filter_passed: bool = True
    hard_filter_reason: str = "通过"
    tier1_raw_score: float = 0.0
    tier1_capped_score: float = 0.0
    tier1_hit_details: List[str] = Field(default_factory=list)
    tier2_semantic_score: float = 0.0
    tier2_penalty_applied: float = 0.0
    tier2_chunk_details: List[ChunkSimDetail] = Field(default_factory=list)
    tier3_llm_raw_scores: Dict[str, float] = Field(default_factory=dict)
    tier3_dimension_evidence: Dict[str, DimensionAssessment] = Field(default_factory=dict)


class EvaluationOutput(BaseModel):
    candidate_key: str
    name: str
    passed_hard_filter: bool = True
    passed_tier2_coarse: bool = False
    coarse_score: float = Field(default=0.0, ge=0, le=100, allow_inf_nan=False)
    total_score: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    scoring_mode: Literal["fast_mode", "deep_mode"] = "deep_mode"
    scoring_status: Literal["COARSE_ONLY", "DEEP_SCORED", "SCORING_FAILED", "DATA_INCOMPLETE", "HARD_FILTERED"] = "COARSE_ONLY"
    routing_decision: Literal["AUTO_GREET", "PENDING_REVIEW", "NOT_RECOMMENDED", "REJECT"]
    matched_tags: List[str] = Field(default_factory=list)
    semantic_similarity: Dict[str, float] = Field(default_factory=dict)
    dimension_scores: Dict[str, float] = Field(default_factory=dict)
    highlights: List[str] = Field(default_factory=list)
    risk_point: str = ""
    debug_trace: DebugTrace = Field(default_factory=DebugTrace)
    evidence_details: List[Evidence] = Field(default_factory=list)
    data_issues: List[DataIssue] = Field(default_factory=list)
    data_completeness: Literal["COMPLETE", "PARTIAL", "COLLECTION_FAILED"] = "PARTIAL"
    version_info: Dict[str, str] = Field(default_factory=dict)
    resume_content_hash: str = ""
    cache_hit: bool = False
    cache_key: str = ""

    @model_validator(mode="after")
    def enforce_scoring_invariants(self):
        deep_complete = (self.scoring_mode == "deep_mode" and self.scoring_status == "DEEP_SCORED"
                         and self.data_completeness == "COMPLETE")
        if self.total_score is not None and not deep_complete:
            raise ValueError("正式深度分仅允许完整简历的成功deep_mode评估")
        if self.scoring_status == "DEEP_SCORED" and self.total_score is None:
            raise ValueError("DEEP_SCORED必须有正式深度分")
        if self.routing_decision == "AUTO_GREET" and not (
                deep_complete and self.total_score is not None and self.passed_hard_filter
                and self.passed_tier2_coarse and self.debug_trace.hard_filter_status == "PASS"):
            raise ValueError("AUTO_GREET必须完成deep_mode并通过全部数据和评分门控")
        return self
