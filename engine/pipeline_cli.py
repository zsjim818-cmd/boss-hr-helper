"""Tier 3 CLI: generic policy-driven review; offline mock by default.

The mock mode exists so the whole pipeline can be exercised deterministically
without any network.  ``--live`` uses the deployment's configured Qwen/OpenAI
endpoint.  ``--persist`` records the attempt through the append-only guards.
"""
import argparse
import json
from pathlib import Path
import re
import sqlite3

from engine.policy_llm_reviewer import PolicyReviewService
from storage.database import Database
from storage.database_cli import database_path
from storage.models import AccessContext
from storage.repositories import RecruitmentRepository


MOCK_MODEL = "offline-tier3-mock"


def offline_response(kwargs):
    """Deterministic Tier 3 double (production demo implementation)."""
    from engine.demo_models import demo_tier3_response
    return demo_tier3_response(kwargs)


def _emit(result, output):
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
        print(json.dumps({"output": str(output)}, ensure_ascii=False))
    else:
        print(encoded, end="")


def main(command, argv=None):
    if command != "tier3-review":
        raise ValueError("Unsupported pipeline command")
    parser = argparse.ArgumentParser(
        description="通用Policy驱动的Tier3终审（默认离线Mock；--live调用真实Qwen）")
    parser.add_argument("--database")
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--job-candidate-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true", help="显式调用真实Qwen/OpenAI兼容端点")
    parser.add_argument("--persist", action="store_true", help="通过不可变attempts表持久化结果")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--max-tokens", type=int)
    args = parser.parse_args(argv)
    try:
        database = Database(database_path(args.database))
        if not database.check()["ok"]:
            raise ValueError("Run db-init/db-migrate/db-check before Tier3 operations")
        context = AccessContext(args.org_id, args.user_id)
        repository = RecruitmentRepository(database)
        if args.live:
            service = PolicyReviewService(repository, model_name=args.model,
                                          timeout_seconds=args.timeout,
                                          max_tokens=args.max_tokens)
        else:
            from engine.demo_models import DeterministicLLMClient
            service = PolicyReviewService(
                repository, client=DeterministicLLMClient(response=offline_response),
                model_name=args.model or MOCK_MODEL,
                timeout_seconds=args.timeout, max_tokens=args.max_tokens)
        if args.persist:
            outcome = service.evaluate_and_persist(context, args.job_id, args.job_candidate_id)
            result = {"live": args.live, "evaluation": outcome["evaluation"].model_dump(mode="json"),
                      "attempt": outcome["attempt"]}
        else:
            evaluation = service.evaluate(context, args.job_id, args.job_candidate_id)
            result = {"live": args.live, "evaluation": evaluation.model_dump(mode="json")}
        _emit(result, args.output)
        return 0
    except (OSError, ValueError, LookupError, RuntimeError, sqlite3.Error) as exc:
        error = {"ok": False, "error_type": type(exc).__name__,
                 "message": str(exc), "live": args.live}
        print(json.dumps(error, ensure_ascii=False))
        return 1
