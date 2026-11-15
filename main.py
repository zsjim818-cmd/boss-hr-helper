"""Unified CLI for the implemented scoring phase; all defaults stay offline."""
import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Scoring Engine V2")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("benchmark", add_help=False, help="离线Mock基准与报告")
    commands.add_parser("diagnose", add_help=False, help="五类样本诊断，默认Mock")
    commands.add_parser("sample", add_help=False, help="单简历诊断，默认Mock")
    commands.add_parser("llm-diagnose", add_help=False, help="单样本真实Tier3诊断，跳过BGE")
    for command in ("db-init", "db-check", "db-migrate"):
        commands.add_parser(command, add_help=False, help="SQLite业务库初始化/检查/迁移")
    policy_commands = ("policy-schema", "policy-validate", "policy-compile", "policy-draft", "policy-update",
                       "policy-confirm", "policy-version", "policy-publish", "policy-current", "policy-history", "policy-runs")
    for command in policy_commands:
        commands.add_parser(command, add_help=False, help="通用岗位策略编译/核验/人工确认发布")
    commands.add_parser("policy-diagnose", add_help=False, help="三个岗位真实Qwen编译验收；需显式--live")
    commands.add_parser("prescreen", add_help=False, help="按候选人绑定版本执行通用Tier0/Tier1，不调用模型")
    commands.add_parser("prescreen-benchmark", add_help=False, help="固定候选集新旧Tier0/Tier1对照，不调用模型")
    commands.add_parser("tier3-review", add_help=False,
                        help="通用Policy驱动的Tier3终审；默认离线Mock，--live调用真实Qwen")
    semantic_commands = ("embedding-check", "semantic-prepare", "semantic-diagnose")
    for command in semantic_commands:
        commands.add_parser(command, add_help=False, help="Stage5语义资产核验/准备与只读Tier2诊断")
    tests = commands.add_parser("test", help="运行全部离线自动化测试")
    tests.add_argument("--quiet", "-q", action="store_true")
    args, remaining = parser.parse_known_args(argv)
    if args.command == "prescreen":
        from engine.prescreen_cli import main as run
        return run(remaining)
    if args.command == "tier3-review":
        from engine.pipeline_cli import main as run
        return run(args.command, remaining)
    if args.command == "prescreen-benchmark":
        from tests.prescreen_benchmark import main as run
        return run(remaining)
    if args.command in semantic_commands:
        from engine.semantic_cli import main as run
        return run(args.command, remaining)
    if args.command in policy_commands:
        from config.policy_cli import main as run
        return run(args.command, remaining)
    if args.command == "policy-diagnose":
        from tests.policy_live import main as run
        return run(remaining)
    if args.command in ("db-init", "db-check", "db-migrate"):
        from storage.database_cli import main as run
        return run(args.command, remaining)
    if args.command == "benchmark":
        from tests.benchmark_runner import main as run
        return run(remaining)
    if args.command == "diagnose":
        from tests.test_benchmark import main as run
        return run(remaining)
    if args.command == "sample":
        from tests.test_engine import main as run
        return run(remaining)
    if args.command == "llm-diagnose":
        from engine.llm_diagnostics import main as run
        return run(remaining)
    if args.command == "test":
        if remaining:
            parser.error("unrecognized arguments: " + " ".join(remaining))
        import unittest
        from config.settings import PROJECT_ROOT
        suite = unittest.defaultTestLoader.discover(
            str(PROJECT_ROOT / "tests"), top_level_dir=str(PROJECT_ROOT))
        result = unittest.TextTestRunner(verbosity=1 if args.quiet else 2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if remaining:
        parser.error("unrecognized arguments: " + " ".join(remaining))
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
