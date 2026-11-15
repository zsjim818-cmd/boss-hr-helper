"""Stage 5.5 Policy Quality Gate entry point; read-only structural review.

The gate inspects an already-compiled Policy's structure and anchor coverage.
It never recomputes candidate scores, never mutates a published Policy, and
never delegates a pass/fail verdict to a model.  Later batches extend this
aggregator with requirement atomicity, ALL/ANY graph and multi-requirement
anchor checks; batch 1 only performs positive-anchor coverage.

Honesty rules for this aggregator:

- Caller supplies the repository identity (policy_id / policy_version /
  policy_hash) when reviewing a stored Policy; a standalone file review gets a
  computed canonical content fingerprint so reports stay comparable.
- With no confirmable semantic scope the coverage is reported as INDETERMINATE
  (rate ``None``) — never a fabricated 0% or 100%.
- ``status`` is a read-only verdict; publishing gates belong to a later batch.
"""
from __future__ import annotations

from engine.policy_anchor_validator import (
    broad_only_requirement_ids,
    dimension_coverage,
    directly_covered_requirement_ids,
    missing_positive_requirement_ids,
    positive_anchor_coverage_issues,
    reference_scope_issues,
    semantic_scope_requirement_ids,
    unclassified_requirement_ids,
)
from engine.policy_interpreter import content_hash
from engine.policy_structure_validator import (
    orphan_group_ids,
    structure_and_wiring_issues,
)
from schemas.job_policy import JobScoringPolicy
from schemas.policy_validation import PolicyQualityReport


POLICY_VALIDATOR_VERSION = "policy-quality-validator-v1.2"


def policy_content_hash(policy) -> str:
    """Canonical SHA-256 of the parsed policy; same form as storage digests."""
    parsed = JobScoringPolicy.model_validate(policy)
    return content_hash(parsed.model_dump(mode="json"))


def validate_policy_quality(policy, *, policy_id=None, policy_version=None,
                            policy_hash=None) -> PolicyQualityReport:
    """Return a deterministic quality report; no score is computed or changed."""
    policy = JobScoringPolicy.model_validate(policy)
    if policy_id is not None and (not isinstance(policy_id, str) or not policy_id.strip()):
        raise ValueError("policy_id must be a nonempty string when supplied")
    if policy_version is not None and (type(policy_version) is not int or policy_version < 1):
        raise ValueError("policy_version must be a positive integer when supplied")
    if policy_hash is not None and (not isinstance(policy_hash, str) or not policy_hash.strip()):
        raise ValueError("policy_hash must be a nonempty string when supplied")
    unclassified = unclassified_requirement_ids(policy)
    issues = [*positive_anchor_coverage_issues(policy), *reference_scope_issues(policy),
              *structure_and_wiring_issues(policy, unclassified)]
    scope = semantic_scope_requirement_ids(policy)
    covered = directly_covered_requirement_ids(policy)
    broad = broad_only_requirement_ids(policy)
    missing = missing_positive_requirement_ids(policy)
    orphan_groups = orphan_group_ids(policy)
    rate = round(len(covered) / len(scope), 6) if scope else None
    if any(issue.severity == "ERROR" for issue in issues):
        status = "BLOCKED"
    elif issues:
        status = "WARNING"
    else:
        status = "PASS"
    return PolicyQualityReport(
        validator_version=POLICY_VALIDATOR_VERSION,
        policy_schema_version=policy.schema_version,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_hash=policy_hash or policy_content_hash(policy),
        job_title=policy.job.title,
        status=status,
        coverage_status=("DETERMINED" if scope else "INDETERMINATE"),
        semantic_requirement_count=len(scope),
        directly_covered_count=len(covered),
        broad_only_count=len(broad),
        missing_positive_count=len(missing),
        unclassified_requirement_count=len(unclassified),
        orphan_group_count=len(orphan_groups),
        positive_coverage_rate=rate,
        dimension_coverage=dimension_coverage(policy, covered, broad, missing),
        issues=issues,
    )
