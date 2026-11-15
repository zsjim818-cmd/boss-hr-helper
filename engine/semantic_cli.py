"""Trusted local CLI for Stage 5 semantic assets and read-only diagnostics.

None of these commands sends messages, writes formal evaluations or routes
candidates; ``semantic-prepare`` only materialises immutable embedding assets.
"""
import argparse
import json
from pathlib import Path
import sqlite3

from config.settings import load_runtime_settings
from engine.embedding_service import PolicyEmbeddingService, ResumeEmbeddingService
from engine.prescreen_engine import PreliminaryScreeningEngine
from engine.semantic_profiles import load_semantic_profiles
from engine.semantic_scoring_service import SemanticScoringService
from engine.semantic_verification import assert_embedding_model_matches, verify_embedding_model
from storage.database import Database
from storage.database_cli import database_path
from storage.models import AccessContext
from storage.repositories import RecruitmentRepository


SEMANTIC_SUCCESS_STATUSES = {"SEMANTIC_SCORED", "PARTIAL_SEMANTIC"}


def _load_embedder(model_path):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_path)


def _emit(result, output):
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
        print(json.dumps({"output": str(output)}, ensure_ascii=False))
    else:
        print(encoded, end="")


def main(command, argv=None):
    parser = argparse.ArgumentParser(description="Stage 5 embedding/semantic diagnostics; no sends or routing")
    parser.add_argument("--output", type=Path)
    if command == "embedding-check":
        parser.add_argument("--profile", type=Path)
        parser.add_argument("--model-path")
    else:
        parser.add_argument("--database")
        parser.add_argument("--org-id", required=True)
        parser.add_argument("--user-id", required=True)
        parser.add_argument("--job-id", required=True)
        parser.add_argument("--profile", type=Path)
        parser.add_argument("--model-path")
    if command == "semantic-prepare":
        parser.add_argument("--job-candidate-id", help="Also prepare this candidate's sealed resume vectors")
    if command == "semantic-diagnose":
        parser.add_argument("--job-candidate-id", required=True)
        parser.add_argument("--skip-prescreen", action="store_true",
                            help="Return only the Tier 2 semantic evaluation")
    args = parser.parse_args(argv)
    try:
        if command == "embedding-check":
            profiles = load_semantic_profiles(args.profile)
            model = _load_embedder(args.model_path or load_runtime_settings().embedding_model_path)
            report = verify_embedding_model(model, profiles.embedding)
            result = {
                "ok": report["ok"],
                "embedding_profile_id": profiles.embedding.embedding_profile_id,
                "embedding_profile_hash": profiles.embedding_hash,
                "declared": profiles.embedding.model_dump(mode="json"),
                "runtime": report["checks"],
            }
            _emit(result, args.output)
            return 0 if report["ok"] else 1

        database = Database(database_path(args.database))
        if not database.check()["ok"]:
            raise ValueError("Run db-init/db-migrate/db-check before semantic operations")
        repository = RecruitmentRepository(database)
        context = AccessContext(args.org_id, args.user_id)
        profiles = load_semantic_profiles(args.profile)

        if command == "semantic-prepare":
            model = _load_embedder(args.model_path or load_runtime_settings().embedding_model_path)
            assert_embedding_model_matches(model, profiles.embedding)
            policy_row = repository.get_current_policy(context, args.job_id)
            service = PolicyEmbeddingService(repository, model, profiles=profiles)
            result = {"policy": service.prepare_policy(
                context, args.job_id, policy_row["policy_id"], policy_row["policy_version"])}
            if args.job_candidate_id:
                resumes = ResumeEmbeddingService(repository, model, profiles=profiles)
                result["candidate"] = resumes.prepare_candidate(context, args.job_id, args.job_candidate_id)
            _emit(result, args.output)
            return 0

        if command == "semantic-diagnose":
            preliminary = None
            preliminary_error = None
            if not args.skip_prescreen:
                try:
                    preliminary = PreliminaryScreeningEngine(repository).evaluate(
                        context, args.job_id, args.job_candidate_id)
                except (ValueError, LookupError, PermissionError) as exc:
                    preliminary_error = {"error_type": type(exc).__name__, "message": str(exc)}
            evaluation = SemanticScoringService(repository, profiles=profiles).evaluate(
                context, args.job_id, args.job_candidate_id, preliminary=preliminary)
            result = {
                "preliminary": preliminary.model_dump(mode="json") if preliminary is not None else None,
                "preliminary_error": preliminary_error,
                "semantic": evaluation.model_dump(mode="json"),
            }
            _emit(result, args.output)
            return 0 if evaluation.scoring_status in SEMANTIC_SUCCESS_STATUSES else 1

        raise ValueError("Unsupported semantic operation")
    except (OSError, ValueError, LookupError, RuntimeError, sqlite3.Error) as exc:
        error = {"ok": False, "error_type": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, ensure_ascii=False))
        return 1
