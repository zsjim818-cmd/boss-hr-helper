"""Strict, job-independent contracts for policy-driven Tier 2 semantics.

These models describe embedding execution inputs and diagnostic semantic
outputs.  They intentionally do not contain hiring decisions, Tier 1/Tier 3
scores, or job-specific capability fields.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class EmbeddingProfile(SemanticModel):
    """Every parameter that can change encoded vectors or their interpretation."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    embedding_profile_id: str = Field(min_length=1, max_length=200)
    profile_version: str = Field(min_length=1, max_length=100)
    model_identifier: str = Field(min_length=1, max_length=500)
    model_revision: str | None = Field(default=None, max_length=500)
    revision_status: Literal["PINNED", "UNPINNED"]
    tokenizer_identifier: str = Field(min_length=1, max_length=500)
    tokenizer_revision: str | None = Field(default=None, max_length=500)
    pooling_strategy: Literal["CLS", "MEAN", "LAST_TOKEN"]
    text_normalization_version: str = Field(min_length=1, max_length=100)
    input_prefix: str = Field(default="", max_length=2000)
    max_length: int = Field(gt=0, le=1_000_000)
    truncation_strategy: Literal["HEAD", "TAIL", "LONGEST_FIRST"]
    l2_normalize: bool
    embedding_dimension: int = Field(gt=0, le=1_000_000)
    dtype: Literal["float32"] = "float32"
    byte_order: Literal["little"] = "little"

    @model_validator(mode="after")
    def validate_revision_claim(self):
        if self.revision_status == "PINNED" and not (
                self.model_revision and self.model_revision.strip()
                and self.tokenizer_revision and self.tokenizer_revision.strip()):
            raise ValueError("A PINNED embedding profile requires concrete model and tokenizer revisions")
        return self


class SemanticSourceWeights(SemanticModel):
    """Resume evidence weights; auxiliary self-reports stay below practice."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    work_responsibility: float = Field(gt=0, le=1)
    project_description: float = Field(gt=0, le=1)
    project_achievement: float = Field(gt=0, le=1)
    thesis_description: float = Field(gt=0, le=1)
    work_tags: float = Field(gt=0, le=1)
    skills: float = Field(gt=0, le=1)
    certification: float = Field(gt=0, le=1)
    advantage: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def auxiliary_sources_are_weaker(self):
        practical = max(self.work_responsibility, self.project_description, self.project_achievement)
        if max(self.work_tags, self.skills, self.certification, self.advantage) >= practical:
            raise ValueError("Auxiliary semantic sources must remain weaker than practical evidence")
        return self


class SemanticExecutionProfile(SemanticModel):
    """Versioned deterministic parameters for mapping similarities to scores."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    semantic_profile_version: str = Field(min_length=1, max_length=100)
    formula_version: Literal["requirement-topk-local-negative-v1"] = "requirement-topk-local-negative-v1"
    similarity_min: float = Field(ge=-1, le=1)
    similarity_max: float = Field(ge=-1, le=1)
    top_k: int = Field(gt=0, le=100)
    top_k_weights: tuple[float, ...] = Field(min_length=1, max_length=100)
    negative_threshold: float = Field(ge=-1, le=1)
    negative_margin: float = Field(ge=0, le=2)
    negative_baseline: float = Field(ge=-1, le=1)
    negative_penalty_weight: float = Field(ge=0, le=100)
    negative_penalty_cap: float = Field(ge=0, le=1)
    deduplicate_by_experience: Literal[True] = True
    source_weights: SemanticSourceWeights

    @model_validator(mode="after")
    def validate_formula_parameters(self):
        if self.similarity_max <= self.similarity_min:
            raise ValueError("similarity_max must exceed similarity_min")
        if len(self.top_k_weights) != self.top_k:
            raise ValueError("top_k_weights must contain exactly top_k entries")
        if any(weight <= 0 or weight > 1 for weight in self.top_k_weights):
            raise ValueError("Top-K weights must be finite values in (0, 1]")
        if not math.isclose(sum(self.top_k_weights), 1.0, abs_tol=1e-9):
            raise ValueError("Top-K weights must sum to one")
        return self


class ResumeChunkerProfile(SemanticModel):
    """Stable rules for turning one immutable resume snapshot into chunks."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    chunker_version: str = Field(min_length=1, max_length=100)
    max_characters: int = Field(gt=0, le=1_000_000)
    min_characters: int = Field(gt=0, le=1_000_000)
    min_label_characters: int = Field(gt=0, le=1_000_000)
    overlap_characters: int = Field(ge=0, le=100_000)
    split_strategy: Literal["FIELD_THEN_SENTENCE", "FIELD_ONLY"]
    boundary_version: str = Field(min_length=1, max_length=100)
    preserve_original_offsets: Literal[True] = True

    @model_validator(mode="after")
    def validate_overlap(self):
        if self.min_characters > self.max_characters or self.min_label_characters > self.max_characters:
            raise ValueError("Minimum chunk length cannot exceed the maximum")
        if self.overlap_characters >= self.max_characters:
            raise ValueError("Chunk overlap must be smaller than the maximum chunk length")
        return self


ChunkSourceType = Literal[
    "WORK_RESPONSIBILITY",
    "PROJECT_DESCRIPTION",
    "PROJECT_ACHIEVEMENT",
    "THESIS_DESCRIPTION",
    "WORK_TAGS",
    "SKILLS",
    "CERTIFICATION",
    "ADVANTAGE",
]
EvidenceQualityType = Literal[
    "PRACTICE",
    "DELIVERABLE",
    "ACADEMIC_PRACTICE",
    "AUXILIARY",
    "SELF_DESCRIPTION",
]


class ResumeChunk(SemanticModel):
    chunk_id: str = Field(min_length=1, max_length=200)
    org_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(min_length=1, max_length=200)
    resume_id: str = Field(min_length=1, max_length=200)
    resume_content_hash: str = Field(pattern=SHA256_PATTERN)
    source_path: str = Field(min_length=1, max_length=1000)
    source_type: ChunkSourceType
    experience_key: str = Field(min_length=1, max_length=1000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=1_000_000)
    text_hash: str = Field(pattern=SHA256_PATTERN)
    chunker_version: str = Field(min_length=1, max_length=100)
    evidence_quality_type: EvidenceQualityType
    split_index: int = Field(ge=0)
    truncated: bool = False

    @model_validator(mode="after")
    def validate_span(self):
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("Chunk offsets must delimit its exact original text")
        return self


class SemanticMissingEvidence(SemanticModel):
    kind: Literal[
        "POLICY_ANCHOR_MISSING",
        "CANDIDATE_EVIDENCE_MISSING",
        "INPUT_INCOMPLETE",
        "ASSET_MISSING",
        "AMBIGUOUS_MAPPING",
    ]
    message: str = Field(min_length=1, max_length=2000)
    requirement_id: str | None = Field(default=None, max_length=80)
    dimension_id: str | None = Field(default=None, max_length=80)
    anchor_id: str | None = Field(default=None, max_length=80)


class SemanticEvidence(SemanticModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    chunk_embedding_asset_id: str = Field(min_length=1, max_length=200)
    anchor_id: str = Field(min_length=1, max_length=80)
    anchor_embedding_asset_id: str = Field(min_length=1, max_length=200)
    anchor_polarity: Literal["POSITIVE", "NEGATIVE"]
    source_path: str = Field(min_length=1, max_length=1000)
    source_type: ChunkSourceType
    experience_key: str = Field(min_length=1, max_length=1000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=1_000_000)
    requirement_ids: list[str] = Field(min_length=1, max_length=60)
    dimension_ids: list[str] = Field(default_factory=list, max_length=8)
    association_kind: Literal["SINGLE_REQUIREMENT", "MULTI_REQUIREMENT_BROAD", "INDIRECT_DIMENSION_JOIN"]
    similarity: float = Field(ge=-1, le=1)
    mapped_similarity: float = Field(ge=0, le=1)
    source_weight: float = Field(ge=0, le=1)
    local_negative_similarity: float | None = Field(default=None, ge=-1, le=1)
    local_negative_penalty: float = Field(default=0.0, ge=0, le=1)
    confidence: Literal["HIGH", "MEDIUM", "LOW", "REVIEW"]
    selected: bool = False
    selection_reason: str | None = Field(default=None, max_length=2000)
    exclusion_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("Semantic evidence offsets must delimit its exact original quote")
        if len(set(self.requirement_ids)) != len(self.requirement_ids):
            raise ValueError("Semantic evidence requirement IDs must be unique")
        if len(set(self.dimension_ids)) != len(self.dimension_ids):
            raise ValueError("Semantic evidence dimension IDs must be unique")
        if self.selected and self.exclusion_reason is not None:
            raise ValueError("Selected evidence cannot also have an exclusion reason")
        if not self.selected and self.selection_reason is not None:
            raise ValueError("Unselected evidence cannot have a selection reason")
        return self


RequirementSemanticStatus = Literal[
    "SCORED",
    "PARTIAL",
    "NO_EVIDENCE",
    "POLICY_ANCHOR_MISSING",
    "REVIEW_REQUIRED",
    "FAILED",
]


class RequirementSemanticScore(SemanticModel):
    requirement_id: str = Field(min_length=1, max_length=80)
    scoring_status: RequirementSemanticStatus
    positive_similarity: float | None = Field(default=None, ge=-1, le=1)
    negative_similarity: float | None = Field(default=None, ge=-1, le=1)
    semantic_score: float | None = Field(default=None, ge=0, le=100)
    policy_anchor_coverage: float = Field(ge=0, le=1)
    selected_evidence: list[SemanticEvidence] = Field(default_factory=list)
    excluded_evidence: list[SemanticEvidence] = Field(default_factory=list)
    negative_evidence: list[SemanticEvidence] = Field(default_factory=list)
    missing_evidence: list[SemanticMissingEvidence] = Field(default_factory=list)
    confidence: Literal["HIGH", "MEDIUM", "LOW", "REVIEW", "UNAVAILABLE"]
    diagnostic_details: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_score_state(self):
        unavailable = self.scoring_status in {"POLICY_ANCHOR_MISSING", "FAILED"}
        if unavailable and self.semantic_score is not None:
            raise ValueError("Unavailable requirement semantics cannot fabricate a zero or numeric score")
        if self.scoring_status == "POLICY_ANCHOR_MISSING" and self.policy_anchor_coverage != 0:
            raise ValueError("A requirement without policy anchors must have zero policy coverage")
        if self.scoring_status == "SCORED" and self.semantic_score is None:
            raise ValueError("A scored requirement needs a numeric semantic score")
        if any(item.anchor_polarity != "POSITIVE" for item in self.selected_evidence):
            raise ValueError("Selected positive evidence must use positive anchors")
        if any(item.anchor_polarity != "NEGATIVE" for item in self.negative_evidence):
            raise ValueError("Negative evidence must use negative anchors")
        return self


class SemanticGroupTrace(SemanticModel):
    group_id: str = Field(min_length=1, max_length=80)
    dimension_id: str = Field(min_length=1, max_length=80)
    operator: Literal["ALL", "ANY"]
    members: list[str] = Field(min_length=1, max_length=40)
    selected_members: list[str] = Field(default_factory=list, max_length=40)
    member_scores: dict[str, float | None]
    semantic_score: float | None = Field(default=None, ge=0, le=100)
    coverage: float = Field(ge=0, le=1)
    scoring_status: Literal["SCORED", "PARTIAL", "UNAVAILABLE"]
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_group(self):
        if len(set(self.members)) != len(self.members):
            raise ValueError("Semantic group members must be unique")
        if not set(self.selected_members) <= set(self.members):
            raise ValueError("Selected group members must belong to the group")
        if set(self.member_scores) != set(self.members):
            raise ValueError("Group member scores must preserve every branch")
        if self.operator == "ANY" and len(self.selected_members) > 1:
            raise ValueError("An ANY semantic trace may select at most one representative branch")
        if self.operator == "ALL" and set(self.selected_members) != set(self.members):
            raise ValueError("An ALL semantic trace must preserve every member")
        if self.scoring_status == "UNAVAILABLE" and self.semantic_score is not None:
            raise ValueError("An unavailable group cannot carry a numeric score")
        if self.scoring_status == "SCORED" and self.semantic_score is None:
            raise ValueError("A scored group needs a numeric score")
        return self


DimensionSemanticStatus = Literal["SCORED", "PARTIAL", "POLICY_ANCHOR_MISSING", "FAILED"]


class SemanticDimensionScore(SemanticModel):
    dimension_id: str = Field(min_length=1, max_length=80)
    dimension_label: str = Field(min_length=1, max_length=200)
    dimension_weight: float = Field(gt=0, le=1)
    requirement_ids: list[str] = Field(min_length=1, max_length=40)
    scoring_status: DimensionSemanticStatus
    semantic_score: float | None = Field(default=None, ge=0, le=100)
    policy_anchor_coverage: float = Field(ge=0, le=1)
    matched_requirements: list[str] = Field(default_factory=list, max_length=40)
    selected_anchors: list[str] = Field(default_factory=list, max_length=100)
    selected_chunks: list[str] = Field(default_factory=list, max_length=1000)
    evidence: list[SemanticEvidence] = Field(default_factory=list)
    excluded_evidence: list[SemanticEvidence] = Field(default_factory=list)
    negative_evidence: list[SemanticEvidence] = Field(default_factory=list)
    missing_evidence: list[SemanticMissingEvidence] = Field(default_factory=list)
    confidence: Literal["HIGH", "MEDIUM", "LOW", "REVIEW", "UNAVAILABLE"]

    @model_validator(mode="after")
    def validate_dimension(self):
        for values, label in ((self.requirement_ids, "requirements"),
                              (self.matched_requirements, "matched requirements"),
                              (self.selected_anchors, "selected anchors"),
                              (self.selected_chunks, "selected chunks")):
            if len(set(values)) != len(values):
                raise ValueError(f"Dimension {label} must be unique")
        if not set(self.matched_requirements) <= set(self.requirement_ids):
            raise ValueError("Matched requirements must belong to the dimension")
        unavailable = self.scoring_status in {"POLICY_ANCHOR_MISSING", "FAILED"}
        if unavailable and self.semantic_score is not None:
            raise ValueError("Unavailable dimensions cannot fabricate a zero or numeric score")
        if self.scoring_status == "POLICY_ANCHOR_MISSING" and self.policy_anchor_coverage != 0:
            raise ValueError("A dimension without policy anchors must have zero policy coverage")
        if self.scoring_status == "SCORED" and (self.semantic_score is None or self.policy_anchor_coverage != 1):
            raise ValueError("A fully scored dimension needs a score and complete policy-anchor coverage")
        return self


SemanticScoringStatus = Literal[
    "SEMANTIC_SCORED",
    "PARTIAL_SEMANTIC",
    "DATA_INCOMPLETE",
    "ASSET_INCOMPLETE",
    "SCORING_FAILED",
]


class SemanticEvaluation(SemanticModel):
    org_id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=200)
    job_candidate_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(min_length=1, max_length=200)
    policy_id: str = Field(min_length=1, max_length=200)
    policy_version: int = Field(gt=0)
    policy_hash: str = Field(pattern=SHA256_PATTERN)
    resume_id: str = Field(min_length=1, max_length=200)
    resume_content_hash: str = Field(pattern=SHA256_PATTERN)
    input_kind: Literal["SEALED", "PARTIAL_PREVIEW"]
    data_completeness: Literal["COMPLETE", "PARTIAL", "COLLECTION_FAILED"]
    input_has_issues: bool = False
    embedding_profile_id: str = Field(min_length=1, max_length=200)
    embedding_profile_hash: str = Field(pattern=SHA256_PATTERN)
    chunker_version: str = Field(min_length=1, max_length=100)
    chunker_profile_hash: str = Field(pattern=SHA256_PATTERN)
    semantic_profile_version: str = Field(min_length=1, max_length=100)
    semantic_profile_hash: str = Field(pattern=SHA256_PATTERN)
    anchor_manifest_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    chunk_manifest_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    candidate_embedding_manifest_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    score_kind: Literal["POLICY_TIER2_SEMANTIC"] = "POLICY_TIER2_SEMANTIC"
    assessment_scope: Literal["TIER2_ONLY"] = "TIER2_ONLY"
    scoring_status: SemanticScoringStatus
    semantic_score: float | None = Field(default=None, ge=0, le=100)
    semantic_coverage: float = Field(ge=0, le=1)
    dimension_scores: list[SemanticDimensionScore] = Field(default_factory=list, max_length=8)
    requirement_scores: list[RequirementSemanticScore] = Field(default_factory=list, max_length=60)
    group_traces: list[SemanticGroupTrace] = Field(default_factory=list)
    selected_evidence: list[SemanticEvidence] = Field(default_factory=list)
    excluded_evidence: list[SemanticEvidence] = Field(default_factory=list)
    negative_evidence: list[SemanticEvidence] = Field(default_factory=list)
    missing_evidence: list[SemanticMissingEvidence] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    debug_trace: list[str] = Field(default_factory=list)
    upstream_preliminary_status: Literal[
        "SCORING_FAILED", "HARD_FILTERED", "DATA_INCOMPLETE", "HARD_UNKNOWN", "BELOW_COARSE", "READY_FOR_DEEP"
    ] | None = None
    routing_authority: Literal[False] = False
    hard_filter_override: Literal[False] = False
    thresholds_applied: Literal[False] = False
    formal_evaluation: Literal[False] = False

    @model_validator(mode="after")
    def validate_evaluation(self):
        dimension_ids = [item.dimension_id for item in self.dimension_scores]
        requirement_ids = [item.requirement_id for item in self.requirement_scores]
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("Semantic evaluation dimension IDs must be unique")
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Semantic evaluation requirement IDs must be unique")

        no_score_statuses = {"DATA_INCOMPLETE", "ASSET_INCOMPLETE", "SCORING_FAILED"}
        if self.scoring_status in no_score_statuses and self.semantic_score is not None:
            raise ValueError("Incomplete or failed semantic execution cannot fabricate a zero or numeric score")
        if self.scoring_status in no_score_statuses and any(item.semantic_score is not None for item in self.dimension_scores):
            raise ValueError("Incomplete or failed semantic execution cannot retain dimension scores")
        if self.scoring_status == "SEMANTIC_SCORED":
            if self.semantic_score is None or self.semantic_coverage != 1:
                raise ValueError("A complete semantic result needs a score and full policy-anchor coverage")
            if not self.dimension_scores or any(item.scoring_status != "SCORED" for item in self.dimension_scores):
                raise ValueError("A complete semantic result requires every dynamic dimension to be fully scored")
        if self.scoring_status == "PARTIAL_SEMANTIC":
            if self.semantic_score is None:
                raise ValueError("A partial semantic result needs an observed score")
            if self.semantic_coverage >= 1 and all(item.scoring_status == "SCORED" for item in self.dimension_scores):
                raise ValueError("A fully covered result must not be labelled partial")

        score_states = {"SEMANTIC_SCORED", "PARTIAL_SEMANTIC"}
        if self.scoring_status in score_states:
            if (self.input_kind != "SEALED" or self.data_completeness != "COMPLETE"
                    or self.input_has_issues):
                raise ValueError("Semantic scoring requires a sealed, complete, issue-free resume input")
            if (self.anchor_manifest_hash is None or self.chunk_manifest_hash is None
                    or self.candidate_embedding_manifest_hash is None):
                raise ValueError("Semantic scoring requires validated anchor, chunk, and vector manifests")
        if self.scoring_status == "DATA_INCOMPLETE" and not (
                self.input_kind == "PARTIAL_PREVIEW" or self.data_completeness != "COMPLETE"
                or self.input_has_issues):
            raise ValueError("DATA_INCOMPLETE requires a visible resume input limitation")

        if self.semantic_score is not None:
            scored = [item for item in self.dimension_scores if item.semantic_score is not None]
            if not self.dimension_scores or not scored:
                raise ValueError("A top-level semantic score requires at least one scored dynamic dimension")
            if not math.isclose(sum(item.dimension_weight for item in self.dimension_scores), 1.0, abs_tol=1e-6):
                raise ValueError("Dynamic semantic dimension weights must sum to one")
            available_weight = sum(item.dimension_weight for item in scored)
            weighted = round(sum(item.dimension_weight * item.semantic_score for item in scored)
                             / available_weight, 2)
            if not math.isclose(weighted, self.semantic_score, abs_tol=.011):
                raise ValueError("Semantic score must equal the available-weight normalized dimension sum")
        return self
