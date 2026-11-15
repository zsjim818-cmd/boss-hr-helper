"""Stage 5.5 Policy Quality Gate report contracts.

These models describe a deterministic structural review of an already-compiled
Policy.  They carry no candidate scores, grant no routing authority, and never
replace the existing publish diagnostics in config.policy_validation.

Batch 1 scope: positive-anchor coverage only (direct / broad-only / missing /
unclassified requirements, per-dimension coverage and reference validity).
Later batches extend this with atomicity, ALL/ANY graph and multi-requirement
anchor governance; this batch deliberately records instead of repairing.

Honest reporting rules enforced by these contracts:

- The semantic denominator only contains requirements that a scoring dimension
  actually projects (the Tier 2 engine aggregates semantics exclusively through
  dimension projections), so RULE (keyword-only) and HARD_FILTER requirements
  never inflate the denominator.
- The coverage rate is ``None`` (INDETERMINATE) whenever the denominator or a
  dimension projection cannot be confirmed; the validator must never fabricate
  0% or 100%.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyValidationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class PolicyQualityIssue(PolicyValidationModel):
    """One deterministic finding; later batches add graph/atomicity/anchor codes."""

    code: str = Field(min_length=1, max_length=100)
    severity: Literal["ERROR", "WARNING"]
    message: str = Field(min_length=1, max_length=2000)
    requirement_id: str | None = None
    group_id: str | None = None
    anchor_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    suggested_action: str = Field(min_length=1, max_length=2000)


class PolicyDimensionCoverage(PolicyValidationModel):
    """Per-dimension positive-anchor coverage; a None rate means INDETERMINATE.

    A dimension whose projection references undefined requirements cannot
    honestly report a coverage rate, so ``direct_coverage_rate`` stays None and
    ``invalid_requirement_ids`` explains why.
    """

    dimension_id: str = Field(min_length=1, max_length=80)
    requirement_ids: list[str] = Field(default_factory=list)
    invalid_requirement_ids: list[str] = Field(default_factory=list)
    semantic_requirement_count: int = Field(ge=0)
    direct_covered: int = Field(ge=0)
    broad_only: int = Field(ge=0)
    missing_positive: int = Field(ge=0)
    direct_coverage_rate: float | None = Field(default=None, ge=0, le=1)


class PolicyQualityReport(PolicyValidationModel):
    """Read-only quality report; publishing gates are applied by a later batch.

    ``policy_id`` / ``policy_version`` / ``policy_hash`` identify the exact
    Policy under review: repository callers pass the stored row identity, while
    a standalone-file review leaves id/version unset and uses the computed
    content fingerprint as ``policy_hash``.
    """

    validator_version: str = Field(min_length=1, max_length=200)
    policy_schema_version: str = Field(min_length=1, max_length=200)
    policy_id: str | None = None
    policy_version: int | None = Field(default=None, ge=1)
    policy_hash: str | None = None
    job_title: str = Field(min_length=1, max_length=200)
    status: Literal["PASS", "WARNING", "BLOCKED"]
    coverage_status: Literal["DETERMINED", "INDETERMINATE"]
    semantic_requirement_count: int = Field(ge=0)
    directly_covered_count: int = Field(ge=0)
    broad_only_count: int = Field(ge=0)
    missing_positive_count: int = Field(ge=0)
    unclassified_requirement_count: int = Field(ge=0)
    orphan_group_count: int = Field(default=0, ge=0)
    positive_coverage_rate: float | None = Field(default=None, ge=0, le=1)
    dimension_coverage: list[PolicyDimensionCoverage] = Field(default_factory=list)
    issues: list[PolicyQualityIssue] = Field(default_factory=list)
