"""Sprint 3 live smoke: real local BGE + real Qwen through the full pipeline.

Runs in an isolated temporary database (the repository's business database is
untouched).  Two heterogeneous policies (Java backend / B2B sales) are made
"fully anchored" so Tier 2 coverage can reach 1.0; the pipeline then performs:

    Policy embeddings -> candidate embeddings -> Tier 0 -> Tier 1 -> Tier 2
    -> Tier 3 (real Qwen) -> pipeline_evaluations (formal result)

Writes ``live_smoke_result.json`` next to this script.
"""
from copy import deepcopy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\Boss-project\boss-qhr-helper-dev")
sys.path.insert(0, str(ROOT))

from engine.embedding_service import PolicyEmbeddingService, ResumeEmbeddingService
from engine.policy_interpreter import PolicyInterpreter
from engine.policy_llm_reviewer import PolicyReviewService
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from engine.policy_validator import validate_policy_quality
from engine.semantic_scoring_service import SemanticScoringService
from engine.semantic_verification import assert_embedding_model_matches
from engine.semantic_profiles import load_semantic_profiles
from config.policy_service import PolicyCompilerService
from config.settings import load_runtime_settings
from schemas.job_policy import JobScoringPolicy
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.policy_fixtures import offline_policy


def fully_anchored(case_id):
    """Add the missing positive anchors so Tier 2 coverage can reach 1.0."""
    value = offline_policy(case_id)
    missing = {"java_backend": ("testing", "duty", "spring_route", "quarkus_route"),
               "b2b_sales": ("duty", "direct_route", "channel_route")}[case_id]
    for requirement_id in missing:
        value["semantic_anchors"].append({
            "anchor_id": "live_" + requirement_id,
            "requirement_ids": [requirement_id], "polarity": "POSITIVE",
            "text": f"候选人能够独立说明{requirement_id}相关的具体实现过程、取舍与可核验结果。"})
    return JobScoringPolicy.model_validate(value)


def publish(repo, ctx, policy, job_id):
    value = policy.model_dump(mode="json")
    repo.create_job(ctx, value["job"]["title"], job_id=job_id)
    source = {"job_title": value["job"]["title"], **value["sources"]}
    draft = repo.create_policy_draft(ctx, job_id, value, source_json=source)
    run = repo.record_policy_compilation(ctx, job_id, source, status="SUCCEEDED",
        provenance=value["provenance"], policy_json=value, draft_id=draft["draft_id"],
        draft_revision=1, metrics={"live_smoke": True})
    confirmation = repo.confirm_policy_draft(ctx, job_id, draft["draft_id"],
                                             run["run_id"], draft["draft_hash"])
    profile = repo.create_execution_profile(ctx, {"assessment_scope": "sprint3-live-smoke"})
    version = repo.create_policy_version(ctx, job_id, value,
        execution_profile_id=profile["profile_id"],
        policy_schema_version="job_scoring_policy_v1",
        source_draft_id=draft["draft_id"], expected_draft_revision=1,
        compilation_run_id=run["run_id"], confirmation_id=confirmation["confirmation_id"])
    # Publish through the gated service: a BLOCKED policy would be refused here.
    PolicyCompilerService(repo).publish_version(
        ctx, job_id, version["policy_id"], confirmation_hash=draft["draft_hash"])
    return version


def resume_for(case_id, key):
    value = deepcopy(BENCHMARK_CANDIDATES[0])
    value.update(name="Live smoke candidate", degree="本科", work_years="5年",
                 job_status="离职-随时到岗", active_status="今日活跃",
                 expectation={"city": "北京", "salary": "12K/月"}, certifications=[])
    if case_id == "java_backend":
        value.update(
            skills="Java, SQL, Spring Boot, 接口自动化测试, Redis, MySQL",
            work_experience=[{"company": "Synthetic payments company", "position": "Java engineer",
                              "responsibility": "负责订单支付后台的Java服务开发，设计SQL事务与幂等约束，使用Spring Boot提供接口并编写接口自动化测试覆盖关键链路。",
                              "keywords": []}],
            project_experience=[{"name": "支付对账服务", "role": "负责人",
                                 "description": "使用Spring Boot与MySQL实现对账批处理，处理并发与事务边界，补充自动化测试。",
                                 "achievement": "将人工对账耗时从小时级降到分钟级。"}],
            education=[{"school": "Synthetic school", "degree": "本科"}], advantage="")
    else:
        value.update(
            skills="企业客户销售, CRM, 合同谈判, 销售漏斗",
            work_experience=[{"company": "Synthetic software vendor", "position": "Enterprise sales",
                              "responsibility": "负责企业客户需求访谈与方案沟通，独立推进合同谈判与签约，按月维护销售漏斗并跟进回款。",
                              "keywords": []}],
            project_experience=[],
            education=[{"school": "Synthetic school", "degree": "本科"}], advantage="")
    value["_meta"]["candidate_key"] = key
    return value


def main():
    settings = load_runtime_settings()
    print("loading real BGE:", settings.embedding_model_path)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(settings.embedding_model_path)
    assert_embedding_model_matches(model, load_semantic_profiles().embedding)
    print("BGE profile verified")

    report = {"mode": "LIVE (real local BGE + real Qwen via .env)",
              "embedding_model_path": settings.embedding_model_path,
              "llm_model": settings.llm_model_name, "cases": [], "ok": True}
    with tempfile.TemporaryDirectory(prefix="sprint3_live_smoke_") as temp:
        database = Database(Path(temp) / "live.sqlite3")
        database.migrate()
        repo = RecruitmentRepository(database)
        system = ProvisioningContext("sprint3 live smoke bootstrap")
        repo.create_user(system, "sprint3:owner", user_id="owner")
        repo.create_organization(system, "Sprint3 live smoke", "owner", org_id="org")
        ctx = AccessContext("org", "owner")

        for case_id, job_id in (("java_backend", "job-java"), ("b2b_sales", "job-b2b")):
            policy = fully_anchored(case_id)
            version = publish(repo, ctx, policy, job_id)
            policy_assets = PolicyEmbeddingService(repo, model).prepare_policy(
                ctx, job_id, version["policy_id"], version["policy_version"])
            batch = repo.create_scan_batch(ctx, job_id)
            acquired = repo.acquire_candidate_for_job(
                ctx, job_id, batch["batch_id"], "live-" + case_id,
                resume=resume_for(case_id, "live-" + case_id))
            jcid = acquired["job_candidate"]["job_candidate_id"]
            resume_assets = ResumeEmbeddingService(repo, model).prepare_candidate(
                ctx, job_id, jcid)
            review_service = PolicyReviewService(repo, timeout_seconds=120.0)
            pipeline = PolicyScoringPipeline(
                repo, semantic_service=SemanticScoringService(repo),
                review_service=review_service)
            outcome = pipeline.run_and_persist(ctx, job_id, jcid, scoring_mode="deep_mode")
            evaluation = outcome["evaluation"] or {}
            tier3 = json.loads(evaluation["tier3_json"]) if evaluation.get("tier3_json") else None
            case = {
                "case_id": case_id,
                "policy_id": version["policy_id"],
                "policy_version": version["policy_version"],
                "quality_gate": validate_policy_quality(policy).model_dump(mode="json"),
                "policy_asset_manifest": policy_assets,
                "resume_asset": resume_assets,
                "status": evaluation.get("scoring_status"),
                "task_status": (outcome.get("task") or {}).get("status"),
                "tier2": {
                    "status": (json.loads(evaluation["tier2_json"])["scoring_status"]
                               if evaluation.get("tier2_json") else None),
                    "score": (json.loads(evaluation["tier2_json"])["semantic_score"]
                              if evaluation.get("tier2_json") else None),
                    "coverage": (json.loads(evaluation["tier2_json"])["semantic_coverage"]
                                 if evaluation.get("tier2_json") else None),
                },
                "tier3": {
                    "total_score": (tier3 or {}).get("total_score"),
                    "routing_suggestion": (tier3 or {}).get("routing_suggestion"),
                    "dimensions": [{"dimension_id": item["dimension_id"],
                                    "label": item["label"], "weight": item["weight"],
                                    "score": item["score"], "confidence": item["confidence"],
                                    "evidence": item["evidence"]}
                                   for item in (tier3 or {}).get("dimensions", [])],
                },
                "llm_metrics": {
                    "status": review_service.last_llm_diagnostics.get("status"),
                    "stage": review_service.last_llm_diagnostics.get("stage"),
                    "model": review_service.last_llm_diagnostics.get("request", {}).get("model"),
                    "elapsed_seconds": review_service.last_llm_diagnostics.get("elapsed_seconds"),
                    "failure_type": review_service.last_llm_diagnostics.get("failure_type"),
                    "validation_error": review_service.last_llm_diagnostics.get("validation_error"),
                } if review_service.last_llm_diagnostics else None,
                "raw_response_excerpt": (review_service.last_llm_response_content or "")[:4000],
            }
            report["cases"].append(case)
            report["ok"] = report["ok"] and evaluation.get("scoring_status") == "DEEP_SCORED"
            print(json.dumps({"case": case_id, "status": case["status"],
                              "tier2": case["tier2"], "tier3": case["tier3"]["total_score"],
                              "routing": case["tier3"]["routing_suggestion"],
                              "llm": case["llm_metrics"]}, ensure_ascii=False))
    output = Path(__file__).parent / "live_smoke_result.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("written:", output, "| ok:", report["ok"])


if __name__ == "__main__":
    main()
