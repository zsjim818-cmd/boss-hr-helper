from copy import deepcopy
import json
from types import SimpleNamespace
import unittest

import numpy as np

from engine.policy_semantic_ranker import evaluate_policy_semantics
from engine.semantic_profiles import load_semantic_profiles
from schemas.job_policy import JobScoringPolicy
from storage.repositories.embeddings import pack_float32
from tests.policy_fixtures import offline_policy


def unit(size, index):
    value = np.zeros(size, dtype=np.float32)
    value[index] = 1
    return value


def policy_with_atomic_anchors(*, include_test=True, broad_java=False, negative_java=False):
    value = deepcopy(offline_policy("java_backend"))
    anchors = [
        {"anchor_id": "a_java", "requirement_ids": (["java", "transactions"]
          if broad_java else ["java"]), "polarity": "POSITIVE",
         "text": "独立实现Java服务并处理明确的服务边界。"},
        {"anchor_id": "a_sql", "requirement_ids": ["transactions"], "polarity": "POSITIVE",
         "text": "设计SQL事务边界并验证数据一致性。"},
        {"anchor_id": "a_spring", "requirement_ids": ["spring_route"], "polarity": "POSITIVE",
         "text": "使用Spring Boot交付真实后台服务。"},
        {"anchor_id": "a_quarkus", "requirement_ids": ["quarkus_route"], "polarity": "POSITIVE",
         "text": "使用Quarkus交付真实后台服务。"},
        {"anchor_id": "a_duty", "requirement_ids": ["duty"], "polarity": "POSITIVE",
         "text": "定位服务故障并与协作方完成问题闭环。"},
    ]
    if include_test:
        anchors.append({"anchor_id": "a_test", "requirement_ids": ["testing"],
                        "polarity": "POSITIVE", "text": "实现接口自动化测试并验证异常分支。"})
    if negative_java:
        anchors.append({"anchor_id": "n_java", "requirement_ids": ["java"],
                        "polarity": "NEGATIVE", "text": "只罗列Java名词但没有开发实现证据。"})
    value["semantic_anchors"] = anchors
    return JobScoringPolicy.model_validate(value)


def plan(policy):
    nodes = {item.requirement_id for item in policy.requirements} | {
        item.group_id for item in policy.requirement_groups}
    children = {member for group in policy.requirement_groups for member in group.members}
    return SimpleNamespace(policy=policy, root_ids=tuple(sorted(nodes - children)))


def anchor_rows(policy, vectors):
    result = []
    for anchor in policy.semantic_anchors:
        vector = vectors[anchor.anchor_id]
        blob, _ = pack_float32(vector, len(vector))
        result.append({
            "anchor_id": anchor.anchor_id,
            "requirement_ids_json": json.dumps(list(anchor.requirement_ids), ensure_ascii=False,
                                                separators=(",", ":")),
            "polarity": anchor.polarity,
            "anchor_text": anchor.text,
            "asset_id": "asset_" + anchor.anchor_id,
            "vector_blob": blob,
            "dimension": len(vector),
        })
    return result


def candidate_row(chunk_id, vector, *, path=None, experience=None, source="PROJECT_DESCRIPTION",
                  quality="PRACTICE", text=None, text_hash=None):
    blob, _ = pack_float32(vector, len(vector))
    quote = text or ("候选证据" + chunk_id)
    return {
        "chunk_id": chunk_id,
        "asset_id": "candidate_asset_" + chunk_id,
        "vector_blob": blob,
        "dimension": len(vector),
        "source_path": path or f"project_experience[0].description",
        "source_type": source,
        "experience_key": experience or f"project_experience[{chunk_id}]",
        "start_offset": 0,
        "end_offset": len(quote),
        "chunk_text": quote,
        "chunk_text_hash": text_hash or (chunk_id * 64)[:64],
        "evidence_quality_type": quality,
    }


class PolicySemanticRankerTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_semantic_profiles().semantic

    def test_dynamic_dimensions_and_any_choose_one_framework_route(self):
        policy = policy_with_atomic_anchors()
        names = [item.anchor_id for item in policy.semantic_anchors]
        vectors = {name: unit(6, index) for index, name in enumerate(names)}
        candidates = [
            candidate_row("j", vectors["a_java"]),
            candidate_row("s", vectors["a_sql"]),
            candidate_row("q", vectors["a_quarkus"]),
            candidate_row("d", vectors["a_duty"]),
            candidate_row("t", vectors["a_test"]),
        ]
        result = evaluate_policy_semantics(plan(policy), candidates, anchor_rows(policy, vectors), self.profile)
        self.assertEqual(result.semantic_score, 100.0)
        self.assertEqual(result.semantic_coverage, 1.0)
        self.assertEqual([item.dimension_id for item in result.dimension_scores],
                         [item.dimension_id for item in policy.scoring_dimensions])
        framework = [item for item in result.group_traces
                     if item.group_id == "alternative_routes"
                     and item.dimension_id == "service_design"]
        self.assertEqual(len(framework), 1)
        self.assertEqual(framework[0].selected_members, ["quarkus_route"])
        self.assertEqual(framework[0].member_scores["spring_route"], 0.0)
        self.assertEqual(framework[0].member_scores["quarkus_route"], 100.0)

    def test_top_k_uses_one_slot_per_experience_and_auxiliary_cannot_reach_full(self):
        policy = policy_with_atomic_anchors()
        names = [item.anchor_id for item in policy.semantic_anchors]
        vectors = {name: unit(6, index) for index, name in enumerate(names)}
        java = vectors["a_java"]
        candidates = [
            candidate_row("a", java, experience="project[0]", text="负责实现Java模块一"),
            candidate_row("b", java, experience="project[0]", text="负责实现Java模块二"),
            candidate_row("c", java, experience="project[1]", text="负责实现Java模块三"),
            candidate_row("d", java, experience="skills", source="SKILLS", quality="AUXILIARY",
                          text="Java"),
        ]
        result = evaluate_policy_semantics(plan(policy), candidates, anchor_rows(policy, vectors), self.profile)
        java_score = next(item for item in result.requirement_scores if item.requirement_id == "java")
        self.assertEqual(java_score.semantic_score, 100.0)
        self.assertEqual(len(java_score.selected_evidence), 2)
        self.assertEqual({item.experience_key for item in java_score.selected_evidence},
                         {"project[0]", "project[1]"})
        self.assertTrue(any(item.source_type == "SKILLS" for item in java_score.excluded_evidence))

        label_only = evaluate_policy_semantics(
            plan(policy), [candidates[-1]], anchor_rows(policy, vectors), self.profile)
        label_java = next(item for item in label_only.requirement_scores
                          if item.requirement_id == "java")
        self.assertEqual(label_java.semantic_score, 20.0)

    def test_negative_penalty_is_local_and_unrelated_history_does_not_reduce_project(self):
        policy = policy_with_atomic_anchors(negative_java=True)
        names = [item.anchor_id for item in policy.semantic_anchors]
        vectors = {name: unit(7, index) for index, name in enumerate(names)}
        positive = candidate_row("p", vectors["a_java"], experience="project[0]",
                                 text="负责实现Java后端服务")
        unrelated_negative = candidate_row("n", vectors["n_java"], experience="work[0]",
                                           text="历史经历只罗列Java名词")
        base = evaluate_policy_semantics(
            plan(policy), [positive], anchor_rows(policy, vectors), self.profile)
        mixed = evaluate_policy_semantics(
            plan(policy), [positive, unrelated_negative], anchor_rows(policy, vectors), self.profile)
        base_java = next(item for item in base.requirement_scores if item.requirement_id == "java")
        mixed_java = next(item for item in mixed.requirement_scores if item.requirement_id == "java")
        self.assertEqual(base_java.semantic_score, 100.0)
        self.assertEqual(mixed_java.semantic_score, 100.0)
        self.assertTrue(mixed_java.negative_evidence)
        self.assertTrue(all(item.local_negative_penalty == 0
                            for item in mixed_java.selected_evidence))

    def test_shared_anchor_is_partial_and_does_not_claim_atomic_match(self):
        policy = policy_with_atomic_anchors(broad_java=True)
        names = [item.anchor_id for item in policy.semantic_anchors]
        vectors = {name: unit(6, index) for index, name in enumerate(names)}
        result = evaluate_policy_semantics(
            plan(policy), [candidate_row("j", vectors["a_java"])],
            anchor_rows(policy, vectors), self.profile)
        java = next(item for item in result.requirement_scores if item.requirement_id == "java")
        self.assertEqual(java.scoring_status, "PARTIAL")
        self.assertEqual(java.confidence, "REVIEW")
        service = next(item for item in result.dimension_scores
                       if item.dimension_id == "service_design")
        self.assertNotIn("java", service.matched_requirements)

    def test_selected_broad_anchor_stays_partial_when_unused_direct_anchor_exists(self):
        value = policy_with_atomic_anchors().model_dump(mode="json")
        value["semantic_anchors"].append({
            "anchor_id": "a_java_broad",
            "requirement_ids": ["java", "transactions"],
            "polarity": "POSITIVE",
            "text": "实现Java服务并设计事务边界与数据一致性。",
        })
        policy = JobScoringPolicy.model_validate(value)
        names = [item.anchor_id for item in policy.semantic_anchors]
        vectors = {name: unit(len(names), index) for index, name in enumerate(names)}
        result = evaluate_policy_semantics(
            plan(policy), [candidate_row("broad", vectors["a_java_broad"])],
            anchor_rows(policy, vectors), self.profile)
        java = next(item for item in result.requirement_scores if item.requirement_id == "java")
        self.assertEqual(java.scoring_status, "PARTIAL")
        self.assertTrue(java.selected_evidence)
        self.assertTrue(all(item.association_kind == "MULTI_REQUIREMENT_BROAD"
                            for item in java.selected_evidence))

    def test_missing_positive_anchor_is_null_not_candidate_zero(self):
        policy = policy_with_atomic_anchors(include_test=False)
        names = [item.anchor_id for item in policy.semantic_anchors]
        vectors = {name: unit(5, index) for index, name in enumerate(names)}
        result = evaluate_policy_semantics(
            plan(policy), [candidate_row("j", vectors["a_java"])],
            anchor_rows(policy, vectors), self.profile)
        test_requirement = next(item for item in result.requirement_scores
                                if item.requirement_id == "testing")
        self.assertEqual(test_requirement.scoring_status, "POLICY_ANCHOR_MISSING")
        self.assertIsNone(test_requirement.semantic_score)
        dimension = next(item for item in result.dimension_scores
                         if item.dimension_id == "testing_quality")
        self.assertEqual(dimension.scoring_status, "POLICY_ANCHOR_MISSING")
        self.assertIsNone(dimension.semantic_score)
        self.assertLess(result.semantic_coverage, 1)


if __name__ == "__main__":
    unittest.main()
