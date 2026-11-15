"""Pure objective execution against real strict Policy/Resume contracts."""
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from engine.policy_hard_filter import evaluate_hard_filters, parse_salary_interval, parse_year_interval
from schemas import ResumeData
from schemas.job_policy import JobScoringPolicy
from schemas.prescreen import ObjectiveFact, ObjectiveFacts, PrescreenExecutionProfile, Tier0Result
from tests.policy_fixtures import offline_policy


def resume(**changes):
    raw = {"name": "Synthetic candidate", "degree": "本科", "work_years": "5年",
           "job_status": "离职-随时到岗", "expectation": {"salary": "12K/月", "city": "北京"},
           "work_experience": [], "project_experience": [], "education": [], "skills": "",
           "_meta": {"candidate_key": "fixture", "encrypt_geek_id": "fixture"}}
    raw.update(changes)
    return ResumeData.model_validate(raw)


def condition(key, field, operator, value=None, *, unit=None, priority="REQUIRED", source="JD", pending=False):
    return {"key": key, "field": field, "operator": operator, "value": value, "unit": unit,
            "priority": priority, "source": source, "pending": pending}


def plan(conditions, *, groups=None, capabilities=(), profile=None):
    raw = offline_policy("java_backend")
    requirements, filters = [], []
    kinds = {"education_level": "EDUCATION", "work_years": "EXPERIENCE", "expected_salary": "COMPENSATION",
             "city": "LOCATION", "work_authorization": "OTHER", "job_status": "OTHER"}
    for item in conditions:
        key, value = item["key"], item["value"]
        requirements.append({"requirement_id": key, "label": key, "source_type": item["source"],
            "source_text": "" if item["source"] == "INFERENCE" else "必须满足" + key,
            "priority": item["priority"], "requirement_type": kinds.get(item["field"], "OTHER"), "needs_confirmation": item["pending"]})
        numeric = isinstance(value, (int, float))
        filters.append({"filter_id": "f_" + key, "requirement_id": key, "field": item["field"], "operator": item["operator"],
            "value_text": value if isinstance(value, str) else None, "value_number": float(value) if numeric else None,
            "value_list": value if isinstance(value, list) else [], "unit": item["unit"], "on_unknown": "REVIEW"})
    for key in capabilities:
        requirements.append({"requirement_id": key, "label": key, "source_type": "JD", "source_text": "必须具备" + key,
                             "priority": "REQUIRED", "requirement_type": "SKILL", "needs_confirmation": False})
    if groups is None:
        groups = [{"group_id": "root", "operator": "ALL", "members": [item["requirement_id"] for item in requirements]}]
    normalized = [{"label": item["group_id"], "priority": "REQUIRED", "source_type": "JD", "source_text": "明确岗位要求",
                   "needs_confirmation": False, **item} for item in groups]
    raw.update(requirements=requirements, hard_filters=filters, requirement_groups=normalized)
    key = requirements[0]["requirement_id"]
    for collection in (raw["keyword_groups"], raw["semantic_anchors"], raw["scoring_dimensions"]):
        for item in collection:
            item["requirement_ids"] = [key]
    referenced = {child for group in normalized for child in group["members"]}
    roots = tuple(item["requirement_id"] for item in requirements if item["requirement_id"] not in referenced) + tuple(
        item["group_id"] for item in normalized if item["group_id"] not in referenced)
    return SimpleNamespace(policy=JobScoringPolicy.model_validate(raw), profile=profile or PrescreenExecutionProfile(), root_ids=roots)


class PrescreenTier0Tests(unittest.TestCase):
    def check(self, condition_item, candidate=None, **options):
        return evaluate_hard_filters(plan([condition_item], profile=options.pop("profile", None)), candidate or resume(), **options)

    def test_all_any_recursive_logic(self):
        conditions = [condition("degree", "education_level", "GTE", "本科", unit="level"),
                      condition("years", "work_years", "GTE", 3, unit="year"),
                      condition("salary", "expected_salary", "LTE", 15, unit="month"),
                      condition("city", "city", "EQ", "北京")]
        graph = [{"group_id": "any", "operator": "ANY", "members": ["salary", "city"]},
                 {"group_id": "root", "operator": "ALL", "members": ["degree", "years", "any"]}]
        executable = plan(conditions, groups=graph)
        for salary, city, expected in (("20K/月", "北京", "PASS"), ("20K/月", "上海", "FAIL"), ("", "上海", "UNKNOWN")):
            with self.subTest(salary=salary, city=city):
                output = evaluate_hard_filters(executable, resume(expectation={"salary": salary, "city": city}))
                self.assertEqual(output.hard_filter_status, expected)

    def test_true_three_state_truth_tables_for_all_and_any(self):
        for operator in ("ALL", "ANY"):
            graph = [{"group_id": "root", "operator": operator, "members": ["degree", "years"]}]
            executable = plan([condition("degree", "education_level", "GTE", "本科", unit="level"),
                               condition("years", "work_years", "GTE", 3, unit="year")], groups=graph)
            for degree, degree_state in (("本科", "PASS"), ("大专", "FAIL"), ("", "UNKNOWN")):
                for years, years_state in (("5年", "PASS"), ("1年", "FAIL"), ("", "UNKNOWN")):
                    states = (degree_state, years_state)
                    expected = ("FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS") if operator == "ALL" else (
                        "PASS" if "PASS" in states else "UNKNOWN" if "UNKNOWN" in states else "FAIL")
                    with self.subTest(operator=operator, states=states):
                        self.assertEqual(evaluate_hard_filters(executable, resume(degree=degree, work_years=years)).hard_filter_status, expected)

    def test_mixed_any_unassessed_capability_cannot_cause_rejection(self):
        executable = plan([condition("degree", "education_level", "GTE", "博士", unit="level")], capabilities=["capability"],
            groups=[{"group_id": "root", "operator": "ANY", "members": ["degree", "capability"]}])
        output = evaluate_hard_filters(executable, resume())
        self.assertEqual(output.hard_filter_status, "UNKNOWN")
        self.assertEqual(output.failed_requirements, ["degree"])
        self.assertTrue(output.review_reasons)

    def test_nested_mixed_any_does_not_fabricate_a_pass(self):
        executable = plan([condition("degree", "education_level", "GTE", "本科", unit="level"),
                           condition("years", "work_years", "GTE", 10, unit="year")], capabilities=["capability"], groups=[
                {"group_id": "branch", "operator": "ALL", "members": ["degree", "capability"]},
                {"group_id": "root", "operator": "ANY", "members": ["branch", "years"]}])
        self.assertEqual(evaluate_hard_filters(executable, resume()).hard_filter_status, "UNKNOWN")

    def test_pure_capability_all_branch_does_not_poison_objective_pass(self):
        executable = plan([condition("degree", "education_level", "GTE", "本科", unit="level")], capabilities=["capability"])
        output = evaluate_hard_filters(executable, resume())
        self.assertEqual(output.hard_filter_status, "PASS")
        self.assertIn("capability", output.skipped_requirements)
        self.assertEqual(len(output.atomic_results), 2)

    def test_preferred_optional_and_inferred_cannot_hard_fail(self):
        for priority in ("PREFERRED", "OPTIONAL"):
            output = self.check(condition("degree", "education_level", "GTE", "博士", unit="level", priority=priority))
            self.assertEqual(output.hard_filter_status, "PASS")
            self.assertEqual(output.failed_requirements, [])
        for options in ({"source": "INFERENCE"}, {"pending": True}):
            output = self.check(condition("degree", "education_level", "GTE", "博士", unit="level", **options))
            self.assertEqual(output.hard_filter_status, "UNKNOWN")
            self.assertEqual(output.failed_requirements, [])

    def test_inferred_group_cannot_make_an_explicit_rejection(self):
        executable = plan([condition("degree", "education_level", "GTE", "博士", unit="level")], groups=[
            {"group_id": "root", "operator": "ALL", "members": ["degree"], "source_type": "INFERENCE", "source_text": ""}])
        self.assertEqual(evaluate_hard_filters(executable, resume()).hard_filter_status, "UNKNOWN")

    def test_degree_is_unknown_and_never_defaults_to_bachelor(self):
        output = self.check(condition("degree", "education_level", "GTE", "本科", unit="level"), resume(degree="", education=[]))
        self.assertEqual(output.hard_filter_status, "UNKNOWN")

    def test_degree_unifies_top_level_and_education_records(self):
        candidate = resume(degree="大专", education=[{"school": "Synthetic", "degree": "硕士"}])
        self.assertEqual(self.check(condition("degree", "education_level", "GTE", "本科", unit="level"), candidate).hard_filter_status, "PASS")
        output = self.check(condition("degree", "education_level", "EQ", "硕士", unit="level"), candidate)
        self.assertEqual(output.hard_filter_status, "PASS")
        self.assertIn("education[0].degree", [item.source_path for item in output.evidence])

    def test_degree_exact_ne_and_in_obey_operator(self):
        for operator, value, expected in (("EQ", "本科", "PASS"), ("NE", "本科", "FAIL"), ("IN", ["大专", "本科"], "PASS"), ("IN", ["硕士"], "FAIL")):
            with self.subTest(operator=operator):
                self.assertEqual(self.check(condition("degree", "education_level", operator, value, unit="level")).hard_filter_status, expected)

    def test_decimal_salary_full_interval_and_overlap(self):
        candidate = resume(expectation={"salary": "12.5-15K/月"})
        for budget, expected in ((12, "FAIL"), (13, "UNKNOWN"), (15, "PASS")):
            with self.subTest(budget=budget):
                self.assertEqual(self.check(condition("salary", "expected_salary", "LTE", budget, unit="month"), candidate).hard_filter_status, expected)

    def test_salary_missing_negotiable_and_invalid_units_are_not_zero(self):
        for salary in ("", "面议", "12-15", "15-12K/月", "12K-1.5万/月", "USD2000/月", "0K/月"):
            with self.subTest(salary=salary):
                output = self.check(condition("salary", "expected_salary", "LTE", 15, unit="month"), resume(expectation={"salary": salary}))
                self.assertEqual(output.hard_filter_status, "UNKNOWN")

    def test_salary_period_is_not_inferred_from_policy(self):
        candidate = resume(expectation={"salary": "12K"})
        condition_item = condition("salary", "expected_salary", "LTE", 15, unit="month")
        self.assertEqual(self.check(condition_item, candidate).hard_filter_status, "UNKNOWN")
        output = self.check(condition_item, candidate, profile=PrescreenExecutionProfile(salary_implicit_period="month"))
        self.assertEqual(output.hard_filter_status, "PASS")
        self.assertIn("显式执行配置", output.atomic_results[0].reason)

    def test_day_year_hour_salary_stays_in_original_period(self):
        for salary, unit, budget in (("300元/天", "day", .3), ("年薪18万", "year", 180), ("时薪80元", "hour", .08)):
            with self.subTest(salary=salary):
                candidate = resume(expectation={"salary": salary})
                self.assertEqual(self.check(condition("salary", "expected_salary", "LTE", budget, unit=unit), candidate).hard_filter_status, "PASS")
                self.assertEqual(self.check(condition("salary", "expected_salary", "LTE", 15, unit="month"), candidate).hard_filter_status, "UNKNOWN")

    def test_thirteenth_salary_is_recorded_without_annual_conversion(self):
        output = self.check(condition("salary", "expected_salary", "LTE", 15, unit="month"), resume(expectation={"salary": "12.5-15K/月·13薪"}))
        self.assertEqual(output.hard_filter_status, "PASS")
        self.assertIn("13薪", output.atomic_results[0].reason)

    def test_work_year_bounds_decimal_and_missing(self):
        for years, expected in (("3.5年", "PASS"), ("1-3年", "UNKNOWN"), ("1-2年", "FAIL"), ("3年以上", "PASS"), ("三年", "PASS"), ("", "UNKNOWN"), ("应届生", "UNKNOWN"), ("无工作经验", "FAIL")):
            with self.subTest(years=years):
                self.assertEqual(self.check(condition("years", "work_years", "GTE", 3, unit="year"), resume(work_years=years)).hard_filter_status, expected)

    def test_numeric_eq_ne_and_in_are_conservative_for_intervals(self):
        for operator, value, expected in (("EQ", 3, "UNKNOWN"), ("NE", 3, "UNKNOWN"), ("EQ", 5, "FAIL"), ("NE", 5, "PASS"), ("IN", ["3", "5"], "UNKNOWN"), ("IN", ["6"], "FAIL")):
            with self.subTest(operator=operator, value=value):
                self.assertEqual(self.check(condition("years", "work_years", operator, value, unit="year"), resume(work_years="1-3年")).hard_filter_status, expected)

    def test_exists_requires_a_known_fact_not_a_fake_zero(self):
        for field, unit, candidate in (("education_level", "level", resume(degree="")), ("work_years", "year", resume(work_years="")),
                                       ("expected_salary", "month", resume(expectation={"salary": "面议"})), ("city", None, resume(expectation=None))):
            with self.subTest(field=field):
                self.assertEqual(self.check(condition("fact", field, "EXISTS", unit=unit), candidate).hard_filter_status, "UNKNOWN")
        self.assertEqual(self.check(condition("years", "work_years", "EXISTS", unit="year"), resume(work_years="0年")).hard_filter_status, "PASS")

    def test_city_is_explicitly_expected_city_by_default(self):
        candidate = resume(expectation={"city": "北京市、上海", "salary": "12K/月"})
        output = self.check(condition("city", "city", "IN", ["上海", "深圳"]), candidate)
        self.assertEqual(output.hard_filter_status, "PASS")
        self.assertIn("不证明现居地", output.atomic_results[0].reason)
        self.assertEqual(self.check(condition("city", "city", "EQ", "北京"), candidate).hard_filter_status, "PASS")

    def test_current_city_requires_verified_same_snapshot_fact(self):
        candidate = resume(current_city="上海市")
        item = condition("city", "city", "EQ", "上海")
        profile = PrescreenExecutionProfile(city_basis="current")
        self.assertEqual(self.check(item, candidate, profile=profile).hard_filter_status, "UNKNOWN")
        facts = ObjectiveFacts(current_city=ObjectiveFact(value="上海市", quote="上海市", source_path="current_city"))
        self.assertEqual(self.check(item, candidate, profile=profile, facts=facts).hard_filter_status, "PASS")

    def test_work_authorization_is_not_inferred_and_missing_is_unknown(self):
        item = condition("auth", "work_authorization", "EQ", "有合法工作许可")
        self.assertEqual(self.check(item).hard_filter_status, "UNKNOWN")
        for value, expected in (("有工作许可", "PASS"), ("无工作许可", "FAIL"), ("需要其他证明", "UNKNOWN")):
            facts = ObjectiveFacts(work_authorization=ObjectiveFact(value=value, quote=value, source_path="work_authorization"))
            with self.subTest(value=value):
                self.assertEqual(self.check(item, resume(work_authorization=value), facts=facts).hard_filter_status, expected)

    def test_forged_supplemental_fact_is_unknown(self):
        facts = ObjectiveFacts(work_authorization=ObjectiveFact(value="有", quote="有", source_path="work_authorization"))
        output = self.check(condition("auth", "work_authorization", "EQ", "有"), resume(work_authorization="无"), facts=facts)
        self.assertEqual(output.hard_filter_status, "UNKNOWN")
        self.assertEqual(output.evidence, [])

    def test_job_status_equality_ne_and_known_aliases(self):
        candidate = resume(job_status="在职看机会")
        self.assertEqual(self.check(condition("status", "job_status", "EQ", "在职-考虑机会"), candidate).hard_filter_status, "PASS")
        self.assertEqual(self.check(condition("status", "job_status", "NE", "在职-暂不考虑"), candidate).hard_filter_status, "PASS")
        self.assertEqual(self.check(condition("status", "job_status", "EQ", "未知业务状态"), candidate).hard_filter_status, "UNKNOWN")

    def test_collection_failure_does_not_hard_fail_on_residual_values(self):
        output = self.check(condition("degree", "education_level", "GTE", "博士", unit="level"), resume(ok=False, degree="大专"))
        self.assertEqual(output.hard_filter_status, "UNKNOWN")

    def test_every_evidence_span_locates_in_original_field(self):
        candidate = resume(education=[{"degree": "硕士", "school": "Synthetic"}])
        conditions = [condition("degree", "education_level", "GTE", "本科", unit="level"),
                      condition("years", "work_years", "GTE", 3, unit="year"),
                      condition("salary", "expected_salary", "LTE", 15, unit="month")]
        output = evaluate_hard_filters(plan(conditions), candidate)
        raw = candidate.model_dump(mode="json", by_alias=True)
        for evidence in output.evidence:
            value = raw["education"][0]["degree"] if evidence.source_path == "education[0].degree" else raw["expectation"]["salary"] if evidence.source_path == "expectation.salary" else raw[evidence.source_path]
            self.assertEqual(value[evidence.start:evidence.end], evidence.quote)
        self.assertEqual(Tier0Result.model_validate_json(output.model_dump_json()), output)

    def test_input_policy_and_resume_are_not_mutated(self):
        executable = plan([condition("salary", "expected_salary", "LTE", 15, unit="month")])
        candidate = resume()
        before_policy = deepcopy(executable.policy.model_dump())
        before_resume = deepcopy(candidate.model_dump())
        first = evaluate_hard_filters(executable, candidate)
        second = evaluate_hard_filters(executable, candidate)
        self.assertEqual(first, second)
        self.assertEqual(executable.policy.model_dump(), before_policy)
        self.assertEqual(candidate.model_dump(), before_resume)

    def test_field_and_operator_whitelists_reject_code_strings(self):
        with self.assertRaises(ValidationError):
            plan([condition("bad", "__import__('os')", "EQ", "x")])
        with self.assertRaises(ValidationError):
            plan([condition("bad", "work_years", "eval", 2, unit="year")])

    def test_parser_bounds_are_valid_and_pay_period_explicit(self):
        self.assertEqual(parse_year_interval("3年6个月"), (3.5, 3.5))
        self.assertIsNone(parse_year_interval("5-3年"))
        self.assertIsNone(parse_year_interval("3年15个月"))
        self.assertIsNone(parse_salary_interval("12K")[0])
        self.assertEqual(parse_salary_interval("1.25-1.5万/月")[0], (12.5, 15.0, "month"))

    def test_multiple_predicates_for_one_requirement_are_all_required(self):
        executable = plan([condition("years", "work_years", "GTE", 3, unit="year")])
        ceiling = executable.policy.hard_filters[0].model_copy(update={"filter_id": "ceiling", "operator": "LTE", "value_number": 10.0})
        executable.policy.hard_filters.append(ceiling)
        for value, expected in (("5年", "PASS"), ("12年", "FAIL"), ("1-5年", "UNKNOWN")):
            with self.subTest(value=value):
                output = evaluate_hard_filters(executable, resume(work_years=value))
                self.assertEqual(output.hard_filter_status, expected)
                self.assertEqual(output.atomic_results[0].filter_ids, ["f_years", "ceiling"])

    def test_unrestricted_expected_city_is_not_an_explicit_mismatch(self):
        output = self.check(condition("city", "city", "EQ", "北京"), resume(expectation={"city": "全国"}))
        self.assertEqual(output.hard_filter_status, "PASS")
        self.assertEqual(self.check(condition("city", "city", "NE", "北京"), resume(expectation={"city": "全国"})).hard_filter_status, "UNKNOWN")

    def test_unspecific_current_city_is_unknown_not_an_observed_location(self):
        candidate = resume(current_city="全国")
        facts = ObjectiveFacts(current_city=ObjectiveFact(value="全国", quote="全国", source_path="current_city"))
        output = self.check(condition("city", "city", "EQ", "北京"), candidate,
                            profile=PrescreenExecutionProfile(city_basis="current"), facts=facts)
        self.assertEqual(output.hard_filter_status, "UNKNOWN")

    def test_numeric_overflow_is_unknown_instead_of_a_false_pass_or_rejection(self):
        value = "9" * 400
        self.assertIsNone(parse_year_interval(value + "年"))
        self.assertIsNone(parse_salary_interval(value + "K/月")[0])

    def test_required_leaf_below_preferred_root_is_not_hard_eligible(self):
        executable = plan([condition("degree", "education_level", "GTE", "博士", unit="level")],
            groups=[{"group_id": "soft", "operator": "ALL", "priority": "PREFERRED", "members": ["degree"]}])
        output = evaluate_hard_filters(executable, resume())
        self.assertEqual(output.hard_filter_status, "PASS")
        self.assertEqual(output.failed_requirements, [])
        self.assertFalse(output.atomic_results[0].hard_eligible)

    def test_qualified_city_statements_are_unknown_not_an_explicit_other_city(self):
        item = condition("city", "city", "EQ", "北京")
        for value in ("北京（可异地）", "全国均可", "意向北京", "期望北京", "北京附近"):
            with self.subTest(value=value):
                output = self.check(item, resume(expectation={"city": value}))
                self.assertEqual(output.hard_filter_status, "UNKNOWN")
                self.assertEqual(output.failed_requirements, [])
                self.assertTrue(output.review_reasons)
                self.assertEqual(output.evidence[0].quote, value)
        # Qualification in a requested value also cannot be normalized by guess.
        self.assertEqual(self.check(condition("city", "city", "EQ", "北京（可异地）")).hard_filter_status, "UNKNOWN")

    def test_partial_employment_status_does_not_prove_immediate_arrival(self):
        for operator in ("EQ", "NE"):
            with self.subTest(operator=operator):
                output = self.check(condition("status", "job_status", operator, "离职-随时到岗"), resume(job_status="离职"))
                self.assertEqual(output.hard_filter_status, "UNKNOWN")
                self.assertEqual(output.failed_requirements, [])
        # Exact simple statements remain facts; the two levels are not aliases.
        self.assertEqual(self.check(condition("status", "job_status", "EQ", "离职"), resume(job_status="离职")).hard_filter_status, "PASS")
        self.assertEqual(self.check(condition("status", "job_status", "EQ", "离职-随时到岗")).hard_filter_status, "PASS")


if __name__ == "__main__":
    unittest.main()
