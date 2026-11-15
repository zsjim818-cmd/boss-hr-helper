"""Batch 1 read-only Policy Quality Gate: positive-anchor coverage facts.

These tests exercise the deterministic validator only; no model or database is
contacted and no candidate score is produced or changed.
"""
import unittest
from copy import deepcopy
import subprocess
import sys

from engine.policy_anchor_validator import (
    BROAD_ONLY_POSITIVE_ANCHOR,
    MISSING_POSITIVE_ANCHOR,
    UNCLASSIFIED_REQUIREMENT_SCOPE,
    UNKNOWN_ANCHOR_REQUIREMENT,
    UNKNOWN_PROJECTION_REQUIREMENT,
)
from engine.policy_structure_validator import (
    ALL_SEMANTICS_MISMATCH,
    CONJOINED_TECH_REQUIREMENT,
    DUPLICATE_GROUP_MEMBERS,
    EMBEDDED_ALTERNATIVE_REQUIREMENT,
    ORPHAN_REQUIREMENT_GROUP,
    PREFERRED_GROUP_DEMOTES_REQUIRED,
    PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED,
    REDUNDANT_GROUP_WRAPPER,
    SINGLE_MEMBER_GROUP,
)
from engine.policy_validator import POLICY_VALIDATOR_VERSION, validate_policy_quality
from tests.policy_fixtures import offline_policy


def _add_positive(policy, anchor_id, requirement_id,
                  text="能够独立提供该原子要求的可核验的具体实现过程与结果。"):
    policy["semantic_anchors"].append({"anchor_id": anchor_id,
                                       "requirement_ids": [requirement_id],
                                       "polarity": "POSITIVE", "text": text})


def _fully_covered(case_id="ai_automation"):
    policy = offline_policy(case_id)
    for requirement_id in ("testing", "duty"):
        _add_positive(policy, "fix_" + requirement_id, requirement_id)
    return policy


class PositiveAnchorCoverageTests(unittest.TestCase):
    def test_offline_fixtures_flag_each_requirement_without_positive_anchor(self):
        expected = {
            "ai_automation": {"testing", "duty"},
            "java_backend": {"spring_route", "quarkus_route", "duty"},
            "b2b_sales": {"direct_route", "channel_route", "duty"},
        }
        for case_id, missing in expected.items():
            with self.subTest(case_id=case_id):
                report = validate_policy_quality(offline_policy(case_id))
                self.assertEqual(report.validator_version, POLICY_VALIDATOR_VERSION)
                found = {item.requirement_id for item in report.issues
                         if item.code == "MISSING_POSITIVE_ANCHOR"}
                self.assertEqual(found, missing)
                self.assertEqual(report.status, "BLOCKED")
                self.assertTrue(all(item.severity == "ERROR" for item in report.issues))

    def test_negative_anchor_never_substitutes_for_positive(self):
        policy = offline_policy("ai_automation")
        duty_anchors = [item for item in policy["semantic_anchors"]
                        if "duty" in item["requirement_ids"]]
        self.assertTrue(duty_anchors)
        self.assertTrue(all(item["polarity"] == "NEGATIVE" for item in duty_anchors))
        report = validate_policy_quality(policy)
        codes = {item.code for item in report.issues if item.requirement_id == "duty"}
        self.assertEqual(codes, {"MISSING_POSITIVE_ANCHOR"})

    def test_hard_filter_requirements_stay_outside_semantic_scope(self):
        report = validate_policy_quality(offline_policy("ai_automation"))
        flagged = {item.requirement_id for item in report.issues}
        self.assertNotIn("degree", flagged)
        self.assertNotIn("salary", flagged)
        # The semantic scope is exactly the union of dimension projections.
        self.assertEqual(report.semantic_requirement_count, 5)

    def test_fully_covered_policy_passes(self):
        report = validate_policy_quality(_fully_covered())
        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.issues, [])
        self.assertEqual(report.positive_coverage_rate, 1.0)
        self.assertEqual(report.directly_covered_count, report.semantic_requirement_count)

    def test_broad_only_anchor_is_warning_and_not_valid_coverage(self):
        policy = _fully_covered()
        policy["semantic_anchors"] = [item for item in policy["semantic_anchors"]
                                      if item["anchor_id"] != "positive_2"]
        policy["semantic_anchors"].append({
            "anchor_id": "broad_reliability",
            "requirement_ids": ["reliability", "testing"], "polarity": "POSITIVE",
            "text": "同时说明程序可靠性设计与接口测试覆盖的具体实现，但属于共享宽泛证据。"})
        report = validate_policy_quality(policy)
        broad = {item.requirement_id for item in report.issues
                 if item.code == "BROAD_ONLY_POSITIVE_ANCHOR"}
        self.assertEqual(broad, {"reliability"})
        self.assertEqual(report.status, "WARNING")
        self.assertEqual(report.directly_covered_count, 4)
        self.assertNotEqual(report.positive_coverage_rate, 1.0)

    def test_report_serializes_as_json_payload(self):
        payload = validate_policy_quality(offline_policy("java_backend")).model_dump(mode="json")
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["semantic_requirement_count"], 6)
        self.assertEqual({item["code"] for item in payload["issues"]},
                         {"MISSING_POSITIVE_ANCHOR"})


class EvaluationScopeClassificationTests(unittest.TestCase):
    """RULE / HARD_FILTER / unclassifiable requirements never enter the denominator."""

    def _projected_requirement_ids(self, report):
        return {requirement_id for dimension in report.dimension_coverage
                for requirement_id in dimension.requirement_ids}

    def test_keyword_only_rule_requirements_stay_out_of_semantic_denominator(self):
        report = validate_policy_quality(offline_policy("ai_automation"))
        # python/docker/workflow_tool are keyword-group-only Tier 1 rule signals.
        projected = self._projected_requirement_ids(report)
        for rule_only in ("python", "docker", "workflow_tool"):
            self.assertNotIn(rule_only, projected)
            self.assertNotIn(rule_only, {item.requirement_id for item in report.issues})
        self.assertEqual(report.semantic_requirement_count, 5)
        self.assertEqual(report.coverage_status, "DETERMINED")

    def test_hard_filter_requirements_never_appear_in_dimension_coverage(self):
        report = validate_policy_quality(offline_policy("java_backend"))
        projected = self._projected_requirement_ids(report)
        self.assertNotIn("degree", projected)
        self.assertNotIn("years", projected)
        self.assertNotIn("salary", projected)
        self.assertEqual(report.semantic_requirement_count, 6)

    def test_unreferenced_requirement_is_unclassified_and_excluded(self):
        # The schema has no per-requirement evaluation-scope field; a requirement
        # referenced by no dimension projection, keyword group or hard filter is
        # reported as UNCLASSIFIED instead of being silently classified.
        policy = _fully_covered()
        policy["keyword_groups"] = [group for group in policy["keyword_groups"]
                                    if group["group_id"] != "extra"]
        report = validate_policy_quality(policy)
        unclassified = {item.requirement_id for item in report.issues
                        if item.code == UNCLASSIFIED_REQUIREMENT_SCOPE}
        self.assertEqual(unclassified, {"docker", "workflow_tool"})
        self.assertEqual(report.unclassified_requirement_count, 2)
        self.assertEqual(report.semantic_requirement_count, 5)
        self.assertEqual(report.positive_coverage_rate, 1.0)
        self.assertEqual(report.status, "WARNING")
        self.assertTrue(all(item.suggested_action for item in report.issues))

    def test_invalid_dimension_projection_is_error_and_dimension_indeterminate(self):
        policy = _fully_covered()
        policy["scoring_dimensions"][0]["requirement_ids"] = ["ghost_requirement"]
        report = validate_policy_quality(policy)
        unknown = [item for item in report.issues
                   if item.code == UNKNOWN_PROJECTION_REQUIREMENT]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0].requirement_id, "ghost_requirement")
        self.assertEqual(unknown[0].dimension_ids, ["delivery"])
        self.assertEqual(unknown[0].severity, "ERROR")
        dimension = report.dimension_coverage[0]
        self.assertEqual(dimension.dimension_id, "delivery")
        self.assertEqual(dimension.requirement_ids, ["ghost_requirement"])
        self.assertEqual(dimension.invalid_requirement_ids, ["ghost_requirement"])
        self.assertEqual(dimension.semantic_requirement_count, 0)
        self.assertIsNone(dimension.direct_coverage_rate)
        # The ghost reference never enters the denominator: the efficiency
        # dimension still projects both routes.
        self.assertEqual(report.semantic_requirement_count, 5)
        self.assertEqual(report.positive_coverage_rate, 1.0)
        self.assertEqual(report.status, "BLOCKED")

    def test_unconfirmable_scope_reports_indeterminate_not_fabricated_rate(self):
        policy = _fully_covered()
        for dimension in policy["scoring_dimensions"]:
            dimension["requirement_ids"] = ["ghost_" + dimension["dimension_id"]]
        report = validate_policy_quality(policy)
        self.assertEqual(report.semantic_requirement_count, 0)
        self.assertEqual(report.coverage_status, "INDETERMINATE")
        self.assertIsNone(report.positive_coverage_rate)
        self.assertTrue(all(dimension.direct_coverage_rate is None
                            for dimension in report.dimension_coverage))
        self.assertEqual(len([item for item in report.issues
                              if item.code == UNKNOWN_PROJECTION_REQUIREMENT]), 4)
        self.assertEqual(report.status, "BLOCKED")

    def test_invalid_anchor_reference_is_error_and_provides_no_coverage(self):
        policy = _fully_covered()
        policy["semantic_anchors"].append({
            "anchor_id": "bad_anchor", "requirement_ids": ["ghost"],
            "polarity": "POSITIVE", "text": "关联了不存在原子要求的正向锚点描述。"})
        report = validate_policy_quality(policy)
        bad = [item for item in report.issues if item.code == UNKNOWN_ANCHOR_REQUIREMENT]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].requirement_id, "ghost")
        self.assertEqual(bad[0].anchor_ids, ["bad_anchor"])
        self.assertEqual(bad[0].severity, "ERROR")
        # The invalid anchor changes nothing about real coverage accounting.
        self.assertEqual(report.directly_covered_count, 5)
        self.assertEqual(report.positive_coverage_rate, 1.0)
        self.assertEqual(report.status, "BLOCKED")

    def test_multiple_direct_positive_anchors_still_count_requirement_once(self):
        policy = _fully_covered()
        _add_positive(policy, "fix_testing_b", "testing",
                      text="第二条仅关联接口测试的正向证据锚点，同样可独立核验。")
        report = validate_policy_quality(policy)
        self.assertEqual(report.issues, [])
        self.assertEqual(report.directly_covered_count, 5)
        self.assertEqual(report.positive_coverage_rate, 1.0)
        self.assertEqual(report.status, "PASS")

    def test_broad_anchor_covering_two_requirements_flags_both(self):
        policy = _fully_covered()
        policy["semantic_anchors"] = [item for item in policy["semantic_anchors"]
                                      if item["anchor_id"] not in ("positive_2", "fix_testing")]
        policy["semantic_anchors"].append({
            "anchor_id": "broad_two", "requirement_ids": ["reliability", "testing"],
            "polarity": "POSITIVE", "text": "同时描述可靠性设计与接口测试的共享宽泛证据。"})
        report = validate_policy_quality(policy)
        broad = {item.requirement_id for item in report.issues
                 if item.code == BROAD_ONLY_POSITIVE_ANCHOR}
        self.assertEqual(broad, {"reliability", "testing"})
        self.assertTrue(all(item.anchor_ids == ["broad_two"] for item in report.issues
                            if item.code == BROAD_ONLY_POSITIVE_ANCHOR))
        self.assertEqual(report.broad_only_count, 2)
        self.assertEqual(report.missing_positive_count, 0)
        self.assertEqual(report.directly_covered_count, 3)
        self.assertEqual(report.positive_coverage_rate, 0.6)
        self.assertEqual(report.status, "WARNING")


class DimensionCoverageTests(unittest.TestCase):
    def test_java_backend_per_dimension_rates_are_exact(self):
        report = validate_policy_quality(offline_policy("java_backend"))
        self.assertEqual(len(report.dimension_coverage), 5)
        expected = {
            "service_design": (round(1 / 3, 6), 3, 1, 2, 0),
            "data_correctness": (1.0, 1, 1, 0, 0),
            "troubleshooting": (0.0, 1, 0, 1, 0),
            "testing_quality": (1.0, 1, 1, 0, 0),
            "communication": (0.0, 1, 0, 1, 0),
        }
        for dimension in report.dimension_coverage:
            with self.subTest(dimension_id=dimension.dimension_id):
                rate, count, direct, missing, broad = expected[dimension.dimension_id]
                self.assertEqual(dimension.direct_coverage_rate, rate)
                self.assertEqual(dimension.semantic_requirement_count, count)
                self.assertEqual(dimension.direct_covered, direct)
                self.assertEqual(dimension.missing_positive, missing)
                self.assertEqual(dimension.broad_only, broad)
        self.assertEqual(report.positive_coverage_rate, 0.5)

    def test_dimension_counts_partition_the_projected_requirements(self):
        for case_id in ("ai_automation", "java_backend", "b2b_sales"):
            with self.subTest(case_id=case_id):
                report = validate_policy_quality(offline_policy(case_id))
                self.assertEqual(report.coverage_status, "DETERMINED")
                for dimension in report.dimension_coverage:
                    self.assertEqual(dimension.semantic_requirement_count,
                                     dimension.direct_covered + dimension.broad_only
                                     + dimension.missing_positive)
                    self.assertEqual(dimension.invalid_requirement_ids, [])


class RobustnessAndIsolationTests(unittest.TestCase):
    # Batch-1 expected facts for the three offline fixtures (denominator,
    # direct, broad-only, missing, rate).
    EXPECTED = {
        "ai_automation": (5, 3, 0, 2, 0.6),
        "java_backend": (6, 3, 0, 3, 0.5),
        "b2b_sales": (6, 3, 0, 3, 0.5),
    }

    def test_offline_fixture_counts_and_rates(self):
        for case_id, (count, direct, broad, missing, rate) in self.EXPECTED.items():
            with self.subTest(case_id=case_id):
                report = validate_policy_quality(offline_policy(case_id))
                self.assertEqual(report.semantic_requirement_count, count)
                self.assertEqual(report.directly_covered_count, direct)
                self.assertEqual(report.broad_only_count, broad)
                self.assertEqual(report.missing_positive_count, missing)
                self.assertEqual(report.unclassified_requirement_count, 0)
                self.assertEqual(report.positive_coverage_rate, rate)
                self.assertEqual(report.coverage_status, "DETERMINED")
                self.assertEqual(report.status, "BLOCKED")

    def test_empty_or_partial_policy_raises_clean_validation_error(self):
        for malformed in ({}, [], None, {"schema_version": "job_scoring_policy_v1"}):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    validate_policy_quality(malformed)

    def test_policy_versions_are_isolated_and_inputs_never_mutated(self):
        base = _fully_covered()
        v1 = deepcopy(base)
        v2 = deepcopy(base)
        v2["semantic_anchors"] = [item for item in v2["semantic_anchors"]
                                  if item["anchor_id"] != "fix_duty"]
        report_v1 = validate_policy_quality(v1, policy_id="shared_policy", policy_version=1)
        report_v2 = validate_policy_quality(v2, policy_id="shared_policy", policy_version=2)
        self.assertEqual(report_v1.policy_version, 1)
        self.assertEqual(report_v1.status, "PASS")
        self.assertEqual(report_v1.positive_coverage_rate, 1.0)
        self.assertEqual(report_v2.policy_version, 2)
        self.assertEqual(report_v2.status, "BLOCKED")
        self.assertEqual(report_v2.missing_positive_count, 1)
        self.assertEqual(report_v2.positive_coverage_rate, 0.8)
        # Pure functions: re-reviewing V1 after V2 returns the identical report.
        self.assertEqual(validate_policy_quality(v1, policy_id="shared_policy", policy_version=1),
                         report_v1)
        # Neither input dict was mutated by any review.
        self.assertEqual(v1, base)
        self.assertEqual(v2["semantic_anchors"],
                         [item for item in base["semantic_anchors"]
                          if item["anchor_id"] != "fix_duty"])

    def test_identity_fields_are_validated_and_hash_is_canonical(self):
        from engine.policy_interpreter import content_hash
        policy = _fully_covered()
        with self.assertRaises(ValueError):
            validate_policy_quality(policy, policy_version=0)
        with self.assertRaises(ValueError):
            validate_policy_quality(policy, policy_id="   ")
        with self.assertRaises(ValueError):
            validate_policy_quality(policy, policy_hash="")
        report = validate_policy_quality(policy)
        self.assertIsNone(report.policy_id)
        self.assertIsNone(report.policy_version)
        self.assertEqual(report.policy_hash, content_hash(policy))
        identified = validate_policy_quality(policy, policy_id="policy_x",
                                             policy_version=3, policy_hash="f" * 64)
        self.assertEqual((identified.policy_id, identified.policy_version, identified.policy_hash),
                         ("policy_x", 3, "f" * 64))

    def test_validator_never_loads_model_or_embedding_or_http_stack(self):
        # A fresh interpreter proves the read-only validator pulls in no BGE /
        # Qwen / numeric vector stack, before or after a full validation run.
        code = (
            "import sys\n"
            "import engine.policy_validator as validator\n"
            "from tests.policy_fixtures import offline_policy\n"
            "banned = ('sentence_transformers', 'httpx', 'numpy', 'torch')\n"
            "for name in banned:\n"
            "    assert name not in sys.modules, name\n"
            "report = validator.validate_policy_quality(offline_policy('java_backend'))\n"
            "assert report.status == 'BLOCKED' and report.semantic_requirement_count == 6\n"
            "for name in banned:\n"
            "    assert name not in sys.modules, name\n"
        )
        from config.settings import PROJECT_ROOT
        subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=str(PROJECT_ROOT),
                       capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)


class AtomicityTests(unittest.TestCase):
    """Batch 2: atomic requirements must not silently bundle alternatives/skills."""

    def _requirement(self, policy, requirement_id):
        return next(item for item in policy["requirements"]
                    if item["requirement_id"] == requirement_id)

    def test_embedded_alternative_outside_any_group_is_warned(self):
        policy = _fully_covered()
        self._requirement(policy, "python")["source_text"] = (
            "具备LangGraph或LangChain的多步骤工具调用应用实现经验")
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues
                if item.code == EMBEDDED_ALTERNATIVE_REQUIREMENT]
        self.assertEqual([item.requirement_id for item in hits], ["python"])
        self.assertEqual(hits[0].severity, "WARNING")
        self.assertTrue(hits[0].suggested_action)
        self.assertEqual(report.status, "WARNING")

    def test_embedded_alternative_inside_any_group_is_exempt(self):
        policy = _fully_covered()
        self._requirement(policy, "application_route")["source_text"] = (
            "使用LangGraph或LangChain实现多步骤工具调用")
        report = validate_policy_quality(policy)
        self.assertNotIn(EMBEDDED_ALTERNATIVE_REQUIREMENT,
                         {item.code for item in report.issues})
        self.assertEqual(report.status, "PASS")

    def test_responsibility_alternative_is_exempt(self):
        # A duty legitimately describes a range of activities (fixture JD:
        # "开发AI应用或流程自动化"); it is not a capability gate.
        policy = _fully_covered()
        self._requirement(policy, "duty")["source_text"] = "面向内部业务开发AI应用或流程自动化"
        report = validate_policy_quality(policy)
        self.assertNotIn(EMBEDDED_ALTERNATIVE_REQUIREMENT,
                         {item.code for item in report.issues})

    def test_quantity_idiom_is_not_an_alternative(self):
        policy = _fully_covered()
        self._requirement(policy, "docker")["source_text"] = "至少一个或多个容器化部署实践"
        report = validate_policy_quality(policy)
        self.assertNotIn(EMBEDDED_ALTERNATIVE_REQUIREMENT,
                         {item.code for item in report.issues})

    def test_conjoined_tech_capabilities_are_warned(self):
        policy = _fully_covered()
        self._requirement(policy, "python")["source_text"] = (
            "熟悉Spring Boot和Kafka的实际交付与排障")
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues if item.code == CONJOINED_TECH_REQUIREMENT]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].requirement_id, "python")
        self.assertIn("Spring", hits[0].message)
        self.assertIn("Kafka", hits[0].message)

    def test_cjk_conjunction_is_not_a_bundled_tech_finding(self):
        policy = _fully_covered()
        self._requirement(policy, "python")["source_text"] = "具备异常处理和接口测试的具体做法"
        report = validate_policy_quality(policy)
        self.assertNotIn(CONJOINED_TECH_REQUIREMENT,
                         {item.code for item in report.issues})


class GroupStructureTests(unittest.TestCase):
    """Batch 2: ALL/ANY structure must express its claimed semantics."""

    def test_single_member_group_is_warned(self):
        policy = _fully_covered()
        group = next(item for item in policy["requirement_groups"]
                     if item["group_id"] == "core_skills")
        group["members"] = ["python"]
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues if item.code == SINGLE_MEMBER_GROUP]
        self.assertEqual([item.group_id for item in hits], ["core_skills"])
        self.assertEqual(hits[0].severity, "WARNING")

    def test_pass_through_group_wrapper_is_warned(self):
        policy = _fully_covered()
        policy["requirement_groups"].append({
            "group_id": "wrapper", "label": "透传包装", "operator": "ALL", "priority": "REQUIRED",
            "members": ["preferred"], "source_type": "JD",
            "source_text": policy["sources"]["jd_text"].splitlines()[4],
            "needs_confirmation": False})
        report = validate_policy_quality(policy)
        hits = {item.group_id for item in report.issues
                if item.code == REDUNDANT_GROUP_WRAPPER}
        self.assertEqual(hits, {"wrapper"})

    def test_duplicate_group_members_with_same_operator_is_warning(self):
        policy = _fully_covered()
        original = next(item for item in policy["requirement_groups"]
                        if item["group_id"] == "preferred")
        duplicate = dict(original, group_id="preferred_copy")
        policy["requirement_groups"].append(duplicate)
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues if item.code == DUPLICATE_GROUP_MEMBERS]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, "WARNING")
        self.assertEqual(hits[0].group_id, "preferred")

    def test_duplicate_group_members_with_conflicting_operator_is_error(self):
        policy = _fully_covered()
        original = next(item for item in policy["requirement_groups"]
                        if item["group_id"] == "preferred")
        conflicting = dict(original, group_id="preferred_all", operator="ALL")
        policy["requirement_groups"].append(conflicting)
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues if item.code == DUPLICATE_GROUP_MEMBERS]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, "ERROR")
        self.assertEqual(report.status, "BLOCKED")

    def test_preferred_group_demoting_required_member_is_error(self):
        # Tier 0 skips a non-REQUIRED group's whole subtree; a REQUIRED hard
        # constraint inside a PREFERRED group would be silently dropped.
        policy = _fully_covered()
        group = next(item for item in policy["requirement_groups"]
                     if item["group_id"] == "preferred")
        group["members"] = [*group["members"], "degree"]
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues if item.code == PREFERRED_GROUP_DEMOTES_REQUIRED]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, "ERROR")
        self.assertEqual(hits[0].requirement_id, "degree")
        self.assertEqual(hits[0].group_id, "preferred")

    def test_any_operator_with_simultaneity_source_is_warned(self):
        policy = _fully_covered()
        group = next(item for item in policy["requirement_groups"]
                     if item["group_id"] == "alternative_routes")
        group["source_text"] = "以下两条必须同时满足：路线A与路线B缺一不可"
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues if item.code == ALL_SEMANTICS_MISMATCH]
        self.assertEqual([item.group_id for item in hits], ["alternative_routes"])
        self.assertEqual(hits[0].severity, "WARNING")

    def test_actual_fixture_alternative_routes_stays_clean(self):
        # "至少满足一条，不要求同时具备" expresses ANY semantics correctly.
        report = validate_policy_quality(offline_policy("ai_automation"))
        self.assertNotIn(ALL_SEMANTICS_MISMATCH, {item.code for item in report.issues})

    def test_orphan_group_is_warned_and_counted(self):
        policy = _fully_covered()
        policy["keyword_groups"] = [group for group in policy["keyword_groups"]
                                    if group["group_id"] != "extra"]
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues if item.code == ORPHAN_REQUIREMENT_GROUP]
        self.assertEqual([item.group_id for item in hits], ["preferred"])
        self.assertEqual(hits[0].severity, "WARNING")
        self.assertEqual(report.orphan_group_count, 1)

    def test_preferred_requirement_without_keyword_group_is_warned(self):
        policy = _fully_covered()
        # Project docker into a dimension (so it is not UNCLASSIFIED) while
        # dropping its keyword-group wiring; Tier 1 can then never score it.
        dimension = next(item for item in policy["scoring_dimensions"]
                         if item["dimension_id"] == "requirements")
        dimension["requirement_ids"] = ["duty", "docker"]
        extra = next(item for item in policy["keyword_groups"]
                     if item["group_id"] == "extra")
        extra["requirement_ids"] = ["workflow_tool"]
        report = validate_policy_quality(policy)
        hits = [item for item in report.issues
                if item.code == PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED]
        self.assertEqual([item.requirement_id for item in hits], ["docker"])


class Batch2CompatibilityTests(unittest.TestCase):
    """Batch 1 findings must not change, and real policies keep exact facts."""

    BATCH2_CODES = {EMBEDDED_ALTERNATIVE_REQUIREMENT, CONJOINED_TECH_REQUIREMENT,
                    SINGLE_MEMBER_GROUP, REDUNDANT_GROUP_WRAPPER, DUPLICATE_GROUP_MEMBERS,
                    PREFERRED_GROUP_DEMOTES_REQUIRED, ALL_SEMANTICS_MISMATCH,
                    ORPHAN_REQUIREMENT_GROUP, PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED}

    def test_offline_fixtures_have_no_batch2_findings(self):
        for case_id in ("ai_automation", "java_backend", "b2b_sales"):
            with self.subTest(case_id=case_id):
                report = validate_policy_quality(offline_policy(case_id))
                codes = {item.code for item in report.issues}
                self.assertEqual(codes, {MISSING_POSITIVE_ANCHOR})
                self.assertEqual(report.orphan_group_count, 0)

    def test_fully_covered_fixture_stays_pass(self):
        report = validate_policy_quality(_fully_covered())
        self.assertEqual(report.issues, [])
        self.assertEqual(report.orphan_group_count, 0)

    def test_real_stage3_policies_keep_their_recorded_structure_facts(self):
        # Pins the Batch 2 scan: the real b2b policy ships exactly one orphan
        # group (grp_bonus_items: crm/english are wired nowhere); ai/java are
        # structurally clean. Regressions here mean a compiler/normalizer change.
        from config.settings import PROJECT_ROOT
        import json
        expected = {"ai_automation": [], "java_backend": [],
                    "b2b_sales": [ORPHAN_REQUIREMENT_GROUP]}
        for case_id, expected_codes in expected.items():
            with self.subTest(case_id=case_id):
                policy = json.loads((PROJECT_ROOT / "reports" / "phase1_7_stage3" /
                                     "policies" / f"{case_id}.json").read_text(encoding="utf-8"))
                report = validate_policy_quality(policy)
                codes = sorted(item.code for item in report.issues
                               if item.code in self.BATCH2_CODES)
                self.assertEqual(codes, sorted(expected_codes))
                self.assertEqual(report.orphan_group_count, len(expected_codes))


if __name__ == "__main__":
    unittest.main()
