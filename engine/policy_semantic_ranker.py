"""Policy-driven Tier 2 semantics with local negatives and bounded Top-K."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from statistics import fmean

import numpy as np

from engine.embedding_vectors import cosine_matrix
from schemas.semantic import (
    RequirementSemanticScore,
    SemanticDimensionScore,
    SemanticEvidence,
    SemanticExecutionProfile,
    SemanticGroupTrace,
    SemanticMissingEvidence,
)
from storage.repositories.embeddings import unpack_float32


SEMANTIC_RANKER_VERSION = "policy-semantic-ranker-v1"


@dataclass(frozen=True)
class SemanticComputation:
    semantic_score: float
    semantic_coverage: float
    dimension_scores: tuple[SemanticDimensionScore, ...]
    requirement_scores: tuple[RequirementSemanticScore, ...]
    group_traces: tuple[SemanticGroupTrace, ...]
    selected_evidence: tuple[SemanticEvidence, ...]
    excluded_evidence: tuple[SemanticEvidence, ...]
    negative_evidence: tuple[SemanticEvidence, ...]
    missing_evidence: tuple[SemanticMissingEvidence, ...]
    review_reasons: tuple[str, ...]
    debug_trace: tuple[str, ...]


def _id(*parts) -> str:
    payload = "|".join(map(str, parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _mean(values):
    values = [float(value) for value in values if value is not None]
    return fmean(values) if values else None


def _source_weight(row, profile: SemanticExecutionProfile) -> float:
    weights = profile.source_weights
    field = {
        "WORK_RESPONSIBILITY": weights.work_responsibility,
        "PROJECT_DESCRIPTION": weights.project_description,
        "PROJECT_ACHIEVEMENT": weights.project_achievement,
        "THESIS_DESCRIPTION": weights.thesis_description,
        "WORK_TAGS": weights.work_tags,
        "SKILLS": weights.skills,
        "CERTIFICATION": weights.certification,
        "ADVANTAGE": weights.advantage,
    }[row["source_type"]]
    if row["evidence_quality_type"] in {"AUXILIARY", "SELF_DESCRIPTION"}:
        field = min(field, weights.work_tags if row["source_type"] in {
            "WORK_RESPONSIBILITY", "PROJECT_DESCRIPTION", "PROJECT_ACHIEVEMENT",
            "THESIS_DESCRIPTION"} else field)
    return float(field)


def _mapped(similarity: float, profile: SemanticExecutionProfile) -> float:
    return float(np.clip((similarity - profile.similarity_min)
                         / (profile.similarity_max - profile.similarity_min), 0.0, 1.0))


def _negative_penalty(positive: float, negative: float | None,
                      profile: SemanticExecutionProfile) -> float:
    if negative is None or negative <= profile.negative_threshold:
        return 0.0
    if negative < positive - profile.negative_margin:
        return 0.0
    return min(profile.negative_penalty_cap,
               max(0.0, (negative - profile.negative_baseline)
                   * profile.negative_penalty_weight))


def _unique_evidence(values):
    result = {}
    for item in values:
        result[item.evidence_id] = item
    return list(result.values())


def evaluate_policy_semantics(plan, candidate_rows, anchor_rows,
                              profile: SemanticExecutionProfile) -> SemanticComputation:
    """Compute only a soft Tier 2 score; this function has no routing authority."""
    policy = plan.policy
    candidates = [dict(row) for row in candidate_rows]
    stored_anchors = [dict(row) for row in anchor_rows]
    expected_anchors = {item.anchor_id: item for item in policy.semantic_anchors}
    if {row["anchor_id"] for row in stored_anchors} != set(expected_anchors):
        raise ValueError("Policy embedding assets do not match the bound Policy anchors")
    if not candidates:
        raise ValueError("Semantic scoring requires at least one traceable resume chunk")
    for row in stored_anchors:
        anchor = expected_anchors[row["anchor_id"]]
        if (row["anchor_text"] != anchor.text or row["polarity"] != anchor.polarity
                or json.loads(row["requirement_ids_json"]) != list(anchor.requirement_ids)):
            raise ValueError("Stored anchor metadata differs from the bound immutable Policy")

    candidate_vectors = [np.asarray(unpack_float32(row["vector_blob"], row["dimension"]))
                         for row in candidates]
    anchor_vectors = [np.asarray(unpack_float32(row["vector_blob"], row["dimension"]))
                      for row in stored_anchors]
    similarities = cosine_matrix(candidate_vectors, anchor_vectors)
    anchor_index = {row["anchor_id"]: index for index, row in enumerate(stored_anchors)}
    dimensions_by_requirement = {}
    for dimension in policy.scoring_dimensions:
        for requirement_id in dimension.requirement_ids:
            dimensions_by_requirement.setdefault(requirement_id, []).append(dimension.dimension_id)

    requirement_scores = []
    requirement_by_id = {}
    for requirement in policy.requirements:
        requirement_id = requirement.requirement_id
        positive = sorted((item for item in policy.semantic_anchors
                           if item.polarity == "POSITIVE" and requirement_id in item.requirement_ids),
                          key=lambda item: item.anchor_id)
        negative = sorted((item for item in policy.semantic_anchors
                           if item.polarity == "NEGATIVE" and requirement_id in item.requirement_ids),
                          key=lambda item: item.anchor_id)
        negative_evidence = []
        for chunk_index, row in enumerate(candidates):
            if not negative:
                continue
            best_negative = max(negative, key=lambda item: (
                float(similarities[chunk_index, anchor_index[item.anchor_id]]), item.anchor_id))
            negative_similarity = float(similarities[chunk_index, anchor_index[best_negative.anchor_id]])
            if negative_similarity <= profile.negative_threshold:
                continue
            negative_evidence.append(SemanticEvidence(
                evidence_id="neg_" + _id(requirement_id, row["chunk_id"], best_negative.anchor_id),
                chunk_id=row["chunk_id"], chunk_embedding_asset_id=row["asset_id"],
                anchor_id=best_negative.anchor_id,
                anchor_embedding_asset_id=stored_anchors[anchor_index[best_negative.anchor_id]]["asset_id"],
                anchor_polarity="NEGATIVE", source_path=row["source_path"],
                source_type=row["source_type"], experience_key=row["experience_key"],
                start=row["start_offset"], end=row["end_offset"], quote=row["chunk_text"],
                requirement_ids=list(best_negative.requirement_ids),
                dimension_ids=sorted(dimensions_by_requirement.get(requirement_id, [])),
                association_kind=("SINGLE_REQUIREMENT" if len(best_negative.requirement_ids) == 1
                                  else "MULTI_REQUIREMENT_BROAD"),
                similarity=round(negative_similarity, 6),
                mapped_similarity=round(max(0.0, (negative_similarity - profile.negative_threshold)
                                                  / (1 - profile.negative_threshold)), 6),
                source_weight=_source_weight(row, profile),
                confidence="REVIEW", selected=False,
                exclusion_reason="局部负向诊断；不能单独证明候选人缺少该能力",
            ))

        if not positive:
            missing = SemanticMissingEvidence(
                kind="POLICY_ANCHOR_MISSING", requirement_id=requirement_id,
                message="已发布Policy未为该原子要求提供正向语义锚点，不能把配置缺口记作候选人0分")
            result = RequirementSemanticScore(
                requirement_id=requirement_id, scoring_status="POLICY_ANCHOR_MISSING",
                policy_anchor_coverage=0.0, negative_similarity=(
                    max((item.similarity for item in negative_evidence), default=None)),
                negative_evidence=negative_evidence, missing_evidence=[missing],
                confidence="UNAVAILABLE",
                diagnostic_details=["negative anchors, if any, remain review-only without a positive anchor"],
            )
            requirement_scores.append(result)
            requirement_by_id[requirement_id] = result
            continue

        evidence_rows = []
        for chunk_index, row in enumerate(candidates):
            best_positive = max(positive, key=lambda item: (
                float(similarities[chunk_index, anchor_index[item.anchor_id]]), item.anchor_id))
            positive_similarity = float(similarities[chunk_index, anchor_index[best_positive.anchor_id]])
            best_negative = None
            negative_similarity = None
            if negative:
                best_negative = max(negative, key=lambda item: (
                    float(similarities[chunk_index, anchor_index[item.anchor_id]]), item.anchor_id))
                negative_similarity = float(similarities[chunk_index, anchor_index[best_negative.anchor_id]])
            mapped = _mapped(positive_similarity, profile)
            penalty = min(mapped, _negative_penalty(positive_similarity, negative_similarity, profile))
            weight = _source_weight(row, profile)
            net = max(0.0, mapped - penalty) * weight * 100.0
            broad = len(best_positive.requirement_ids) > 1
            confidence = "REVIEW" if broad else (
                "LOW" if row["evidence_quality_type"] in {"AUXILIARY", "SELF_DESCRIPTION"}
                else "MEDIUM")
            evidence = SemanticEvidence(
                evidence_id="pos_" + _id(requirement_id, row["chunk_id"], best_positive.anchor_id),
                chunk_id=row["chunk_id"], chunk_embedding_asset_id=row["asset_id"],
                anchor_id=best_positive.anchor_id,
                anchor_embedding_asset_id=stored_anchors[anchor_index[best_positive.anchor_id]]["asset_id"],
                anchor_polarity="POSITIVE", source_path=row["source_path"],
                source_type=row["source_type"], experience_key=row["experience_key"],
                start=row["start_offset"], end=row["end_offset"], quote=row["chunk_text"],
                requirement_ids=list(best_positive.requirement_ids),
                dimension_ids=sorted(dimensions_by_requirement.get(requirement_id, [])),
                association_kind=("MULTI_REQUIREMENT_BROAD" if broad else "SINGLE_REQUIREMENT"),
                similarity=round(positive_similarity, 6), mapped_similarity=round(mapped, 6),
                source_weight=weight,
                local_negative_similarity=(round(negative_similarity, 6)
                                           if negative_similarity is not None else None),
                local_negative_penalty=round(penalty, 6), confidence=confidence,
                selected=False, exclusion_reason=("BELOW_SIMILARITY_FLOOR" if net <= 0
                                                  else "NOT_SELECTED_BY_TOP_K"),
            )
            evidence_rows.append({"evidence": evidence, "net": net,
                                  "text_hash": row["chunk_text_hash"],
                                  "practice": row["evidence_quality_type"] in {
                                      "PRACTICE", "DELIVERABLE", "ACADEMIC_PRACTICE"}})

        # Exact repeated text and multiple windows from one experience cannot fill Top-K.
        deduplicated = {}
        excluded = []
        for item in sorted(evidence_rows, key=lambda value: (
                -value["net"], value["evidence"].source_path,
                value["evidence"].start, value["evidence"].evidence_id)):
            key = item["text_hash"]
            if key in deduplicated:
                excluded.append(item["evidence"].model_copy(update={
                    "exclusion_reason": "DUPLICATE_TEXT_LOWER_OR_EQUAL_SIGNAL"}))
            else:
                deduplicated[key] = item
        by_experience = {}
        for item in deduplicated.values():
            key = item["evidence"].experience_key
            current = by_experience.get(key)
            if current is None or (item["net"], item["evidence"].evidence_id) > (
                    current["net"], current["evidence"].evidence_id):
                if current is not None:
                    excluded.append(current["evidence"].model_copy(update={
                        "exclusion_reason": "SAME_EXPERIENCE_LOWER_SIGNAL"}))
                by_experience[key] = item
            else:
                excluded.append(item["evidence"].model_copy(update={
                    "exclusion_reason": "SAME_EXPERIENCE_LOWER_SIGNAL"}))

        aggregates = []
        for practice in (True, False):
            population = sorted((item for item in by_experience.values()
                                 if item["practice"] == practice and item["net"] > 0),
                                key=lambda item: (-item["net"], item["evidence"].evidence_id))
            ranked = population[:profile.top_k]
            weights = profile.top_k_weights[:len(ranked)]
            score = (sum(item["net"] * weight for item, weight in zip(ranked, weights))
                     / sum(weights)) if weights else 0.0
            aggregates.append((score, practice, ranked, population[profile.top_k:]))
        chosen = sorted(aggregates, key=lambda item: (-item[0], not item[1]))[0]
        selected = [item["evidence"].model_copy(update={
            "selected": True, "selection_reason": "TOP_K_EXPERIENCE_SLOT",
            "exclusion_reason": None}) for item in chosen[2]]
        for score, practice, ranked, overflow in aggregates:
            if (score, practice) != (chosen[0], chosen[1]):
                excluded.extend(item["evidence"].model_copy(update={
                    "exclusion_reason": "WEAKER_PRACTICE_OR_AUXILIARY_POPULATION"}) for item in ranked)
            excluded.extend(item["evidence"].model_copy(update={
                "exclusion_reason": "OUTSIDE_TOP_K"}) for item in overflow)
        selected_ids = {item.evidence_id for item in selected}
        excluded_ids = {item.evidence_id for item in excluded}
        excluded.extend(item["evidence"] for item in evidence_rows
                        if item["evidence"].evidence_id not in selected_ids | excluded_ids)

        # A direct anchor merely existing in the Policy is not enough: the
        # evidence actually selected by Top-K must contain an atomic mapping.
        # Otherwise a stronger broad anchor could incorrectly promote an
        # ambiguous multi-requirement signal to SCORED.
        broad_only = bool(selected) and all(
            item.association_kind == "MULTI_REQUIREMENT_BROAD" for item in selected)
        score = round(chosen[0], 2)
        if not selected:
            status, confidence = "NO_EVIDENCE", "LOW"
            missing = [SemanticMissingEvidence(
                kind="CANDIDATE_EVIDENCE_MISSING", requirement_id=requirement_id,
                message="有效锚点存在，但候选人文本相似度未超过当前映射下限")]
        elif broad_only:
            status, confidence = "PARTIAL", "REVIEW"
            missing = [SemanticMissingEvidence(
                kind="AMBIGUOUS_MAPPING", requirement_id=requirement_id,
                message="正向锚点同时关联多个原子要求，只能作为共享软信号，不能独立证明本要求满足")]
        else:
            status = "SCORED"
            confidence = "MEDIUM" if any(item.confidence == "MEDIUM" for item in selected) else "LOW"
            missing = []
        result = RequirementSemanticScore(
            requirement_id=requirement_id, scoring_status=status,
            positive_similarity=round(max(item["evidence"].similarity for item in evidence_rows), 6),
            negative_similarity=max((item.similarity for item in negative_evidence), default=None),
            semantic_score=score, policy_anchor_coverage=1.0,
            selected_evidence=selected, excluded_evidence=_unique_evidence(excluded),
            negative_evidence=negative_evidence, missing_evidence=missing,
            confidence=confidence,
            diagnostic_details=["score is bounded Top-K similarity, not skill probability"],
        )
        requirement_scores.append(result)
        requirement_by_id[requirement_id] = result

    groups = {item.group_id: item for item in policy.requirement_groups}
    requirements = {item.requirement_id for item in policy.requirements}
    group_traces = []

    def select_node(node_id, target, dimension_id):
        if node_id in requirements:
            if node_id not in target:
                return set(), set(), None, 0.0
            score = requirement_by_id[node_id].semantic_score
            return {node_id}, {node_id}, score, 1.0 if score is not None else 0.0
        group = groups[node_id]
        children = {}
        for member in group.members:
            selected, all_leaves, score, coverage = select_node(member, target, dimension_id)
            if all_leaves:
                children[member] = (selected, all_leaves, score, coverage)
        if not children:
            return set(), set(), None, 0.0
        member_scores = {member: value[2] for member, value in children.items()}
        if group.operator == "ANY":
            available = [member for member, value in children.items() if value[2] is not None]
            chosen_member = (sorted(available, key=lambda member: (-children[member][2], member))[0]
                             if available else sorted(children)[0])
            chosen_members = [chosen_member]
            selected, _, score, coverage = children[chosen_member]
            reason = "ANY保留全部分支诊断，选择可评分软分最高分支；同分按ID稳定选择"
        else:
            chosen_members = sorted(children)
            selected = set().union(*(value[0] for value in children.values()))
            score = _mean(requirement_by_id[item].semantic_score for item in selected)
            coverage = (sum(requirement_by_id[item].semantic_score is not None for item in selected)
                        / len(selected)) if selected else 0.0
            reason = "ALL保留所有相关成员；缺失成员保留coverage缺口，不由单个高分覆盖"
        all_leaves = set().union(*(value[1] for value in children.values()))
        group_traces.append(SemanticGroupTrace(
            group_id=group.group_id, dimension_id=dimension_id, operator=group.operator,
            members=sorted(children), selected_members=chosen_members,
            member_scores={key: (round(value, 2) if value is not None else None)
                           for key, value in member_scores.items()},
            semantic_score=round(score, 2) if score is not None else None,
            coverage=round(coverage, 6),
            scoring_status=("UNAVAILABLE" if score is None else
                            ("SCORED" if coverage == 1 else "PARTIAL")),
            reason=reason,
        ))
        return selected, all_leaves, score, coverage

    dimension_scores = []
    for dimension in policy.scoring_dimensions:
        target = set(dimension.requirement_ids)
        selected_leaves = set()
        seen_leaves = set()
        for root in plan.root_ids:
            selected, all_leaves, _, _ = select_node(root, target, dimension.dimension_id)
            selected_leaves.update(selected)
            seen_leaves.update(all_leaves)
        # Defensive fallback for a valid atom not reachable from a malformed root projection.
        selected_leaves.update(target - seen_leaves)
        values = [requirement_by_id[item].semantic_score for item in selected_leaves
                  if requirement_by_id[item].semantic_score is not None]
        score = round(fmean(values), 2) if values else None
        coverage = (sum(requirement_by_id[item].semantic_score is not None for item in selected_leaves)
                    / len(selected_leaves)) if selected_leaves else 0.0
        selected_evidence = _unique_evidence(
            evidence for requirement_id in selected_leaves
            for evidence in requirement_by_id[requirement_id].selected_evidence)
        excluded_evidence = _unique_evidence(
            evidence for requirement_id in selected_leaves
            for evidence in requirement_by_id[requirement_id].excluded_evidence)
        negative_evidence = _unique_evidence(
            evidence for requirement_id in selected_leaves
            for evidence in requirement_by_id[requirement_id].negative_evidence)
        missing = [item for requirement_id in sorted(selected_leaves)
                   for item in requirement_by_id[requirement_id].missing_evidence]
        ambiguous = any(requirement_by_id[item].scoring_status == "PARTIAL"
                        for item in selected_leaves)
        if score is None:
            status, confidence = "POLICY_ANCHOR_MISSING", "UNAVAILABLE"
        elif coverage == 1 and not ambiguous:
            status = "SCORED"
            confidence = "MEDIUM" if selected_evidence else "LOW"
        else:
            status, confidence = "PARTIAL", "REVIEW"
        dimension_scores.append(SemanticDimensionScore(
            dimension_id=dimension.dimension_id, dimension_label=dimension.label,
            dimension_weight=dimension.weight, requirement_ids=list(dimension.requirement_ids),
            scoring_status=status, semantic_score=score,
            policy_anchor_coverage=round(coverage, 6),
            matched_requirements=sorted(item for item in selected_leaves
                if requirement_by_id[item].scoring_status == "SCORED"
                and (requirement_by_id[item].semantic_score or 0) > 0),
            selected_anchors=sorted({item.anchor_id for item in selected_evidence}),
            selected_chunks=sorted({item.chunk_id for item in selected_evidence}),
            evidence=selected_evidence, excluded_evidence=excluded_evidence,
            negative_evidence=negative_evidence, missing_evidence=missing,
            confidence=confidence,
        ))

    scored_dimensions = [item for item in dimension_scores if item.semantic_score is not None]
    if not scored_dimensions:
        raise ValueError("Bound Policy has no semantically executable dynamic dimension")
    available_weight = sum(item.dimension_weight for item in scored_dimensions)
    semantic_score = round(sum(item.dimension_weight * item.semantic_score
                               for item in scored_dimensions) / available_weight, 2)
    semantic_coverage = round(sum(item.dimension_weight * item.policy_anchor_coverage
                                  for item in dimension_scores), 6)
    selected = _unique_evidence(item for dimension in dimension_scores for item in dimension.evidence)
    excluded = _unique_evidence(item for dimension in dimension_scores for item in dimension.excluded_evidence)
    negatives = _unique_evidence(item for dimension in dimension_scores for item in dimension.negative_evidence)
    missing = [item for dimension in dimension_scores for item in dimension.missing_evidence]
    reviews = []
    if semantic_coverage < 1:
        reviews.append("已发布Policy的正向语义锚点未覆盖全部动态维度要求；总分仅描述可执行覆盖部分")
    if any(item.scoring_status == "PARTIAL" for item in requirement_scores):
        reviews.append("部分锚点同时关联多个原子要求；共享相似度不得解释为每项能力均已验证")
    if negatives:
        reviews.append("负向相似度仅在同文本块和同requirement范围内有界扣分，并保留人工核验")
    return SemanticComputation(
        semantic_score=semantic_score, semantic_coverage=semantic_coverage,
        dimension_scores=tuple(dimension_scores), requirement_scores=tuple(requirement_scores),
        group_traces=tuple(group_traces), selected_evidence=tuple(selected),
        excluded_evidence=tuple(excluded), negative_evidence=tuple(negatives),
        missing_evidence=tuple(missing), review_reasons=tuple(reviews),
        debug_trace=(f"ranker={SEMANTIC_RANKER_VERSION}",
                     f"matrix={len(candidates)}x{len(stored_anchors)}",
                     "semantic_score_is_not_probability_or_hiring_decision"),
    )
