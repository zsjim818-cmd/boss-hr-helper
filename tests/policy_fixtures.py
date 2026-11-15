"""Human-authored compiler inputs and explicitly offline policy fixtures.

These fixtures do not represent API responses. Real Qwen output belongs in the
separate reports/phase1_7_stage3/live directory and must not be substituted here.
"""
from copy import deepcopy
import json
from pathlib import Path


INPUT_PATH = Path(__file__).parent / "fixtures" / "job_compiler_inputs.json"


def compiler_cases():
    """Return fresh copies so tests cannot mutate shared acceptance inputs."""
    return deepcopy(json.loads(INPUT_PATH.read_text(encoding="utf-8"))["cases"])


def compiler_input(case_id):
    case = next(case for case in compiler_cases() if case["case_id"] == case_id)
    return {key: case[key] for key in ("job_title", "raw_jd_text", "hr_custom_requirements")}


def policy_input(case_id):
    """Canonical Stage 3 PolicyCompileInput-shaped source snapshot."""
    legacy = compiler_input(case_id)
    return {"job_title": legacy["job_title"], "jd_text": legacy["raw_jd_text"],
            "hr_requirements": legacy["hr_custom_requirements"]}


def offline_policy(case_id):
    """Build a human-authored policy fixture, never a simulated live result."""
    source = policy_input(case_id)
    lines = source["jd_text"].splitlines()
    cases = {case["case_id"]: case for case in compiler_cases()}
    case = cases[case_id]
    requirements = []
    def requirement(key, label, line, kind="SKILL", priority="REQUIRED", source_type="JD"):
        requirements.append({"requirement_id": key, "label": label, "source_type": source_type,
                             "source_text": lines[line] if source_type == "JD" else source["hr_requirements"],
                             "priority": priority, "requirement_type": kind, "needs_confirmation": False})
        return key
    duty = requirement("duty", lines[0], 0, "RESPONSIBILITY")
    hard_filters = []
    mandatory = []
    def hard(key, label, field, operator, unit, *, text=None, number=None):
        kind = {"education_level": "EDUCATION", "work_years": "EXPERIENCE", "expected_salary": "COMPENSATION"}[field]
        mandatory.append(requirement(key, label, 1, kind))
        hard_filters.append({"filter_id": "filter_" + key, "requirement_id": key,
                             "field": field, "operator": operator, "value_text": text,
                             "value_number": number, "value_list": [], "unit": unit, "on_unknown": "REVIEW"})
    if case_id == "ai_automation":
        hard("degree", "大专及以上学历", "education_level", "GTE", "level", text="大专")
        hard("salary", "期望月薪不高于18K", "expected_salary", "LTE", "month", number=18.0)
        skills = [requirement("python", "Python编程基础", 2),
                  requirement("reliability", "异常处理", 2), requirement("testing", "接口测试", 2)]
        routes = [requirement("application_route", "工具调用与RAG应用实现", 3, "EXPERIENCE"),
                  requirement("automation_route", "网页自动化采集与任务恢复", 3, "EXPERIENCE")]
        preferred = [requirement("docker", "Docker部署加分", 4, priority="PREFERRED"),
                     requirement("workflow_tool", "n8n配置加分", 4, priority="PREFERRED")]
        keyword_specs = [("programming", ["python", "testing", "reliability"], [("Python", []), ("接口测试", []), ("异常处理", [])]),
                         ("application", ["application_route"], [("LangGraph", []), ("LangChain", []), ("RAG", [])]),
                         ("automation", ["automation_route"], [("Playwright", []), ("Selenium", []), ("任务恢复", [])]),
                         ("extra", preferred, [("Docker", []), ("n8n", [])])]
        dimension_specs = [("delivery", "应用交付", "交付可运行的工具调用或自动化应用", routes),
                           ("reliability", "程序可靠性", "说明异常处理、接口测试及故障恢复的实现", ["reliability", "testing"]),
                           ("efficiency", "流程提效", "说明自动化流程替代人工工作的实际范围", routes),
                           ("requirements", "需求理解", "把内部业务需求拆解为可验证程序行为", [duty])]
        anchors = [("application_route", "基于工具调用和知识检索实现多步骤业务应用，并验证工具失败时的处理。"),
                   ("automation_route", "实现网页数据采集和页面操作，处理重试与中断后恢复，交付运行记录。"),
                   ("reliability", "为程序编写接口测试，说明异常捕获、日志和故障恢复的具体实现。")]
        weak_anchor = "只列出AI或自动化工具名称，无法说明实现步骤、测试或故障处理。"
    elif case_id == "java_backend":
        hard("degree", "本科及以上学历", "education_level", "GTE", "level", text="本科")
        hard("years", "至少3年工作经验", "work_years", "GTE", "year", number=3.0)
        hard("salary", "期望月薪不高于30K", "expected_salary", "LTE", "month", number=30.0)
        skills = [requirement("java", "Java服务端开发", 2), requirement("transactions", "SQL与事务设计", 2),
                  requirement("testing", "接口自动化测试", 2)]
        routes = [requirement("spring_route", "Spring Boot服务开发", 3, "EXPERIENCE"),
                  requirement("quarkus_route", "Quarkus服务开发", 3, "EXPERIENCE")]
        preferred = [requirement("messaging", "Kafka消息队列加分", 4, priority="PREFERRED"),
                     requirement("deployment", "Kubernetes部署加分", 4, priority="PREFERRED")]
        keyword_specs = [("service", ["java", *routes], [("Java", []), ("Spring Boot", ["SpringBoot"]), ("Quarkus", [])]),
                         ("data", ["transactions"], [("SQL", []), ("事务", [])]),
                         ("quality", ["testing", duty], [("接口自动化测试", []), ("故障排查", [])]),
                         ("extra", preferred, [("Kafka", []), ("Kubernetes", [])])]
        dimension_specs = [("service_design", "服务设计", "说明订单支付服务的接口边界和设计取舍", ["java", *routes]),
                           ("data_correctness", "数据正确性", "分析SQL事务与数据一致性的具体约束", ["transactions"]),
                           ("troubleshooting", "故障排查", "根据日志和指标定位性能及一致性问题", [duty]),
                           ("testing_quality", "测试质量", "用接口自动化测试验证关键业务路径", ["testing"]),
                           ("communication", "业务沟通", "把订单支付规则转换为清晰的接口验收条件", [duty])]
        anchors = [("java", "独立实现订单或支付后台接口，说明服务边界、错误处理与接口约定。"),
                   ("transactions", "设计关系型数据库事务，排查并修复订单状态或支付数据不一致问题。"),
                   ("testing", "编写接口自动化测试覆盖关键路径，复现故障并验证修复结果。")]
        weak_anchor = "只罗列服务框架和数据库名称，不能解释接口实现、事务边界或测试过程。"
    elif case_id == "b2b_sales":
        hard("years", "至少2年工作经验", "work_years", "GTE", "year", number=2.0)
        hard("salary", "期望月薪不高于20K", "expected_salary", "LTE", "month", number=20.0)
        skills = [requirement("discovery", "企业客户需求访谈", 2), requirement("negotiation", "合同谈判", 2),
                  requirement("pipeline", "销售漏斗管理", 2)]
        routes = [requirement("direct_route", "企业软件直销", 3, "EXPERIENCE"),
                  requirement("channel_route", "渠道销售", 3, "EXPERIENCE")]
        preferred = [requirement("crm", "CRM系统加分", 4, priority="PREFERRED"),
                     requirement("language", "英语沟通加分", 4, priority="PREFERRED")]
        keyword_specs = [("discovery", ["discovery", *routes], [("企业客户", []), ("需求访谈", []), ("渠道销售", [])]),
                         ("commercial", ["negotiation", "pipeline"], [("合同谈判", []), ("销售漏斗", []), ("签约", []), ("回款", [])]),
                         ("extra", preferred, [("CRM", []), ("英语", [])])]
        dimension_specs = [("prospecting", "客户开拓", "说明企业客户或渠道商机的开拓与筛选过程", routes),
                           ("discovery_solution", "需求与方案沟通", "通过需求访谈形成对应客户问题的销售方案", ["discovery"]),
                           ("commercial_progress", "商务推进", "记录谈判取舍、销售漏斗推进与合同签署过程", ["negotiation", "pipeline"]),
                           ("collections", "回款管理", "说明签约后的回款计划、风险跟进和可核验结果", [duty])]
        anchors = [("discovery", "独立访谈企业客户，明确采购目标与决策流程，并形成可追踪商机。"),
                   ("negotiation", "推进合同谈判，说明报价取舍、关键异议处理和签约结果的核验方式。"),
                   ("pipeline", "管理销售漏斗并跟进签约回款，保留客户推进记录与对应结果。")]
        weak_anchor = "只列客户或产品名称，无法说明需求访谈、谈判推进或签约回款的个人贡献。"
    else:
        raise ValueError("Unknown human-authored fixture job")
    groups = [{"group_id": "core_skills", "label": "共同必需能力", "operator": "ALL", "priority": "REQUIRED",
               "members": skills, "source_type": "JD", "source_text": lines[2], "needs_confirmation": False},
              {"group_id": "alternative_routes", "label": "可替代经验路线", "operator": "ANY", "priority": "REQUIRED",
               "members": routes, "source_type": "JD", "source_text": lines[3], "needs_confirmation": False},
              {"group_id": "mandatory", "label": "岗位必要要求组合", "operator": "ALL", "priority": "REQUIRED",
               "members": [*mandatory, "core_skills", "alternative_routes"], "source_type": "JD", "source_text": lines[1], "needs_confirmation": False},
              {"group_id": "preferred", "label": "非必要加分项", "operator": "ANY", "priority": "PREFERRED",
               "members": preferred, "source_type": "JD", "source_text": lines[4], "needs_confirmation": False}]
    keywords = [{"group_id": key, "label": key, "requirement_ids": refs,
                 "keywords": [{"canonical": word, "aliases": aliases, "weight": 2.0 if key == "extra" else 10.0}
                              for word, aliases in words]} for key, refs, words in keyword_specs]
    weights = case["expected_features"]["expected_dimension_weights"]
    dimensions = []
    for (key, label, description, refs), weight in zip(dimension_specs, weights):
        dimensions.append({"dimension_id": key, "label": label, "description": description,
                           "requirement_ids": refs, "weight": weight,
                           "rubric": [
                               {"min_score": 0, "max_score": 39, "description": "仅能复述" + label + "的概念，缺少可核验的具体个人行动。"},
                               {"min_score": 40, "max_score": 69, "description": "能说明" + label + "中实际承担的部分，过程和结果证据仍不完整。"},
                               {"min_score": 70, "max_score": 100, "description": "能够独立完成" + description + "，提供具体过程、取舍与可核验结果。"}]})
    threshold = case["expected_features"]["expected_thresholds"]
    return {"schema_version": "job_scoring_policy_v1", "job": {"title": source["job_title"], "summary": lines[0]},
            "sources": {"jd_text": source["jd_text"], "hr_requirements": source["hr_requirements"]},
            "requirements": requirements, "requirement_groups": groups, "hard_filters": hard_filters,
            "keyword_groups": keywords,
            "semantic_anchors": [{"anchor_id": "positive_" + str(index), "requirement_ids": [key], "polarity": "POSITIVE", "text": text}
                                 for index, (key, text) in enumerate(anchors)] +
                                [{"anchor_id": "weak_evidence", "requirement_ids": [duty], "polarity": "NEGATIVE", "text": weak_anchor}],
            "scoring_dimensions": dimensions,
            "thresholds": {"coarse_pass": float(threshold[0]), "deep_review": float(threshold[1]), "deep_recommend": float(threshold[2])},
            "diagnostics": [],
            "provenance": {"compiler_version": "human-authored-offline-fixture", "prompt_version": "not-a-live-prompt",
                           "validator_version": "policy-validator-v1", "model_version": "NO_MODEL_CALLED"}}


def check_policy_matches_case(policy, case_id):
    """Semantic acceptance assertions reusable for separate live output reports.

    Checking a real response does not turn this into a live test. Callers must
    keep the response, transport metrics and whether a model was called.
    """
    from config.policy_validation import diagnose_policy
    from schemas.job_policy import JobScoringPolicy
    value = JobScoringPolicy.model_validate(policy)
    case = next(item for item in compiler_cases() if item["case_id"] == case_id)
    expected = case["expected_features"]
    assert value.job.title == case["job_title"], "Job title changed"
    errors = [item.code for item in diagnose_policy(value, policy_input(case_id)) if item.severity == "ERROR"]
    assert not errors, "Policy has deterministic errors: " + ", ".join(errors)
    requirements = {item.requirement_id: item for item in value.requirements}
    hard_refs = {item.requirement_id for item in value.hard_filters}
    for word in expected["preferred_terms"]:
        preferred = [item for item in value.requirements
                     if word.casefold() in (item.label + item.source_text).casefold()
                     and item.priority in ("PREFERRED", "OPTIONAL")]
        assert preferred, "Missing preferred requirement: " + word
        assert not hard_refs.intersection(item.requirement_id for item in preferred), "Preference became a hard filter: " + word
    assert any(group.operator == "ALL" and group.priority == "REQUIRED" and len(group.members) >= 2
               for group in value.requirement_groups), "Missing required ALL capability combination"
    assert any(group.operator == "ANY" and group.priority == "REQUIRED" and len(group.members) >= 2
               for group in value.requirement_groups), "Missing alternative required ANY route"
    assert sorted(round(item.weight, 8) for item in value.scoring_dimensions) == sorted(expected["expected_dimension_weights"]), "HR dimension weights were changed"
    actual_thresholds = [value.thresholds.coarse_pass, value.thresholds.deep_review, value.thresholds.deep_recommend]
    assert actual_thresholds == expected["expected_thresholds"], "Explicit HR thresholds were changed"
    hard_fields = {item.field for item in value.hard_filters}
    if case_id == "ai_automation":
        assert "work_years" not in hard_fields, "AI fixture explicitly forbids an added work-year gate"
    if case_id == "b2b_sales":
        assert "education_level" not in hard_fields, "Sales fixture explicitly has no education gate"
    keyword_text = " ".join(word.canonical + " " + " ".join(word.aliases)
                            for group in value.keyword_groups for word in group.keywords).casefold()
    if case_id in ("java_backend", "b2b_sales"):
        for forbidden in ("langgraph", "langchain", "rag", "playwright", "selenium", "agent", "rpa"):
            assert forbidden not in keyword_text, "Unrelated AI/RPA keyword leaked into another job: " + forbidden
    if case_id == "b2b_sales":
        assert "java" not in keyword_text, "Java leaked into sales keywords"
    return {
        "case_id": case_id,
        "hard_filters": [{"field": item.field, "operator": item.operator,
                          "value_text": item.value_text, "value_number": item.value_number, "unit": item.unit}
                         for item in value.hard_filters],
        "required_all_groups": [item.group_id for item in value.requirement_groups if item.operator == "ALL" and item.priority == "REQUIRED"],
        "required_any_groups": [item.group_id for item in value.requirement_groups if item.operator == "ANY" and item.priority == "REQUIRED"],
        "keyword_groups": [item.label for item in value.keyword_groups],
        "keywords": [word.canonical for group in value.keyword_groups for word in group.keywords],
        "anchors": [item.text for item in value.semantic_anchors],
        "dimensions": [{"dimension_id": item.dimension_id, "label": item.label, "weight": item.weight,
                        "rubric": [band.description for band in item.rubric]} for item in value.scoring_dimensions],
        "thresholds": actual_thresholds,
        "hard_requirement_sources": [requirements[key].source_text for key in sorted(hard_refs)],
    }
