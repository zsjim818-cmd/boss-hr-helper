"""Offline, human-authored three-job compiler policy acceptance fixtures.

No API calls occur in this module. The separate live runner must record actual
Qwen responses and metrics before using the shared case acceptance assertions.
"""
from copy import deepcopy
import json
import unittest

from pydantic import ValidationError

from config.policy_validation import assert_publishable, diagnose_policy, validate_dynamic_assessment
from schemas.job_policy import DynamicPolicyAssessment, JobScoringPolicy, PolicyCompileInput
from tests.policy_fixtures import check_policy_matches_case, compiler_cases, offline_policy, policy_input


class GenericPolicyFixtureTests(unittest.TestCase):
    def test_three_distinct_sources_compile_input_contract(self):
        cases = compiler_cases()
        self.assertEqual(len(cases), 3)
        self.assertEqual(len({item["raw_jd_text"] for item in cases}), 3)
        for case in cases:
            with self.subTest(case=case["case_id"]):
                PolicyCompileInput.model_validate(policy_input(case["case_id"]))

    def test_ai_automation_policy_has_valid_provenance_and_generality(self):
        policy = offline_policy("ai_automation")
        self.assertEqual(diagnose_policy(policy, policy_input("ai_automation")), [])
        check_policy_matches_case(policy, "ai_automation")
        self.assertEqual(assert_publishable(policy).provenance.model_version, "NO_MODEL_CALLED")

    def test_java_backend_policy_has_valid_provenance_and_generality(self):
        policy = offline_policy("java_backend")
        self.assertEqual(diagnose_policy(policy, policy_input("java_backend")), [])
        check_policy_matches_case(policy, "java_backend")
        self.assertEqual(len(JobScoringPolicy.model_validate(policy).scoring_dimensions), 5)

    def test_sales_policy_has_valid_provenance_and_generality(self):
        policy = offline_policy("b2b_sales")
        self.assertEqual(diagnose_policy(policy, policy_input("b2b_sales")), [])
        check_policy_matches_case(policy, "b2b_sales")
        self.assertNotIn("education_level", {item["field"] for item in policy["hard_filters"]})

    def test_three_jobs_have_different_actual_policy_sections(self):
        policies = [offline_policy(case["case_id"]) for case in compiler_cases()]
        for field in ("hard_filters", "keyword_groups", "semantic_anchors", "scoring_dimensions"):
            with self.subTest(section=field):
                self.assertEqual(len({json.dumps(item[field], ensure_ascii=False, sort_keys=True) for item in policies}), 3)
        self.assertEqual(len({tuple(item["weight"] for item in policy["scoring_dimensions"]) for policy in policies}), 3)
        self.assertEqual(len({tuple(item["label"] for item in policy["requirements"]) for policy in policies}), 3)
        self.assertEqual(len({tuple(band["description"] for dimension in policy["scoring_dimensions"]
                                    for band in dimension["rubric"]) for policy in policies}), 3)

    def test_all_and_any_are_distinct_from_requirement_priority(self):
        for case in compiler_cases():
            policy = offline_policy(case["case_id"])
            groups = {group["group_id"]: group for group in policy["requirement_groups"]}
            with self.subTest(case=case["case_id"]):
                self.assertEqual(groups["core_skills"]["operator"], "ALL")
                self.assertEqual(groups["alternative_routes"]["operator"], "ANY")
                self.assertEqual(groups["alternative_routes"]["priority"], "REQUIRED")
                self.assertEqual(groups["preferred"]["priority"], "PREFERRED")
                self.assertIn("alternative_routes", groups["mandatory"]["members"])
                self.assertNotIn("preferred", groups["mandatory"]["members"])

    def test_preferred_items_never_enter_hard_filter_references(self):
        for case in compiler_cases():
            policy = offline_policy(case["case_id"])
            priorities = {item["requirement_id"]: item["priority"] for item in policy["requirements"]}
            with self.subTest(case=case["case_id"]):
                self.assertTrue(all(priorities[item["requirement_id"]] == "REQUIRED" for item in policy["hard_filters"]))
                for item in policy["hard_filters"]:
                    self.assertEqual(item["on_unknown"], "REVIEW")

    def test_same_dynamic_output_schema_accepts_all_three_dimension_sets(self):
        for case in compiler_cases():
            policy = offline_policy(case["case_id"])
            assessment = {"dimensions": [{"dimension_id": dimension["dimension_id"], "score": 65.0,
                                           "evidence_quotes": [], "missing_evidence": ["Offline structure test only"],
                                           "confidence": 0.2} for dimension in policy["scoring_dimensions"]]}
            with self.subTest(case=case["case_id"]):
                value = validate_dynamic_assessment(policy, assessment)
                self.assertIsInstance(value, DynamicPolicyAssessment)
                self.assertEqual(len(value.dimensions), len(policy["scoring_dimensions"]))

    def test_dynamic_output_rejects_missing_extra_and_duplicate_dimension_ids(self):
        policy = offline_policy("java_backend")
        items = [{"dimension_id": item["dimension_id"], "score": 65.0, "evidence_quotes": [],
                  "missing_evidence": [], "confidence": 0.2} for item in policy["scoring_dimensions"]]
        for wrong in (items[:-1], items + [dict(items[0])], [{**items[0], "dimension_id": "unrelated"}, *items[1:]]):
            with self.subTest(ids=[item["dimension_id"] for item in wrong]), self.assertRaises(ValueError):
                validate_dynamic_assessment(policy, {"dimensions": wrong})

    def test_policy_outer_schema_is_shared_and_has_no_role_named_fields(self):
        expected = set(JobScoringPolicy.model_fields)
        for case in compiler_cases():
            policy = offline_policy(case["case_id"])
            with self.subTest(case=case["case_id"]):
                self.assertEqual(set(policy), expected)
                self.assertEqual(set(JobScoringPolicy.model_validate(policy).model_dump()), expected)
        forbidden = {"agent", "rpa", "java", "sales", "engineering_foundation", "ai_automation_practice"}
        self.assertFalse(expected.intersection(forbidden))

    def test_inputs_and_fixtures_do_not_share_mutable_state(self):
        first = offline_policy("java_backend")
        first["scoring_dimensions"][0]["weight"] = 0.99
        self.assertEqual(offline_policy("java_backend")["scoring_dimensions"][0]["weight"], 0.3)
        cases = compiler_cases()
        cases[0]["raw_jd_text"] = "mutated"
        self.assertNotEqual(compiler_cases()[0]["raw_jd_text"], "mutated")

    def test_policy_rejects_extra_role_specific_output_fields(self):
        policy = deepcopy(offline_policy("b2b_sales"))
        policy["agent"] = {"required": True}
        with self.assertRaises(ValidationError):
            JobScoringPolicy.model_validate(policy)
