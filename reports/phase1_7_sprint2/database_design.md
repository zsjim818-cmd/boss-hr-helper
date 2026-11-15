# Sprint 2 — 通用评估与 HR 审核 增量数据库设计（Migration 009）

日期：2026-11-15
定位：为通用 Policy 流水线（Tier 0→3）提供**正式结果、执行历史、异步任务、HR 审核与联系状态**的持久化结构。
硬约束：**不修改**任何既有表、列、约束或触发器（001–008 原样保留）；仅新增表与新增触发器。旧 V2 `evaluations` / `evaluation_attempts` / `hr_review_events` 语义不受影响。

---

## 1. 为什么需要新结构

- `evaluations` 表绑定旧 V2 形状（`tier1_score ≤45`、`tier2_score ≤55`，且有 `evaluation_payload_matches_columns` 触发器强制 `EvaluationOutput` JSON 形状）；通用流水线的 Tier1/Tier2 是 0–100 维度分，混写只能伪造语义。
- `hr_review_events` 外键指向 `evaluations.evaluation_id`，无法承载通用评估的审核。
- 因此新增一组平行业务表，语义独立、互不污染。

## 2. 表设计

### 2.1 `pipeline_tasks`（异步评分任务注册表）

| 列 | 说明 |
| --- | --- |
| org_id, job_id, pipeline_task_id | 主键（组织+岗位隔离） |
| job_candidate_id | 目标候选人绑定 |
| scoring_mode | `fast_mode`（Tier0/1）或 `deep_mode`（Tier0–3） |
| idempotency_key | 可空；同一岗位内幂等键唯一（部分唯一索引） |
| status | `PENDING`→`RUNNING`→`SUCCEEDED`/`FAILED`；`FAILED`→`RUNNING` 允许重试；`SUCCEEDED` 终态 |
| pipeline_evaluation_id | 成功后的正式结果引用（可空） |
| error_type, error_message | 失败诊断 |
| created_by, created_at, updated_at | 审计 |

触发器：状态单调迁移、身份列不可改、不可删除、不可重复插入同一任务。

### 2.2 `pipeline_evaluations`（通用正式评估结果，不可变）

主键 `(org_id, job_id, pipeline_evaluation_id)`；**唯一绑定** `(org_id, job_id, job_candidate_id, policy_id, policy_version, resume_id)` —— 同一候选人首绑版本 + 同一封存简历只允许一份正式结果（天然幂等 + 并发由 UNIQUE 收口）。

列：身份（candidate_id/candidate_key/policy_id/policy_version/policy_hash/resume_id/resume_content_hash）、`pipeline_version`、`scoring_mode`、`scoring_status`（HARD_FILTERED / DATA_INCOMPLETE / NEEDS_REVIEW / COARSE_ONLY / DEEP_SCORED）、`total_score`（可空）、`routing_suggestion`（仅 DEEP_SCORED 非空）、`tier0_json`（preliminary 摘要）、`tier1_json`（规则维度）、`tier2_json`（语义评估，可空）、`tier3_json`（Tier3 评估，可空）、`review_reasons_json`、`created_by/created_at`。

CHECK：`DEEP_SCORED` 必须有 total_score 与 routing_suggestion；其它状态不得携带 routing_suggestion。
触发器：不可更新/删除/替换（`no_update_/no_delete_/no_replace_pipeline_evaluations`）。

### 2.3 `pipeline_attempts`（执行历史，追加式）

`(org_id, job_id, attempt_id)` 主键；`job_candidate_id/policy_id/policy_version`、`status`（SUCCEEDED / TECHNICAL_FAILED）、`error_type/error_message`、`result_summary_json`、`metrics_json`、`pipeline_evaluation_id`（可空，成功时引用）、`created_at`。
触发器：仅允许 INSERT；不可更新/删除/替换。失败尝试可无限追加（重试审计）。

### 2.4 `pipeline_hr_reviews`（HR 审核事件，追加式）

`(org_id, job_id, review_event_id)` 主键；`pipeline_evaluation_id` FK → `pipeline_evaluations`；`action ∈ {APPROVE, REJECT, REQUEST_REVIEW, NOTE}`；`reason`、`actor_id`、`created_at`。
触发器：插入前校验评估存在；不可更新/删除/替换。**审核状态 = 最新事件**（APPROVE/REJECT/REQUEST_REVIEW；NOTE 不改变状态）。

### 2.5 `pipeline_contact_events`（联系状态，追加式，与审核分离）

`(org_id, job_id, contact_event_id)` 主键；`pipeline_evaluation_id` FK；`action ∈ {CONTACTED, DECLINED, PENDING}`；`note`、`actor_id`、`created_at`。
触发器同审核表。当前阶段**不允许任何自动发送**：仅记录 HR 手工事实。

## 3. 关系与唯一约束汇总

```
jobs ─┬─ job_candidates ── pipeline_tasks ──(成功)── pipeline_evaluations
      │                                                        │
      │                                          ├── pipeline_hr_reviews
      │                                          └── pipeline_contact_events
      └─ pipeline_attempts（按任务/候选人绑定追加）
```

- 正式结果唯一键：`(org_id, job_id, job_candidate_id, policy_id, policy_version, resume_id)`。
- 幂等键唯一：`UNIQUE(org_id, job_id, idempotency_key) WHERE idempotency_key IS NOT NULL`。
- 审核/联系事件只能引用存在的正式结果（触发器 + FK）。

## 4. 不可变性与旧数据兼容

1. 新表全部 append-only（审核/联系/尝试/正式结果）；旧表零变更。
2. 旧 `evaluations`/`hr_review_events` 继续由旧路径使用；新 API 不读写它们。
3. 迁移 009 仅 `CREATE TABLE/INDEX/TRIGGER`，不含 `ALTER TABLE`、不触碰 001–008；对既有 v8 数据库可无损升级（`db-migrate` 追加 009）。
4. 回滚策略：新表独立；如需回退，停用新 API 即可，旧数据无影响（回退脚本不在本 Sprint 范围）。

## 5. 实现边界

- 若本 Sprint 发现必须修改既有正式表/触发器，**暂停实施并报告**（本设计不涉及）。
- `pipeline_version` 参与身份与唯一键之外的结果标识；升级编排算法时旧结果仍可追溯。
