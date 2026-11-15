"""Explicit live Qwen acceptance; synthesized JD inputs, real API outputs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from config.policy_compiler import CompilationError, JobPolicyCompiler, PROMPT_PATH
from schemas.job_policy import JobScoringPolicy
from config.policy_service import PolicyCompilerService
from config.settings import PROJECT_ROOT
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository
from tests.policy_fixtures import compiler_cases, policy_input, check_policy_matches_case


def main(argv=None):
    parser = argparse.ArgumentParser(description="Real Qwen compiler acceptance; creates drafts only, never publishes")
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--case", default="all")
    parser.add_argument("--label", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports/phase1_7_stage3/live")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--max-tokens", type=int, default=12288)
    args = parser.parse_args(argv)
    if not args.label or Path(args.label).name != args.label or args.label in (".", ".."):
        parser.error("label must be a single directory name")
    cases = compiler_cases()
    if args.case != "all":
        cases = [case for case in cases if case["case_id"] == args.case]
        if not cases:
            parser.error("Unknown case")
    output = args.output / args.label
    output.mkdir(parents=True, exist_ok=False)
    (output / "prompt.txt").write_text(PROMPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (output / "schema.json").write_text(json.dumps(JobScoringPolicy.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "inputs.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    database = Database(output / "drafts.sqlite3")
    database.migrate()
    repository = RecruitmentRepository(database)
    system = ProvisioningContext("isolated real compiler acceptance with synthetic JD inputs")
    owner = repository.create_user(system, "compiler-live:" + uuid4().hex)
    org = repository.create_organization(system, "Compiler live acceptance", owner["user_id"])
    context = AccessContext(org["org_id"], owner["user_id"])
    compiler = JobPolicyCompiler(timeout_seconds=args.timeout, max_tokens=args.max_tokens)
    service = PolicyCompilerService(repository, compiler)
    summary = {"input_origin": "Synthetic JD written for acceptance, not external job listings",
               "model_calls": "REAL_QWEN_API", "compiler_output": "Qwen extraction plus audited deterministic compilation",
               "automatic_publication": False, "cases": []}
    for case in cases:
        case_id = case["case_id"]
        upload = policy_input(case_id)
        job = repository.create_job(context, upload["job_title"])
        entry = {"case_id": case_id, "job_id": job["job_id"]}
        try:
            result = service.compile_draft(context, job["job_id"], upload)
            policy = json.loads(result["draft"]["policy_json"])
            (output / (case_id + ".policy.json")).write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            entry.update(draft_id=result["draft"]["draft_id"], run_id=result["compilation_run"]["run_id"],
                         draft_hash=result["draft"]["draft_hash"], metrics=result["metrics"],
                         diagnostics=result["diagnostics"], publishable=result["publishable"])
            try:
                entry["generality"] = check_policy_matches_case(policy, case_id)
                entry["acceptance_passed"] = result["publishable"]
            except (AssertionError, ValueError) as exc:
                entry["acceptance_passed"] = False
                entry["acceptance_error"] = str(exc)
        except CompilationError as exc:
            entry.update(acceptance_passed=False, metrics=exc.metrics, error_type=type(exc).__name__)
        if compiler.last_response_content is not None:
            (output / (case_id + ".response.txt")).write_text(compiler.last_response_content, encoding="utf-8")
        (output / (case_id + ".diagnosis.json")).write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["cases"].append(entry)
        print(json.dumps({"case_id": case_id, "acceptance_passed": entry["acceptance_passed"],
                          "elapsed_seconds": entry["metrics"]["elapsed_seconds"],
                          "diagnostic_codes": entry["metrics"].get("diagnostic_codes", []),
                          "failure_type": entry["metrics"].get("failure_type")}, ensure_ascii=False), flush=True)
    summary["ok"] = all(item["acceptance_passed"] for item in summary["cases"])
    summary["database_check"] = database.check()
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
