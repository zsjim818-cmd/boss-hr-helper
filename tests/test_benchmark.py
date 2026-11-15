from engine.hybrid_engine import HybridScoringEngine
from config.settings import DEFAULT_CONFIG_PATH
from tests.mock_data import BENCHMARK_CANDIDATES


def print_diagnostic_report(res):
    t = res.debug_trace
    print("\n" + "=" * 78)
    print(f"👤 候选人: {res.name}")
    print(f"🚦 路由: 【{res.routing_decision}】 | 正式深度分: {res.total_score} (粗排: {res.coarse_score})")
    print(f"  模式/状态: {res.scoring_mode}/{res.scoring_status} | 数据: {res.data_completeness} | 缓存: {res.cache_hit}")
    print(f"  路由原因: {t.route_reason}")
    print(f"  版本: {res.version_info}")
    if res.data_issues:
        print(f"  数据问题: {[i.model_dump() for i in res.data_issues]}")
    print("-" * 78)
    print(f"  [Tier 0 硬性过滤] {t.hard_filter_status}: {t.hard_filter_checks}")
    if not t.hard_filter_passed:
        return

    print(f"  [Tier 1 规则引擎] 原始分: {t.tier1_raw_score} -> 封顶折算: {t.tier1_capped_score}/45.0")
    print(f"     └─ 命中明细: {', '.join(t.tier1_hit_details) if t.tier1_hit_details else '无'}")

    print(f"  [Tier 2 BGE语义] 语义得分: {t.tier2_semantic_score}/55.0 (负向惩罚扣分: -{t.tier2_penalty_applied})")
    print(f"     └─ 路线 OR 聚合: {res.semantic_similarity} (max相似度仅诊断，不直接评分)")
    for cd in t.tier2_chunk_details:
        print(f"        • 段落: {cd.chunk_preview}")
        print(f"          正相似度: {cd.best_pos_sim} (匹配锚点: {cd.best_pos_anchor}) | 负相似度: {cd.best_neg_sim}")
        print(f"          来源: {cd.source_path} ({cd.source}) | 路线分: {cd.route_scores} | 同块惩罚: {cd.route_penalties} | Top-K: {cd.selected_routes}")

    if t.tier3_llm_raw_scores:
        print(f"  [Tier 3 LLM终审] 四维原始百分制: {t.tier3_llm_raw_scores}")
        print(f"     └─ 动态加权折算分: {res.dimension_scores}")
        print(f"     └─ 证据/缺失/置信度: { {k:v.model_dump() for k,v in t.tier3_dimension_evidence.items()} }")
    print(f"  ✨ 核心亮点: {res.highlights}")
    print(f"  ⚠️ 风险/说明: {res.risk_point}")


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="五类原样本诊断；默认离线Mock")
    parser.add_argument("--live", action="store_true", help="显式使用本地BGE；deep_mode还会调用配置的LLM API")
    parser.add_argument("--mode", choices=["fast_mode", "deep_mode"], default="fast_mode")
    parser.add_argument("--json-output", help="将本次参数、版本和完整评分输出写入JSON")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    if args.live:
        engine = HybridScoringEngine(config_path=args.config)
    else:
        from tests.mock_models import DeterministicEmbedder, DeterministicLLMClient
        from tests.mock_models import valid_review
        print("OFFLINE MOCK：固定响应仅验证算法流程，不代表BGE/Qwen评分质量")
        engine = HybridScoringEngine(config_path=args.config, embedder=DeterministicEmbedder(), llm_client=DeterministicLLMClient(response=valid_review), llm_model_name="mock-fixed-v2")
    results = engine.evaluate_batch(BENCHMARK_CANDIDATES, force_mode=args.mode)
    for result in results:
        print_diagnostic_report(result)
    if args.json_output:
        import json
        from pathlib import Path
        Path(args.json_output).write_text(json.dumps({
            "execution": "real_bge" if args.live else "offline_mock",
            "scoring_mode": args.mode, "config_snapshot": engine.config,
            "candidates": [r.model_dump(mode="json") for r in results],
        }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
