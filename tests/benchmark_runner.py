"""Reproducible, offline-only comparison of five preserved benchmark fixtures.

No branch in this CLI constructs a real embedding model or a remote LLM client.
The fixed LLM response checks contracts and routing, not recruiting quality.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from config.settings import PROJECT_ROOT, DEFAULT_CONFIG_PATH

from storage.scoring_cache import stable_hash
from engine.hybrid_engine import HybridScoringEngine
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import DeterministicEmbedder, DeterministicLLMClient
from tests.mock_models import valid_review


ROOT = PROJECT_ROOT
WARNING = (
    "OFFLINE MOCK ONLY: deterministic feature vectors are not BGE scores. "
    "Fixed four-dimensional LLM scores 90/85/80/90 (weighted 86.5) only test contracts "
    "and routing; they do not assess candidate quality or reproduce historical Qwen results."
)


def mock_engine(config_path=DEFAULT_CONFIG_PATH, *, error=None, response=valid_review):
    client = DeterministicLLMClient(response=response, error=error)
    client.version = "mock-fixed-evidence-v2"
    return HybridScoringEngine(
        str(config_path), embedding_model_path="mock-feature-v1",
        llm_base_url="http://offline-mock.invalid/v1", llm_api_key="offline-no-key",
        llm_model_name="mock-fixed-review-v2", embedder=DeterministicEmbedder(),
        llm_client=client,
    )


def _dump(result):
    return result.model_dump(mode="json")


def _same_score(first, second):
    return first.model_dump(exclude={"cache_hit"}) == second.model_dump(exclude={"cache_hit"})


def boundary_results(config_path):
    base = deepcopy(BENCHMARK_CANDIDATES[0])
    results = {
        "llm_timeout": _dump(mock_engine(config_path, error=TimeoutError("offline timeout fixture")).evaluate(base, "deep_mode")),
        "llm_invalid_json": _dump(mock_engine(config_path, response="invalid-json").evaluate(base, "deep_mode")),
    }
    for key, changes in {
        "partial_resume": {"data_completeness": "PARTIAL"},
        "collection_failed": {"ok": False},
        "unknown_degree": {"degree": "未知", "education": []},
        "labels_only": {"advantage": "", "skills": "", "project_experience": [], "education": [],
                        "work_experience": [{"position": "LangGraph工程师", "keywords": ["Python", "LangGraph", "RAG"]}]},
        "history_customer_service_with_agent_project": {"work_experience": [{"position": "客服", "responsibility": "负责客户咨询服务"}]},
    }.items():
        data = deepcopy(base)
        data.update(changes)
        results[key] = _dump(mock_engine(config_path).evaluate(data, "deep_mode"))
    for key, salary in [("daily_salary", "150-200元/天"), ("missing_salary", ""), ("decimal_over_budget", "12.5-15K")]:
        data = deepcopy(base)
        data["expectation"]["salary"] = salary
        results[key] = _dump(mock_engine(config_path).evaluate(data, "deep_mode"))
    return results


def collect_benchmark(config_path=DEFAULT_CONFIG_PATH):
    config_path = Path(config_path)
    candidates_before = deepcopy(BENCHMARK_CANDIDATES)
    engine = mock_engine(config_path)
    rows, stability = [], []
    for sample in BENCHMARK_CANDIDATES:
        fast = engine.evaluate(deepcopy(sample), "fast_mode")
        deep = engine.evaluate(deepcopy(sample), "deep_mode")
        repeated = engine.evaluate(deepcopy(sample), "deep_mode")
        fresh = mock_engine(config_path).evaluate(deepcopy(sample), "deep_mode")
        rows.append({"candidate_key": sample["_meta"]["candidate_key"], "name": sample["name"],
                     "fast": _dump(fast), "deep": _dump(deep)})
        stability.append({"candidate_key": deep.candidate_key,
                          "repeat_same_output_except_cache_hit": _same_score(deep, repeated),
                          "fresh_engine_same_output": _same_score(deep, fresh),
                          "repeat_cache_hit": repeated.cache_hit})
    if BENCHMARK_CANDIDATES != candidates_before:
        raise AssertionError("Benchmark fixture mutation detected")
    sources = ["engine/hybrid_engine.py", "schemas/common.py", "schemas/candidate.py",
               "schemas/card.py", "schemas/evaluation.py", "engine/resume_text.py",
               "engine/rule_matcher.py", "engine/semantic_ranker.py", "engine/llm_reviewer.py",
               "engine/scoring_session.py", "config/settings.py", "config/validation.py",
               "engine/hard_filter.py", "storage/scoring_cache.py", "tests/benchmark_runner.py",
               "tests/mock_data.py", "tests/mock_models.py"]
    return {
        "report_version": "scoring-v2-offline-benchmark-1",
        "warning": WARNING,
        "model_execution": {"embedding": "DeterministicEmbedder/mock-feature-v1", "llm": "fixed-evidence-v2",
                            "fixed_dimension_scores": [90, 85, 80, 90], "network_calls": 0, "real_models_loaded": False},
        "fixture_count": len(BENCHMARK_CANDIDATES),
        "fixture_content_hash": stable_hash(BENCHMARK_CANDIDATES),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
        "config_snapshot": engine.config,
        "version_info": rows[0]["deep"]["version_info"],
        "candidates": rows,
        "stability_checks": stability,
        "boundaries": boundary_results(config_path),
    }


def _number(value):
    return "—" if value is None else f"{value:.2f}"


def comparison_markdown(report, baseline):
    old_rows = {row["candidate_key"]: row for row in baseline["candidates"]}
    lines = [
        "# Scoring Engine V2 五类基准对比（离线 Mock）", "",
        "**这些分数仅验证算法公式、数据契约和路由，不代表真实 BGE/Qwen 匹配质量。**", "",
        "同一确定性 Mock Embedding；LLM 固定四维 90/85/80/90，权重 40/30/20/10 得 86.5。"
        "固定深度分没有区分候选人能力，不能据此判断匹配质量提升。", "",
        "旧基线在修改前实际执行并保存在 `baseline_v1.json`；V2 由 `python -m tests.benchmark_runner` 生成。"
        "旧版 total 字段包含粗排返回值，V2 只有成功深度评分才有正式分，空值显示为 —。"
        "V1 未记录独立 scoring_status，状态列仅展示 V2 实际输出。", "",
        "| 原始样本 | Tier1 原始 V1→V2 | Tier1 折算 V1→V2 | Tier2 V1→V2 | 粗排 V1→V2 | 旧 total → V2 深度分 | V2 状态 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, row in enumerate(report["candidates"], 1):
        old, new = old_rows[row["candidate_key"]], row["deep"]
        trace = new["debug_trace"]
        label = ["Agent 实战", "工作流/RPA", "传统 Python", "文案运营", "超预算"][index - 1]
        lines.append(f"| {index}. {label} | {_number(old['tier1_raw'])} → {_number(trace['tier1_raw_score'])} | "
                     f"{_number(old['tier1_capped'])} → {_number(trace['tier1_capped_score'])} | "
                     f"{_number(old['tier2_semantic'])} → {_number(trace['tier2_semantic_score'])} | "
                     f"{_number(old['coarse_score'])} → {_number(new['coarse_score'])} | "
                     f"{_number(old['deep_total_mock'])} → {_number(new['total_score'])} | {new['scoring_status']} |")
    lines.extend(["", "| 样本 | V1 fast 路由 | V2 fast 路由 | V1 deep 路由 | V2 deep 路由 |", "| --- | --- | --- | --- | --- |"])
    for index, row in enumerate(report["candidates"], 1):
        old = old_rows[row["candidate_key"]]
        lines.append(f"| {index} | {old['fast_route_old']} | {row['fast']['routing_decision']} | {old['deep_route_old']} | {row['deep']['routing_decision']} |")
    lines.extend(["", "所有 V2 fast 结果均无正式深度分，且不进入 AUTO_GREET。NOT_RECOMMENDED 仅表示不推荐，最终招聘判断交 HR。", "",
                  "## 边界场景", "", "| 场景 | 状态 | 正式分 | 路由 |", "| --- | --- | ---: | --- |"])
    for key, result in report["boundaries"].items():
        lines.append(f"| {key} | {result['scoring_status']} | {_number(result['total_score'])} | {result['routing_decision']} |")
    stable = all(row["repeat_same_output_except_cache_hit"] and row["fresh_engine_same_output"] for row in report["stability_checks"])
    lines.extend(["", f"重复输入与新引擎复算：五类样本 {'全部稳定' if stable else '存在差异，请查看 JSON'}。"
                  "该结论仅适用于确定性 Mock；temperature=0 不能保证远端模型跨版本的确定性。", "",
                  "完整参数快照、版本、每块相似度、惩罚、证据、路由原因及边界输出见 `benchmark_v2_mock.json`。", "",
                  "## 验证边界", "", "本报告的 Mock 运行未加载真实 BGE，也未调用 Qwen；另外独立完成的真实 BGE fast 检查见 local_bge_fast.json 和 delivery_v2.md。当前阈值和映射区间仍需人工标注样本及真实模型校准。"
                  "开发文档中的历史分数未按本次环境复现，不能与此 Mock 分数直接比较。", ""])
    return "\n".join(lines)


def write_reports(config_path=DEFAULT_CONFIG_PATH, output_dir=ROOT / "reports"):
    report = collect_benchmark(config_path)
    baseline = json.loads((ROOT / "reports" / "baseline_v1.json").read_text(encoding="utf-8"))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "benchmark_v2_mock.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "benchmark_comparison.md").write_text(comparison_markdown(report, baseline), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline Mock benchmark only; never calls BGE or a remote LLM")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    args = parser.parse_args(argv)
    report = write_reports(args.config, args.output_dir)
    print(WARNING)
    for row in report["candidates"]:
        result = row["deep"]
        print(f"{row['name']}: coarse={result['coarse_score']}, deep={result['total_score']}, "
              f"status={result['scoring_status']}, route={result['routing_decision']}")
    print(f"Reports: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
