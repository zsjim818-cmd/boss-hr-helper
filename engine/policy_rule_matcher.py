"""Job-independent Tier 1: traceable signals, conservative context and soft pools.

This module never loads a configuration file, queries a repository or initializes
a model. A keyword supports its declared dimension pool; it proves an atomic
requirement only when the Interpreter supplied a precise requirement mapping.
"""
from dataclasses import dataclass
import math
import re
import unicodedata

from schemas.prescreen import (GroupSelection, PreliminaryDimensionScore,
                               PrescreenEvidence, Tier1Result)


_CLAUSE_BOUNDARY = re.compile(r"[。！？!?\n；;，,]|但是|不过|然而|但|\bbut\b|\bhowever\b", re.I)
_NO_PRACTICE = re.compile(
    r"(?:没有|缺乏|缺少|无)(?:实际使用|使用|用|处理|开发|参与|负责|实践)[^。！？!?；;，,]{0,64}(?:经验|经历)|"
    r"尚未实践|未实践|没有(?:实际|实战|项目|实践|使用|开发|工作)*经验|无(?:实际|实战|项目|实践|使用|开发|工作)*经验|"
    r"未(?:实际)?使用(?:过)?|没用过|没做过|从未(?:实际)?使用|未做过项目|"
    r"\bno\s+(?:hands[- ]on|practical|project)\s+experience\b|\bnever\s+used\b|\bhave\s+not\s+used\b|\bhaven['’]t\s+used\b", re.I)
_NEGATED = re.compile(
    r"(?:没有|缺乏|缺少|无)[^。！？!?；;，,]{0,64}(?:相关经验|经验|经历|能力|基础)|"
    r"不熟悉|不具备|未掌握|不了解|不会|不懂|未接触(?:过)?|从未接触|没有|缺乏|缺少|不使用|"
    r"(?:没有|缺乏|缺少|无)[^。！？!?；;，,]{0,32}(?:相关经验|经验|经历|能力|基础)|"
    r"\bnot\s+(?:familiar|proficient|experienced|skilled)\b|\bdo\s+not\s+know\b|"
    r"\b(?:no|without)\b[^.;,!?:]{0,32}\bexperience\b", re.I)
_LEARNING = re.compile(r"(?:仅|只|正在|计划|准备|自|本人|目前|我)学习|(?<![\u4e00-\u9fff])学习|\b(?:learning|studying)\b", re.I)
_IDENTIFIER = r"[A-Za-z0-9_+#]"


@dataclass(frozen=True)
class _Source:
    kind: str
    path: str
    text: str


def _key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _pattern(value):
    """ASCII names retain identifier boundaries while Chinese prose may adjoin."""
    escaped = re.escape(value)
    left = rf"(?<!{_IDENTIFIER})" if value and value[0].isascii() and (value[0].isalnum() or value[0] in "_+#") else ""
    right = rf"(?!{_IDENTIFIER})" if value and value[-1].isascii() and (value[-1].isalnum() or value[-1] in "_+#") else ""
    return re.compile(left + escaped + right, re.I)


def _sources(resume):
    for i, work in enumerate(resume.work_experience):
        for field in ("responsibility", "performance"):
            text = getattr(work, field) or ""
            if text.strip():
                yield _Source("work", f"work_experience[{i}].{field}", text)
        for j, text in enumerate(work.keywords):
            if text.strip():
                yield _Source("work_tags", f"work_experience[{i}].keywords[{j}]", text)
    for i, project in enumerate(resume.project_experience):
        for field in ("description", "achievement"):
            text = getattr(project, field) or ""
            if text.strip():
                yield _Source("project", f"project_experience[{i}].{field}", text)
    for i, education in enumerate(resume.education):
        if education.thesis_description and education.thesis_description.strip():
            yield _Source("thesis", f"education[{i}].thesis_description", education.thesis_description)
    if isinstance(resume.skills, list):
        for i, text in enumerate(resume.skills):
            if text.strip():
                yield _Source("skills", f"skills[{i}]", text)
    elif resume.skills and resume.skills.strip():
        yield _Source("skills", "skills", resume.skills)
    for i, text in enumerate(resume.certifications):
        if text.strip():
            yield _Source("skills", f"certifications[{i}]", text)
    if resume.advantage and resume.advantage.strip():
        yield _Source("advantage", "advantage", resume.advantage)


def _clauses(text, profile):
    # Explicit new assertions/actions after a conjunction open an independent
    # context; a bare list such as '不熟悉X和Y' remains one negated enumeration.
    starters = sorted(set((*profile.practice_actions, *profile.assertion_markers,
                          *profile.learning_markers, "使用", "采用", "基于", "用", "有", "不熟悉", "没有", "未")), key=len, reverse=True)
    transition = re.compile(r"(?:并且|同时|然后|并|且|也|\band\b|\bwhile\b)(?=\s*(?:" +
                            "|".join(re.escape(item) for item in starters) + "))", re.I)
    boundaries = sorted([*_CLAUSE_BOUNDARY.finditer(text), *transition.finditer(text)], key=lambda item: item.start())
    result, previous = [], 0
    for boundary in [*boundaries, None]:
        if boundary is not None and boundary.start() < previous:
            continue
        end = boundary.start() if boundary is not None else len(text)
        start = previous
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            result.append((start, end, text[start:end]))
        if boundary is not None:
            previous = boundary.end()
    return result


def _enumeration_only(text, variants):
    for value in sorted(variants, key=len, reverse=True):
        text = _pattern(value).sub("", text)
    text = re.sub(r"\b(?:and|or|the|any)\b|以及|或者|相关|任何|实际|和|与|及|或|了|过", "", text, flags=re.I)
    return not re.sub(r"[\s:：、/|()（）\[\]{}\-]+", "", text)


def _scoped(marker, text, start, end, variants):
    """Bind a qualifier to the target or a plain coordinated keyword list."""
    if marker.start() <= start < marker.end():
        return True
    if marker.end() <= start:
        return _enumeration_only(text[marker.end():start], variants)
    if marker.start() >= end:
        # A direct suffix may qualify its own target; arbitrary later clauses do
        # not. 'X系统从未使用Y' does not establish that X itself was never used.
        gap = text[end:marker.start()]
        if any(item.start() >= marker.start() for value in variants for item in _pattern(value).finditer(text)):
            return False
        return bool(re.fullmatch(r"\s*(?:(?:相关|方面|项目|开发|使用|的|还|尚)\s*)*", gap))
    return False


def _learning_markers(text, profile):
    matches = list(_LEARNING.finditer(text))
    for value in profile.learning_markers:
        matches.extend(_pattern(value).finditer(text))
    for marker in matches:
        if marker.group().casefold() in ("learning", "studying"):
            previous = re.search(r"([A-Za-z]+)\s+$", text[:marker.start()])
            if previous and previous.group(1).casefold() not in {"i", "am", "is", "are", "currently", "still", "only", "just", "self", "started", "been"}:
                continue
        yield marker


def _has_marker(markers, text):
    return any(_pattern(marker).search(text) for marker in markers)


def _is_label_only(text, variants, profile):
    value = text.strip()
    for prefix in sorted(profile.label_prefixes, key=len, reverse=True):
        if value.casefold().startswith(prefix.casefold()):
            value = value[len(prefix):].lstrip(":： ")
            break
    for variant in sorted(variants, key=len, reverse=True):
        value = _pattern(variant).sub("", value)
    return not re.sub(r"[\s:：|/、,，;；()（）\[\]{}\-]+", "", value)


def _context(source, clause_index, clauses, match, variants, profile):
    start, end, text = clauses[clause_index]
    notes = []
    relative_start, relative_end = match.start() - start, match.end() - start
    action_names = sorted(set((*profile.practice_actions, "参与", "使用")), key=len, reverse=True)
    negated_action = re.compile(r"(?:并不|并未|尚未|从未|没有|未曾|不曾|未|不|\bdid\s+not\b|\bhave\s+not\b|\bnever\b|\bnot\b)\s*(?:" +
                                "|".join(re.escape(value) for value in action_names) + ")", re.I)
    def external(markers):
        return [item for item in markers if not (item.start() >= relative_start and item.end() <= relative_end)]
    no_practice = external(_NO_PRACTICE.finditer(text))
    negative = external([*_NEGATED.finditer(text), *negated_action.finditer(text)])
    if any(_scoped(item, text, relative_start, relative_end, variants) for item in no_practice):
        return "NO_PRACTICE", 0.0, ["原文明确说明未实际使用或缺少实践经验"]
    if any(_scoped(item, text, relative_start, relative_end, variants) for item in negative):
        return "NEGATED", 0.0, ["原文存在明确的否定能力/经验表述"]
    action = False
    for marker in profile.practice_actions:
        if any(not (item.start() >= relative_start and item.end() <= relative_end)
               for item in _pattern(marker).finditer(text)):
            action = True
            break
    learning = any(not (item.start() >= relative_start and item.end() <= relative_end)
                   and _scoped(item, text, relative_start, relative_end, variants)
                   for item in _learning_markers(text, profile))
    asserted = any(_scoped(item, text, relative_start, relative_end, variants)
                   and not any(qualifier.start() <= item.start() < qualifier.end()
                               or (qualifier.end() <= item.start() and not text[qualifier.end():item.start()].strip())
                               for qualifier in [*negative, *no_practice])
                   for marker in profile.assertion_markers for item in _pattern(marker).finditer(text))
    # A following elliptical qualifier such as "了解X，但未实践" qualifies X.
    if clause_index + 1 < len(clauses):
        following_start, _, following = clauses[clause_index + 1]
        gap = source.text[end:following_start]
        qualifier = _NO_PRACTICE.search(following)
        if (not re.search(r"[。！？!?\n；;]", gap) and qualifier and qualifier.start() <= 2
                and not any(_pattern(value).search(following) for value in variants)):
            return "NO_PRACTICE", 0.0, ["紧邻后句明确限制为未实践；不将学习自述当作实践"]
    if learning:
        if action:
            return "AMBIGUOUS", 0.0, ["同一局部语境同时出现学习与实施表述，需人工核验"]
        return "LEARNING", min(getattr(profile.source_weights, source.kind), profile.source_weights.work_tags), ["学习信号按已有辅助标签权重限制"]
    # Do not turn the second word of an explicitly negated enumeration into a
    # fabricated assertion merely because commas separated its first member.
    if clause_index and (_NEGATED.search(clauses[clause_index - 1][2]) or _NO_PRACTICE.search(clauses[clause_index - 1][2])):
        gap = source.text[clauses[clause_index - 1][1]:start]
        if not re.search(r"[。！？!?\n；;]", gap) and not action and not asserted:
            return "AMBIGUOUS", 0.0, ["前句为否定，当前片段缺少独立正向断言，需核验否定范围"]
    if (negative or no_practice) and not asserted:
        return "AMBIGUOUS", 0.0, ["同句存在其他对象的否定表述，目标能力缺少独立断言，需核验作用域"]
    base = getattr(profile.source_weights, source.kind)
    labels = _is_label_only(text, variants, profile)
    if source.kind in ("work", "project", "thesis"):
        if action and not labels:
            return "PRACTICE", base, ["具体职责/项目中的实施自述；不证明经历真实性"]
        if asserted:
            return "ASSERTED", min(base, profile.source_weights.skills), ["明确技能自述，未见具体实施动作"]
        return "MENTION", min(base, profile.source_weights.work_tags), ["仅提及或标签列表，不能获得实践来源权重"]
    if source.kind == "work_tags":
        return "MENTION", base, ["工作经历技术标签仅作辅助信号"]
    if source.kind == "skills":
        return "ASSERTED", base, ["技能/证书自述，不能当作项目实践"]
    if asserted or action or labels:
        return "ASSERTED", base, ["个人优势自述按独立辅助权重计算"]
    return "AMBIGUOUS", 0.0, ["个人优势只出现术语，无法可靠判断正向能力"]


def _pool(keywords, leaves):
    return [item for item in keywords
            if set(item.precise_requirement_ids or item.declared_requirement_ids) & leaves]


def _pool_score(keywords, best, maximum):
    budget = sum(float(item.weight) for item in keywords)
    matched = [item for item in keywords if _key(item.canonical) in best]
    matched_budget = sum(float(item.weight) for item in matched)
    raw = sum(float(item.weight) * best[_key(item.canonical)].source_weight for item in matched)
    quality = min(1.0, raw / (matched_budget * maximum)) if matched_budget else 0.0
    coverage = -math.expm1(-matched_budget / budget) / -math.expm1(-1.0) if budget else 0.0
    return min(100.0, 100.0 * coverage * quality), raw, budget, matched_budget, quality


def evaluate_policy_rules(plan, resume, hard_result=None):
    """Return a strict dynamic Tier 1 result from an already compiled policy."""
    policy, profile = plan.policy, plan.profile
    requirements = {item.requirement_id: item for item in policy.requirements}
    groups = {item.group_id: item for item in policy.requirement_groups}
    keywords = list(plan.keywords)
    variants = list(dict.fromkeys(value for item in keywords for value in (item.canonical, *item.aliases)))
    hits, by_canonical, reviews = [], {}, []
    for source in _sources(resume):
        clauses = _clauses(source.text, profile)
        for item in keywords:
            seen = set()
            for variant in (item.canonical, *item.aliases):
                for match in _pattern(variant).finditer(source.text):
                    location = (match.start(), match.end())
                    if location in seen:
                        continue
                    seen.add(location)
                    index = next((i for i, (start, end, _) in enumerate(clauses)
                                  if start <= match.start() and end >= match.end()), None)
                    if index is None:
                        continue
                    start, end, quote = clauses[index]
                    context, weight, notes = _context(source, index, clauses, match, variants, profile)
                    evidence = PrescreenEvidence(source_path=source.path, quote=quote, start=start, end=end,
                        source_kind=source.kind, context=context, canonical=item.canonical, matched_variant=variant,
                        requirement_ids=list(item.precise_requirement_ids), dimension_ids=list(item.dimension_ids),
                        source_weight=float(weight), notes=notes)
                    hits.append(evidence)
                    by_canonical.setdefault(_key(item.canonical), []).append(evidence)
    best = {}
    for canonical, candidates in by_canonical.items():
        positive = [item for item in candidates if item.source_weight > 0]
        if positive:
            best[canonical] = sorted(positive, key=lambda item: (-item.source_weight, item.source_path, item.start, item.end))[0]
        contexts = {item.context for item in candidates}
        if contexts & {"AMBIGUOUS", "LEARNING", "NEGATED", "NO_PRACTICE", "MENTION"}:
            reviews.append(f"关键词{candidates[0].canonical}存在弱证据/否定/待核验语境，详见keyword_evidence")
        if positive and contexts & {"NEGATED", "NO_PRACTICE"}:
            reviews.append(f"关键词{candidates[0].canonical}存在正负自述冲突；保留不同经历原文，不据此硬拒绝")
    for item in keywords:
        if _key(item.canonical) in best and not item.precise_requirement_ids:
            reviews.append(f"关键词{item.canonical}只有组级关联，计入维度软信号但不宣称相关原子要求全部满足")

    selections = []
    memo = {}
    def select(node, target, dimension_id=None):
        memo_key = (node, frozenset(target), dimension_id)
        if memo_key in memo:
            return memo[memo_key]
        if node in requirements:
            return {node} if node in target else set()
        group = groups[node]
        children = {member: select(member, target, dimension_id) for member in group.members}
        children = {member: leaves for member, leaves in children.items() if leaves}
        if not children:
            memo[memo_key] = set()
            return set()
        scores = {member: _pool_score(_pool(keywords, leaves), best, profile.maximum_source_weight)[0]
                  for member, leaves in children.items()}
        if group.operator == "ANY":
            selected = [sorted(children, key=lambda member: (-scores[member], member))[0]]
            reason = "先按维度要求投影，再选择有效分支中归一化软证据分最高者；相同分数按ID稳定选择"
        else:
            selected = list(children)
            reason = "同时保留全部成员，叶子与canonical按集合去重"
        leaves = set().union(*(children[member] for member in selected))
        memo[memo_key] = leaves
        if dimension_id is not None:
            selections.append(GroupSelection(group_id=node, operator=group.operator, dimension_id=dimension_id,
                selected_members=selected, member_scores={key: round(value, 6) for key, value in scores.items()}, reason=reason))
        return leaves
    active = set().union(*(select(root, set(requirements)) for root in plan.root_ids)) if plan.root_ids else set(requirements)
    dimensions = []
    used = set()
    hard_atoms = {item.requirement_id: item for item in hard_result.atomic_results} if hard_result else {}
    precise_positive = {requirement for item in keywords
                        if _key(item.canonical) in best and best[_key(item.canonical)].context in ("PRACTICE", "ASSERTED")
                        for requirement in item.precise_requirement_ids}
    objective_positive = {key for key, item in hard_atoms.items() if item.status == "PASS"}
    matched_requirements = precise_positive | objective_positive
    for dimension in policy.scoring_dimensions:
        target = set(dimension.requirement_ids)
        leaves = set().union(*(select(root, target, dimension.dimension_id) for root in plan.root_ids)) if plan.root_ids else target
        active.update(leaves)
        pool = _pool(keywords, leaves)
        score, raw, budget, matched_budget, quality = _pool_score(pool, best, profile.maximum_source_weight)
        evidence = []
        for item in pool:
            key = _key(item.canonical)
            if key in best:
                used.add(key)
                evidence.append(best[key].model_copy(update={"selected": True}))
        missing = [f"{key}: 缺少可精确归属的正向证据" for key in sorted(leaves - matched_requirements)]
        if not leaves:
            missing.append("本维度没有当前选中替代路线的原子要求；需核验策略维度与路线的关系")
        elif not budget:
            objective_only = all(key in hard_atoms and hard_atoms[key].filter_ids for key in leaves)
            if objective_only:
                passed = len(leaves & objective_positive)
                score, raw, quality = 100.0 * passed / len(leaves), float(passed), passed / len(leaves)
                evidence = [e for key in sorted(leaves) for e in hard_atoms[key].evidence]
            else:
                reviews.append(f"维度{dimension.dimension_id}没有可执行关键词池且不是纯客观条件，保留0分与缺失证据")
        dimensions.append(PreliminaryDimensionScore(dimension_id=dimension.dimension_id,
            dimension_label=dimension.label, dimension_weight=dimension.weight, raw_score=round(raw, 6),
            normalized_score=round(score, 2), matched_requirements=sorted(leaves & matched_requirements),
            evidence=evidence, missing_evidence=missing, keyword_budget=round(budget, 6),
            matched_budget=round(matched_budget, 6), evidence_quality=round(quality, 6)))
    final_hits = [item.model_copy(update={"selected": _key(item.canonical or "") in used and best.get(_key(item.canonical or "")) is item})
                  for item in hits]
    score = round(sum(item.dimension_weight * item.normalized_score for item in dimensions), 2)
    return Tier1Result(preliminary_score=score, dimension_scores=dimensions,
        matched_requirements=sorted(matched_requirements), unknown_requirements=sorted(active - matched_requirements),
        keyword_evidence=final_hits, review_reasons=list(dict.fromkeys(reviews)), group_selections=selections,
        canonical_count=len(keywords), matched_canonical_count=len(used))
