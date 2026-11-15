"""Fixed offline Tier 0/1 comparison on real Repository-bound policy versions.

Policies are preserved Stage 3 Qwen artifacts (their JD inputs were synthetic).
The 27 new resumes are human-authored synthetic data. Existing five repository
fixtures are a separate, unverified-origin group. No model or Mock model runs.
"""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import statistics
import tempfile
from time import perf_counter_ns

from config.settings import PROJECT_ROOT
from engine.hard_filter import check_hard_filters
from engine.policy_interpreter import PolicyInterpreter
from engine.prescreen_engine import PreliminaryScreeningEngine
from engine.rule_matcher import RuleMatcherMixin
from schemas import ResumeData
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository
from tests.mock_data import BENCHMARK_CANDIDATES


DATASET_PATH = PROJECT_ROOT / "tests/fixtures/prescreen_candidates.json"
POLICY_DIRECTORY = PROJECT_ROOT / "reports/phase1_7_stage3/policies"
REPORT_DIRECTORY = PROJECT_ROOT / "reports/phase1_7_stage4"
JOB_CASES = ("ai_automation", "java_backend", "b2b_sales")


class _LegacyDiagnosticRules(RuleMatcherMixin):
    """Use only the existing pure rule mixin, never HybridScoringEngine."""
    def __init__(self, config):
        self.config = deepcopy(config)


def _milliseconds(started):
    return round((perf_counter_ns() - started) / 1_000_000, 6)


def _timing_summary(samples):
    ordered = sorted(samples)
    return {"samples": len(samples), "median_ms": round(statistics.median(samples), 6),
            "p95_observed_ms": ordered[max(0, math.ceil(.95 * len(ordered)) - 1)],
            "min_ms": ordered[0], "max_ms": ordered[-1]}


def _differences(case, legacy, current):
    explanations = ["旧Tier1诊断值为0–45类别饱和分；新分为0–100动态规则初筛，分数不是同一口径",
                    "旧对照固定读取原AI config.json；新结果读取此岗位候选人的绑定Policy，未运行Tier2/Tier3"]
    if legacy["hard_filter_status"] != current["hard_filter_status"]:
        explanations.append("硬条件结论不同：旧检查为学历/12K预算等固定AI条件，新检查以本岗位Policy客观谓词和三态图为准")
    profile = case.get("profile")
    if profile == "negated_experience":
        explanations.append("旧关键词函数会命中否定句内术语；新局部语境将明确否定/无实践证据置0，不把缺经验当正向命中")
    if profile == "keyword_only":
        explanations.append("只有技能标签，按skills辅助权重计算；不冒充职责/项目实践")
    if profile == "repeated_keywords":
        explanations.append("重复职责与多个别名只保留同canonical的最强证据，不能靠重复次数叠分")
    if profile == "alternative_route":
        explanations.append("要求图按ANY选一条可替代路线，不同时强制两条技术/业务路线")
    if profile == "missing_information":
        explanations.append("缺失学历/年限/薪资及采集区块，保留UNKNOWN和未封存预览，不填默认值或自动淘汰")
    if profile == "similar_but_different":
        explanations.append("相似术语不等于当前Policy的完整关键词；英文保留标识符边界，中文复杂语义仍需人工核验")
    if case["origin"] != "human_authored_synthetic_candidate":
        explanations.append("原五份样本含旧式无显式周期K薪资；新执行profile不默认月薪，因此可能UNKNOWN；没有核实平台数据真实性")
    if legacy["hard_filter_status"] == "FAIL":
        explanations.append("旧生产链路会在Tier0结束；此处旧Tier1数值仅为独立纯函数诊断，不是既有正式评分")
    return explanations


def _fixed_assertions(records):
    failures, checks = [], 0
    synthetic = [row for row in records if row["origin"] == "human_authored_synthetic_candidate"]
    def verify(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(message)
    for row in records:
        value = row["new"]
        verify(value["scoring_status"] != "SCORING_FAILED", row["case_id"] + ": screening execution failed")
        verify(value["policy_version"] == 1, row["case_id"] + ": wrong bound policy version")
        verify(value["assessment_scope"] == "TIER0_TIER1_ONLY", row["case_id"] + ": wrong scoring scope")
        expected_dimensions = row["policy_dimension_ids"]
        verify([item["dimension_id"] for item in value["dimension_scores"]] == expected_dimensions,
               row["case_id"] + ": dynamic dimensions differ from policy")
        verify(value["deep_score"] is None and value["deep_thresholds_applied"] is False,
               row["case_id"] + ": preliminary result pretends to be a deep result")
    for row in synthetic:
        for key in ("hard_status", "scoring_status"):
            if key in row["expected"]:
                actual_key = "hard_filter_status" if key == "hard_status" else key
                verify(row["new"][actual_key] == row["expected"][key], row["case_id"] + ": unexpected " + actual_key)
    for job in JOB_CASES:
        selected = {row["profile"]: row["new"] for row in synthetic if row["job_case_id"] == job}
        full = selected["complete_match"]
        for weaker in ("partial_match", "keyword_only", "negated_experience", "similar_but_different"):
            low = selected[weaker]["preliminary_score"]
            verify(full["preliminary_score"] is not None and low is not None and full["preliminary_score"] > low,
                   job + ": practical full profile must exceed " + weaker)
        verify(full["preliminary_score"] == selected["repeated_keywords"]["preliminary_score"],
               job + ": repeated keywords inflated score")
        verify(selected["alternative_route"]["hard_filter_status"] == "PASS", job + ": valid alternative was hard rejected")
        verify(selected["keyword_only"]["preliminary_score"] is not None and selected["keyword_only"]["preliminary_score"] <= 50,
               job + ": label-only evidence approached practical full score")
        verify(any(item["context"] in ("NEGATED", "NO_PRACTICE") for item in selected["negated_experience"]["keyword_evidence"]),
               job + ": missing explicit negative evidence trace")
        verify(all(item["source_weight"] == 0 for item in selected["negated_experience"]["keyword_evidence"]),
               job + ": an exclusively negative resume produced positive keyword evidence")
        learning = [item for item in selected["partial_match"]["keyword_evidence"] if item["context"] == "LEARNING"]
        verify(bool(learning) and all(item["source_weight"] <= .35 for item in learning),
               job + ": explicit learning must stay an auxiliary signal, not practical experience")
    return {"assertions": checks, "failures": failures, "ok": not failures}


def _fixture_control_assertions(cases):
    """Check the repetition control independently of computed scoring results."""
    failures, checks = [], 0
    def verify(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(message)
    def vocabulary(value):
        if isinstance(value, str):
            return set(re.findall(r"[\w+#]+", value.casefold()))
        if isinstance(value, list):
            return set().union(*(vocabulary(item) for item in value)) if value else set()
        if isinstance(value, dict):
            return set().union(*(vocabulary(item) for key, item in value.items() if key not in ("name", "_meta")))
        return set()
    for job in JOB_CASES:
        base = next(case for case in cases if case["job_case_id"] == job and case["profile"] == "complete_match")
        repeated = next(case for case in cases if case["job_case_id"] == job and case["profile"] == "repeated_keywords")
        control = repeated.get("control_equivalence", {})
        first, second = base["resume"], repeated["resume"]
        verify(control.get("comparison_case_id") == base["case_id"], job + ": repetition control references the wrong base profile")
        verify(second["work_experience"] == first["work_experience"] * control.get("work_repetitions", 0),
               job + ": repeated profile changed work evidence instead of duplicating existing sources")
        verify(second["skills"] == first["skills"] * control.get("skills_repetitions", 0),
               job + ": repeated profile introduced new skill labels")
        excluded = {"name", "_meta", "work_experience", "skills"}
        verify({key: value for key, value in first.items() if key not in excluded} ==
               {key: value for key, value in second.items() if key not in excluded},
               job + ": repetition profile changed an independent input variable")
        verify(vocabulary(first) == vocabulary(second), job + ": repetition profile changed the independent source vocabulary")
    return {"assertions": checks, "failures": failures, "ok": not failures}


def run_benchmark(*, include_original=True, measure_performance=True):
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = deepcopy(dataset["cases"])
    if len(cases) != 27 or any(sum(case["job_case_id"] == job for case in cases) != 9 for job in JOB_CASES):
        raise ValueError("The fixed dataset must contain nine profiles for each of three jobs")
    if include_original:
        for index, resume in enumerate(BENCHMARK_CANDIDATES, 1):
            for job in JOB_CASES:
                cases.append({"case_id": f"repository_original_{index}:{job}", "job_case_id": job,
                    "profile": f"repository_original_{index}", "origin": "repository_original_fixture_unverified",
                    "expected": {}, "resume": deepcopy(resume)})
    legacy_config = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))
    legacy_rules = _LegacyDiagnosticRules(legacy_config)
    policies = {job: json.loads((POLICY_DIRECTORY / (job + ".json")).read_text(encoding="utf-8")) for job in JOB_CASES}
    fixture_controls = _fixture_control_assertions(dataset["cases"])
    records, elapsed = [], []
    performance = {"scope": "Local file SQLite and pure Tier0/1; not a production SLA", "model_calls": 0}
    with tempfile.TemporaryDirectory(prefix="prescreen_benchmark_") as temporary:
        database = Database(Path(temporary) / "benchmark.sqlite3")
        database.migrate()
        repository = RecruitmentRepository(database)
        system = ProvisioningContext("isolated deterministic Tier0/1 benchmark; no model calls")
        repository.create_user(system, "benchmark:owner", user_id="owner")
        repository.create_organization(system, "Offline benchmark", "owner", org_id="benchmark")
        context = AccessContext("benchmark", "owner")
        for job, policy in policies.items():
            repository.create_job(context, policy["job"]["title"], job_id=job)
            profile = repository.create_execution_profile(context, {"purpose": "offline prescreen benchmark"})
            source = {"job_title": policy["job"]["title"], **policy["sources"]}
            draft = repository.create_policy_draft(context, job, policy, source_json=source)
            run = repository.record_policy_compilation(context, job, source, status="SUCCEEDED",
                provenance=policy["provenance"], policy_json=policy, draft_id=draft["draft_id"], draft_revision=1,
                metrics={"mode": "OFFLINE_STAGE3_POLICY_ARTIFACT_REPLAY", "model_calls_this_run": 0,
                         "origin": str(POLICY_DIRECTORY / (job + ".json"))})
            confirmation = repository.confirm_policy_draft(context, job, draft["draft_id"], run["run_id"], draft["draft_hash"])
            version = repository.create_policy_version(context, job, policy,
                execution_profile_id=profile["profile_id"], policy_schema_version="job_scoring_policy_v1",
                source_draft_id=draft["draft_id"], expected_draft_revision=1,
                compilation_run_id=run["run_id"], confirmation_id=confirmation["confirmation_id"])
            repository.publish_policy(context, job, version["policy_id"], confirmation_hash=draft["draft_hash"])
            repository.create_scan_batch(context, job, batch_id="batch:" + job)
        engine = PreliminaryScreeningEngine(repository)
        for case in cases:
            job = case["job_case_id"]
            raw = case["resume"]
            resume = ResumeData.model_validate(raw)
            hard_status, checks = check_hard_filters(resume, legacy_config)
            original, capped, tags, details = legacy_rules._run_tier1_rules(resume)
            acquired = repository.acquire_candidate_for_job(context, job, "batch:" + job, case["case_id"],
                source="stage4_offline_benchmark", external_id=resume.meta.encrypt_geek_id, resume=raw)
            start = perf_counter_ns()
            output = engine.evaluate(context, job, acquired["job_candidate"]["job_candidate_id"],
                resume_id=acquired["resume_snapshot"]["resume_id"])
            duration = _milliseconds(start)
            elapsed.append(duration)
            value = output.model_dump(mode="json")
            old = {"hard_filter_status": hard_status, "hard_checks": checks, "tier1_raw_score": original,
                   "tier1_diagnostic_score_45": capped, "matched_tags": tags, "hit_details": details,
                   "production_would_run_tier1": hard_status != "FAIL",
                   "calculation_kind": "PURE_TIER1_DIAGNOSTIC_EVEN_WHEN_OLD_TIER0_FAILS"}
            records.append({"case_id": case["case_id"], "job_case_id": job, "profile": case["profile"],
                "origin": case["origin"], "expected": case["expected"], "legacy": old, "new": value,
                "policy_dimension_ids": [item["dimension_id"] for item in policies[job]["scoring_dimensions"]],
                "elapsed_ms": duration, "difference_explanations": _differences(case, old, value)})
        performance["repository_bound_end_to_end"] = _timing_summary(elapsed)
        performance["interpreter_cache"] = {"hits": engine.interpreter.cache_hits,
            "misses": engine.interpreter.cache_misses, "size": engine.interpreter.cache_size}
        if measure_performance:
            cold, warm = [], []
            for job, policy in policies.items():
                interpreter = PolicyInterpreter()
                identity = {"org_id": "benchmark", "job_id": job, "policy_id": "timing:" + job, "policy_version": 1}
                start = perf_counter_ns(); interpreter.compile_policy(policy, **identity); cold.append(_milliseconds(start))
                for _ in range(100):
                    start = perf_counter_ns(); interpreter.compile_policy(policy, **identity); warm.append(_milliseconds(start))
            performance["cold_compile"] = _timing_summary(cold)
            performance["warm_compile"] = _timing_summary(warm)
        check = database.check()
        if not check["ok"]:
            raise AssertionError("Benchmark database integrity check failed")
    assertions = _fixed_assertions(records)
    assertions["assertions"] += fixture_controls["assertions"]
    assertions["failures"].extend(fixture_controls["failures"])
    assertions["ok"] = not assertions["failures"]
    return {"dataset_version": dataset["dataset_version"], "model_calls_this_run": 0,
        "data_origin": {"synthetic": "27 human-authored fixed resumes; not real platform data",
                        "original_five": "Repository fixtures; screenshot claims/authenticity not verified this stage",
                        "policies": "Preserved real Stage3 Qwen outputs from synthetic JDs, replayed with no new model call"},
        "groups": {"synthetic_profiles": 27, "original_fixture_job_evaluations": len(cases) - 27},
        "fixture_control_checks": fixture_controls, "assertions": assertions,
        "performance": performance, "records": records, "ok": assertions["ok"]}


def write_report(report, directory=REPORT_DIRECTORY):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    (directory / "benchmark_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Stage 4 固定候选集对照", "", "本次仅运行真实规则和实际Repository，没有Qwen、BGE或Mock模型调用。",
             "27份候选人为人工合成；原五份仓库样本来源本阶段未核实，按岗位分别形成15项。Policy来自Stage3真实Qwen输出，JD输入为合成数据。", "",
             "旧分仅为纯函数0–45诊断值，旧Tier0失败时原生产链路本不会运行Tier1；新分为0–100初筛，不能相减当作匹配质量提升。", ""]
    for group, description in (("human_authored_synthetic_candidate", "人工合成27画像"),
                               ("repository_original_fixture_unverified", "原五份样本×三岗位（来源未核实）")):
        lines += ["## " + description, "", "| 岗位/画像 | 旧硬状态 | 旧Tier1诊断/45 | 新硬状态 | 新初筛/100 | 新状态 |", "| --- | --- | --- | --- | --- | --- |"]
        for row in report["records"]:
            if row["origin"] == group:
                lines.append(f"| {row['job_case_id']}/{row['profile']} | {row['legacy']['hard_filter_status']} | {row['legacy']['tier1_diagnostic_score_45']} | {row['new']['hard_filter_status']} | {row['new']['preliminary_score']} | {row['new']['scoring_status']} |")
        lines.append("")
    lines += ["## 差异解释与证据", "", "每条结果的旧检查/命中、动态维度、精确原文片段和偏移、要求图路径、差异解释均保存在benchmark_results.json。",
              "固定AI预算/学历与岗位独立配置、来源强度和语境、新旧分数公式、默认薪资周期差异是主要来源，不能把岗位更换造成的分数差异当模型质量指标。", "",
              "重复画像采用prescreen-fixed-v2：只复制complete画像已有职责和skills，独立检查源字段等价与词集不变。初始v1曾额外加入此前没有的技能标签，不能用该无效控制样本声称重复计分回归；原始数据和结果保留在initial_repetition_fixture_failure.json。", "",
              "## 自动断言", "", f"执行{report['assertions']['assertions']}项固定集断言，失败{len(report['assertions']['failures'])}项。"]
    lines += ["- " + failure for failure in report["assertions"]["failures"]]
    lines += ["", "## 本机测量", "", "下列耗时为本次临时SQLite与本地规则执行的观测值，不是生产SLA。冷编译仅3次，缓存命中编译300次；端到端包含Repository读取与证据校验。", "", "```json", json.dumps(report["performance"], ensure_ascii=False, indent=2), "```", ""]
    (directory / "benchmark_comparison.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline fixed Tier0/1 benchmark; no model calls")
    parser.add_argument("--output", type=Path, default=REPORT_DIRECTORY)
    parser.add_argument("--synthetic-only", action="store_true")
    args = parser.parse_args(argv)
    report = run_benchmark(include_original=not args.synthetic_only)
    write_report(report, args.output)
    print(json.dumps({"ok": report["ok"], "groups": report["groups"], "assertions": report["assertions"],
                      "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
