"""Trusted local administration CLI; no HTTP identity or frontend database access."""
import argparse
import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError

from config.policy_compiler import CompilationError, JobPolicyCompiler
from config.policy_service import PolicyCompilerService, PolicyQualityBlocked
from config.policy_validation import diagnose_policy
from engine.policy_validator import validate_policy_quality
from schemas.job_policy import JobScoringPolicy, PolicyCompileInput
from storage.database import Database
from storage.database_cli import database_path
from storage.models import AccessContext
from storage.repositories import RecruitmentRepository


def main(command, argv=None):
    parser = argparse.ArgumentParser(description="Generic policy compiler and explicit HR publication")
    parser.add_argument("--output", type=Path)
    if command == "policy-update":
        parser.add_argument("--policy", type=Path, required=True)
    if command == "policy-validate":
        # File mode validates a standalone Policy JSON; scoped mode validates the
        # job's current published Policy, mirroring the other read-only commands.
        parser.add_argument("--policy", type=Path)
        parser.add_argument("--database")
        parser.add_argument("--org-id")
        parser.add_argument("--user-id")
        parser.add_argument("--job-id")
    if command == "policy-compile":
        parser.add_argument("--live", action="store_true", required=True)
        parser.add_argument("--input", type=Path, required=True)
        parser.add_argument("--timeout", type=float, default=90)
        parser.add_argument("--max-tokens", type=int, default=12288)
    if command not in ("policy-schema", "policy-validate"):
        parser.add_argument("--database")
        parser.add_argument("--org-id", required=True)
        parser.add_argument("--user-id", required=True)
        parser.add_argument("--job-id", required=True)
    if command in ("policy-draft", "policy-update", "policy-confirm", "policy-version"):
        parser.add_argument("--draft-id", required=True)
    if command in ("policy-confirm", "policy-version"):
        parser.add_argument("--run-id", required=True)
    if command in ("policy-confirm", "policy-publish"):
        parser.add_argument("--confirm-hash", required=True)
    if command == "policy-publish":
        parser.add_argument("--allow-blocked-quality", action="store_true",
                            help="显式豁免 Stage 5.5 质量门控（仅限 ORG_ADMIN，需 --override-reason）")
        parser.add_argument("--override-reason",
                            help="质量门控豁免原因；与 --allow-blocked-quality 同时使用并写入审计")
    if command == "policy-version":
        parser.add_argument("--confirmation-id", required=True)
        parser.add_argument("--execution-profile-id", required=True)
    if command == "policy-publish":
        parser.add_argument("--policy-id", required=True)
    if command == "policy-history":
        parser.add_argument("--version", type=int, required=True)
    if command == "policy-update":
        parser.add_argument("--expected-revision", type=int, required=True)
        parser.add_argument("--parent-run-id", required=True)
        parser.add_argument("--source-input", type=Path)
    args = parser.parse_args(argv)
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        if command == "policy-schema":
            result = JobScoringPolicy.model_json_schema()
        elif command == "policy-validate":
            if args.policy is not None:
                policy = JobScoringPolicy.model_validate(read(args.policy))
                quality = validate_policy_quality(policy)
            else:
                if not (args.org_id and args.user_id and args.job_id):
                    raise ValueError("policy-validate requires --policy or --org-id/--user-id/--job-id")
                database = Database(database_path(args.database))
                if not database.check()["ok"]:
                    raise ValueError("Initialize/migrate/check the business database before policy operations")
                current = RecruitmentRepository(database).get_current_policy(
                    AccessContext(args.org_id, args.user_id), args.job_id)
                policy = JobScoringPolicy.model_validate(json.loads(current["policy_json"]))
                # The stored row is the authoritative identity: report exactly
                # which published version was reviewed.
                quality = validate_policy_quality(
                    policy, policy_id=current["policy_id"],
                    policy_version=current["policy_version"], policy_hash=current["policy_hash"])
            diagnostics = [item.model_dump(mode="json") for item in diagnose_policy(policy)]
            result = {"pydantic_valid": True, "diagnostics": diagnostics,
                      "publishable": not any(item["severity"] == "ERROR" for item in diagnostics),
                      "quality": quality.model_dump(mode="json")}
        else:
            database = Database(database_path(args.database))
            if not database.check()["ok"]:
                raise ValueError("Initialize/migrate/check the business database before policy operations")
            repository = RecruitmentRepository(database)
            context = AccessContext(args.org_id, args.user_id)
            service = PolicyCompilerService(repository)
            if command == "policy-compile":
                upload = PolicyCompileInput.model_validate(read(args.input))
                service.compiler = JobPolicyCompiler(timeout_seconds=args.timeout, max_tokens=args.max_tokens)
                result = service.compile_draft(context, args.job_id, upload)
            elif command == "policy-draft":
                result = repository.get_policy_draft(context, args.job_id, args.draft_id)
            elif command == "policy-update":
                result = service.update_draft(context, args.job_id, args.draft_id, read(args.policy),
                    expected_revision=args.expected_revision, parent_run_id=args.parent_run_id,
                    source_input=read(args.source_input) if args.source_input else None)
            elif command == "policy-confirm":
                result = service.confirm_draft(context, args.job_id, args.draft_id, args.run_id,
                                               confirmation_hash=args.confirm_hash)
            elif command == "policy-version":
                result = service.create_version(context, args.job_id, args.draft_id, args.run_id,
                    args.confirmation_id, execution_profile_id=args.execution_profile_id)
            elif command == "policy-publish":
                result = service.publish_version(context, args.job_id, args.policy_id,
                                                 confirmation_hash=args.confirm_hash,
                                                 enforce_quality_gate=not args.allow_blocked_quality,
                                                 quality_override_reason=args.override_reason)
            elif command == "policy-current":
                result = repository.get_current_policy(context, args.job_id)
            elif command == "policy-history":
                result = repository.get_policy_version(context, args.job_id, policy_version=args.version)
            elif command == "policy-runs":
                result = repository.list_policy_compilations(context, args.job_id)
            else:
                raise ValueError("Unsupported policy operation")
        encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
            print(json.dumps({"output": str(args.output)}, ensure_ascii=False))
        else:
            print(encoded, end="")
        return 1 if isinstance(result, dict) and result.get("publishable") is False else 0
    except (OSError, ValueError, RuntimeError, LookupError, sqlite3.Error) as exc:
        error = {"ok": False, "error_type": type(exc).__name__}
        if isinstance(exc, ValidationError):
            error["validation_errors"] = [{"path": ".".join(map(str, item["loc"])), "type": item["type"]}
                                           for item in exc.errors(include_input=False)]
        elif isinstance(exc, CompilationError):
            error["metrics"] = exc.metrics
        elif isinstance(exc, PolicyQualityBlocked):
            error["quality_issues"] = [item.model_dump(mode="json") for item in exc.issues]
            error["message"] = str(exc)
        else:
            error["message"] = str(exc)
        print(json.dumps(error, ensure_ascii=False))
        return 1
