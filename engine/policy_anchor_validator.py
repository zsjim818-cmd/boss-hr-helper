"""Stage 5.5 anchor-coverage checks; deterministic and strictly read-only.

A Policy declares which atomic requirements Tier 2 must evaluate through its
``scoring_dimensions[*].requirement_ids`` projections — the same projections the
Tier 2 ranker aggregates, so this mirrors the real evaluation scope instead of
guessing a routing that the schema does not express.  This module reports the
requirements that lack an independent positive semantic anchor.  A NEGATIVE
anchor is diagnostic only and can never substitute for a positive anchor, and a
broad anchor shared by several requirements cannot independently prove any one
of them (the Tier 2 ranker keeps such matches review-only for the same reason).

The module never mutates the reviewed policy, never repairs anything, and never
contacts a model or database.  Requirements that no evaluation layer references
at all (no dimension projection, no keyword group, no hard filter) are reported
as UNCLASSIFIED instead of being silently pushed into or out of the coverage
denominator.
"""
from __future__ import annotations

from schemas.job_policy import JobScoringPolicy
from schemas.policy_validation import PolicyDimensionCoverage, PolicyQualityIssue


MISSING_POSITIVE_ANCHOR = "MISSING_POSITIVE_ANCHOR"
BROAD_ONLY_POSITIVE_ANCHOR = "BROAD_ONLY_POSITIVE_ANCHOR"
UNCLASSIFIED_REQUIREMENT_SCOPE = "UNCLASSIFIED_REQUIREMENT_SCOPE"
UNKNOWN_PROJECTION_REQUIREMENT = "UNKNOWN_PROJECTION_REQUIREMENT"
UNKNOWN_ANCHOR_REQUIREMENT = "UNKNOWN_ANCHOR_REQUIREMENT"

_SUGGESTED_ACTIONS = {
    MISSING_POSITIVE_ANCHOR:
        "为该原子要求补充一条仅关联本要求的 POSITIVE 语义锚点；负向锚点只能作诊断，不能证明能力存在",
    BROAD_ONLY_POSITIVE_ANCHOR:
        "补充仅关联本要求的正向锚点，或由HR确认拆分共享锚点；共享相似度不能独立证明本要求",
    UNCLASSIFIED_REQUIREMENT_SCOPE:
        "人工核对该要求应属 Tier0/Tier1/Tier2 哪一层，补齐对应投影（硬过滤/关键词组/评分维度）或明确人工处理方式",
    UNKNOWN_PROJECTION_REQUIREMENT:
        "修正该评分维度投影，使其只引用已定义的原子要求；当前数据契约没有需求级评估范围字段，投影是唯一可靠依据",
    UNKNOWN_ANCHOR_REQUIREMENT:
        "修正该锚点的 requirement_ids，使其只引用已定义的原子要求；无效引用不能计为有效覆盖",
}


def _defined_requirement_ids(policy: JobScoringPolicy) -> set[str]:
    return {item.requirement_id for item in policy.requirements}


def semantic_scope_requirement_ids(policy: JobScoringPolicy) -> list[str]:
    """Atomic requirements declared Tier 2-evaluable by dimension projections.

    Only references that resolve to a defined atomic requirement enter the
    scope; invalid references are reported separately so they can neither
    inflate nor silently shrink the coverage denominator.
    """
    defined = _defined_requirement_ids(policy)
    scope: list[str] = []
    for dimension in policy.scoring_dimensions:
        for requirement_id in dimension.requirement_ids:
            if requirement_id in defined and requirement_id not in scope:
                scope.append(requirement_id)
    return scope


def dimensions_by_requirement(policy: JobScoringPolicy) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for dimension in policy.scoring_dimensions:
        for requirement_id in dimension.requirement_ids:
            ids = mapping.setdefault(requirement_id, [])
            if dimension.dimension_id not in ids:
                ids.append(dimension.dimension_id)
    return mapping


def hard_filter_requirement_ids(policy: JobScoringPolicy) -> set[str]:
    """Requirements evaluated by Tier 0 typed predicates."""
    return {condition.requirement_id for condition in policy.hard_filters}


def keyword_requirement_ids(policy: JobScoringPolicy) -> set[str]:
    """Requirements evaluated by Tier 1 keyword pools."""
    return {requirement_id for group in policy.keyword_groups
            for requirement_id in group.requirement_ids}


def unclassified_requirement_ids(policy: JobScoringPolicy) -> list[str]:
    """Requirements no evaluation layer references; conservatively excluded.

    The policy schema has no per-requirement evaluation-scope field, so a
    requirement referenced by no projection, keyword group or hard filter is
    reported as UNCLASSIFIED (a documented schema gap) instead of being
    silently classified or counted in any coverage denominator.
    """
    claimed = (set(semantic_scope_requirement_ids(policy))
               | hard_filter_requirement_ids(policy)
               | keyword_requirement_ids(policy))
    return [item.requirement_id for item in policy.requirements
            if item.requirement_id not in claimed]


def _positive_anchors(policy: JobScoringPolicy, requirement_id: str):
    return [anchor for anchor in policy.semantic_anchors
            if anchor.polarity == "POSITIVE" and requirement_id in anchor.requirement_ids]


def requirement_anchor_classification(policy: JobScoringPolicy) -> tuple[list[str], list[str], list[str]]:
    """Classify every in-scope requirement as direct / broad-only / missing.

    Deterministic ordering: policy declaration order for requirements, then
    first-seen order inside the dimension projections.
    """
    direct: list[str] = []
    broad: list[str] = []
    missing: list[str] = []
    for requirement_id in semantic_scope_requirement_ids(policy):
        positive = _positive_anchors(policy, requirement_id)
        if not positive:
            missing.append(requirement_id)
        elif any(len(anchor.requirement_ids) == 1 for anchor in positive):
            direct.append(requirement_id)
        else:
            broad.append(requirement_id)
    return direct, broad, missing


def directly_covered_requirement_ids(policy: JobScoringPolicy) -> list[str]:
    """Requirements with at least one single-requirement (atomic) positive anchor."""
    direct, _, _ = requirement_anchor_classification(policy)
    return direct


def broad_only_requirement_ids(policy: JobScoringPolicy) -> list[str]:
    """In-scope requirements whose positive anchors are all shared/broad."""
    _, broad, _ = requirement_anchor_classification(policy)
    return broad


def missing_positive_requirement_ids(policy: JobScoringPolicy) -> list[str]:
    """In-scope requirements without any positive anchor."""
    _, _, missing = requirement_anchor_classification(policy)
    return missing


def positive_anchor_coverage_issues(policy: JobScoringPolicy) -> list[PolicyQualityIssue]:
    direct, broad, missing = requirement_anchor_classification(policy)
    mapping = dimensions_by_requirement(policy)
    issues: list[PolicyQualityIssue] = []
    for requirement_id in missing:
        issues.append(PolicyQualityIssue(
            code=MISSING_POSITIVE_ANCHOR, severity="ERROR", requirement_id=requirement_id,
            dimension_ids=sorted(mapping.get(requirement_id, [])),
            message="该原子要求被声明为Tier2语义评价，却没有正向语义锚点；负向锚点不能替代正向证据",
            suggested_action=_SUGGESTED_ACTIONS[MISSING_POSITIVE_ANCHOR]))
    for requirement_id in broad:
        issues.append(PolicyQualityIssue(
            code=BROAD_ONLY_POSITIVE_ANCHOR, severity="WARNING", requirement_id=requirement_id,
            anchor_ids=sorted(anchor.anchor_id for anchor in _positive_anchors(policy, requirement_id)),
            dimension_ids=sorted(mapping.get(requirement_id, [])),
            message="该要求仅有同时关联多个原子要求的宽泛正向锚点，一次匹配不能独立证明本要求",
            suggested_action=_SUGGESTED_ACTIONS[BROAD_ONLY_POSITIVE_ANCHOR]))
    return issues


def reference_scope_issues(policy: JobScoringPolicy) -> list[PolicyQualityIssue]:
    """Invalid references and layer-less requirements; deterministic, read-only.

    An invalid dimension projection also makes that dimension's coverage
    INDETERMINATE (see dimension_coverage); an invalid anchor reference can
    never count as coverage for anything.
    """
    defined = _defined_requirement_ids(policy)
    issues: list[PolicyQualityIssue] = []
    for dimension in policy.scoring_dimensions:
        for requirement_id in dimension.requirement_ids:
            if requirement_id not in defined:
                issues.append(PolicyQualityIssue(
                    code=UNKNOWN_PROJECTION_REQUIREMENT, severity="ERROR",
                    requirement_id=requirement_id, dimension_ids=[dimension.dimension_id],
                    message="评分维度投影引用了未定义的原子要求，无法确认其语义评价覆盖",
                    suggested_action=_SUGGESTED_ACTIONS[UNKNOWN_PROJECTION_REQUIREMENT]))
    for anchor in policy.semantic_anchors:
        for requirement_id in anchor.requirement_ids:
            if requirement_id not in defined:
                issues.append(PolicyQualityIssue(
                    code=UNKNOWN_ANCHOR_REQUIREMENT, severity="ERROR",
                    requirement_id=requirement_id, anchor_ids=[anchor.anchor_id],
                    message="语义锚点关联了未定义的原子要求；无效引用不能计为有效覆盖",
                    suggested_action=_SUGGESTED_ACTIONS[UNKNOWN_ANCHOR_REQUIREMENT]))
    mapping = dimensions_by_requirement(policy)
    for requirement_id in unclassified_requirement_ids(policy):
        issues.append(PolicyQualityIssue(
            code=UNCLASSIFIED_REQUIREMENT_SCOPE, severity="WARNING", requirement_id=requirement_id,
            dimension_ids=sorted(mapping.get(requirement_id, [])),
            message="该要求未被任何评价层结构引用（无维度投影/关键词组/硬过滤），无法确定评价范围",
            suggested_action=_SUGGESTED_ACTIONS[UNCLASSIFIED_REQUIREMENT_SCOPE]))
    return issues


def dimension_coverage(policy: JobScoringPolicy, direct: list[str],
                       broad: list[str], missing: list[str]) -> list[PolicyDimensionCoverage]:
    """Per-dimension coverage; invalid projections yield None (INDETERMINATE)."""
    direct_set, broad_set, missing_set = set(direct), set(broad), set(missing)
    defined = _defined_requirement_ids(policy)
    result: list[PolicyDimensionCoverage] = []
    for dimension in policy.scoring_dimensions:
        # Diagnosis already rejects duplicate references; count unique here anyway.
        ids = list(dict.fromkeys(dimension.requirement_ids))
        invalid = [requirement_id for requirement_id in ids if requirement_id not in defined]
        valid = [requirement_id for requirement_id in ids if requirement_id in defined]
        rate = (round(sum(requirement_id in direct_set for requirement_id in valid) / len(valid), 6)
                if valid and not invalid else None)
        result.append(PolicyDimensionCoverage(
            dimension_id=dimension.dimension_id, requirement_ids=ids,
            invalid_requirement_ids=invalid, semantic_requirement_count=len(valid),
            direct_covered=sum(requirement_id in direct_set for requirement_id in valid),
            broad_only=sum(requirement_id in broad_set for requirement_id in valid),
            missing_positive=sum(requirement_id in missing_set for requirement_id in valid),
            direct_coverage_rate=rate))
    return result
