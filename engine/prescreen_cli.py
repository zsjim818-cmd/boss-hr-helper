"""Trusted local CLI for read-only, candidate-bound initial screening."""
import argparse
import json
from pathlib import Path
import sqlite3

from engine.prescreen_engine import PreliminaryScreeningEngine
from storage.database import Database
from storage.database_cli import database_path
from storage.models import AccessContext
from storage.repositories import RecruitmentRepository


def main(argv=None):
    parser = argparse.ArgumentParser(description="Policy Tier 0/1 only; no model calls, result writes or sends")
    parser.add_argument("--database")
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--job-candidate-id", required=True)
    parser.add_argument("--resume-id", help="Required only for an unsealed partial preview")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        database = Database(database_path(args.database))
        if not database.check()["ok"]:
            raise ValueError("Run db-init/db-migrate/db-check before screening")
        engine = PreliminaryScreeningEngine(RecruitmentRepository(database), profile_path=args.profile)
        result = engine.evaluate(AccessContext(args.org_id, args.user_id), args.job_id,
                                 args.job_candidate_id, resume_id=args.resume_id)
        encoded = result.model_dump_json(indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
            print(json.dumps({"output": str(args.output), "scoring_status": result.scoring_status}, ensure_ascii=False))
        else:
            print(encoded, end="")
        return 1 if result.scoring_status == "SCORING_FAILED" else 0
    except (OSError, ValueError, LookupError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1
