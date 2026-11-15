"""Generic alias proof and explicit boolean lowering; no job dictionaries/API."""
from copy import deepcopy
import hashlib
import unittest

from config.policy_normalization import (alias_is_grounded, alias_key,
    alternative_analysis, normalize_policy, normalization_diagnostics)
from config.policy_validation import PolicyNotPublishable, assert_publishable, diagnose_policy
from schemas.job_policy import JobScoringPolicy
from tests.test_policy_schema import generic_policy


def alternative_policy(label="使用ToolOne或ToolTwo完成需求沟通", source=None):
    policy = generic_policy()
    quote = source or ("必须" + label + "。")
    policy["sources"]["jd_text"] += quote
    policy["requirements"][0].update(label=label, source_text=quote)
    return policy


class PolicyNormalizationTests(unittest.TestCase):
    def codes(self, policy):
        return {item.code for item in diagnose_policy(policy)}

    def test_case_unicode_and_whitespace_variants_preserve_symbols(self):
        self.assertEqual(alias_key(" Ｓｐｒｉｎｇ\t Boot "), alias_key("springboot"))
        self.assertNotEqual(alias_key("C++"), alias_key("C#"))
        policy = JobScoringPolicy.model_validate(generic_policy())
        self.assertTrue(alias_is_grounded("Tool One", "toolone", policy.sources))
        self.assertFalse(alias_is_grounded("ToolOne", "ToolTwo", policy.sources))

    def test_explicit_original_parenthesis_and_naming_prove_alias(self):
        for quote in ("ToolOne（T1）", "T1(ToolOne)", "ToolOne简称T1", "ToolOne, also known as T1"):
            policy = generic_policy()
            policy["sources"]["jd_text"] += quote
            sources = JobScoringPolicy.model_validate(policy).sources
            self.assertTrue(alias_is_grounded("ToolOne", "T1", sources), quote)

    def test_unproven_alias_is_removed_with_trace_without_mutating_input(self):
        policy = generic_policy()
        policy["keyword_groups"][0]["keywords"][0]["aliases"] += ["DifferentTool"]
        original = deepcopy(policy)
        normalized = normalize_policy(policy)
        self.assertEqual(policy, original)
        self.assertEqual(normalized.keyword_groups[0].keywords[0].aliases, ["需求访谈"])
        warning = next(item for item in normalized.diagnostics if item.code == "ALIASES_REMOVED_UNGROUNDED")
        self.assertIn("DifferentTool", warning.message)
        self.assertEqual(warning.requirement_ids, ["communication"])

    def test_duplicate_aliases_are_removed_once(self):
        policy = generic_policy()
        policy["keyword_groups"][0]["keywords"][0]["aliases"] = ["需求访谈", "需求访谈"]
        normalized = normalize_policy(policy)
        self.assertEqual(normalized.keyword_groups[0].keywords[0].aliases, ["需求访谈"])
        self.assertIn("ALIASES_DEDUPLICATED", {item.code for item in normalized.diagnostics})

    def test_lowering_uses_old_id_for_any_and_original_tokens_for_leaves(self):
        raw = alternative_policy()
        normalized = normalize_policy(raw)
        group = next(item for item in normalized.requirement_groups if item.group_id == "communication")
        self.assertEqual(group.operator, "ANY")
        self.assertEqual(group.priority, "REQUIRED")
        leaves = [item for item in normalized.requirements if item.requirement_id in group.members]
        self.assertEqual({item.source_text for item in leaves}, {"ToolOne", "ToolTwo"})
        self.assertEqual({item.label for item in leaves}, {"使用ToolOne完成需求沟通", "使用ToolTwo完成需求沟通"})
        self.assertEqual(group.source_text, raw["requirements"][0]["source_text"])
        self.assertNotIn("communication", {item.requirement_id for item in normalized.requirements})
        self.assertIsNotNone(assert_publishable(normalized))

    def test_references_change_only_in_atomic_projections_and_old_parent_link_remains(self):
        raw = alternative_policy()
        normalized = normalize_policy(raw)
        group = next(item for item in normalized.requirement_groups if item.group_id == "communication")
        self.assertIn("communication", normalized.requirement_groups[0].members)
        for item in [*normalized.keyword_groups, *normalized.semantic_anchors, *normalized.scoring_dimensions]:
            self.assertNotIn("communication", item.requirement_ids)
        self.assertEqual(normalized.scoring_dimensions[0].requirement_ids, group.members)

    def test_multi_alternative_and_source_mismatch_require_confirmation(self):
        for label, source in (("使用ToolOne或ToolTwo或ToolThree完成任务", None),
                              ("使用ToolOne或ToolTwo完成任务", "必须使用ToolThree完成任务。")):
            normalized = normalize_policy(alternative_policy(label, source))
            self.assertIn("communication", {item.requirement_id for item in normalized.requirements})
            self.assertIn("AMBIGUOUS_EXPLICIT_ALTERNATIVE", self.codes(normalized))
            with self.assertRaises(PolicyNotPublishable):
                assert_publishable(normalized)

    def test_mixed_english_phrases_and_adjacent_and_are_not_guessed(self):
        for label in ("Python developer or Java developer", "Alpha and Beta or Gamma", "Alpha or Beta and Gamma"):
            raw = alternative_policy(label)
            analysis = alternative_analysis(JobScoringPolicy.model_validate(raw).requirements[0])
            self.assertEqual(analysis["status"], "ambiguous", label)

    def test_asymmetric_english_prose_is_not_guessed_as_two_names(self):
        for label in ("Python developer or Java", "Python or Java developer"):
            raw = alternative_policy(label)
            normalized = normalize_policy(raw)
            self.assertIn("communication", {item.requirement_id for item in normalized.requirements}, label)
            self.assertIn("AMBIGUOUS_EXPLICIT_ALTERNATIVE", self.codes(normalized), label)

    def test_title_case_multiword_names_lower_with_exact_source(self):
        raw = alternative_policy("使用Google Cloud或AWS完成交付")
        normalized = normalize_policy(raw)
        group = next(item for item in normalized.requirement_groups if item.group_id == "communication")
        leaves = [item for item in normalized.requirements if item.requirement_id in group.members]
        self.assertEqual({item.source_text for item in leaves}, {"Google Cloud", "AWS"})

    def test_composite_hard_filter_is_not_silently_rewritten(self):
        raw = alternative_policy()
        raw["hard_filters"][0]["requirement_id"] = "communication"
        normalized = normalize_policy(raw)
        self.assertIn("communication", {item.requirement_id for item in normalized.requirements})
        self.assertEqual(normalized.hard_filters[0].requirement_id, "communication")
        self.assertIn("ALTERNATIVE_HARD_FILTER_REFERENCE", self.codes(normalized))

    def test_ids_stay_short_and_do_not_overwrite_a_collision(self):
        raw = alternative_policy()
        old = "r" * 80
        raw["requirements"][0]["requirement_id"] = old
        for group in raw["requirement_groups"]:
            group["members"] = [old if key == "communication" else key for key in group["members"]]
        for item in [*raw["keyword_groups"], *raw["semantic_anchors"], *raw["scoring_dimensions"]]:
            item["requirement_ids"] = [old if key == "communication" else key for key in item["requirement_ids"]]
        collision = "alt_" + hashlib.sha256(f"{old}|toolone|0".encode()).hexdigest()[:24]
        existing = deepcopy(raw["requirements"][1])
        existing["requirement_id"] = collision
        raw["requirements"].append(existing)
        normalized = normalize_policy(raw)
        group = next(item for item in normalized.requirement_groups if item.group_id == old)
        self.assertNotIn(collision, group.members)
        self.assertTrue(all(len(key) <= 80 for key in group.members))
        self.assertEqual(next(item for item in normalized.requirements if item.requirement_id == collision).label, existing["label"])

    def test_pass_is_idempotent_and_does_not_fix_weights_or_rubric(self):
        raw = alternative_policy()
        raw["scoring_dimensions"][0]["weight"] = .9
        normalized = normalize_policy(raw)
        twice = normalize_policy(normalized)
        self.assertEqual(normalized.model_dump(mode="json"), twice.model_dump(mode="json"))
        self.assertEqual(normalized.scoring_dimensions[0].weight, .9)
        self.assertEqual([band.model_dump() for band in normalized.scoring_dimensions[0].rubric], raw["scoring_dimensions"][0]["rubric"])
        self.assertIn("INVALID_WEIGHT_SUM", self.codes(normalized))

    def test_missing_atomic_positive_anchors_are_safely_autofilled(self):
        policy = generic_policy()  # communication has an anchor, portfolio does not
        normalized = normalize_policy(policy)
        anchors = {(tuple(item.requirement_ids), item.polarity)
                   for item in normalized.semantic_anchors}
        self.assertIn((("portfolio",), "POSITIVE"), anchors)
        self.assertIn("NORMALIZED_POSITIVE_ANCHOR",
                      {item.code for item in normalized.diagnostics})
        self.assertNotIn("MISSING_POSITIVE_ANCHOR", self.codes(normalized))
        self.assertNotIn("portfolio", {
            tuple(item.requirement_ids) for item in
            JobScoringPolicy.model_validate(policy).semantic_anchors})

    def test_direct_repository_lint_blocks_unknown_alias_and_unlowered_alternative(self):
        raw = alternative_policy()
        raw["keyword_groups"][0]["keywords"][0]["aliases"] = ["DifferentTool"]
        issues = normalization_diagnostics(raw)
        self.assertEqual({item.code for item in issues}, {"UNGROUNDED_KEYWORD_ALIAS", "UNLOWERED_EXPLICIT_ALTERNATIVE"})
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(raw)
