"""Stage 5.5 Batch 1 — read-only policy quality scan of the three fixture jobs.

Facts this scan must respect (see batch1_audit.md / batch1_validation_design.md):

- The main business database has no published policy for any of the three cases;
  the real compiled artifacts are Stage 3 live acceptance *drafts* (revision 1).
- Case identity must be discovered from the databases themselves (source
  snapshots equal to the fixture PolicyCompileInput), never guessed.
- Strictly read-only: originals are opened via ``mode=ro`` for discovery and
  byte-copied to a temp directory for the authorized Repository probes, so the
  scan cannot mutate any acceptance database.
- No model inference, no embedding computation, no candidate rescoring.

Usage (from project root):
    python reports/phase1_7_stage5_5/scan_policy_validation.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.policy_anchor_validator import (
    hard_filter_requirement_ids,
    keyword_requirement_ids,
    semantic_scope_requirement_ids,
    unclassified_requirement_ids,
)
from engine.policy_validator import POLICY_VALIDATOR_VERSION, validate_policy_quality
from schemas.job_policy import JobScoringPolicy
from storage.database import Database
from storage.models import AccessContext
from storage.repositories import RecruitmentRepository
from storage.repositories.base import NotFound
from tests.policy_fixtures import policy_input


LIVE_DIR = PROJECT_ROOT / "reports" / "phase1_7_stage3" / "live"
FINAL_POLICIES = PROJECT_ROOT / "reports" / "phase1_7_stage3" / "policies"
CASES = ("ai_automation", "java_backend", "b2b_sales")
SCAN_VERSION = "stage5.5-batch1-policy-validation-scan-v1"

SCHEMA_GAPS = [
    "PolicyRequirement 没有显式评估范围字段（无 SEMANTIC/RULE/HUMAN_REVIEW 路由枚举）；"
    "本报告的语义范围由 scoring_dimensions 投影推导，与 Tier 2 引擎真实聚合路径一致。",
    "HUMAN_REVIEW 路由在 Schema 中不存在；无法确定评价层的孤儿 requirement 报告为 "
    "UNCLASSIFIED 并排除在覆盖率分母之外，不伪造归类。",
    "needs_confirmation 是编译期人工确认标记，不是评估路由；Batch 1 不据其臆测评价层。",
    "一个正向锚点可关联多个 requirement（MULTI_REQUIREMENT_BROAD）；Batch 1 仅记录 "
    "BROAD_ONLY 警告，完整风险治理属于后续批次。",
]


def ro_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def discover_database(path: Path) -> dict:
    """Read-only identity discovery: org, admin user, jobs, drafts, publications."""
    connection = ro_connect(path)
    try:
        org = dict(connection.execute(
            "SELECT org_id, name FROM organizations ORDER BY org_id LIMIT 1").fetchone())
        membership = dict(connection.execute(
            """SELECT m.user_id, m.org_role FROM organization_memberships m
               WHERE m.org_id=? AND m.state='ACTIVE' ORDER BY m.user_id LIMIT 1""",
            (org["org_id"],)).fetchone())
        jobs = [dict(row) for row in connection.execute(
            "SELECT org_id, job_id, title, current_policy_id, next_policy_version FROM jobs")]
        drafts = [dict(row) for row in connection.execute(
            """SELECT job_id, draft_id, revision, draft_hash, source_json, policy_json, diagnostics_json
               FROM policy_drafts""")]
        counts = {
            "job_policy_versions": connection.execute(
                "SELECT COUNT(*) FROM job_policy_versions").fetchone()[0],
            "policy_publications": connection.execute(
                "SELECT COUNT(*) FROM policy_publications").fetchone()[0],
        }
    finally:
        connection.close()
    return {"path": path, "org": org, "membership": membership, "jobs": jobs,
            "drafts": drafts, "counts": counts}


def all_live_databases() -> list[dict]:
    return [discover_database(path) for path in sorted(LIVE_DIR.glob("*/drafts.sqlite3"))]


def match_case_drafts(case_id: str, databases: list[dict]) -> list[dict]:
    """Find drafts whose frozen source snapshot equals the fixture input."""
    expected = policy_input(case_id)
    matches = []
    for database in databases:
        for draft in database["drafts"]:
            if json.loads(draft["source_json"]) == expected:
                if any(job["job_id"] == draft["job_id"] for job in database["jobs"]):
                    matches.append({"database": database, "draft": draft})
    return matches


def repository_probe(database: dict, draft: dict, workspace: Path) -> dict:
    """Authorized Repository probes on a byte copy; originals stay untouched."""
    copy = workspace / ("probe_" + database["path"].parent.name + ".sqlite3")
    shutil.copyfile(database["path"], copy)
    repository = RecruitmentRepository(Database(copy))
    context = AccessContext(database["org"]["org_id"], database["membership"]["user_id"])
    probe = {"database_label": database["path"].parent.name,
             "org_id": database["org"]["org_id"],
             "probing_user_id": database["membership"]["user_id"],
             "probing_user_role": database["membership"]["org_role"]}
    try:
        current = repository.get_current_policy(context, draft["job_id"])
        probe["get_current_policy"] = {"found": True, "policy_id": current["policy_id"],
                                       "policy_version": current["policy_version"],
                                       "policy_hash": current["policy_hash"]}
    except NotFound:
        probe["get_current_policy"] = {"found": False, "probe": "NotFound"}
    row = repository.get_policy_draft(context, draft["job_id"], draft["draft_id"])
    probe["get_policy_draft"] = {"draft_id": row["draft_id"], "revision": row["revision"],
                                 "draft_hash": row["draft_hash"],
                                 "draft_json_matches_discovery": (
                                     json.loads(row["policy_json"])
                                     == json.loads(draft["policy_json"]))}
    probe["draft_row"] = {"draft_id": row["draft_id"], "revision": row["revision"],
                          "draft_hash": row["draft_hash"]}
    probe["_policy_json"] = row["policy_json"]
    return probe


def main() -> dict:
    if not LIVE_DIR.is_dir():
        raise SystemExit("Stage 3 live acceptance directory is missing")
    databases = all_live_databases()
    report = {
        "scan_metadata": {
            "scan_version": SCAN_VERSION,
            "executed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "mode": "read-only (mode=ro discovery + Repository probes on byte copies)",
            "policy_validator_version": POLICY_VALIDATOR_VERSION,
            "model_calls": "NONE (no Qwen, no BGE, no embeddings, no rescoring)",
            "scanned_databases": [str(db["path"].relative_to(PROJECT_ROOT)) for db in databases],
        },
        "main_business_database": {},
        "cases": [],
        "schema_gaps": SCHEMA_GAPS,
    }

    summary = []
    with tempfile.TemporaryDirectory(prefix="policy_quality_scan_") as temp:
        workspace = Path(temp)
        business = discover_database(PROJECT_ROOT / "data" / "recruitment.sqlite3")
        # Condition introduced DURING this session by an external, concurrently
        # running uvicorn server.app process (verified on a byte copy so the
        # scan itself stays strictly read-only): its storage/repository.py
        # constructor auto-created three Stage-1-era legacy tables (job_policies /
        # candidate_records / daily_quotas — all empty) that the current migration
        # set does not define, so Database.check() reports schema drift and every
        # policy CLI command correctly refuses to operate on it. At takeover time
        # the business database was a clean 41-table v8 schema.
        business_copy = workspace / "business_copy.sqlite3"
        shutil.copyfile(business["path"], business_copy)
        business_check = Database(business_copy).check()
        drift = {
            "introduced_during_batch1_session": True,
            "detected_by": "storage.Database.check() on a byte copy",
            "ok": business_check["ok"],
            "issues": business_check["issues"],
            "root_cause": ("会话期间（2026-11-15 13:05:11）外部进程启动了 uvicorn server.app:app "
                           "--reload（绑定 127.0.0.1:8000），server/app.py 实例化 Stage-1 风格的 "
                           "storage/repository.py，其构造函数于 13:05:13 在业务库自动创建 3 张迁移集之外的"
                           "遗留表 job_policies/candidate_records/daily_quotas（均为空表，无数据行），"
                           "造成 schema drift。接管时（12:51 首次只读检查）业务库为干净的 41 表 v8 schema，"
                           "drift 并非接管前状态，也非 Batch 1 任何命令产生（Batch 1 对该库只使用 mode=ro "
                           "连接与字节副本）。uvicorn 进程至今仍在运行并持有该库连接。"),
            "takeover_time_state": "41 tables, schema v8, no legacy tables, db-check would pass",
            "remediation_owner": "外部并发开发者；修复属业务库数据变更，超出本批只读授权范围",
        }
        report["main_business_database"] = {
            "path": "data/recruitment.sqlite3",
            "counts": business["counts"],
            "db_check": drift,
            "jobs": [{"org_id": job["org_id"], "job_id": job["job_id"], "title": job["title"],
                      "current_policy_id": job["current_policy_id"],
                      "next_policy_version": job["next_policy_version"],
                      "published_policy": None} for job in business["jobs"]],
            "note": ("主业务库中三个案例岗位均无已发布策略（无 job_policy_versions 与 "
                     "policy_publications 行）。该库在 13:05:13 被一个外部并发启动的 uvicorn "
                     "server.app:app --reload 进程引入 schema drift（3 张空遗留表，见 db_check）；"
                     "按项目既有守卫，所有 policy CLI 命令（含 policy-validate 库内模式）都会拒绝该库。"
                     "三个案例的真实编译产物在 Stage 3 live 验收库中，本扫描对其副本执行。 "
                     "另注：该岗位 title 在库中即存储为字面 'AI???????/??'（早期写入的编码残留，"
                     "属既有业务数据，本批不修改）。"),
        }
        for case_id in CASES:
            matches = match_case_drafts(case_id, databases)
            final_copy = FINAL_POLICIES / (case_id + ".json")
            final_policy = json.loads(final_copy.read_text(encoding="utf-8"))
            chosen = None
            alternates = []
            for match in matches:
                policy = json.loads(match["draft"]["policy_json"])
                if policy == final_policy:
                    chosen = match
                else:
                    alternates.append(match["database"]["path"].parent.name)
            entry = {"case_id": case_id,
                     "fixture_policy_source": "tests/fixtures (offline, human-authored)",
                     "source_input_matches": [match["database"]["path"].parent.name
                                              for match in matches]}
            if chosen is None:
                entry["error"] = ("No live draft matches the final accepted policy copy "
                                  + str(final_copy.relative_to(PROJECT_ROOT)))
                report["cases"].append(entry)
                summary.append({"case_id": case_id, "status": "SCAN_ERROR"})
                continue
            database = chosen["database"]
            draft = chosen["draft"]
            job = next(item for item in database["jobs"] if item["job_id"] == draft["job_id"])
            probe = repository_probe(database, draft, workspace)
            policy_json = probe.pop("_policy_json")
            policy = JobScoringPolicy.model_validate_json(policy_json)
            quality = validate_policy_quality(json.loads(policy_json))
            entry.update({
                "database": str(database["path"].relative_to(PROJECT_ROOT)),
                "job": {"org_id": job["org_id"], "job_id": job["job_id"], "title": job["title"],
                        "current_policy_id": job["current_policy_id"],
                        "next_policy_version": job["next_policy_version"]},
                "publication_status": {
                    "job_policy_versions": database["counts"]["job_policy_versions"],
                    "policy_publications": database["counts"]["policy_publications"],
                    "repository_probe": probe["get_current_policy"],
                    "note": ("Stage 3 live 验收库只保存 revision 1 草稿，从未发布；"
                             "本报告验证对象为该草稿（版本字段以 draft 标注）。")},
                "policy_under_review": {
                    "kind": "policy_draft",
                    "draft_id": draft["draft_id"], "revision": draft["revision"],
                    "draft_hash": draft["draft_hash"],
                    "repository_probe": probe["get_policy_draft"],
                    "matches_final_acceptance_copy": True,
                    "final_acceptance_copy": str(final_copy.relative_to(PROJECT_ROOT)),
                    "alternate_source_matches": alternates},
                "requirement_layers": {
                    "tier2_semantic": semantic_scope_requirement_ids(policy),
                    "tier1_keyword_rule": sorted(keyword_requirement_ids(policy)),
                    "tier0_hard_filter": sorted(hard_filter_requirement_ids(policy)),
                    "unclassified": unclassified_requirement_ids(policy)},
                "quality": quality.model_dump(mode="json"),
            })
            report["cases"].append(entry)
            summary.append({
                "case_id": case_id, "job_id": job["job_id"], "draft_id": draft["draft_id"],
                "status": quality.status, "coverage_status": quality.coverage_status,
                "semantic_requirement_count": quality.semantic_requirement_count,
                "directly_covered_count": quality.directly_covered_count,
                "broad_only_count": quality.broad_only_count,
                "missing_positive_count": quality.missing_positive_count,
                "unclassified_requirement_count": quality.unclassified_requirement_count,
                "positive_coverage_rate": quality.positive_coverage_rate,
                "issue_count": len(quality.issues)})
    report["summary"] = summary
    output = Path(__file__).parent / "batch1_policy_validation.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "summary": summary}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
