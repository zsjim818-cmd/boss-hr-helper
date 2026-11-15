# Sprint 3.1 + Sprint 4 Batch 1 交付报告

日期：2026-11-15
范围：Sprint 3.1（P0-1 Live 语义版本绑定、P0-2 前端状态隔离/竞态、P1-3 迁移 010 验收）+ Sprint 4 Batch 1（browser/ 抽象层、Mock 浏览器、采集→评分→审核链路、发送 dry-run）。
安全约束：未 `git clean`/`reset --hard`；根 `config.json` 零改动；001–009 未改；未删除历史测试；未使用真实 BOSS 账号；**未发送任何真实招呼语**。

---

## 1. Sprint 3.1 修复证据

### P0-1 Live Semantic Policy 版本绑定
- `server/semantic_runtime.py::LiveSemanticRuntime.prepare` 现在：
  - 传入 `job_candidate_id` 时，从候选人绑定（`job_candidates.policy_id/policy_version`）解析 Policy 并校验版本行一致；
  - 语义向量准备、Tier 2（`SemanticScoringService` 经 Interpreter）与整条 Pipeline 使用同一 Policy 版本；不自动升级旧批次。
  - 未传候选人时，才使用岗位当前发布 Policy。
- 证据：`tests/test_semantic_binding.py`（3 项）：
  - 批次 A 绑定 v1 → 发布 v2 → `prepare` 返回 v1 manifest、Tier2 评估 identity=`v1`；
  - 仅 Policy 准备使用 v2；
  - 同一候选人再次导入仍绑定 v1（永不自动升级）。

### P0-2 Vue3 多岗位/多身份状态残留与竞态
- 新增 `frontend/src/lib/requestGate.ts`（`RequestGate`/`loadIfCurrent`）：作用域令牌，旧作用域晚到响应被丢弃。
- `useAppStore`：
  - `selectJob` / `applyIdentity` 递增 `dataEpoch` 并使 job/identity 两个 gate 失效；
  - 身份切换清空 jobs / currentPolicy / activeJobId；岗位切换清空 currentPolicy；
  - `refreshJobs`/`refreshCurrentPolicy` 使用 gate，旧岗位响应不会覆盖新岗位。
- 三个视图：
  - `JobPolicyView`：`dataEpoch` 变化清空 compile/draft/quality/version/form；编译/确认/发布均校验 epoch；
  - `ScannerView`：切换时清空 batch/imported/task/evaluation/candidates；**轮询按 epoch 终止**；导入/准备/评分/刷新均校验 epoch；
  - `ReviewBoardView`：切换时清空 ranking/detail/reviews/contacts；详情/审核/联系均校验 epoch。
- 证据：
  - `frontend/tests/requestGate.test.ts`（`node --test`，5 项）：旧响应丢弃、旧请求不覆盖新请求、身份切换使双 scope 失效、轮询 scope 判断、连续切换只应用各自作用域。
  - `tests/frontend_e2e.py`（Playwright + 本机 Edge，7/7 通过）：三岗位列表 → 在 AI 编译草稿 → 切 Java 草稿清空、质量面板消失 → 切销售（表单岗位名跟随）→ 切 HR 身份（岗位清空、显示"请先创建岗位"）→ 切回 owner（岗位恢复）。
  - 未引入 Mock API 回退（`frontend/src/api/index.ts` 全部错误抛 `ApiError`）。

### P1-3 Migration 010 数据迁移验收
- `tests/test_migration_010.py`（2 项）：
  - 构造 v9 库（001–009）并写入正式评分 + HR 审核 + 联系事件 → 升级 v10：行数据逐列一致、审核/联系仍指向同一 `pipeline_evaluation_id`、`foreign_key_check` 空、`integrity_check=ok`、不可变触发器仍生效（UPDATE/DELETE/无效引用均被拒）；升级后同一绑定可保存 fast+deep 两条正式结果，同模式重复保存返回原行。
  - 注入失败迁移（011 坏 SQL）→ `MigrationError`、`user_version` 仍为 9、无残留表、数据快照不变；随后正确迁移集可正常升级到 10（失败无半完成状态）。
- **迁移实现说明**：010 调整为"暂存表 → 删旧三表 → 按最终名重建 → 回填"的数据安全方案（避免 SQLite 重命名改写子表 FK）。主库早前已按旧版 010 应用；本次仅将 `schema_migrations` 的 010 校验和对齐到修正后的文件（schema hash 前后一致、`db-check ok`），备份：`D:\Boss-project\recruitment.sqlite3.bak_sprint3_010`。**未修改任何 001–010 已应用的业务表/数据**。

## 2. Browser Adapter 接口设计（Sprint 4 Batch 1）

```
browser/
  adapter.py            BrowserAdapter(ABC): connect/close/health/
                        list_cards(page,page_size)/open_detail(identity)/
                        send_greeting(identity,message,dry_run)
  contracts.py          BrowserHealth / CandidateCard / CandidateDetail /
                        CollectionIssue / CollectionReport / GreetingOutcome /
                        BrowserError(code,recoverable) + 状态枚举
  identity.py           CandidateIdentityResolver（encrypt_geek_id 唯一键、
                        candidate_key = job_id:geek_id、card/detail 一致性）
  parser.py(合并于identity) CandidateDetailParser → 严格 ResumeData 校验
  mock_adapter.py       MockBossAdapter：内存站点 + 可注入故障（分页/滚动/
                        登录失效/页面失败/超时/详情失败/卡片消失/刷新重排/
                        招呼确认/发送不确定）
  collector.py          CandidateListCollector：分页采集 → 详情解析 → 
                        Repository acquire（去重/批次 Policy 锁定/结构化失败）
  greeting.py           GreetingExecutor：审核状态校验（仅 APPROVE）→ 重复联系
                        保护（CONTACTED 事件）→ 重定位 → 身份校验 → dry-run；
                        真实发送在本批返回 UNSUPPORTED（禁止）
  mock_page.py          Playwright Mock 招聘页（列表/详情弹窗/招呼确认/失败态）
  playwright_adapter.py PlaywrightBossAdapter（本机 Edge channel，Mock DOM）
```

要点：浏览器层**不拥有数据库**；所有写入走 `storage.repositories`（采集用 `acquire_candidate_for_job`，审核/联系分别写 `pipeline_hr_reviews` 与 `pipeline_contact_events`）。失败以 `BrowserError.code` 结构化返回：`PAGE_UNAVAILABLE/TIMEOUT/LOGIN_REQUIRED/NOT_FOUND/IDENTITY_MISMATCH/PAGE_CHANGED/UNEXPECTED_STATE/UNSUPPORTED`。

## 3. Mock 浏览器 E2E 执行步骤

1. `.\\.venv\\Scripts\\python.exe -m unittest tests.test_browser_adapter tests.test_browser_greeting -v`（内存 Mock 站点，无浏览器依赖）。
2. `.\\.venv\\Scripts\\python.exe -m unittest tests.test_browser_e2e -v`（Playwright + 本机 Edge `channel="msedge"`，无 Chromium 下载）：Mock 推荐页 → 适配器采集 2 人 → Repository 导入 → Tier0–3（离线 stub）→ HR APPROVE → 招呼 dry-run `DRY_RUN_READY`；页面失败/登录失效/候选人消失分别得到 `PAGE_UNAVAILABLE`/`LOGIN_REQUIRED`/`NOT_FOUND`。
3. 前端切换 E2E：`.\\.venv\\Scripts\\python.exe -m tests.frontend_e2e`（自动起临时后端 8010 + vite 5174，Edge 驱动，7 项断言）。用户在 8000/5173 的服务不会被影响。

## 4. 测试与构建结果

- 后端全量：见 §6（Definitive run）。
- 新增测试：`test_semantic_binding` 3、`test_migration_010` 2、`test_browser_adapter` 10、`test_browser_greeting` 9、`test_browser_e2e` 1；前端 Node 测试 5；前端 E2E 断言 7。
- 前端：`vue-tsc --noEmit` 0 错误；`vite build` 成功（产物清理未入库）。
- 真实模型冒烟（Sprint 3 已完成，未重跑）：两岗位真实 BGE + Qwen 均 DEEP_SCORED 并写入正式结果（`reports/phase1_7_sprint3/live_smoke_result.json`）。

## 5. 文件清单

新增：
- `browser/{__init__,adapter,contracts,identity,collector,greeting,mock_adapter,mock_data,mock_page,playwright_adapter}.py`
- `frontend/src/lib/requestGate.ts`、`frontend/tests/requestGate.test.ts`
- `tests/{test_semantic_binding,test_migration_010,test_browser_adapter,test_browser_greeting,test_browser_e2e,frontend_e2e}.py`
- `reports/sprint4/{source_adaptation_audit.md, sprint31_sprint4b1_delivery_report.md}`

修改：
- `server/semantic_runtime.py`（候选人绑定 Policy）
- `storage/database/migrations/010_pipeline_scoring_mode.sql`（数据安全重建；主库仅重基线校验和）
- `frontend/src/stores/useAppStore.ts`、`frontend/src/views/{JobPolicyView,ScannerView,ReviewBoardView}.vue`、`frontend/vite.config.ts`（`VITE_API_TARGET`）
- `pyproject.toml` 之外新增 Python 依赖：`playwright==1.65.2`（仅测试/工具用途，未写入生产路径；建议后续放入可选 dev 依赖）

## 6. 全量测试（Definitive run）

全量 `unittest discover`：**Ran 586 tests — OK**（0 失败、0 error、0 skip）。
构成：561 基线 + P0-1/P1-3 5 项（`test_semantic_binding` 3 + `test_migration_010` 2）+ 浏览器层 20 项（adapter 10 + greeting 9 + Playwright E2E 1）。
前端：`vue-tsc --noEmit` 0 错误；`vite build` 成功；Node 竞态测试 5/5；前端 Playwright 切换 E2E 7/7。
日志：`reports/sprint4/sprint31_tests.log`（P0-1/P1-3、浏览器层、Playwright E2E、全量回归）。

## 7. Sprint 4 Batch 2 真实浏览器接入前置条件

1. **真实账号授权**：用户明确提供 BOSS 招聘端账号，并完成人工登录（工具不代填、不绕验证码）。
2. **真实 DOM/接口核验**：参考仓库基于 v1.2.2（2026-10 页面）；需重新核验推荐页 iframe、卡片选择器、详情接口字段、`encrypt_geek_id/securityId` 来源，禁止沿用未验证选择器（本批只实现 Mock 选择器）。
3. **CDP 环境**：Edge 专用 profile + `127.0.0.1:9222`，登录态/招聘者页面预检（可复用参考仓库语义，但需本项目独立实现与测试）。
4. **发送语义与风控**：真实"打招呼"按钮文本/确认弹窗/上限提示需重测；发送前必须 HR 明确批准（本项目已有审核表）；仍保持"不确定不记成功"。
5. **合规/频控**：发送频率上限、幂等/去重（本项目 contact 事件）、会话敏感信息不落库策略需确认。
6. **Playwright/patchright 选型**：本项目使用 Playwright Python + 本机 Edge；参考仓库使用 patchright（反检测增强）。若真实页面需要，需评估后引入（许可证 MIT，兼容）。
7. **数据契约映射**：参考仓库详情接口字段与本项目 ResumeData 的映射已按 Mock 验证；真实字段差异需新增映射测试。
