"""Graph-aware hard-filter conflicts; no scoring models or network calls."""
from copy import deepcopy
import unittest

from config.policy_validation import (MAX_HARD_CONFLICT_PATHS, PolicyNotPublishable,
                                      assert_publishable, diagnose_policy)
from tests.test_policy_schema import generic_policy


class PrescreenPolicyConflictTests(unittest.TestCase):
    def setUp(self):
        self.policy = generic_policy()
        self.policy["hard_filters"] = []

    def condition(self, key, operator, value, *, field="work_years", unit="year", priority="REQUIRED"):
        if field == "work_years":
            relation = {"GTE": "至少", "LTE": "不超过", "EQ": "为"}[operator]
            quote, kind = f"要求工作经验{relation}{value:g}年。", "EXPERIENCE"
        elif field == "expected_salary":
            period = {"month": "月", "year": "年"}[unit]
            quote, kind = f"要求期望{period}薪为{value:g}K。", "COMPENSATION"
        else:
            quote, kind = f"要求意向工作城市为{value}。", "LOCATION"
        self.policy["sources"]["jd_text"] += "\n" + quote
        requirement = {"requirement_id": key, "label": "条件" + key, "source_type": "JD", "source_text": quote,
                       "priority": priority, "requirement_type": kind, "needs_confirmation": False}
        self.policy["requirements"].append(requirement)
        condition = {"filter_id": "filter_" + key, "requirement_id": key, "field": field,
                     "operator": operator, "value_text": value if isinstance(value, str) else None,
                     "value_number": None if isinstance(value, str) else float(value), "value_list": [],
                     "unit": unit, "on_unknown": "REVIEW"}
        self.policy["hard_filters"].append(condition)
        return key

    def group(self, key, operator, members, *, priority="REQUIRED"):
        quote = "业务分组关系按以下组合。"
        self.policy["sources"]["jd_text"] += "\n" + quote
        self.policy["requirement_groups"].append({"group_id": key, "label": key, "operator": operator,
            "priority": priority, "members": members, "source_type": "JD", "source_text": quote,
            "needs_confirmation": False})
        return key

    def diagnostics(self):
        return diagnose_policy(self.policy)

    def codes(self):
        return {item.code for item in self.diagnostics()}

    def test_required_all_bounds_still_conflict(self):
        low = self.condition("low", "GTE", 5)
        high = self.condition("high", "LTE", 2)
        self.group("bounds", "ALL", [low, high])
        self.assertIn("CONFLICTING_HARD_FILTERS", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy)

    def test_mutually_alternative_bounds_do_not_conflict(self):
        low = self.condition("low", "GTE", 5)
        high = self.condition("high", "LTE", 2)
        self.group("routes", "ANY", [low, high])
        self.assertNotIn("CONFLICTING_HARD_FILTERS", self.codes())
        self.assertNotIn("UNSATISFIABLE_REQUIRED_BRANCH", self.codes())
        self.assertIsNotNone(assert_publishable(self.policy))

    def test_one_impossible_alternative_warns_without_blocking_feasible_route(self):
        low = self.condition("low", "GTE", 5)
        bad = self.condition("bad", "LTE", 2)
        good = self.condition("good", "LTE", 8)
        choice = self.group("choice", "ANY", [bad, good])
        self.group("combined", "ALL", [low, choice])
        diagnostics = self.diagnostics()
        self.assertNotIn("CONFLICTING_HARD_FILTERS", {item.code for item in diagnostics})
        warnings = [item for item in diagnostics if item.code == "UNSATISFIABLE_REQUIRED_BRANCH"]
        self.assertTrue(warnings)
        self.assertTrue(all(item.severity == "WARNING" for item in warnings))
        self.assertIsNotNone(assert_publishable(self.policy))

    def test_every_alternative_impossible_is_an_error(self):
        low = self.condition("low", "GTE", 5)
        high = self.condition("high", "LTE", 2)
        low2 = self.condition("low2", "GTE", 8)
        high2 = self.condition("high2", "LTE", 3)
        first = self.group("first", "ALL", [low, high])
        second = self.group("second", "ALL", [low2, high2])
        self.group("routes", "ANY", [first, second])
        self.assertIn("CONFLICTING_HARD_FILTERS", self.codes())
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy)

    def test_required_top_level_roots_are_implicitly_all(self):
        self.condition("low", "GTE", 5)
        self.condition("high", "LTE", 2)
        self.assertIn("CONFLICTING_HARD_FILTERS", self.codes())

    def test_multiple_filters_of_one_atomic_requirement_remain_all(self):
        key = self.condition("range", "GTE", 5)
        original = self.policy["requirements"][-1]
        original["source_text"] = "要求工作经验至少5年且不超过2年。"
        self.policy["sources"]["jd_text"] += "\n" + original["source_text"]
        second = deepcopy(self.policy["hard_filters"][-1])
        second.update(filter_id="range_upper", operator="LTE", value_number=2.0)
        self.policy["hard_filters"].append(second)
        self.assertIn("CONFLICTING_HARD_FILTERS", self.codes())
        self.assertEqual(key, second["requirement_id"])

    def test_equality_alternatives_do_not_become_two_mandatory_cities(self):
        beijing = self.condition("beijing", "EQ", "北京", field="city", unit=None)
        shanghai = self.condition("shanghai", "EQ", "上海", field="city", unit=None)
        self.group("cities", "ANY", [beijing, shanghai])
        self.assertNotIn("CONFLICTING_HARD_FILTERS", self.codes())
        self.assertIsNotNone(assert_publishable(self.policy))

    def test_equality_all_of_two_different_cities_still_conflicts(self):
        beijing = self.condition("beijing", "EQ", "北京", field="city", unit=None)
        shanghai = self.condition("shanghai", "EQ", "上海", field="city", unit=None)
        self.group("cities", "ALL", [beijing, shanghai])
        self.assertIn("CONFLICTING_HARD_FILTERS", self.codes())

    def test_equality_outside_simultaneous_bound_conflicts(self):
        minimum = self.condition("minimum", "GTE", 5)
        exact = self.condition("exact", "EQ", 2)
        self.group("years", "ALL", [minimum, exact])
        self.assertIn("CONFLICTING_HARD_FILTERS", self.codes())

    def test_compatible_equality_and_bound_are_publishable(self):
        minimum = self.condition("minimum", "GTE", 5)
        exact = self.condition("exact", "EQ", 6)
        self.group("years", "ALL", [minimum, exact])
        self.assertNotIn("CONFLICTING_HARD_FILTERS", self.codes())
        self.assertIsNotNone(assert_publishable(self.policy))

    def test_different_salary_periods_are_not_assumed_comparable(self):
        month = self.condition("month_salary", "EQ", 12, field="expected_salary", unit="month")
        year = self.condition("year_salary", "EQ", 120, field="expected_salary", unit="year")
        self.group("salary", "ALL", [month, year])
        self.assertNotIn("CONFLICTING_HARD_FILTERS", self.codes())
        self.assertIsNotNone(assert_publishable(self.policy))

    def test_preferred_parent_does_not_make_children_globally_mandatory(self):
        low = self.condition("low", "GTE", 5)
        high = self.condition("high", "LTE", 2)
        self.group("soft_bounds", "ALL", [low, high], priority="PREFERRED")
        self.assertNotIn("CONFLICTING_HARD_FILTERS", self.codes())

    def test_optional_parent_does_not_make_children_globally_mandatory(self):
        low = self.condition("low", "GTE", 5)
        high = self.condition("high", "LTE", 2)
        self.group("soft_bounds", "ALL", [low, high], priority="OPTIONAL")
        self.assertNotIn("CONFLICTING_HARD_FILTERS", self.codes())

    def test_preference_leaf_remains_invalid_as_a_hard_filter_not_a_conflict(self):
        self.condition("low", "GTE", 5, priority="PREFERRED")
        self.condition("high", "LTE", 2, priority="OPTIONAL")
        self.assertIn("PREFERENCE_AS_HARD_FILTER", self.codes())
        self.assertNotIn("CONFLICTING_HARD_FILTERS", self.codes())

    def test_path_expansion_limit_requires_confirmation_instead_of_guessing(self):
        choices = []
        for index in range(9):
            zero = self.condition(f"zero_{index}", "EQ", 0)
            one = self.condition(f"one_{index}", "EQ", 1)
            choices.append(self.group(f"choice_{index}", "ANY", [zero, one]))
        self.group("all_choices", "ALL", choices)
        self.assertLess(MAX_HARD_CONFLICT_PATHS, 2 ** 9)
        diagnostics = self.diagnostics()
        limited = [item for item in diagnostics if item.code == "HARD_CONFLICT_ANALYSIS_LIMIT"]
        self.assertTrue(limited)
        self.assertEqual(limited[0].severity, "CONFIRM")
        self.assertNotIn("CONFLICTING_HARD_FILTERS", {item.code for item in diagnostics})
        with self.assertRaises(PolicyNotPublishable):
            assert_publishable(self.policy)

    def test_invalid_graph_cycle_is_reported_without_recursive_crash(self):
        low = self.condition("low", "GTE", 5)
        high = self.condition("high", "LTE", 2)
        group = self.group("bad_cycle", "ALL", [low, high, "bad_cycle"])
        self.policy["requirement_groups"][0]["members"].append(group)
        self.assertIn("CYCLIC_REQUIREMENT_GRAPH", self.codes())
        self.assertNotIn("HARD_CONFLICT_ANALYSIS_LIMIT", self.codes())

    def test_shared_requirement_dag_is_not_a_cycle_or_global_any_conflict(self):
        shared = self.condition("shared", "GTE", 5)
        upper = self.condition("upper", "LTE", 8)
        exact = self.condition("exact", "EQ", 6)
        first = self.group("first", "ALL", [shared, upper])
        second = self.group("second", "ALL", [shared, exact])
        self.group("alternatives", "ANY", [first, second])
        self.assertNotIn("CYCLIC_REQUIREMENT_GRAPH", self.codes())
        self.assertNotIn("CONFLICTING_HARD_FILTERS", self.codes())
        self.assertIsNotNone(assert_publishable(self.policy))
