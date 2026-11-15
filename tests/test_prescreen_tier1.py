"""Actual Interpreter + generic Tier 1; no model/network dependencies."""
from copy import deepcopy
import math
import unittest

from engine.policy_interpreter import PolicyInterpreter
from engine.policy_rule_matcher import evaluate_policy_rules
from schemas import ResumeData
from schemas.prescreen import RequirementCheck, Tier0Result
from tests.mock_data import BENCHMARK_CANDIDATES


def policy_fixture():
    source = "必须同时具备Java开发能力与SQL事务设计能力；框架路线：Spring Boot或Quarkus至少具备一种，不要求同时具备；Docker部署只是加分。"
    requirements = []
    for key, label in (("java", "Java开发能力"), ("sql", "SQL事务设计能力"),
                       ("boot", "Spring Boot服务开发"), ("quarkus", "Quarkus服务开发")):
        requirements.append({"requirement_id": key, "label": label, "source_type": "JD",
            "source_text": "Spring Boot" if key == "boot" else "Quarkus" if key == "quarkus" else label,
            "priority": "REQUIRED", "requirement_type": "SKILL", "needs_confirmation": False})
    dimensions = []
    for key, label, refs, weight in (("core", "基础实现", ["java", "sql"], .6),
                                      ("delivery", "服务交付", ["boot", "quarkus"], .4)):
        dimensions.append({"dimension_id": key, "label": label, "description": "依据本人实际实现及可核验交付证据",
            "requirement_ids": refs, "weight": weight, "rubric": [
                {"min_score": 0, "max_score": 49, "description": "缺少具体职责或只有工具名称自述"},
                {"min_score": 50, "max_score": 100, "description": "能够说明本人实现动作与可核验结果"}]})
    return {"schema_version": "job_scoring_policy_v1", "job": {"title": "通用服务岗位", "summary": "交付服务与事务能力"},
        "sources": {"jd_text": source, "hr_requirements": ""}, "requirements": requirements,
        "requirement_groups": [
            {"group_id": "root", "label": "同时具备基础能力和一条路线", "operator": "ALL", "priority": "REQUIRED",
             "members": ["java", "sql", "route"], "source_type": "JD", "source_text": "必须同时具备Java开发能力与SQL事务设计能力", "needs_confirmation": False},
            {"group_id": "route", "label": "至少一条框架路线", "operator": "ANY", "priority": "REQUIRED",
             "members": ["boot", "quarkus"], "source_type": "JD", "source_text": "框架路线：Spring Boot或Quarkus至少具备一种，不要求同时具备", "needs_confirmation": False}],
        "hard_filters": [], "keyword_groups": [
            {"group_id": "java_words", "label": "语言", "requirement_ids": ["java"], "keywords": [{"canonical": "Java", "aliases": ["JAVA"], "weight": 10.0}]},
            {"group_id": "data_words", "label": "事务", "requirement_ids": ["sql"], "keywords": [{"canonical": "SQL", "aliases": [], "weight": 10.0}]},
            {"group_id": "framework_words", "label": "框架", "requirement_ids": ["boot", "quarkus"], "keywords": [
                {"canonical": "Spring Boot", "aliases": ["SpringBoot"], "weight": 10.0},
                {"canonical": "Quarkus", "aliases": [], "weight": 10.0}]}],
        "semantic_anchors": [{"anchor_id": "implementation", "requirement_ids": ["java", "sql"], "polarity": "POSITIVE", "text": "负责实现服务与事务处理，并交付可验证结果"}],
        "scoring_dimensions": dimensions, "thresholds": {"coarse_pass": 50.0, "deep_review": 60.0, "deep_recommend": 85.0},
        "diagnostics": [], "provenance": {"compiler_version": "offline-authored", "prompt_version": "not-called", "validator_version": "offline", "model_version": "NO_MODEL_CALLED"}}


def resume_fixture(*, project="", skills="", advantage="", tags=None):
    value = deepcopy(BENCHMARK_CANDIDATES[0])
    value.update(advantage=advantage, work_experience=[], education=[], skills=skills,
                 project_experience=[{"name": "Evidence fixture", "description": project}] if project else [])
    if tags:
        value["work_experience"] = [{"keywords": list(tags)}]
    return ResumeData.model_validate(value)


class PolicyTier1Tests(unittest.TestCase):
    def setUp(self):
        self.policy = policy_fixture()

    def plan(self, policy=None):
        return PolicyInterpreter().compile_policy(policy or self.policy, org_id="test-org", job_id="test-job",
                                                  policy_id="test-policy", policy_version=1)

    def score(self, resume, policy=None, hard=None):
        return evaluate_policy_rules(self.plan(policy), resume, hard)

    def test_complete_practical_match_reaches_normalized_budget(self):
        result = self.score(resume_fixture(project="负责Java订单服务并实现SQL事务处理；使用Spring Boot完成服务交付。"))
        self.assertEqual(result.preliminary_score, 100)
        self.assertEqual({d.dimension_id for d in result.dimension_scores}, {"core", "delivery"})
        self.assertTrue(all(d.evidence_quality == 1 for d in result.dimension_scores))

    def test_either_framework_route_is_sufficient_without_combining_budgets(self):
        results = [self.score(resume_fixture(project=f"负责Java订单服务并实现SQL事务处理；使用{framework}完成服务交付。"))
                   for framework in ("Spring Boot", "Quarkus")]
        self.assertEqual([item.preliminary_score for item in results], [100, 100])
        for result in results:
            selected = next(item for item in result.group_selections if item.group_id == "route")
            self.assertEqual(len(selected.selected_members), 1)
            self.assertEqual(sorted(selected.member_scores.values()), [0, 100])

    def test_java_does_not_match_javascript_and_sql_does_not_match_mysql(self):
        result = self.score(resume_fixture(project="负责JavaScript和MySQL页面程序开发。"))
        self.assertEqual(result.preliminary_score, 0)
        self.assertFalse(result.keyword_evidence)

    def test_alias_and_repetition_do_not_duplicate_points(self):
        one = self.score(resume_fixture(project="负责Java与SQL订单服务；使用SpringBoot完成交付。"))
        many = self.score(resume_fixture(project="负责Java JAVA Java与SQL SQL订单服务；使用Spring Boot SpringBoot完成交付。"))
        self.assertEqual(one.preliminary_score, many.preliminary_score)
        self.assertEqual(sum(item.selected for item in many.keyword_evidence), 3)
        self.assertGreater(len(many.keyword_evidence), 3)

    def test_duplicate_canonical_declarations_still_use_one_global_signal(self):
        baseline = self.score(resume_fixture(project="负责Java与SQL订单服务；使用Spring Boot完成交付。"))
        self.policy["keyword_groups"].append({"group_id": "duplicate", "label": "重复声明", "requirement_ids": ["java"],
            "keywords": [{"canonical": "JAVA", "aliases": [], "weight": 2.0}]})
        result = self.score(resume_fixture(project="负责Java与SQL订单服务；使用Spring Boot完成交付。"))
        self.assertEqual(result.canonical_count, baseline.canonical_count)
        self.assertEqual(result.preliminary_score, baseline.preliminary_score)

    def test_skill_lists_never_get_full_practice_quality(self):
        result = self.score(resume_fixture(skills=["Java", "SQL", "SpringBoot"]))
        self.assertAlmostEqual(result.preliminary_score, 100 * .5 / 1.2, places=2)
        self.assertTrue(all(item.context == "ASSERTED" for item in result.keyword_evidence))

    def test_work_tags_and_mere_project_mentions_are_auxiliary(self):
        tags = self.score(resume_fixture(tags=["Java", "SQL", "Spring Boot"]))
        mentions = self.score(resume_fixture(project="本项目涉及Java、SQL与Spring Boot。"))
        self.assertAlmostEqual(tags.preliminary_score, 100 * .35 / 1.2, places=2)
        self.assertEqual(tags.preliminary_score, mentions.preliminary_score)

    def test_learning_and_no_practice_do_not_become_implementation_evidence(self):
        learning = self.score(resume_fixture(skills="正在学习Java；正在学习SQL；正在学习Spring Boot"))
        self.assertTrue(all(item.context == "LEARNING" for item in learning.keyword_evidence))
        self.assertLess(learning.preliminary_score, 42)
        absent = self.score(resume_fixture(skills="没有Java经验；不熟悉SQL；Spring Boot尚未实践"))
        self.assertEqual(absent.preliminary_score, 0)
        self.assertTrue(all(item.context in ("NEGATED", "NO_PRACTICE") for item in absent.keyword_evidence))

    def test_elliptical_no_practice_qualifier_does_not_leave_a_positive_claim(self):
        result = self.score(resume_fixture(skills="了解Java，但尚未实践。"))
        self.assertEqual(result.preliminary_score, 0)
        self.assertEqual(result.keyword_evidence[0].context, "NO_PRACTICE")

    def test_negation_is_local_and_independent_positive_experience_survives(self):
        result = self.score(resume_fixture(project="未使用Java，但负责SQL事务处理和Spring Boot服务交付。"))
        selected = {item.canonical for item in result.keyword_evidence if item.selected}
        self.assertEqual(selected, {"SQL", "Spring Boot"})
        self.assertGreater(result.preliminary_score, 0)

    def test_positive_and_negative_experiences_are_both_retained_for_review(self):
        result = self.score(resume_fixture(project="负责Java订单服务开发。", skills="未使用Java"))
        self.assertTrue(any(item.selected and item.context == "PRACTICE" for item in result.keyword_evidence))
        self.assertTrue(any(item.context == "NO_PRACTICE" for item in result.keyword_evidence))
        self.assertTrue(any("正负" in reason for reason in result.review_reasons))

    def test_exact_source_offsets_use_original_list_elements(self):
        resume = resume_fixture(skills=[" 熟悉Java ", "掌握SQL", "SpringBoot"])
        result = self.score(resume)
        for item in result.keyword_evidence:
            index = int(item.source_path.removeprefix("skills[").removesuffix("]"))
            raw = resume.skills[index]
            self.assertEqual(raw[item.start:item.end], item.quote)
            self.assertNotIn(" | ", item.quote)

    def test_strongest_source_wins_across_fields(self):
        result = self.score(resume_fixture(project="负责Java服务实现。", skills="Java", advantage="熟悉Java", tags=["Java"]))
        selected = [item for item in result.keyword_evidence if item.selected]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].source_kind, "project")
        self.assertEqual(selected[0].source_weight, 1.2)

    def test_group_scoped_signal_can_score_dimension_without_proving_all_atoms(self):
        self.policy["keyword_groups"][0]["keywords"] = [{"canonical": "交付", "aliases": [], "weight": 10.0}]
        self.policy["keyword_groups"][0]["requirement_ids"] = ["java", "sql"]
        result = self.score(resume_fixture(project="负责业务交付并完成验收。"))
        self.assertGreater(result.preliminary_score, 0)
        self.assertNotIn("java", result.matched_requirements)
        self.assertNotIn("sql", result.matched_requirements)
        self.assertTrue(any("组级" in reason for reason in result.review_reasons))

    def test_policy_weights_are_used_and_missing_evidence_is_explicit(self):
        self.policy["scoring_dimensions"][0]["weight"] = .9
        self.policy["scoring_dimensions"][1]["weight"] = .1
        result = self.score(resume_fixture(project="负责Java订单服务并实现SQL事务处理。"))
        self.assertEqual(result.preliminary_score, 90)
        self.assertTrue(result.dimension_scores[1].missing_evidence)
        self.assertEqual([item.dimension_weight for item in result.dimension_scores], [.9, .1])

    def test_partial_coverage_uses_documented_exponential_formula(self):
        result = self.score(resume_fixture(project="负责Java订单服务实现。"))
        expected = 100 * (1 - math.exp(-.5)) / (1 - math.exp(-1))
        self.assertAlmostEqual(result.dimension_scores[0].normalized_score, expected, places=2)

    def test_objective_only_dimension_can_use_explicit_tier0_facts(self):
        self.policy["sources"]["jd_text"] += "硬性要求：本科及以上学历。"
        self.policy["requirements"].append({"requirement_id": "degree", "label": "本科学历", "source_type": "JD",
            "source_text": "硬性要求：本科及以上学历。", "priority": "REQUIRED", "requirement_type": "EDUCATION", "needs_confirmation": False})
        self.policy["scoring_dimensions"][1]["requirement_ids"] = ["degree"]
        hard = Tier0Result(hard_filter_status="PASS", atomic_results=[RequirementCheck(requirement_id="degree", status="PASS", reason="明确本科", filter_ids=["degree_filter"])])
        result = self.score(resume_fixture(project="负责Java和SQL服务实现。"), hard=hard)
        self.assertEqual(result.dimension_scores[1].normalized_score, 100)

    def test_preferred_missing_does_not_create_hard_failure_or_extra_multiplier(self):
        self.policy["requirements"].append({"requirement_id": "bonus", "label": "Docker部署", "source_type": "JD", "source_text": "Docker部署只是加分",
            "priority": "PREFERRED", "requirement_type": "SKILL", "needs_confirmation": False})
        self.policy["keyword_groups"].append({"group_id": "bonus_words", "label": "偏好", "requirement_ids": ["bonus"],
            "keywords": [{"canonical": "Docker", "aliases": [], "weight": 5.0}]})
        self.policy["scoring_dimensions"][0]["requirement_ids"].append("bonus")
        result = self.score(resume_fixture(project="负责Java和SQL服务实现；使用Spring Boot完成交付。"))
        self.assertGreater(result.preliminary_score, 0)
        self.assertIn("bonus", result.unknown_requirements)
        self.assertNotIn("bonus", result.matched_requirements)

    def test_source_certifications_are_auxiliary_exact_items(self):
        resume = resume_fixture()
        resume.certifications = ["Java认证"]
        result = self.score(resume)
        self.assertEqual(result.keyword_evidence[0].source_path, "certifications[0]")
        self.assertEqual(result.keyword_evidence[0].source_weight, .5)

    def test_input_and_cached_plan_are_not_mutated(self):
        plan = self.plan()
        before = plan.policy_json
        resume = resume_fixture(project="负责Java服务实现。")
        snapshot = resume.model_dump(mode="json")
        first = evaluate_policy_rules(plan, resume)
        second = evaluate_policy_rules(plan, resume)
        self.assertEqual(first, second)
        self.assertEqual(plan.policy_json, before)
        self.assertEqual(resume.model_dump(mode="json"), snapshot)

    def test_negation_does_not_cross_into_a_new_conjoined_action(self):
        result = self.score(resume_fixture(project="我不熟悉SQL并负责Java模块实现。"))
        java = [item for item in result.keyword_evidence if item.canonical == "Java"]
        sql = [item for item in result.keyword_evidence if item.canonical == "SQL"]
        self.assertTrue(java and all(item.context == "PRACTICE" for item in java))
        self.assertTrue(sql and all(item.context == "NEGATED" for item in sql))
        self.assertIn("java", result.matched_requirements)
        self.assertNotIn("sql", result.matched_requirements)

    def test_unrelated_negation_never_becomes_a_fabricated_target_denial(self):
        result = self.score(resume_fixture(skills="我不熟悉SQL并有Java项目经验。"))
        java = [item for item in result.keyword_evidence if item.canonical == "Java"]
        self.assertTrue(java)
        self.assertTrue(all(item.context not in ("NEGATED", "NO_PRACTICE") for item in java))

    def test_auxiliary_learning_and_mentions_do_not_claim_atomic_satisfaction(self):
        for resume in (resume_fixture(skills="正在学习Java"), resume_fixture(project="本项目涉及Java"), resume_fixture(tags=["Java"])):
            with self.subTest(resume=resume.skills or resume.project_experience):
                result = self.score(resume)
                self.assertGreater(result.preliminary_score, 0)
                self.assertNotIn("java", result.matched_requirements)
                self.assertIn("java", result.unknown_requirements)

    def test_no_practice_qualifier_does_not_cross_a_sentence_boundary(self):
        result = self.score(resume_fixture(project="负责Java模块实现。上一个岗位没有项目经验。"))
        self.assertTrue(any(item.context == "PRACTICE" and item.selected for item in result.keyword_evidence))

    def test_technical_learning_noun_is_not_self_learning(self):
        for text in ("负责Java机器学习平台实现。", "Built Java deep learning inference service."):
            with self.subTest(text=text):
                result = self.score(resume_fixture(project=text))
                self.assertTrue(any(item.context == "PRACTICE" and item.selected for item in result.keyword_evidence))

    def test_explicit_learning_enumeration_remains_learning_for_both_members(self):
        result = self.score(resume_fixture(skills="Learning Java and SQL"))
        self.assertEqual({item.canonical for item in result.keyword_evidence}, {"Java", "SQL"})
        self.assertTrue(all(item.context == "LEARNING" for item in result.keyword_evidence))

    def test_suffix_negation_with_another_explicit_object_does_not_deny_first_skill(self):
        for text in ("熟悉Java但没有SQL经验", "熟悉Java没有SQL经验"):
            with self.subTest(text=text):
                result = self.score(resume_fixture(skills=text))
                java = [item for item in result.keyword_evidence if item.canonical == "Java"]
                sql = [item for item in result.keyword_evidence if item.canonical == "SQL"]
                self.assertTrue(all(item.context == "ASSERTED" for item in java))
                self.assertTrue(all(item.context == "NEGATED" for item in sql))

    def test_learning_ability_keyword_is_not_an_external_self_learning_qualifier(self):
        self.policy["sources"]["jd_text"] += "必须具备学习能力。"
        self.policy["requirements"][0].update(label="学习能力", source_text="必须具备学习能力。")
        self.policy["keyword_groups"][0]["keywords"] = [{"canonical": "学习能力", "aliases": [], "weight": 10.0}]
        result = self.score(resume_fixture(project="负责团队学习能力培养并完成培训交付。"))
        self.assertTrue(any(item.canonical == "学习能力" and item.context == "PRACTICE" and item.selected
                            for item in result.keyword_evidence))

    def test_uniform_keyword_weight_rescaling_does_not_change_normalized_scores(self):
        resume = resume_fixture(project="负责Java订单模块实现；使用Spring Boot完成服务交付。")
        baseline = self.score(resume)
        scaled = deepcopy(self.policy)
        for group in scaled["keyword_groups"]:
            for keyword in group["keywords"]:
                keyword["weight"] *= 7.0
        result = self.score(resume, scaled)
        self.assertEqual(result.preliminary_score, baseline.preliminary_score)
        self.assertEqual([item.normalized_score for item in result.dimension_scores],
                         [item.normalized_score for item in baseline.dimension_scores])
        self.assertEqual(result.group_selections, baseline.group_selections)
        for before, after in zip(baseline.dimension_scores, result.dimension_scores):
            self.assertAlmostEqual(after.raw_score, before.raw_score * 7)
            self.assertAlmostEqual(after.keyword_budget, before.keyword_budget * 7)

    def test_negated_practice_action_is_not_positive_and_task_name_is_not_a_qualifier(self):
        for text in ("不负责Java开发", "未参与Java项目", "没有完成Java交付"):
            with self.subTest(text=text):
                result = self.score(resume_fixture(project=text))
                self.assertEqual(result.preliminary_score, 0)
                self.assertTrue(all(item.context in ("NEGATED", "NO_PRACTICE", "AMBIGUOUS")
                                    and item.source_weight == 0 for item in result.keyword_evidence))
                self.assertNotIn("java", result.matched_requirements)
        self.policy["sources"]["jd_text"] += "必须处理未完成订单。"
        self.policy["requirements"][0].update(label="未完成订单", source_text="必须处理未完成订单。")
        self.policy["keyword_groups"][0]["keywords"] = [{"canonical": "未完成订单", "aliases": [], "weight": 10.0}]
        positive = self.score(resume_fixture(project="负责未完成订单状态修复。"))
        self.assertTrue(any(item.canonical == "未完成订单" and item.context == "PRACTICE" and item.selected
                            for item in positive.keyword_evidence))
