"""Stage 5 Step 7 real-BGE end-to-end acceptance.

This harness runs the actual pinned local BGE model (never a Mock embedder)
over the preserved Stage 3 Qwen policies and the fixed synthetic candidate set,
then records scores, cache behaviour, version isolation and local timings.

It writes an independent report and never overwrites earlier Stage 5 artifacts.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import tempfile
from time import perf_counter

from config.settings import PROJECT_ROOT, load_runtime_settings
from engine.embedding_service import PolicyEmbeddingService, ResumeEmbeddingService
from engine.prescreen_engine import PreliminaryScreeningEngine
from engine.semantic_profiles import load_semantic_profiles
from engine.semantic_scoring_service import SemanticScoringService
from engine.semantic_verification import assert_embedding_model_matches
from schemas import ResumeData
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import NotFound, RecruitmentRepository


DATASET_PATH = PROJECT_ROOT / "tests/fixtures/prescreen_candidates.json"
POLICY_DIRECTORY = PROJECT_ROOT / "reports/phase1_7_stage3/policies"
REPORT_DIRECTORY = PROJECT_ROOT / "reports/phase1_7_stage5"
JOB_CASES = ("ai_automation", "java_backend", "b2b_sales")
SUCCESS_STATUSES = {"SEMANTIC_SCORED", "PARTIAL_SEMANTIC"}
ACCEPTED_STATUSES = SUCCESS_STATUSES | {"DATA_INCOMPLETE"}


def _milliseconds(started):
    return round((perf_counter() - started) * 1000, 6)


def _timing_summary(samples):
    if not samples:
        return {"samples": 0}
    ordered = sorted(samples)
    return {"samples": len(samples), "median_ms": round(statistics.median(ordered), 6),
            "p95_observed_ms": ordered[max(0, math.ceil(.95 * len(ordered)) - 1)],
            "min_ms": ordered[0], "max_ms": ordered[-1]}


def _load_bge(path):
    from sentence_transformers import SentenceTransformer
    started = perf_counter()
    model = SentenceTransformer(path)
    model.eval()
    return model, _milliseconds(started)


def _publish_policy(repository, context, job, policy):
    """Replay one preserved Stage 3 policy version without any compiler call."""
    value = deepcopy(policy)
    source = {"job_title": value["job"]["title"], **value["sources"]}
    draft = repository.create_policy_draft(context, job, value, source_json=source)
    run = repository.record_policy_compilation(
        context, job, source, status="SUCCEEDED", provenance=value["provenance"],
        policy_json=value, draft_id=draft["draft_id"], draft_revision=1,
        metrics={"mode": "OFFLINE_STAGE3_POLICY_ARTIFACT_REPLAY", "model_calls_this_run": 0,
                 "origin": str(POLICY_DIRECTORY / (job + ".json"))})
    confirmation = repository.confirm_policy_draft(
        context, job, draft["draft_id"], run["run_id"], draft["draft_hash"])
    profile = repository.create_execution_profile(context, {"purpose": "stage5 real-bge acceptance"})
    version = repository.create_policy_version(
        context, job, value, execution_profile_id=profile["profile_id"],
        policy_schema_version="job_scoring_policy_v1", source_draft_id=draft["draft_id"],
        expected_draft_revision=1, compilation_run_id=run["run_id"],
        confirmation_id=confirmation["confirmation_id"])
    repository.publish_policy(context, job, version["policy_id"], confirmation_hash=draft["draft_hash"])
    return version, draft


def run_acceptance(*, job_limit=None):
    profiles = load_semantic_profiles()
    model_path = load_runtime_settings().embedding_model_path
    model, load_ms = _load_bge(model_path)
    verification = assert_embedding_model_matches(model, profiles.embedding)

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    cases = deepcopy(dataset["cases"])
    jobs = JOB_CASES if job_limit is None else JOB_CASES[:job_limit]

    report = {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "execution": "REAL_LOCAL_BGE_NOT_MOCK",
        "model_path": model_path,
        "embedding_profile_id": profiles.embedding.embedding_profile_id,
        "embedding_profile_hash": profiles.embedding_hash,
        "model_load_ms": load_ms,
        "model_verification": verification,
        "dataset_version": dataset["dataset_version"],
        "data_origin": "Preserved Stage 3 Qwen policies (synthetic JDs) replayed; "
                       "synthetic fixed candidates; real local BGE inference.",
        "jobs": {}, "cache": {}, "version_isolation": {}, "cross_org_isolation": {},
        "performance": {}, "assertions": {"checks": 0, "failures": []}, "records": [],
    }
    checks = {"count": 0}

    def verify(condition, message):
        checks["count"] += 1
        if not condition:
            report["assertions"]["failures"].append(message)

    with tempfile.TemporaryDirectory(prefix="semantic_acceptance_") as temporary:
        database = Database(Path(temporary) / "acceptance.sqlite3")
        database.migrate()
        repository = RecruitmentRepository(database)
        system = ProvisioningContext("isolated Stage 5 real-BGE acceptance")
        repository.create_user(system, "semantic:owner", user_id="owner")
        repository.create_organization(system, "Semantic acceptance", "owner", org_id="org")
        context = AccessContext("org", "owner")

        policies_service = PolicyEmbeddingService(repository, model, profiles=profiles)
        resume_service = ResumeEmbeddingService(repository, model, profiles=profiles)
        scoring_service = SemanticScoringService(repository, profiles=profiles)
        prescreen = PreliminaryScreeningEngine(repository)

        anchor_cold, anchor_hit, resume_cold, resume_hit, evaluate_ms = [], [], [], [], []
        versions = {}
        for job in jobs:
            policy = json.loads((POLICY_DIRECTORY / (job + ".json")).read_text(encoding="utf-8"))
            repository.create_job(context, policy["job"]["title"], job_id=job)
            version, _ = _publish_policy(repository, context, job, policy)
            versions[job] = version
            batch = repository.create_scan_batch(context, job, batch_id="batch:" + job)

            started = perf_counter()
            first = policies_service.prepare_policy(context, job, version["policy_id"], version["policy_version"])
            anchor_cold.append(_milliseconds(started))
            verify(first["status"] == "READY", job + ": anchor preparation failed")
            verify(first["anchor_count"] == len(policy["semantic_anchors"]), job + ": anchor count mismatch")

            started = perf_counter()
            again = policies_service.prepare_policy(context, job, version["policy_id"], version["policy_version"])
            anchor_hit.append(_milliseconds(started))
            verify(again["cache_hit"] and again["encoded_count"] == 0, job + ": anchor manifest was not a cache hit")

            first_jcid = None
            for case in [item for item in cases if item["job_case_id"] == job]:
                raw = case["resume"]
                ResumeData.model_validate(raw)
                acquired = repository.acquire_candidate_for_job(
                    context, job, batch["batch_id"], case["case_id"],
                    source="stage5_semantic_acceptance", external_id=raw.get("_meta", {}).get("encrypt_geek_id"),
                    resume=raw)
                jcid = acquired["job_candidate"]["job_candidate_id"]
                first_jcid = first_jcid or jcid
                sealed = acquired["sealed_input"]

                preliminary = prescreen.evaluate(
                    context, job, jcid, resume_id=acquired["resume_snapshot"]["resume_id"])

                if sealed is None:
                    # Tier 2 only ever scores the immutable sealed input. An unsealed
                    # (partial) resume yields no semantic result instead of a fabricated one.
                    report["records"].append({
                        "case_id": case["case_id"], "job_case_id": job, "profile": case["profile"],
                        "preliminary_status": preliminary.scoring_status,
                        "preliminary_score": preliminary.preliminary_score,
                        "semantic_status": "UNSEALED_NOT_SCORED", "semantic_score": None,
                        "semantic_coverage": None, "dimension_scores": [], "review_reasons": [],
                        "resume_id": acquired["resume_snapshot"]["resume_id"], "sealed": False,
                        "prepare_error": None,
                    })
                    continue

                prepared = None
                prepare_error = None
                try:
                    started = perf_counter()
                    prepared = resume_service.prepare_candidate(context, job, jcid)
                    resume_cold.append(_milliseconds(started))
                except (ValueError, NotFound) as exc:
                    prepare_error = f"{type(exc).__name__}:{exc}"

                started = perf_counter()
                evaluation = scoring_service.evaluate(context, job, jcid, preliminary=preliminary)
                evaluate_ms.append(_milliseconds(started))
                payload = evaluation.model_dump(mode="json")

                verify(payload["scoring_status"] in ACCEPTED_STATUSES,
                       f"{case['case_id']}: unexpected semantic status {payload['scoring_status']}")
                verify(payload["assessment_scope"] == "TIER2_ONLY", case["case_id"] + ": wrong scope")
                verify(payload["score_kind"] == "POLICY_TIER2_SEMANTIC", case["case_id"] + ": wrong score kind")
                verify(payload["routing_authority"] is False and payload["hard_filter_override"] is False,
                       case["case_id"] + ": semantic result claimed routing authority")
                verify(payload["thresholds_applied"] is False and payload["formal_evaluation"] is False,
                       case["case_id"] + ": semantic result pretended to be a formal evaluation")
                verify(payload["policy_version"] == version["policy_version"], case["case_id"] + ": wrong bound policy version")
                verify(payload["upstream_preliminary_status"] == preliminary.scoring_status,
                       case["case_id"] + ": preliminary status was not carried through")
                if payload["scoring_status"] in SUCCESS_STATUSES:
                    verify(payload["semantic_score"] is not None and 0 <= payload["semantic_score"] <= 100,
                           case["case_id"] + ": success status without a valid score")
                    verify(prepared is not None, case["case_id"] + ": scored without prepared vectors")
                else:
                    verify(payload["semantic_score"] is None, case["case_id"] + ": incomplete input fabricated a score")

                report["records"].append({
                    "case_id": case["case_id"], "job_case_id": job, "profile": case["profile"],
                    "preliminary_status": preliminary.scoring_status,
                    "preliminary_score": preliminary.preliminary_score,
                    "semantic_status": payload["scoring_status"],
                    "semantic_score": payload["semantic_score"],
                    "semantic_coverage": payload["semantic_coverage"],
                    "dimension_scores": [{"dimension_id": item["dimension_id"],
                                          "semantic_score": item["semantic_score"],
                                          "scoring_status": item["scoring_status"]}
                                         for item in payload["dimension_scores"]],
                    "review_reasons": payload["review_reasons"][:4],
                    "resume_id": (sealed or acquired["resume_snapshot"] or {}).get("resume_id"),
                    "sealed": sealed is not None,
                    "prepare_error": prepare_error,
                })

            # Candidate vector cache: re-preparing the first scorable candidate must hit the manifest.
            if scores_prepared(report, job):
                started = perf_counter()
                cached = resume_service.prepare_candidate(context, job, first_jcid)
                resume_hit.append(_milliseconds(started))
                verify(cached["cache_hit"] and cached["encoded_count"] == 0,
                       job + ": candidate vector manifest was not a cache hit")

            report["jobs"][job] = {
                "policy_id": version["policy_id"], "policy_version": version["policy_version"],
                "anchor_count": first["anchor_count"], "dimensions": len(policy["scoring_dimensions"]),
            }

        runtime = {"anchor_cold": _timing_summary(anchor_cold), "anchor_cache_hit": _timing_summary(anchor_hit),
                   "resume_cold": _timing_summary(resume_cold), "resume_cache_hit": _timing_summary(resume_hit),
                   "semantic_evaluate": _timing_summary(evaluate_ms)}

        _record_cache_acceptance(repository, context, cases, job=jobs[0],
                                 resume_service=resume_service, report=report, verify=verify)
        _record_version_isolation(repository, context, jobs, cases, model, profiles, report, verify, versions)
        _record_cross_org_isolation(repository, context, system, jobs[0], versions[jobs[0]], report, verify)

        report["performance"] = {"scope": "Local CPU SQLite and single-process local BGE; not a production SLA",
                                 "runtime_ms": runtime}

        check = database.check()
        verify(check["ok"], "acceptance database integrity check failed")

    report["assertions"]["checks"] = checks["count"]
    report["assertions"]["ok"] = not report["assertions"]["failures"]
    report["ok"] = report["assertions"]["ok"]
    return report


def scores_prepared(report, job):
    return any(row["job_case_id"] == job and row["semantic_status"] in SUCCESS_STATUSES for row in report["records"])


def _record_cache_acceptance(repository, context, cases, *, job, resume_service, report, verify):
    """Cross-job physical vector reuse for one identical sealed resume."""
    shared = next(case for case in cases if case["job_case_id"] == job and case["profile"] == "complete_match")
    second_job = "java_backend" if job != "java_backend" else "ai_automation"
    policy = json.loads((POLICY_DIRECTORY / (second_job + ".json")).read_text(encoding="utf-8"))
    repository.create_job(context, "Cross job reuse", job_id="reuse_job")
    _publish_policy(repository, context, "reuse_job", policy)
    repository.create_job(context, policy["job"]["title"], job_id="reuse_source")
    _publish_policy(repository, context, "reuse_source", policy)
    batch = repository.create_scan_batch(context, "reuse_source", batch_id="batch:reuse_source")
    first = repository.acquire_candidate_for_job(
        context, "reuse_source", batch["batch_id"], "reuse-first",
        external_id=shared["resume"].get("_meta", {}).get("encrypt_geek_id"), resume=shared["resume"])
    first_prepared = resume_service.prepare_candidate(
        context, "reuse_source", first["job_candidate"]["job_candidate_id"])

    batch2 = repository.create_scan_batch(context, "reuse_job", batch_id="batch:reuse_job")
    second = repository.acquire_candidate_for_job(
        context, "reuse_job", batch2["batch_id"], "reuse-second",
        external_id=shared["resume"].get("_meta", {}).get("encrypt_geek_id"), resume=shared["resume"])
    second_prepared = resume_service.prepare_candidate(
        context, "reuse_job", second["job_candidate"]["job_candidate_id"])
    verify(first_prepared["resume_id"] == second_prepared["resume_id"],
           "cross-job resume identity changed for identical sealed input")
    verify(second_prepared["cache_hit"] and second_prepared["encoded_count"] == 0,
           "cross-job identical resume did not reuse physical vectors")
    report["cache"] = {
        "anchor_manifest_reused": True, "resume_manifest_reused": True,
        "cross_job_resume_cache_hit": second_prepared["cache_hit"],
        "cross_job_reused_chunk_count": second_prepared.get("reused_asset_count", 0),
        "note": "Cross-job reuse shares physical vectors; semantic results are still recomputed per job.",
    }


def _record_version_isolation(repository, context, jobs, cases, model, profiles, report, verify, versions):
    """Publish a weight-only V2 and prove old and new candidates keep their versions."""
    job = "java_backend" if "java_backend" in jobs else jobs[-1]
    original = json.loads((POLICY_DIRECTORY / (job + ".json")).read_text(encoding="utf-8"))
    policies_service = PolicyEmbeddingService(repository, model, profiles=profiles)
    resume_service = ResumeEmbeddingService(repository, model, profiles=profiles)
    scoring_service = SemanticScoringService(repository, profiles=profiles)

    v1 = versions[job]
    old_case = next(case for case in cases if case["job_case_id"] == job and case["profile"] == "complete_match")
    batch = repository.create_scan_batch(context, job, batch_id="batch:version-old")
    old = repository.acquire_candidate_for_job(
        context, job, batch["batch_id"], "version-old",
        external_id=old_case["resume"].get("_meta", {}).get("encrypt_geek_id"), resume=old_case["resume"])
    old_jcid = old["job_candidate"]["job_candidate_id"]
    policies_service.prepare_policy(context, job, v1["policy_id"], v1["policy_version"])
    resume_service.prepare_candidate(context, job, old_jcid)

    # V2 changes only dimension weights, so anchor vectors must be reused, not recomputed.
    # Swap two distinct weights (rather than rescaling) so the published policy keeps a
    # dimension-weight sum of exactly one and remains publishable.
    changed = deepcopy(original)
    dimensions = changed["scoring_dimensions"]
    for index in range(len(dimensions) - 1):
        if dimensions[index]["weight"] != dimensions[index + 1]["weight"]:
            dimensions[index]["weight"], dimensions[index + 1]["weight"] = (
                dimensions[index + 1]["weight"], dimensions[index]["weight"])
            break
    v2, _ = _publish_policy(repository, context, job, changed)
    v2_prepare = policies_service.prepare_policy(context, job, v2["policy_id"], v2["policy_version"])
    verify(v2_prepare["encoded_count"] == 0, "weight-only V2 unexpectedly re-encoded anchors")

    # A genuinely distinct candidate acquired after V2 must bind the newer version.
    new_case = next(case for case in cases if case["job_case_id"] == job and case["profile"] == "partial_match")
    batch2 = repository.create_scan_batch(context, job, batch_id="batch:version-new")
    new = repository.acquire_candidate_for_job(
        context, job, batch2["batch_id"], "version-new",
        external_id=new_case["resume"].get("_meta", {}).get("encrypt_geek_id"), resume=new_case["resume"])
    new_jcid = new["job_candidate"]["job_candidate_id"]
    resume_service.prepare_candidate(context, job, new_jcid)

    old_result = scoring_service.evaluate(context, job, old_jcid).model_dump(mode="json")
    new_result = scoring_service.evaluate(context, job, new_jcid).model_dump(mode="json")
    verify(old_result["policy_version"] == v1["policy_version"], "old candidate drifted from its bound V1")
    verify(new_result["policy_version"] == v2["policy_version"], "new candidate did not bind the latest V2")
    verify(old_result["policy_id"] != new_result["policy_id"], "V2 did not create a distinct policy identity")
    report["version_isolation"] = {
        "job": job, "old_candidate_policy_version": old_result["policy_version"],
        "new_candidate_policy_version": new_result["policy_version"],
        "old_candidate_status": old_result["scoring_status"],
        "new_candidate_status": new_result["scoring_status"],
        "note": "Weights changed in V2; both candidates still use their own first-bound immutable version.",
    }


def _record_cross_org_isolation(repository, context, system, job, version, report, verify):
    repository.create_user(system, "semantic:owner2", user_id="owner2")
    repository.create_organization(system, "Second isolated tenant", "owner2", org_id="org2")
    other = AccessContext("org2", "owner2")
    repository.create_job(other, "Other tenant job", job_id="other_job")
    isolated = False
    try:
        repository.get_policy_embedding_manifest(
            other, "other_job", version["policy_id"], version["policy_version"],
            load_semantic_profiles().embedding.embedding_profile_id)
    except (NotFound, PermissionError):
        isolated = True
    verify(isolated, "second organization could read the first organization's semantic assets")
    report["cross_org_isolation"] = {"checked": True, "isolated": isolated}


def write_report(report, directory=REPORT_DIRECTORY):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "semantic_acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Phase 1.7 Stage 5 — Step 7 真实 BGE 全链路验收", "",
             f"执行时间：{report['executed_at']}",
             f"执行类型：`{report['execution']}`（真实本地 BGE，非 Mock embedder）",
             f"模型：`{report['model_path']}`",
             f"Embedding Profile：`{report['embedding_profile_id']}` / hash `{report['embedding_profile_hash']}`",
             f"模型加载耗时：{report['model_load_ms']} ms", "",
             "本报告只记录本次真实模型运行观测；合成 JD/候选人不等同真实招聘数据，本机耗时不是生产 SLA。", "",
             "## 模型一致性校验", "", "```json",
             json.dumps(report["model_verification"], ensure_ascii=False, indent=2), "```", "",
             "## 三岗位语义评分结果", "",
             "| 岗位/画像 | Tier1 状态 | Tier1 分 | Tier2 状态 | Tier2 分 | 语义覆盖 |", "| --- | --- | --- | --- | --- | --- |"]
    for row in report["records"]:
        lines.append(f"| {row['job_case_id']}/{row['profile']} | {row['preliminary_status']} | "
                     f"{row['preliminary_score']} | {row['semantic_status']} | {row['semantic_score']} | "
                     f"{row['semantic_coverage']} |")
    lines += ["", "Tier1（Stage 4）与 Tier2（Stage 5）分口径独立，不直接相加；语义分不覆盖 Tier0 结论。", "",
              "## 缓存与版本隔离", "", "```json",
              json.dumps({"cache": report["cache"], "version_isolation": report["version_isolation"],
                          "cross_org_isolation": report["cross_org_isolation"]}, ensure_ascii=False, indent=2),
              "```", "", "## 本机性能观测", "", "```json",
              json.dumps(report["performance"], ensure_ascii=False, indent=2), "```", "",
              "## 自动断言", "",
              f"执行 {report['assertions']['checks']} 项断言，失败 {len(report['assertions']['failures'])} 项。"]
    lines += ["- " + failure for failure in report["assertions"]["failures"]]
    lines.append("")
    (directory / "semantic_acceptance.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage 5 real-BGE end-to-end acceptance; requires the local pinned model")
    parser.add_argument("--output", type=Path, default=REPORT_DIRECTORY)
    parser.add_argument("--job-limit", type=int, default=None)
    args = parser.parse_args(argv)
    report = run_acceptance(job_limit=args.job_limit)
    write_report(report, args.output)
    print(json.dumps({"ok": report["ok"], "assertions": report["assertions"],
                      "records": len(report["records"]), "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
