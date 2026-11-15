# Sprint 3 交付报告 — 语义准备 API + Live 冒烟 + P0 修复 + Vue3 新契约

日期：2026-11-15
基线：Sprint 2 验收（545 项）。本 Sprint 完成 P0-1（fast→deep 升级）、P0-2（幂等身份校验）、语义准备 API 与双运行模式、真实模型 Live Smoke、Vue3 全量切换新 API。
安全约束：未执行 `git clean`/`reset --hard`；未覆盖根 `config.json`；未修改既有 001–009 迁移文件（仅新增 010）；未删除历史测试；未接入 Boss 页面/自动打招呼。

---

## 1. P0 修复结果

### P0-1 fast_mode → deep_mode 阶段升级

**问题**：已有 fast 正式结果会被 deep 请求直接复用，且 009 的唯一约束不含评分模式，无法持久化两条正式结果。

**方案**：
- **Migration 010**（新增文件，不修改 009）：重建 `pipeline_evaluations`，唯一键改为 `(org_id, job_id, job_candidate_id, policy_id, policy_version, resume_id, scoring_mode)`；行数据原样复制；`PRAGMA defer_foreign_keys=ON` + 先移除再重建两个子表触发器，保证已有审核/联系数据兼容。CHECK 增加 `DEEP_SCORED ⇒ deep_mode`。
- **复用协议**（`PolicyScoringPipeline.reusable_result`）：deep 结果满足任何请求；仅当请求为 fast 时复用 fast 结果；**fast-only + deep 请求 ⇒ 执行升级**（新任务、新正式结果，不覆盖不可变行）。
- 仓储：`save_pipeline_result` 唯一查询与 attempt 绑定均按 `scoring_mode` 区分；`fast_mode` 结果不得携带 Tier2 载荷。
- 查询：`latest_pipeline_result` 与 `list_job_candidates` 优先 deep；排名按「候选人内优先 deep」的窗口函数取最新正式结果。

**测试**（`tests/test_pipeline_upgrade.py`）：
- fast（COARSE_ONLY，ZERO 模型调用）→ deep（DEEP_SCORED，Tier2/3 各 1 次）→ 重复 deep（复用、无新调用、仍 2 条正式结果）→ 再 fast（复用 deep）；`test_fast_then_deep_upgrade_then_idempotent_deep_replay`。
- 重复 fast 幂等；API 全流程 `test_api_fast_to_deep_upgrade_flow`（history = [fast_mode, deep_mode]，latest = deep）。

### P0-2 幂等键身份校验

- `create_pipeline_task`：命中 `(org, job, idempotency_key)` 时校验 `job_candidate_id` 与 `scoring_mode`，不一致抛 `PipelineConflict`（HTTP 409）；并发同键插入由唯一索引兜底后同样校验。
- `run_and_persist(task_id=...)`：任何复用任务在执行前重新校验组织/岗位/候选人/模式绑定（`get_pipeline_task` 已按 org+job 定位，再比对 candidate+mode），不一致抛 `PipelineConflict`，任务不会被执行到错误候选人。
- API 新增 409 处理器；冲突不会产生正式评估。
- **测试**：同键不同候选人冲突、同键不同模式冲突、任务身份错绑（候选人/模式）拒绝且不产生任何正式结果；API 冲突返回 409/`PipelineConflict`。

## 2. 运行模式与语义准备 API

- `create_app(mode="deterministic"|"live")`，默认确定性；环境变量 `APP_RUN_MODE`。
  - **deterministic**：`DemoJobPolicyCompiler`（本地确定性编译，产出合规 Policy）+ 确定性 Tier2 载荷 + 确定性 Tier3 客户端；明确标注 Mock。
  - **live**：真实 `JobPolicyCompiler` + 本地 BGE（惰性加载并校验 embedding profile）+ 真实 Qwen `PolicyReviewService`。
- `POST /api/jobs/{job_id}/semantic-prepare` `{job_candidate_id?}`：live 模式下准备 Policy 向量 + 候选人向量（幂等资产），deterministic 模式返回 `DETERMINISTIC_NO_ASSETS_REQUIRED`（不伪装资产存在）。
- live 模式 deep 流水线在 Tier2 前自动准备资产（`_PreparingSemanticService`），无需人工 CLI。
- `GET /api/runtime-mode`：模式、是否需要模型、认证方式、是否本地演示。
- **新增约束（安全）**：可信头 `X-Org-Id/X-User-Id` 仅在 deterministic 或显式 `APP_ALLOW_TRUSTED_HEADERS=1`（本地开发）启用；live 部署默认 `auth_mode=gateway-required`，业务路由返回 503，直到接入真实认证网关。`GET /api/local-identities` 仅在确定性+可信头模式下注册（演示选择器数据源），live 模式下 404。
- `response_model`：岗位/Policy/候选人/评分/审核/语义准备/运行时等 20+ 关键路由均声明 Pydantic 响应模型（`server/responses.py`，允许扩展字段），OpenAPI 成为前端类型契约。

## 3. 真实模型 Live Smoke（`reports/phase1_7_sprint3/live_smoke_result.json`）

方法：隔离临时库；两份**质量 PASS**（100% 正向锚点覆盖，quality issues 为空）的 Policy（Java / B2B，基于 Stage 3 真实策略补齐缺失锚点）；真实本地 BGE（profile 校验通过）+ `.env` 真实 Qwen（`qwen3.8-27b`）；**通过正式门控服务发布**（未绕过质量门控）；完整执行 Policy 向量准备 → 简历向量准备 → Tier0 → Tier1 → Tier2 → Tier3 → 写入 `pipeline_evaluations`。

| case | 质量门控 | Tier2（真实 BGE） | Tier3（真实 Qwen） | 路由 | 写入 |
| --- | --- | --- | --- | --- | --- |
| java_backend | PASS（覆盖率 1.0） | SEMANTIC_SCORED，41.24，覆盖 1.0 | 57.00（64.8s），校验通过 | NOT_RECOMMENDED | DEEP_SCORED |
| b2b_sales | PASS（覆盖率 1.0） | SEMANTIC_SCORED，45.20，覆盖 1.0 | 53.25（49.5s），校验通过 | NOT_RECOMMENDED | DEEP_SCORED |

**Prompt 引导摩擦与微调（实测记录）**：
- 第 1 次运行：java 维度多，45s 超时（已改默认 90s，冒烟用 120s）；b2b 成功但发现真实 Qwen 在「prospecting」维度**无证据却给高分/高置信**，被 `verify_tier3_evaluation` 正确拒绝（这正是质量门的作用）。
- 第 2 次运行（未改 Prompt）：b2b 仍因同一原因失败 → 确认是 Prompt 引导不足，而非校验过严。
- **Prompt 微调**（`build_pipeline_request`）：明确硬性规则——无可逐字摘录证据时 `evidence_quotes=[]` 且 `score≤60`、`confidence≤0.5`，`missing_evidence` 非空时 `confidence≤0.8`；禁止省略号/引号/markdown/改写引用。
- 第 3 次运行：两岗位均通过 Pydantic 与证据校验并写入正式结果（本轮结果）。
- 结论：真实 Qwen 需要显式数值约束才能稳定满足证据规则；校验逻辑保持严格（未放宽），调整仅发生在 Prompt。

## 4. Vue3 前端切换

| 文件 | 变更 |
| --- | --- |
| `frontend/src/types/index.ts` | 全量重写：通用 Policy（维度/Rubric/要求组）、质量报告、Pipeline 结果/Tier2/Tier3 证据、审核/联系、任务、运行时模式 |
| `frontend/src/api/index.ts` | 全量重写：类型化 API；**删除全部静默 Mock 回退**；网络/业务错误统一抛 `ApiError`（含 quality_issues）；请求自动附加可信身份头；身份持久化 |
| `frontend/src/stores/useAppStore.ts` | 运行时模式、模拟身份（local-only 才显示选择器）、岗位/当前发布策略、错误横幅 |
| `frontend/src/App.vue` | 顶部岗位切换、身份选择器（仅 local_only+trusted-headers 显示）、开发模式横幅、错误可见化 |
| `frontend/src/views/JobPolicyView.vue` | 岗位创建/切换、JD 编译、动态维度（权重/Rubric）、要求组 ALL/ANY、Stage 5.5 质量报告（计数/问题/建议）、确认→版本→发布（BLOCKED 时显示豁免原因输入） |
| `frontend/src/views/ScannerView.vue` | 批次创建（锁定 Policy 版本）、模板/JSON 简历导入、语义准备、fast/deep 评分、任务轮询与状态展示、候选人列表刷新 |
| `frontend/src/views/ReviewBoardView.vue` | 排名（deep 优先）、评估历史切换、Tier0/1/2/3 详情、逐字证据（source_path+quote）、置信度、审核（批准/拒绝/复核/备注）与联系状态历史 |

**构建**：`npx vue-tsc --noEmit` 0 错误；`npx vite build` 成功（产物已清理，未入库）。

## 5. 全量测试

- 后端全量：**561 项**（545 基线 + P0 升级 7 + 语义/模式/响应契约 9），0 失败、0 skip（Definitive run 见 §6）。
- 新增/变更测试文件：`tests/test_pipeline_upgrade.py`（7）、`tests/test_api_semantic.py`（9）、`tests/test_api_pipeline.py`/`tests/test_api_semantic.py` 清理竞态（等待后台任务再销毁临时库）、`tests/test_semantic_storage.py` 迁移版本按迁移集推导、3 处历史发布调用带审计原因（断言未改）。
- `tests/test_policy_llm_reviewer.py` 新增模型名解析/失败指标；`tests/test_policy_compiler.py` 新增豁免审计（原因/管理员/审计事件）。

## 6. 一键启动与浏览器体验（MVP 闭环）

```powershell
# 1) 安装/迁移（已就绪可跳过）
.\.venv\Scripts\python.exe main.py db-init
.\.venv\Scripts\python.exe main.py db-migrate      # 009/010 增量

# 2) 后端（默认确定性联调，无模型）
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
#    真实模型模式（本地 BGE + .env Qwen；本机开发需显式允许可信头）
$env:APP_RUN_MODE="live"; $env:APP_ALLOW_TRUSTED_HEADERS="1"
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000

# 3) 前端
cd frontend; npm run dev        # http://127.0.0.1:5173 （/api 代理到 8000）
```

浏览器流程：顶部选择模拟 HR 身份（local 模式）→ 新建岗位 → 编译 Policy → 查看质量报告 → 确认/版本/发布 → 批次 → 导入演示简历 → （live：语义准备）→ deep 评分 → 审核看板查看维度/证据/路由 → 批准/拒绝/复核与联系记录。OpenAPI：`http://127.0.0.1:8000/docs`。

## 7. 未验证 / 已知问题

1. **真实模型 API 全链路**：Live Smoke 直接调用服务层（同一条流水线），未通过 HTTP + BGE 资产准备的浏览器路径实测（HTTP 语义准备端点仅在确定性模式有测试；live 端点的真实 BGE 加载由冒烟脚本等价覆盖）。标注：live HTTP 语义准备“未验收”。
2. **真实认证网关**：live 模式默认拒绝可信头；网关适配器尚未实现。
3. **前端未接真实 BGE/Qwen 的浏览器联调**（确定性模式 UI 已可完整演示；live 模式需本机模型与显式开发开关）。
4. 前端无自动化测试（vue-tsc/build 静态通过；交互为手工验收清单）。
5. 正式评估表仍与旧 `evaluations` 分叉；排名/列表为逐候选查询（N+1）；任务无超时/取消；`uv.lock` 未刷新。
6. 演示策略的阈值（coarse_pass=0）使所有可评分候选人进入深度阶段；真实岗位请使用真实 Policy。

## 8. Sprint 4 建议门槛

1. live 模式 HTTP 语义准备 + 浏览器闭环实测（含 BGE 资产冷/热路径）。
2. 真实认证网关适配与部署拓扑定案（禁止可信头直连）。
3. 前端交互自动化（Playwright）与错误态验收。
4. `uv.lock` 锁定与依赖审计；评估表统一/保留期决策。
