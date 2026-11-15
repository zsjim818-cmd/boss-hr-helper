"""One real Tier 3 request without loading BGE, mocks, or a five-resume batch."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter

from openai import OpenAI, DefaultHttpxClient

from config.settings import DEFAULT_CONFIG_PATH, REPORTS_DIR, load_runtime_settings
from config.validation import validate_config
from engine.hard_filter import check_hard_filters
from engine.llm_reviewer import LLMReviewerMixin, PROMPT_VERSION
from schemas import ResumeData, EvaluationOutput, DebugTrace
from storage.scoring_cache import stable_hash


class Tier3DiagnosticReviewer(LLMReviewerMixin):
    def __init__(self, config, client, model):
        self.config = deepcopy(config)
        self.llm_client, self.llm_model_name = client, model


def diagnose_once(resume_input, config, client, model):
    """Use the production request builder, Pydantic and citation validator."""
    resume = ResumeData.model_validate(resume_input)
    reviewer = Tier3DiagnosticReviewer(config, client, model)
    state, checks = check_hard_filters(resume, config)
    trace = DebugTrace(hard_filter_status=state, hard_filter_passed=state != "FAIL",
                       hard_filter_checks={k: list(v) for k, v in checks.items()},
                       route_reason="仅Tier3真实诊断；Tier1/Tier2未运行；仅人工核验，不具备自动发送资格")
    out = EvaluationOutput(candidate_key=resume.meta.candidate_key, name=resume.name,
                           passed_hard_filter=state != "FAIL", scoring_mode="deep_mode",
                           scoring_status="SCORING_FAILED", data_completeness=resume.data_completeness,
                           routing_decision="PENDING_REVIEW", debug_trace=trace, data_issues=resume.data_issues)
    if resume.data_completeness != "COMPLETE" or resume.data_issues or resume.collection_errors:
        out.scoring_status = "DATA_INCOMPLETE"
        return {"scope": "tier3_only_diagnostic", "skipped_stages": ["tier1", "tier2"],
                "metrics": {"status": "NOT_CALLED", "failure_type": "IncompleteResume"}, "evaluation": out.model_dump(mode="json")}
    try:
        evaluation = reviewer._run_tier3_llm(resume, [])
        out.total_score, out.dimension_scores = reviewer._compute_weighted_dimensions(evaluation)
        out.highlights, out.risk_point = evaluation.highlights, evaluation.risk_point
        trace.tier3_dimension_evidence = {key: getattr(evaluation, key) for key in ("ai_automation", "engineering", "efficiency", "job_fit")}
        trace.tier3_llm_raw_scores = {key: value.score for key, value in trace.tier3_dimension_evidence.items()}
        out.scoring_status = "DEEP_SCORED"
    except Exception as exc:
        out.risk_point = f"Tier3诊断失败:{type(exc).__name__}；无正式分；不回退粗排"
    out.version_info = {"prompt_version": PROMPT_VERSION, "job_config_version": stable_hash(config),
                        "llm_model_version": model, "assessment_scope": "tier3_only_diagnostic",
                        "embedding_model_version": "NOT_RUN"}
    # Revalidate the final result just as a stored result would be validated.
    out = EvaluationOutput.model_validate(out.model_dump(mode="json"))
    return {"scope": "tier3_only_diagnostic", "skipped_stages": ["tier1", "tier2"],
            "metrics": reviewer.last_llm_diagnostics, "evaluation": out.model_dump(mode="json"),
            "model_response_content": reviewer.last_llm_response_content}


def main(argv=None):
    parser = argparse.ArgumentParser(description="单样本真实Tier3诊断，不加载BGE，不具备自动发送资格")
    parser.add_argument("--live", action="store_true", help="明确调用.env配置的真实API")
    parser.add_argument("--candidate", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--resume", type=Path, help="使用原始ResumeData JSON，替代内置候选人")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--thinking", choices=["default", "on", "off"], default="default", help="default不覆盖配置；on/off只覆盖本次诊断")
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--thinking-budget", type=int)
    parser.add_argument("--label", default="single")
    parser.add_argument("--output-dir", type=Path, default=REPORTS_DIR / "llm_live")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("此入口只运行真实API；请显式传入 --live")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", args.label):
        parser.error("label只允许字母、数字、下划线和短横线")
    if any(v is not None and v <= 0 for v in (args.max_tokens, args.thinking_budget)):
        parser.error("Token预算必须为正整数")
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    review = config["scoring_config"]["llm_review"]
    if args.thinking != "default":
        review["enable_thinking"] = args.thinking == "on"
    if args.max_tokens is not None:
        review["max_tokens"] = args.max_tokens
    if args.thinking_budget is not None:
        review["thinking_budget"] = args.thinking_budget
    try:
        config = validate_config(config)
    except ValueError as exc:
        parser.error(str(exc))
    if args.resume:
        data = json.loads(args.resume.read_text(encoding="utf-8"))
    else:
        from tests.mock_data import BENCHMARK_CANDIDATES
        data = BENCHMARK_CANDIDATES[args.candidate - 1]
    settings = load_runtime_settings()
    started = perf_counter()
    http = {"attempts": [], "responses": []}

    def on_request(request):
        body = json.loads(request.content)
        http["attempts"].append({"elapsed_seconds": round(perf_counter() - started, 4),
                                 "timeout": request.extensions.get("timeout"),
                                 "body_bytes": len(request.content),
                                 "wire_parameters": {k: body[k] for k in ("model", "temperature", "max_tokens", "enable_thinking", "thinking_budget", "stream") if k in body},
                                 "wire_strict": body.get("response_format", {}).get("json_schema", {}).get("strict")})
        http["attempts"][-1]["wire_schema_root_type"] = body.get("response_format", {}).get("json_schema", {}).get("schema", {}).get("type")

    def on_response(response):
        http["responses"].append({"headers_elapsed_seconds": round(perf_counter() - started, 4),
                                  "status_code": response.status_code})

    with OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, max_retries=0,
                http_client=DefaultHttpxClient(event_hooks={"request": [on_request], "response": [on_response]})) as client:
        record = diagnose_once(data, config, client, settings.llm_model_name)
    record.update(execution="REAL_LLM", captured_at_utc=datetime.now(timezone.utc).isoformat(),
                  http=http, config_snapshot=config, input_content_hash=stable_hash(data))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{args.label}.json"
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    result, metrics = record["evaluation"], record["metrics"]
    print(json.dumps({"execution": "REAL_LLM", "status": result["scoring_status"], "total_score": result["total_score"],
                      "elapsed_seconds": metrics.get("elapsed_seconds"), "failure_type": metrics.get("failure_type"),
                      "usage": metrics.get("usage"), "reasoning_tokens": metrics.get("reasoning_tokens"),
                      "finish_reason": metrics.get("finish_reason"), "output": str(output)}, ensure_ascii=False))
    return 0 if result["scoring_status"] == "DEEP_SCORED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
