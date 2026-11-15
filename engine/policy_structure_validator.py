"""Stage 5.5 Batch 2 structure checks; deterministic and strictly read-only.

Batch 1 proved the anchor-coverage facts; this module reviews the *shape* of a
compiled Policy: whether atomic requirements stay atomic, whether ALL/ANY
groups express the semantics their sources claim, and whether every emitted
requirement and group is actually wired into an evaluation layer.

Grounding decisions (all verified against the offline fixtures in
``tests/fixtures/job_compiler_inputs.json`` and the real Stage 3 Qwen policies
in ``reports/phase1_7_stage3/policies/``):

- Embedded alternatives ("X或Y") inside one requirement are only reported when
  the requirement is NOT a direct member of an ANY group: membership of an ANY
  group (or normalization's label-based or-lowering, see
  ``config/policy_normalization.py``) already expresses the choice structurally.
  RESPONSIBILITY requirements are excluded: a job duty legitimately describes a
  range of activities and is not a pass/fail capability gate.
- Tech conjunctions ("X和Y" both Latin tech tokens) are reported because such a
  requirement bundles two independently provable skills into one anchor target.
  Coordinators that commonly join verb phrases (并/并且/同时) and enumeration
  punctuation (、/，) are deliberately not checked: partial keyword scoring
  already covers plain lists and verb chaining is normal Chinese phrasing.
- A PREFERRED/OPTIONAL group containing REQUIRED members is an ERROR: the Tier 0
  evaluator skips a non-REQUIRED node's whole subtree
  (``engine/policy_hard_filter.py`` evaluate(), priority gate), silently
  dropping mandatory hard constraints — the mirror image of the existing
  GROUP_PROMOTES_PREFERENCE diagnostic.
- A group whose leaf closure intersects no evaluation layer (hard filters, any
  keyword group, any dimension projection) is reported as an orphan group: its
  ALL/ANY combination semantics are never consulted by any scoring path.  This
  mirrors Batch 1's UNCLASSIFIED_REQUIREMENT_SCOPE at the group level; the
  real b2b_sales policy ships exactly one such group (grp_bonus_items).

Like Batch 1, this module only records findings.  It never mutates the policy,
never repairs structure, and never contacts a model or database.
"""
from __future__ import annotations

import re

from schemas.job_policy import JobScoringPolicy
from schemas.policy_validation import PolicyQualityIssue


EMBEDDED_ALTERNATIVE_REQUIREMENT = "EMBEDDED_ALTERNATIVE_REQUIREMENT"
CONJOINED_TECH_REQUIREMENT = "CONJOINED_TECH_REQUIREMENT"
SINGLE_MEMBER_GROUP = "SINGLE_MEMBER_GROUP"
REDUNDANT_GROUP_WRAPPER = "REDUNDANT_GROUP_WRAPPER"
DUPLICATE_GROUP_MEMBERS = "DUPLICATE_GROUP_MEMBERS"
PREFERRED_GROUP_DEMOTES_REQUIRED = "PREFERRED_GROUP_DEMOTES_REQUIRED"
ALL_SEMANTICS_MISMATCH = "ALL_SEMANTICS_MISMATCH"
ORPHAN_REQUIREMENT_GROUP = "ORPHAN_REQUIREMENT_GROUP"
PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED = "PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED"

_TECH_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.]{1,}")
# Quantity idioms use 或 grammatically but express amounts, not alternatives.
_QUANTITY_IDIOM = re.compile(
    r"(?:一个|一种|一项|任一|至少一个|其一之一)(?:或(?:者)?(?:多个|几种|多种|多项|一次))|或多或少|或早或晚|或大或小")
# Coordinators that join two noun phrases.  Preceding-character blacklists
# remove common two-character words where 和/与 is the second character.
_CONJUNCTION = re.compile(r"(和|与|以及)")
_CONJ_PREV_BLACKLIST = {"和": set("饱谐平缓共和附"), "与": set("参给赋授")}
# Simultaneity phrases that contradict an ANY operator when present in a
# group's own source quote.  "不要求同时具备" must not match, hence the
# precise two-character tails.
_ALL_SEMANTICS = re.compile(r"同时满足|全部都要|都必须|均需|都要满足|缺一不可|以上全部|两者都要|都需要")

_SUGGESTED_ACTIONS = {
    EMBEDDED_ALTERNATIVE_REQUIREMENT:
        "把该要求按选择语义拆分为独立的原子要求并放入 ANY 组（或由HR确认该措辞不表达任一即可）",
    CONJOINED_TECH_REQUIREMENT:
        "把连接的两个技术能力拆分为独立原子要求，各自可被关键词/锚点独立验证；共享同一来源引用是被允许的",
    SINGLE_MEMBER_GROUP:
        "补齐该组的其他成员，或直接引用底层原子要求，消除无组合语义的单成员组",
    REDUNDANT_GROUP_WRAPPER:
        "移除该透传包装组，或把组合语义上提到真正承担选择的层级",
    DUPLICATE_GROUP_MEMBERS:
        "合并成员集相同的两个组；若两组的 operator/priority 不一致，必须先由HR明确真实语义",
    PREFERRED_GROUP_DEMOTES_REQUIRED:
        "把 REQUIRED 成员移出加分/可选组，或明确降级其优先级；Tier0 会跳过非必需组的整个子树，硬约束会被静默丢弃",
    ALL_SEMANTICS_MISMATCH:
        "来源引用表达同时满足语义时不应使用 ANY；由HR确认应改为 ALL 还是修订组来源引用",
    ORPHAN_REQUIREMENT_GROUP:
        "为该组的成员补齐硬过滤/关键词组/评分维度引用，或删除该死结构组；其 ALL/ANY 组合语义当前从未被执行",
    PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED:
        "为该加分要求接入关键词组，使其可在 Tier 1 获得部分加分信号；或由HR确认仅按深层评价处理",
}


def _issue(code, severity, message, *, requirement_id=None, group_id=None,
           anchor_ids=(), dimension_ids=()):
    return PolicyQualityIssue(code=code, severity=severity, message=message,
                              requirement_id=requirement_id, group_id=group_id,
                              anchor_ids=list(anchor_ids), dimension_ids=list(dimension_ids),
                              suggested_action=_SUGGESTED_ACTIONS[code])


def any_group_direct_members(policy: JobScoringPolicy) -> set[str]:
    """Atoms that are direct members of some ANY group."""
    return {member for group in policy.requirement_groups
            if group.operator == "ANY" for member in group.members}


_BOUNDARY = "。！？；;\n，,、"


def _segments_around(text: str, start: int, end: int, span: int = 16) -> tuple[str, str]:
    """Neighbourhood of a marker, clipped at clause boundaries so tokens from
    unrelated clauses never leak into the evidence for this marker."""
    left = text[max(0, start - span):start]
    right = text[end:end + span]
    cuts = [left.rfind(c) for c in _BOUNDARY]
    cut = max(cuts)
    if cut >= 0:
        left = left[cut + 1:]
    firsts = [right.find(c) for c in _BOUNDARY]
    firsts = [pos for pos in firsts if pos >= 0]
    if firsts:
        right = right[:min(firsts)]
    return left, right


def _has_alternative_marker(text: str) -> bool:
    cleaned = _QUANTITY_IDIOM.sub("", text)
    if "或者" in cleaned:
        return True
    for match in re.finditer(r"或", cleaned):
        left, right = _segments_around(cleaned, match.start(), match.end())
        if _TECH_TOKEN.search(left) and _TECH_TOKEN.search(right):
            return True
    return False


def _conjoined_tech_tokens(text: str) -> list[tuple[str, str]]:
    cleaned = _QUANTITY_IDIOM.sub("", text)
    pairs = []
    for match in _CONJUNCTION.finditer(cleaned):
        coordinator = match.group(1)
        if coordinator in _CONJ_PREV_BLACKLIST and cleaned[match.start() - 1:match.start()] in _CONJ_PREV_BLACKLIST[coordinator]:
            continue
        left, right = _segments_around(cleaned, match.start(), match.end())
        left_token = _TECH_TOKEN.search(left)
        right_token = _TECH_TOKEN.search(right)
        if left_token and right_token and (
                left_token.group().casefold() != right_token.group().casefold()):
            pairs.append((left_token.group(), right_token.group()))
    return pairs


def atomicity_issues(policy: JobScoringPolicy) -> list[PolicyQualityIssue]:
    """Report requirements whose own text carries non-atomic semantics.

    ANY-group membership exempts a requirement only from the embedded
    alternative check (that group already expresses the choice); a bundled
    "A和B" atom stays composite even inside an alternative branch.
    """
    alternative_exempt = any_group_direct_members(policy)
    issues: list[PolicyQualityIssue] = []
    for requirement in policy.requirements:
        if requirement.requirement_type == "RESPONSIBILITY":
            continue
        if (requirement.requirement_id not in alternative_exempt
                and _has_alternative_marker(requirement.source_text)):
            issues.append(_issue(
                EMBEDDED_ALTERNATIVE_REQUIREMENT, "WARNING",
                "该原子要求的来源引用内嵌『X或Y』型选择语义，单一要求的锚点无法表达任一即可",
                requirement_id=requirement.requirement_id))
        for first, second in _conjoined_tech_tokens(requirement.source_text):
            issues.append(_issue(
                CONJOINED_TECH_REQUIREMENT, "WARNING",
                f"该要求把两个技术能力（{first} 与 {second}）捆绑在同一锚点目标上，无法分别验证",
                requirement_id=requirement.requirement_id))
            break  # one finding per requirement is enough for review
    return issues


def _leaf_closure(policy: JobScoringPolicy) -> dict[str, set[str]]:
    """Leaf (atomic) sets reachable from every group; cycles were already
    rejected by the publish diagnostics, so visiting guards are defensive."""
    requirements = {item.requirement_id for item in policy.requirements}
    groups = {item.group_id: item for item in policy.requirement_groups}
    memo: dict[str, set[str]] = {}

    def closure(key: str, seen: frozenset[str]) -> set[str]:
        if key in memo:
            return memo[key]
        if key in requirements:
            return {key}
        if key not in groups or key in seen:
            return set()
        leaves: set[str] = set()
        for member in groups[key].members:
            leaves |= closure(member, seen | {key})
        memo[key] = leaves
        return leaves

    return {group_id: closure(group_id, frozenset()) for group_id in groups}


def referenced_requirement_ids(policy: JobScoringPolicy) -> set[str]:
    """Atoms referenced by any evaluation layer (Batch 1 classification)."""
    from engine.policy_anchor_validator import (
        hard_filter_requirement_ids,
        keyword_requirement_ids,
        semantic_scope_requirement_ids,
    )
    return (set(semantic_scope_requirement_ids(policy))
            | hard_filter_requirement_ids(policy)
            | keyword_requirement_ids(policy))


def orphan_group_ids(policy: JobScoringPolicy) -> list[str]:
    """Groups whose ALL/ANY semantics no evaluation layer ever consults."""
    referenced = referenced_requirement_ids(policy)
    return [group_id for group_id, leaves in sorted(_leaf_closure(policy).items())
            if not (leaves & referenced)]


def structure_issues(policy: JobScoringPolicy) -> list[PolicyQualityIssue]:
    groups = {item.group_id: item for item in policy.requirement_groups}
    requirements = {item.requirement_id: item for item in policy.requirements}
    issues: list[PolicyQualityIssue] = []
    for group in policy.requirement_groups:
        if len(group.members) == 1:
            issues.append(_issue(
                SINGLE_MEMBER_GROUP, "WARNING",
                "该组只有一个成员，ALL/ANY 组合语义为空",
                group_id=group.group_id))
        if len(group.members) == 1 and group.members[0] in groups:
            issues.append(_issue(
                REDUNDANT_GROUP_WRAPPER, "WARNING",
                f"该组仅透传另一个组（{group.members[0]}），不表达任何组合语义",
                group_id=group.group_id))
        if group.operator == "ANY" and _ALL_SEMANTICS.search(group.source_text):
            issues.append(_issue(
                ALL_SEMANTICS_MISMATCH, "WARNING",
                "组来源引用带同时满足语义，却使用了 ANY 操作符",
                group_id=group.group_id))
        if group.priority in ("PREFERRED", "OPTIONAL"):
            demoted = [member for member in group.members
                       if (requirements.get(member) or groups.get(member)) is not None
                       and (requirements.get(member) or groups.get(member)).priority == "REQUIRED"]
            if demoted:
                issues.append(_issue(
                    PREFERRED_GROUP_DEMOTES_REQUIRED, "ERROR",
                    f"加分/可选组包含 REQUIRED 成员（{', '.join(demoted)}）；Tier0 会跳过非必需组的整个子树",
                    requirement_id=demoted[0], group_id=group.group_id))
    signature: dict[tuple[str, ...], list] = {}
    for group in policy.requirement_groups:
        signature.setdefault(tuple(sorted(set(group.members))), []).append(group)
    for members, same in signature.items():
        if len(same) < 2:
            continue
        first = same[0]
        conflicting = [other.group_id for other in same[1:]
                       if (other.operator, other.priority) != (first.operator, first.priority)]
        issues.append(_issue(
            DUPLICATE_GROUP_MEMBERS, "ERROR" if conflicting else "WARNING",
            f"成员集完全相同的组：{', '.join(item.group_id for item in same)}",
            group_id=first.group_id))
    for group_id in orphan_group_ids(policy):
        issues.append(_issue(
            ORPHAN_REQUIREMENT_GROUP, "WARNING",
            "该组的全部叶子要求未被任何评价层（硬过滤/关键词组/评分维度）引用，组合语义从未被执行",
            group_id=group_id))
    return issues


def wiring_issues(policy: JobScoringPolicy,
                  unclassified: list[str]) -> list[PolicyQualityIssue]:
    """Preferred requirements must stay earnable in Tier 1 as bonus signals."""
    keyword_refs = {requirement_id for group in policy.keyword_groups
                    for requirement_id in group.requirement_ids}
    skipped = set(unclassified)
    issues: list[PolicyQualityIssue] = []
    for requirement in policy.requirements:
        if (requirement.priority == "PREFERRED" and requirement.requirement_id not in keyword_refs
                and requirement.requirement_id not in skipped):
            issues.append(_issue(
                PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED, "WARNING",
                "该加分要求未接入任何关键词组，无法在 Tier 1 产生部分加分信号",
                requirement_id=requirement.requirement_id))
    return issues


def structure_and_wiring_issues(policy: JobScoringPolicy,
                                unclassified: list[str]) -> list[PolicyQualityIssue]:
    return [*atomicity_issues(policy), *structure_issues(policy),
            *wiring_issues(policy, unclassified)]
