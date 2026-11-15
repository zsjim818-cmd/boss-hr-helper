"""Generic policy structure and static-analysis contracts; no network or model."""
from copy import deepcopy
import unittest

from pydantic import ValidationError

from config.policy_validation import (PolicyNotPublishable, assert_publishable,
    diagnose_policy, validate_dynamic_assessment, validate_publishable_policy)
from schemas.job_policy import JobScoringPolicy, PolicyCompileInput


def generic_policy():
    return {
        "schema_version": "job_scoring_policy_v1",
        "job": {"title": "项目协调", "summary": "根据实际项目需求开展沟通与协作"},
        "sources": {"jd_text": "必须能够独立完成需求沟通。有公开作品者优先。需求沟通又称需求访谈。", "hr_requirements": "必须具备大专及以上学历。"},
        "requirements": [
            {"requirement_id": "communication", "label": "独立需求沟通", "source_type": "JD",
             "source_text": "必须能够独立完成需求沟通。", "priority": "REQUIRED", "requirement_type": "SKILL", "needs_confirmation": False},
            {"requirement_id": "portfolio", "label": "公开作品", "source_type": "JD",
             "source_text": "有公开作品者优先。", "priority": "PREFERRED", "requirement_type": "EXPERIENCE", "needs_confirmation": False},
            {"requirement_id": "education", "label": "大专学历", "source_type": "HR",
             "source_text": "必须具备大专及以上学历。", "priority": "REQUIRED", "requirement_type": "EDUCATION", "needs_confirmation": False}],
        "requirement_groups": [
            {"group_id": "required", "label": "必要条件", "operator": "ALL", "priority": "REQUIRED", "members": ["communication", "education"],
             "source_type": "JD", "source_text": "必须能够独立完成需求沟通。", "needs_confirmation": False},
            {"group_id": "preferred", "label": "加分条件", "operator": "ALL", "priority": "PREFERRED", "members": ["portfolio"],
             "source_type": "JD", "source_text": "有公开作品者优先。", "needs_confirmation": False}],
        "hard_filters": [{"filter_id": "degree", "requirement_id": "education", "field": "education_level", "operator": "GTE",
                          "value_text": "大专", "value_number": None, "value_list": [], "unit": "level", "on_unknown": "REVIEW"}],
        "keyword_groups": [{"group_id": "communication_terms", "label": "沟通能力", "requirement_ids": ["communication"],
                            "keywords": [{"canonical": "需求沟通", "aliases": ["需求访谈"], "weight": 8.0}]}],
        "semantic_anchors": [{"anchor_id": "practical", "requirement_ids": ["communication"], "polarity": "POSITIVE", "text": "独立开展需求沟通并记录交付约定"}],
        "scoring_dimensions": [
            {"dimension_id": "delivery", "label": "实际执行", "description": "根据具体项目原文判断实际执行能力", "requirement_ids": ["communication"], "weight": 0.7,
             "rubric": [{"min_score": 0, "max_score": 49, "description": "缺少具体职责或可核验实施证据"},
                        {"min_score": 50, "max_score": 100, "description": "有明确职责和可核验的执行过程"}]},
            {"dimension_id": "portfolio_evidence", "label": "成果证据", "description": "根据原文作品材料判断结果证据充分性", "requirement_ids": ["portfolio"], "weight": 0.3,
             "rubric": [{"min_score": 0, "max_score": 49, "description": "没有说明具体作品的产出和责任"},
                        {"min_score": 50, "max_score": 100, "description": "作品说明完整且个人责任可以核验"}]}],
        "thresholds": {"coarse_pass": 50.0, "deep_review": 65.0, "deep_recommend": 85.0},
        "diagnostics": [],
        "provenance": {"compiler_version": "test-compiler", "prompt_version": "test-prompt", "validator_version": "policy-validator-v1", "model_version": "offline-fixture"}}


class GenericPolicySchemaTests(unittest.TestCase):
    def setUp(self):
        self.policy = generic_policy()

    def codes(self, policy=None):
        return {item.code for item in diagnose_policy(policy or self.policy)}

    def test_generic_policy_is_strict_and_publishable_without_job_specific_fields(self):
        validated = assert_publishable(self.policy)
        self.assertEqual(validated.job.title, "项目协调")
        self.assertEqual(self.codes(), set())
        self.policy["agent_route"] = []
        with self.assertRaises(ValidationError):
            JobScoringPolicy.model_validate(self.policy)

    def test_all_json_schema_object_fields_are_required_with_closed_objects(self):
        schema = JobScoringPolicy.model_json_schema()
        def walk(item):
            if isinstance(item, dict):
                if item.get("type") == "object":
                    self.assertFalse(item.get("additionalProperties", True))
                    self.assertEqual(set(item.get("required", [])), set(item.get("properties", {})))
                for value in item.values():
                    walk(value)
            elif isinstance(item, list):
                for value in item:
                    walk(value)
        walk(schema)

    def test_missing_fields_and_nonfinite_weights_are_rejected(self):
        missing = deepcopy(self.policy)
        del missing["hard_filters"][0]["value_number"]
        with self.assertRaises(ValidationError):
            JobScoringPolicy.model_validate(missing)
        self.policy["scoring_dimensions"][0]["weight"] = float("nan")
        with self.assertRaises(ValidationError):
            JobScoringPolicy.model_validate(self.policy)

    def test_original_input_is_exactly_preserved(self):
        expected = PolicyCompileInput(job_title="项目协调", **self.policy["sources"])
        self.assertFalse(diagnose_policy(self.policy, expected))
        expected = expected.model_copy(update={"jd_text": "Changed original JD"})
        self.assertIn("SOURCE_INPUT_MISMATCH", {item.code for item in diagnose_policy(self.policy, expected)})

    def test_source_quote_must_exist_in_declared_source(self):
        self.policy["requirements"][0]["source_text"] = "不存在的经历或要求"
        self.assertIn("SOURCE_QUOTE_NOT_FOUND", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy)

    def test_preferred_cannot_become_hard_filter_even_when_model_relabeled_required(self):
        preference = self.policy["requirements"][1]
        preference["priority"] = "REQUIRED"
        self.policy["hard_filters"][0]["requirement_id"] = preference["requirement_id"]
        self.assertIn("PREFERRED_PROMOTED_TO_REQUIRED", self.codes())
        self.assertIn("PREFERENCE_AS_HARD_FILTER", self.codes())

    def test_required_all_cannot_force_preferred_member(self):
        self.policy["requirement_groups"][0]["members"].append("portfolio")
        self.assertIn("GROUP_PROMOTES_PREFERENCE", self.codes())

    def test_required_all_can_reference_an_any_route(self):
        first = self.policy["requirements"][0]
        second = deepcopy(first)
        second["requirement_id"] = "alternative"
        self.policy["requirements"].append(second)
        route = deepcopy(self.policy["requirement_groups"][0])
        route.update(group_id="alternatives", operator="ANY", members=["communication", "alternative"])
        self.policy["requirement_groups"].append(route)
        self.policy["requirement_groups"][0]["members"] = ["alternatives", "education"]
        self.assertNotIn("CYCLIC_REQUIREMENT_GRAPH", self.codes())
        self.assertIsNotNone(assert_publishable(self.policy))

    def test_graph_cycles_and_undefined_members_are_rejected(self):
        self.policy["requirement_groups"][0]["members"] = ["required", "undefined"]
        self.assertIn("CYCLIC_REQUIREMENT_GRAPH", self.codes())
        self.assertIn("UNKNOWN_REQUIREMENT_REFERENCE", self.codes())

    def test_dimension_ids_are_unique_and_weights_sum_to_one(self):
        self.policy["scoring_dimensions"][1]["dimension_id"] = "delivery"
        self.policy["scoring_dimensions"][0]["weight"] = 0.9
        self.assertIn("DUPLICATE_DIMENSION_ID", self.codes())
        self.assertIn("INVALID_WEIGHT_SUM", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy)

    def test_rubric_must_cover_every_integer_score_once(self):
        self.policy["scoring_dimensions"][0]["rubric"][1]["min_score"] = 51
        self.assertIn("INVALID_RUBRIC_COVERAGE", self.codes())

    def test_inference_is_not_hard_evidence_and_requires_human_confirmation(self):
        requirement = self.policy["requirements"][0]
        requirement.update(source_type="INFERENCE", source_text="", priority="OPTIONAL", needs_confirmation=True)
        self.policy["requirement_groups"][0]["members"] = ["education"]
        self.assertIn("REQUIREMENT_NEEDS_CONFIRMATION", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy)
        self.assertIsNotNone(assert_publishable(self.policy, confirmed_requirement_ids=["communication"]))
        self.policy["hard_filters"][0]["requirement_id"] = "communication"
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy, confirmed_requirement_ids=["communication"])

    def test_conflicting_numeric_constraints_are_diagnosed(self):
        requirement = deepcopy(self.policy["requirements"][0])
        requirement.update(requirement_id="years", label="工作年限", requirement_type="EXPERIENCE")
        self.policy["requirements"].append(requirement)
        self.policy["hard_filters"] = [
            {"filter_id": "min_years", "requirement_id": "years", "field": "work_years", "operator": "GTE", "value_text": None, "value_number": 5.0, "value_list": [], "unit": "year", "on_unknown": "REVIEW"},
            {"filter_id": "max_years", "requirement_id": "years", "field": "work_years", "operator": "LTE", "value_text": None, "value_number": 2.0, "value_list": [], "unit": "year", "on_unknown": "REVIEW"}]
        self.assertIn("CONFLICTING_HARD_FILTERS", self.codes())

    def test_hard_filter_unknown_is_always_review_not_zero_or_fail(self):
        self.policy["hard_filters"][0]["on_unknown"] = "FAIL"
        with self.assertRaises(ValidationError):
            JobScoringPolicy.model_validate(self.policy)

    def test_projection_references_and_predicate_values_are_checked(self):
        self.policy["semantic_anchors"][0]["requirement_ids"] = ["not_present"]
        self.policy["hard_filters"][0]["value_number"] = 3.0
        self.assertIn("UNKNOWN_REQUIREMENT_REFERENCE", self.codes())
        self.assertIn("INVALID_PREDICATE_VALUE", self.codes())

    def test_model_diagnostics_cannot_be_erased_by_empty_business_diagnostics(self):
        self.policy["diagnostics"] = [{"code": "MODEL_CONFLICT", "severity": "CONFIRM", "message": "请确认原始要求冲突", "requirement_ids": []}]
        self.assertIn("MODEL_CONFLICT", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy, confirmed_requirement_ids=["communication", "education", "portfolio"])

    def test_dynamic_assessment_exactly_matches_policy_dimension_ids(self):
        assessment = {"dimensions": [{"dimension_id": item["dimension_id"], "score": 65.0,
            "evidence_quotes": [], "missing_evidence": ["缺少原文"], "confidence": 0.2}
            for item in self.policy["scoring_dimensions"]]}
        self.assertEqual(len(validate_dynamic_assessment(self.policy, assessment).dimensions), 2)
        assessment["dimensions"][0]["dimension_id"] = "undefined"
        with self.assertRaises(ValueError):
            validate_dynamic_assessment(self.policy, assessment)

    def test_repository_gate_rechecks_input_and_business_rules(self):
        source = {"job_title": "项目协调", **self.policy["sources"]}
        self.assertIsNotNone(validate_publishable_policy(self.policy, source))
        self.policy["scoring_dimensions"][0]["weight"] = 0.9
        with self.assertRaises(PolicyNotPublishable):
            validate_publishable_policy(self.policy, source)

    def salary_policy(self, quote, value=18.0):
        self.policy["sources"]["hr_requirements"] += quote
        self.policy["requirements"].append({"requirement_id": "salary", "label": "明确薪资上限", "source_type": "HR",
            "source_text": quote, "priority": "REQUIRED", "requirement_type": "COMPENSATION", "needs_confirmation": False})
        self.policy["requirement_groups"][0]["members"].append("salary")
        self.policy["hard_filters"].append({"filter_id": "salary_cap", "requirement_id": "salary", "field": "expected_salary",
            "operator": "LTE", "value_text": None, "value_number": value, "value_list": [], "unit": "month", "on_unknown": "REVIEW"})
        return self.policy

    def test_salary_numeric_grounding_normalizes_ten_thousands_and_yuan_to_cny_k(self):
        for quote in ("要求月薪不高于18K。", "要求月薪不高于1.8万元。", "要求月薪不高于18000元。"):
            self.policy = generic_policy()
            self.salary_policy(quote)
            self.assertNotIn("PREDICATE_VALUE_NOT_GROUNDED", self.codes(), quote)
            self.assertIsNotNone(assert_publishable(self.policy))

    def test_guessed_salary_and_salary_without_period_do_not_publish(self):
        self.salary_policy("要求预算不高于18K。", value=25.0)
        self.assertIn("PREDICATE_VALUE_NOT_GROUNDED", self.codes())
        self.assertIn("SALARY_PERIOD_NEEDS_CONFIRMATION", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy)

    def test_foreign_salary_is_not_executed_as_cny(self):
        self.salary_policy("要求月薪不高于18K美元。")
        self.assertIn("UNSUPPORTED_SALARY_CURRENCY", self.codes())
        self.assertIn("UNSUPPORTED_CURRENCY_HARD_FILTER", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy, confirmed_requirement_ids=["salary"])

    def test_education_higher_than_quoted_degree_is_rejected(self):
        self.policy["hard_filters"][0]["value_text"] = "硕士"
        self.assertIn("PREDICATE_VALUE_NOT_GROUNDED", self.codes())

    def test_filter_cannot_claim_work_years_from_education_requirement(self):
        self.policy["hard_filters"][0].update(field="work_years", value_text=None, value_number=3.0, unit="year")
        self.assertIn("PREDICATE_REQUIREMENT_TYPE_MISMATCH", self.codes())

    def test_required_any_also_cannot_promote_preferred_leaf(self):
        self.policy["requirement_groups"][0].update(operator="ANY", members=["communication", "portfolio"])
        self.assertIn("GROUP_PROMOTES_PREFERENCE", self.codes())

    def test_explicitly_required_choice_of_one_route_cannot_be_demoted_to_preferred(self):
        quote = "可替代实战路线：以下两条至少满足一条，不要求同时具备。"
        self.policy["sources"]["jd_text"] += quote
        route = deepcopy(self.policy["requirement_groups"][1])
        route.update(group_id="routes", operator="ANY", priority="PREFERRED", source_text=quote)
        self.policy["requirement_groups"].append(route)
        self.assertIn("REQUIRED_ALTERNATIVE_DEMOTED", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy)

    def test_explicit_choice_of_one_is_not_compiled_as_all(self):
        quote = "路线至少满足其中一种。"
        self.policy["sources"]["jd_text"] += quote
        self.policy["requirement_groups"][0].update(operator="ALL", source_text=quote)
        self.assertIn("ALTERNATIVE_RELATION_MISMATCH", self.codes())

    def test_preferred_choice_of_one_stays_preferred(self):
        quote = "加分项：以下至少具备一种更好。"
        self.policy["sources"]["jd_text"] += quote
        self.policy["requirement_groups"][1].update(operator="ANY", source_text=quote)
        self.assertNotIn("REQUIRED_ALTERNATIVE_DEMOTED", self.codes())

    def test_unambiguous_lower_and_upper_bounds_cannot_be_reversed(self):
        self.policy["hard_filters"][0]["operator"] = "LTE"
        self.assertIn("PREDICATE_DIRECTION_MISMATCH", self.codes())
        self.policy = generic_policy()
        self.salary_policy("要求月薪不高于18K。")
        self.policy["hard_filters"][1]["operator"] = "GTE"
        self.assertIn("PREDICATE_DIRECTION_MISMATCH", self.codes())

    def test_mixed_bounds_are_not_guessed_from_a_shared_quote(self):
        self.salary_policy("要求大专及以上学历，月薪不高于18K。")
        self.assertNotIn("PREDICATE_DIRECTION_MISMATCH", self.codes())

    def test_list_heading_below_is_not_an_upper_bound(self):
        quote = "以下要求：必须具备大专学历。"
        self.policy["sources"]["hr_requirements"] = quote
        self.policy["requirements"][2]["source_text"] = quote
        self.assertNotIn("PREDICATE_DIRECTION_MISMATCH", self.codes())

    def test_explicit_candidate_age_gender_dimensions_cannot_publish_in_either_language(self):
        for label in ("candidate age and gender", "年龄及婚育评分", "Age and gender fit"):
            self.policy = generic_policy()
            self.policy["scoring_dimensions"][0]["label"] = label
            self.assertIn("IRRELEVANT_PERSONAL_ATTRIBUTE_DIMENSION", self.codes(), label)
            with self.assertRaises(PolicyNotPublishable):
                assert_publishable(self.policy)

    def test_subjective_candidate_stability_and_retention_prediction_are_blocked(self):
        for description in ("根据候选人主观稳定性预测未来是否离职", "Predict candidate retention and score the expected tenure"):
            self.policy = generic_policy()
            self.policy["scoring_dimensions"][0]["description"] = description
            self.assertIn("IRRELEVANT_PERSONAL_ATTRIBUTE_DIMENSION", self.codes(), description)

    def test_sensitive_candidate_attributes_cannot_hide_in_a_rubric(self):
        self.policy["scoring_dimensions"][0]["rubric"][1]["description"] = "候选人年龄低于30岁时直接给满分"
        self.assertIn("IRRELEVANT_PERSONAL_ATTRIBUTE_DIMENSION", self.codes())

    def test_customer_stability_and_customer_demographic_work_are_not_personal_traits(self):
        for label, description in (("客户稳定性分析", "依据客户续费和需求变化分析客户稳定性"),
                                   ("客户画像分析", "根据客户年龄分布和用户需求完成客群画像分析"),
                                   ("服务稳定性", "根据测试和监控结果判断服务稳定性与恢复能力")):
            self.policy = generic_policy()
            self.policy["scoring_dimensions"][0].update(label=label, description=description)
            self.assertNotIn("IRRELEVANT_PERSONAL_ATTRIBUTE_DIMENSION", self.codes(), description)

    def test_explicit_exclusion_of_personal_attributes_is_allowed(self):
        for description in ("不得根据候选人年龄性别评分；只依据具体项目交付原文", "Do not score candidate age or gender; assess the documented work evidence"):
            self.policy = generic_policy()
            self.policy["scoring_dimensions"][0]["description"] = description
            self.assertNotIn("IRRELEVANT_PERSONAL_ATTRIBUTE_DIMENSION", self.codes(), description)

    def test_unrelated_or_contrasting_exclusions_do_not_hide_personal_attribute_scoring(self):
        for description in ("无需编程，但根据候选人年龄直接评分", "Do not assess the project history, but score candidate gender fit"):
            self.policy = generic_policy()
            self.policy["scoring_dimensions"][0]["description"] = description
            self.assertIn("IRRELEVANT_PERSONAL_ATTRIBUTE_DIMENSION", self.codes(), description)
