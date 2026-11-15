# Sprint 4 Batch 1.1 交付报告（安全缺陷修复与 HTTP 集成）

日期：2026-11-15
范围：Greeting 安全加固、前端 epoch 快照、Mock 浏览器扫描/招呼 Dry-Run 的 FastAPI + Vue 接入、Migration 010 只读审计。
安全约束执行情况：
- 未执行 `git clean` / `git reset --hard`；未提交、未删除任何既有文件；
- 根 `config.json` **零 diff**；`data/recruitment.sqlite3` 与备份未被本批次写入（仅只读）；
- 001–010 SQL、`schema_migrations`、正式业务数据本批次**均未修改**；
- 未连接任何真实 BOSS 会话，未发送任何真实招呼语（真实发送仍返回 `UNSUPPORTED`）。

---

## 1. 实际修改文件

### 1.1 Greeting 安全（一、）

| 文件 | 变更 |
| --- | --- |
| `browser/greeting.py` | 数据库绑定身份为唯一权威：显式 identity 仅用于**确认**，不一致立即返回 `IDENTITY_MISMATCH` 且不触达 adapter；`CONTACTED` 去重改为调用 `has_pipeline_contact_event`（覆盖同一 `job_candidate` 的 fast/deep 与任意 Policy 版本）；最新非 `NOTE` 审核状态非 `APPROVE` 时拒绝执行；真实发送仍 `UNSUPPORTED`。 |
| `storage/repositories/pipeline.py` | 新增 `has_pipeline_contact_event(ctx, job_id, job_candidate_id, action="CONTACTED")`：contact → evaluation → `job_candidate_id` 联表，任意评分模式/Policy 版本命中的 `CONTACTED` 都会阻断。 |
| `tests/test_browser_greeting.py` | 新增 4 项安全测试（批准 A 传 B、fast 已联系 deep 再发、旧 Policy 版本联系记录、撤销批准），共 **13 项**。 |

### 1.2 前端 epoch（二、）

| 文件 | 变更 |
| --- | --- |
| `frontend/src/lib/requestGate.ts` | 新增 `OperationScope` / `captureScope(jobId, epoch)` / `scopeValid(scope, jobId, epoch)`。 |
| `frontend/src/views/JobPolicyView.vue` | `compile()`、`confirmAndVersion()`、`publish()` 均先快照 `job_id + epoch + 业务参数（draft_id/hash/run_id/policy_id/reason）`，每个 `await` 恢复后校验作用域；失效即中止后续步骤（不再使用新岗位的 `activeJobId` 混用旧参数）。 |
| `frontend/src/views/ScannerView.vue` | `createBatch()` 改为快照式；新增 Mock 扫描状态/报告/错误与 epoch 重置；导入/评分/轮询/刷新沿用作用域校验。 |
| `frontend/src/views/ReviewBoardView.vue` | 新增 Dry-Run 预演入口、`approved`（最新非 NOTE 审核）与联系状态展示；审核/联系/预演均快照校验。 |
| `frontend/tsconfig.json` | 删除 `"baseUrl": "."`，`paths` 改为 `"@/*": ["./src/*"]`；`vue-tsc --noEmit` 零告警。 |
| `frontend/tests/requestGate.test.ts` | 新增「模拟延迟 + 期间切岗位」多步用例与 scope 保持用例，共 **7 项**。 |

### 1.3 Mock 浏览器 HTTP + Vue 接入（三、）

| 文件 | 变更 |
| --- | --- |
| `server/schemas.py` | `BrowserScanRequest`（batch_id/pages/page_size）、`GreetingDryRunRequest`（message）。 |
| `server/responses.py` | `BrowserScanOut` / `BrowserScanStatusOut` / `GreetingDryRunOut`。 |
| `server/api.py` | 新增 `browser_router`：`POST /api/jobs/{job_id}/browser/scan`（复用 `get_scan_batch` 权限校验 + `CandidateListCollector`，结果写入 `app.state.browser_scan_results`）、`GET .../browser/scan`（复用 `get_job` 岗位权限校验；状态/错误查询，未扫描返回 404）、`POST .../evaluations/{id}/greeting-dry-run`（强制 `dry_run=True`，经 `GreetingExecutor`，仅 `DRY_RUN_READY` 时写入 `PENDING` + `DRY_RUN:` 前缀事件，否则 409 + `state`）。 |
| `server/app.py` | `_default_browser_adapter`（按岗位标题派生 ai/sales/java 的确定性 Mock 卡片，内存站点）；`create_app(..., browser_adapter_factory=None)`；仅 `mode == "deterministic"` 挂载 `browser_router`（live 模式 404）。 |
| `tests/test_api_browser.py` | 新增 **7 项 HTTP E2E**（见 §2）。 |
| `frontend/src/api/index.ts` | `browserScan` / `browserScanStatus` / `greetingDryRun`；`ApiError` 增加 `state`（409 拒绝原因可展示）。 |
| `frontend/src/types/index.ts` | `BrowserScanResult` / `BrowserScanStatus` / `GreetingDryRunResult` / `BrowserScanIssue` 等；`ApiErrorPayload.state`。 |
| `frontend/src/views/ScannerView.vue` | 新增「Mock 浏览器扫描」按钮、采集/导入/复用/失败与 issue 展示、扫描状态展示、epoch 重置。 |
| `frontend/src/views/ReviewBoardView.vue` | 新增「Greeting Dry-Run 预演」（仅最新审核 APPROVE 可用）、结果/拒绝原因展示、PENDING 联系状态表与「已联系/尚未联系」标签。 |
| `tests/frontend_e2e.py` | 新增 UI 全链路 9 项断言（策略发布 seed → 建批次 → Mock 扫描 → HTTP 深度评分 → UI 批准 → Dry-Run → PENDING 校验），共 **16 项**。 |

### 1.4 报告与日志（四/五）

- 新增 `reports/sprint4/migration_010_rebaseline_audit.md`；
- 新增 `reports/sprint4/greeting_dedupe_concurrency_design.md`（跨岗位去重与并发发送方案，**Schema 变更未实施，等待批准**）；
- 新增 `reports/sprint4/sprint4b11_delivery_report.md`（本文件）；
- 新增 `reports/sprint4/sprint4b11_tests.log`（全部测试原始输出）。

---

## 2. 测试结果

| 测试 | 结果 |
| --- | --- |
| 后端全量 `unittest discover -s tests -t .` | **Ran 597 tests — OK**（0 失败 / 0 error / 0 skip，约 169s） |
| 后端聚焦（browser adapter/greeting + API browser + migration 010 + semantic binding） | **Ran 35 — OK** |
| HTTP E2E `tests/test_api_browser.py` | **7/7**（见下） |
| Playwright Mock 浏览器 E2E `tests/test_browser_e2e.py` | **1/1 OK**（本机 Edge，无 Chromium 下载） |
| 前端 Node 竞态测试 | **7/7** |
| 前端 `vue-tsc --noEmit` | **0 错误 / 0 告警** |
| 前端 `vite build` | **成功**（产物已清理，未入库） |
| 前端 Playwright E2E `tests/frontend_e2e.py` | **16/16 检查通过**（含 UI 扫描→审核→Dry-Run） |
| `main.py db-check` | **ok=true, schema_version=10, issues=[]** |

HTTP E2E 覆盖（`tests/test_api_browser.py`）：
1. 首次扫描前 `GET /browser/scan` → 404；扫描后状态 `COMPLETED` 且与批次一致。
2. 扫描导入 3 人，Policy 版本与批次锁定一致；重复扫描 `imported=0/reused=3`，无重复候选人。
3. 未知批次 404；无岗位权限的 HR 对扫描 POST 与状态 GET 均为 403；live 模式浏览器路由 404。
4. 全链路：扫描 → deep 评分 → 未批准 Dry-Run 409 `NOT_APPROVED` → 批准后 200 `DRY_RUN_READY`（`dry_run=true`、`recorded=false`）→ 仅 `PENDING` 事件（note `DRY_RUN: `）→ 重复预演仍全为 PENDING → `REJECT` 后再次 409。
5. 跨模式去重：fast 结果标记 `CONTACTED` 后，deep 结果 Dry-Run → 409 `ALREADY_SENT`，未写入新事件。
6. 手工导入、无浏览器稳定身份的候选人 → 409 `UNSUPPORTED`。

前端 E2E 覆盖（`tests/frontend_e2e.py`，16 项）：
- 岗位/身份切换 7 项（原有）：三岗位列表、AI 编译草稿、切 Java 清空草稿/质量、切销售标题跟随、HR 身份清空岗位、切回 owner 恢复；
- 新增 9 项：HTTP 发布会话策略 → UI 创建批次 → UI「Mock 浏览器扫描」→ 候选人表出现 `job-java:geek-java-1..3` → HTTP deep 评分成功 → UI 批准 → UI Dry-Run 显示 `DRY_RUN_READY` → 联系状态仍「尚未联系」→ 联系表出现 `PENDING` + `DRY_RUN:`。

---

## 3. Migration 010 收尾（只读）

详见 `reports/sprint4/migration_010_rebaseline_audit.md`。要点：
- 010 文件 SHA-256 = `d590941a…52c12`，与主库 `schema_migrations` 记录一致；主库 `schema_hash` 仍为重基线前的 `332808b9…a1e11`；
- 备份 `D:\Boss-project\recruitment.sqlite3.bak_sprint3_010`（802,816 bytes，`integrity_check=ok`、无 FK 违规）保留 010 历史校验和 `e0af8e80…eda69`；
- 当前迁移集从零构建与主库 235 个 schema 对象一致，唯一文本差异为 `pipeline_evaluations` 表名引号；
- 后续冻结：001–010 与 `schema_migrations` 不得再改，结构演进只走 `011+` 新迁移。

---

## 4. 剩余问题 / 风险

1. **跨岗位去重未实现**：当前去重键为 `(org_id, job_id, job_candidate_id)`。同一人在不同岗位可各自联系；是否改为全局一次需产品确认，方案与 Schema 变更见 `greeting_dedupe_concurrency_design.md`（**未实施，待批准**）。
2. **并发发送未加固**：Dry-Run 无副作用，重复预演只会多写 `PENDING`；真实发送（Batch 2）前必须落地方案 B（意图日志 + 部分唯一索引）或明确单实例限制。**新增 Schema 需批准**。
3. **扫描状态为进程内状态**：`app.state.browser_scan_results` 不持久化，重启/多 worker 不共享；未扫描时状态查询返回 404。若需审计留存，应写入独立表（新迁移）。
4. **Mock 数据由岗位标题派生**：默认适配器按标题含 AI/自动化→ai、销售→sales、否则 java。手工导入的候选人无浏览器身份，Dry-Run 返回 `UNSUPPORTED`，符合预期。
5. **Browser 路由仅 deterministic 模式**：live 模式不挂载（404）；真实 BOSS 接入的前置条件仍见 Batch 1 报告 §7（授权账号、真实 DOM/接口核验、CDP 专用 profile、发送语义与风控复测）。
6. **Playwright 为可选 dev 依赖**（`pyproject.toml [project.optional-dependencies].dev`），CI/新环境需 `pip install -e .[dev]` 且本机安装 Edge 才能跑浏览器测试；缺失时相关测试自动 skip。
7. **前端 E2E 的策略发布通过 HTTP seed**：界面发布 BLOCKED 质量门需要人工 override 流程，E2E 以 owner 身份直接走公开 API 完成，避免脆弱的表单操作；该步骤仍验证了真实 HTTP 契约。
8. 仓库根存在两个**既有未跟踪文件** `audit_git_status.txt`、`audit_migration010_diff.patch`（非本批次生成），按要求原样保留、未修改。

---

## 5. 复现命令

```powershell
# 后端全量
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -t .

# 本批次重点
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_adapter tests.test_browser_greeting `
    tests.test_api_browser tests.test_migration_010 tests.test_semantic_binding -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_e2e -v

# 前端
cd frontend
node --test tests/requestGate.test.ts
npx vue-tsc --noEmit
npx vite build

# 前端 UI 全链路（自动起 8010/5174，不触碰用户的 8000/5173）
cd ..
.\.venv\Scripts\python.exe -X utf8 -m tests.frontend_e2e
```
