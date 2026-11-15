# Greeting 去重与并发发送设计方案（待批准，本批次未实施 Schema 变更）

日期：2026-11-15（Sprint 4 Batch 1.1）
状态：**设计提案**。当前代码只实现了「同一岗位内、跨评分模式/跨 Policy 版本」的去重；跨岗位去重与并发发送的 Schema 变更**未实施**，需产品/技术批准后以新迁移 `011_*.sql` 落地。

---

## 1. 现状（已实现并有测试）

| 保证 | 实现位置 | 证据 |
| --- | --- | --- |
| 身份以数据库绑定为准，外部 identity 只能确认不能覆盖 | `browser/greeting.py`：`db_identity = from_candidate_key(...)`；不一致直接 `IDENTITY_MISMATCH`，不触达 adapter | `test_explicit_identity_cannot_override_the_approved_candidate` |
| 仅最新审核状态为 `APPROVE` 可预演/发送（`NOTE` 不算批准，撤销后立即失效） | `GreetingExecutor.review_state` + 路由前置校验 | `test_approval_is_required_and_note_does_not_count`、`test_revoked_approval_blocks_dry_run` |
| `CONTACTED` 去重覆盖同一 `job_candidate` 的**所有**正式评分（fast/deep、任意 Policy 版本） | `PipelineRepository.has_pipeline_contact_event`：contact→evaluation→`job_candidate_id` 联表 | `test_duplicate_contact_is_blocked`、`test_cross_mode_contact_blocks_send`、`test_older_policy_version_contact_cannot_be_bypassed`、`test_cross_mode_contact_blocks_dry_run_over_http` |
| Dry-Run 只写 `PENDING`（note 前缀 `DRY_RUN: `），绝不写 `CONTACTED` | `server/api.py::greeting_dry_run` | `test_full_chain_scan_score_approve_dry_run`（两次预演仍全为 PENDING） |
| 真实发送禁用 | `GreetingExecutor.execute(dry_run=False)` 直接返回 `UNSUPPORTED`；路由强制 `dry_run=True` | `test_real_sending_is_disabled_in_this_batch` |

**当前边界**：去重键是 `(org_id, job_id, job_candidate_id)`。同一个人投递/被推荐到**不同岗位**时是不同 `job_candidate`，各自可以联系——这符合"按岗位招聘"的业务语义，但若产品要求"同一人全局只联系一次"，则当前实现不满足。

---

## 2. 跨岗位去重

### 2.1 方案 A：不新增 Schema（可用，但依赖身份串约定）

BOSS 稳定身份 `encrypt_geek_id` 已包含在 `job_candidates.candidate_key = "{job_id}:{geek_id}"` 中（`browser/identity.py` 强制该格式）。可新增只读查询：

```sql
-- 伪代码：同一 org 下该 geek 身份是否已在任意岗位被联系
SELECT 1
FROM pipeline_contact_events c
JOIN pipeline_evaluations e ON e.org_id=c.org_id AND e.job_id=c.job_id
                           AND e.pipeline_evaluation_id=c.pipeline_evaluation_id
JOIN job_candidates jc ON jc.org_id=e.org_id AND jc.job_id=e.job_id
                      AND jc.job_candidate_id=e.job_candidate_id
WHERE c.org_id=? AND c.action='CONTACTED'
  AND substr(jc.candidate_key, instr(jc.candidate_key, ':') + 1) = ?
LIMIT 1;
```

- 优点：零迁移、可立即接入 `GreetingExecutor`；对浏览器采集的候选人 100% 准确。
- 缺点：`candidate_key` 是业务字符串而非受约束的列；手工导入的 key 可能不含 `:`，此时只能退回"仅本岗位去重"；`substr/instr` 无法走索引，数据量大时需全表扫描（可加表达式索引，但那也是 Schema 变更）。
- 适用：仅在确认"跨岗位只联系一次"是硬性业务规则、且接受性能/格式假设时启用；建议以开关（Policy/profile 字段）形式默认关闭。

### 2.2 方案 B：新增 Schema（推荐用于长期，需批准）

新增 migration `011_contact_identity_dedupe.sql`：

1. `job_candidates` 增加 `browser_identity TEXT`（回填：`substr(candidate_key, instr(candidate_key, ':') + 1)`；无 `:` 的保持 NULL），并建索引 `(org_id, browser_identity)`。
2. 新增 `contact_suppressions(org_id, browser_identity, first_contact_event_id, created_at, PRIMARY KEY(org_id, browser_identity))`：首次 `CONTACTED` 时写入；发送前查此表即可 O(1) 判断跨岗位联系。
3. 若产品规则允许"同一身份不同岗位可各联系一次"，则该表按 `(org_id, job_id, browser_identity)` 建键，等价于把去重键从 `job_candidate_id` 提升为稳定身份。

- 优点：显式数据模型、可索引、可审计（记录首次联系事件）。
- 代价：一次迁移 + 写入路径改造 + 回填测试；需要重跑 `test_migration_010` 同规格的迁移验收。
- 对已应用库的影响：只增列/增表（`job_candidates` 无不可变触发器，可直接 `ALTER TABLE ADD COLUMN`；`contact_suppressions` 为新增表）。

**本批次决定**：不实施任何一项；保持 `(org_id, job_id, job_candidate_id)` 去重。等待明确"跨岗位"业务语义后再定 A/B。

---

## 3. 并发发送

### 3.1 风险

当前 `GreetingExecutor` 是"先读后写"：读取审核/联系状态 → 调用浏览器 → 由调用方写事件。两个并发请求（或多进程 worker）可能同时通过检查，造成重复发送。Dry-Run 无外部副作用，重复预演只会多写 `PENDING` 审计行（当前行为），可接受；**真实发送（Batch 2）不可接受**。

### 3.2 方案 A：进程内锁 + 事务内复查（零 Schema，单进程适用）

- `GreetingExecutor` 持有按 `(org_id, job_id, job_candidate_id)` 的 `threading.Lock`；
- 进入临界区后开启 `BEGIN IMMEDIATE`，再次执行 `has_pipeline_contact_event` 与审核状态检查，再执行发送；
- 发送成功后在同一事务内写 `CONTACTED`（提交前不释放锁）。
- 局限：多进程/多副本部署失效；只能作为 Batch 2 单实例演示的过渡方案。

### 3.3 方案 B：意图日志 + 部分唯一索引（推荐，需 Schema 批准）

核心原则：**先登记意图，再执行不可逆动作，最后按页面确证结果落状态**。

migration `011_greeting_send_guard.sql`（示意）：

1. `pipeline_contact_events` 增加 `job_candidate_id TEXT`（迁移内从 `pipeline_evaluations` 回填；表为 append-only 且有不可变触发器，需按 010 的"暂存表三段式重建"实现，重建后恢复触发器）。
2. 增加 `SENDING`/`PENDING_RECONCILIATION` 状态（扩展 `action` CHECK）。
3. 部分唯一索引保证同一候选人至多一条"已发送/发送中"：

```sql
CREATE UNIQUE INDEX ux_contact_once_per_candidate
ON pipeline_contact_events(org_id, job_id, job_candidate_id)
WHERE action IN ('SENDING','CONTACTED');
```

4. 发送流程：
   - 事务 1：`INSERT ... action='SENDING', note='attempt:<idempotency_key>'`（唯一索引冲突 → 直接映射为 `ALREADY_SENT`/`IN_FLIGHT`）；
   - 浏览器点击并**确证**（按钮文本/确认弹窗等页面事实）；
   - 事务 2：确证成功 → 追加 `CONTACTED`（由触发器/应用层删除或终态化 `SENDING` 行，具体取决于是否允许更新；若保持 append-only，则 `SENDING` 行保留且唯一索引需改为"每个候选人至多一条 CONTACTED + 至多一条未过期 SENDING"）；
   - 页面状态不确定 → `PENDING_RECONCILIATION`，**不重试、不记成功**，转人工核对。
5. `SENDING` 需租约（`created_at` + 超时）避免进程崩溃后永久阻塞；超时后人工确认或由离线任务核对。

> 注：由于 `pipeline_contact_events` 现状为完全 append-only（触发器禁止 UPDATE/DELETE），"SENDING → CONTACTED"不能用更新实现，推荐**追加两条事件 + 部分唯一索引只约束终态**，或引入独立 `greeting_attempts` 表（可更新状态机），后者更清晰。

### 3.4 幂等键

- HTTP 层接受 `Idempotency-Key`（或复用 `note` 中的 attempt id），重放同一 key 返回首次结果，不重复发送；
- 不做自动重试：`PAGE_UNCERTAIN`/超时一律转人工。

---

## 4. 迁移与回滚要求（若批准）

1. 新迁移编号从 `011` 起，禁止修改 001–010 与 `schema_migrations`（见 `migration_010_rebaseline_audit.md` §6）。
2. 采用 010 的"暂存表 → 删旧表 → 按最终名重建 → 回填 → 重建索引/触发器"范式，避免 `RENAME` 改写子表 FK。
3. 必须提供：
   - v10→v11 带数据升级测试（联系人/审核/评分逐列一致、FK/integrity、触发器仍生效）；
   - 失败迁移回滚测试（无半完成状态）；
   - 并发测试：多线程/多进程同时发送，断言唯一索引只允许一次 `CONTACTED`；
   - 前后备份与 `db-check` 记录（备份放项目目录外）。

## 5. 决策与待办

- [x] 同岗位跨模式/跨 Policy 去重：已实施（无需 Schema）。
- [x] 身份不可被外部参数覆盖：已实施。
- [x] Dry-Run 与真实联系状态分离：已实施（PENDING + `DRY_RUN:` 前缀）。
- [ ] 跨岗位去重：**待产品确认语义**；推荐先用方案 A（开关默认关闭），长期用方案 B。
- [ ] 并发发送：Batch 2 真实发送前必须落地方案 B（或明确限定单实例并采用方案 A）；**需批准新增 Schema**。
