from engine.hybrid_engine import HybridScoringEngine
from config.settings import DEFAULT_CONFIG_PATH
from tests.mock_data import SAMPLE_RESUME_JSON


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="单简历诊断；默认离线Mock")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mode", choices=["fast_mode", "deep_mode"], default="fast_mode")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    if args.live:
        engine = HybridScoringEngine(config_path=args.config)
    else:
        from tests.mock_models import DeterministicEmbedder, DeterministicLLMClient
        from tests.mock_models import valid_review
        print("OFFLINE MOCK：仅验证评分流程，不代表真实模型评分质量")
        engine = HybridScoringEngine(config_path=args.config, embedder=DeterministicEmbedder(), llm_client=DeterministicLLMClient(response=valid_review), llm_model_name="mock-fixed-v2")
    result = engine.evaluate(SAMPLE_RESUME_JSON, force_mode=args.mode)
    print(result.model_dump_json(indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
