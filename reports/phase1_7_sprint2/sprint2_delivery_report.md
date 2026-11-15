# Sprint 2 交付报告 — Stage 6 修复 + FastAPI + 全流水线编排

日期：2026-11-15
基线：Stage 5.5 封口 + Stage 6 离线版，全量 500 项测试通过。
范围：P0（模型名/统一证据校验/发布豁免审计）、Tier 0–3 编排服务、通用评估与 HR 审核持久化（Migration 009）、FastAPI 服务层、验收测试。
安全约束：未执行 `git clean`/`reset --hard`；未覆盖根 `config.json`；未修改 001–008 迁移与旧 V2 正式结果语义；未触发任何真实 Qwen/BGE 调用；未接入 Boss 页面、无任何自动打招呼。

---

## 1. 新增与修改文件清单

### 新增

| 文件 | 说明 |
| --- | --- |
| `storage/database/migrations/009_pipeline_results.sql` | 通用流水线任务/正式结果/尝试/HR 审核/联系事件（纯新增） |
| `storage/repositories/pipeline.py` | `PipelineRepository`：任务、正式结果（唯一绑定+校验）、追加式尝试、审核/联系事件、候选人列表/排名 |
| `engine/policy_scoring_pipeline.py` | `PolicyScoringPipeline`：Tier0→1→2→3 状态矩阵、幂等、任务认领、技术失败处理 |
| `schemas/pipeline.py`（扩展） | `PolicyPipelineResult` + `PipelineStatus` |
| `server/__init__.py` / `server/auth.py` / `server/schemas.py` / `server/api.py` / `server/app.py` | FastAPI 服务：可信身份依赖、请求/响应契约、5 组路由、应用工厂与异常映射 |
| `tests/pipeline_support.py` | 离线夹具：确定性 Tier2 stub、Mock Tier3 包装、发布/导入辅助 |
| `tests/test_pipeline_service.py` | 编排状态矩阵/模型调用边界/幂等/并发/技术失败（13 项） |
| `tests/test_pipeline_repository.py` | 仓储校验（伪造分/路由/短引用/超置信/错误路径）、不可变、审核/联系、排名（15 项） |
| `tests/test_api_pipeline.py` | API 认证/权限/门控/批次/导入/异步评分/审核/联系/幂等（10 项） |
| `reports/phase1_7_sprint2/database_design.md` | 数据库设计（实施前输出） |
| `reports/phase1_7_sprint2/api_reference.md` | REST API 清单与 OpenAPI 地址 |
| `reports/phase1_7_sprint2/e2e_demo_steps.md` | 三岗位后端端到端演示步骤 |

### 修改

| 文件 | 变更 |
| --- | --- |
| `engine/policy_llm_reviewer.py` | 模型名构造期解析（默认 settings、显式覆盖、空值报错）；新增 `verify_tier3_evaluation` 单一证据规则入口 |
| `storage/repositories/evaluations.py` | `_validate_pipeline_result` 改为复用 `verify_tier3_evaluation`（Service/Repository 规则不漂移） |
| `config/policy_service.py` | 默认质量门控；豁免需显式原因；ORG_ADMIN 校验与审计事件 |
| `config/policy_cli.py` | `--override-reason`（配合 `--allow-blocked-quality`） |
| `storage/repositories/__init__.py` | 注册 `PipelineRepository` |
| `main.py` | （无新增命令；API 通过 uvicorn 启动） |
| `pyproject.toml` | 声明 fastapi/uvicorn/httpx（已在 .venv 安装） |
| `tests/test_policy_compiler.py` | +3 门控审计测试；历史发布调用显式原因豁免 |
| `tests/test_policy_cli.py` | +1 CLI 门控测试；2 处豁免带原因 |
| `tests/test_policy_llm_reviewer.py` | +5 模型名/失败指标测试 |
| `tests/test_policy_normalization_integration.py` | 1 处豁免带原因 |
| `tests/test_semantic_storage.py` | 迁移版本断言改为按当前迁移集动态推导（009 增加后必要更新，非弱化断言） |

## 2. 数据库迁移与兼容性

- **009_pipeline_results.sql** 仅新增 5 表 + 索引 + 触发器（详见 `database_design.md`）：`pipeline_tasks`、`pipeline_evaluations`（唯一绑定：候选+策略版本+简历）、`pipeline_attempts`（追加式）、`pipeline_hr_reviews`、`pipeline_contact_events`。
- 不允许 UPDATE/DELETE/替换；任务状态单调；审核/联系事件必须引用存在的正式结果。
- **旧表零变更**：`evaluations`/`evaluation_attempts`/`hr_review_events` 仍是旧 V2 契约，不被新代码读写。
- 主库已执行增量 `db-migrate`（v8→v9，`already_current=false`，随后 `db-check` ok）；旧数据保留（Stage 3 残留与审计事件不变）。

## 3. REST API 与 OpenAPI

完整清单见 `reports/phase1_7_sprint2/api_reference.md`。OpenAPI：`GET /docs`、`GET /openapi.json`（`uvicorn server.app:app`）。

能力覆盖：岗位创建/查询；Policy 编译/草稿/质量报告/确认/版本化/发布/当前/历史；批次创建（锁定发布版本）/候选人导入/列表分页过滤/详情；异步评分与任务查询/结果与历史/排名；HR 审核（批准/拒绝/要求复核/备注）与联系事件（审核状态与联系状态分离）；全部经 `AccessContext`+Repository 权限，跨岗位访问为 404。

## 4. Tier 0～3 跳转路径与验证结果

| 路径 | 结果状态 | Tier2(BGE) | Tier3(Qwen) | 测试 |
| --- | --- | --- | --- | --- |
| Tier0 明确 FAIL | HARD_FILTERED | 不调用 | 不调用 | ✅ `test_hard_filter_failure_never_calls_models` |
| 输入不完整（未封存） | 任务 FAILED / API 409 | 不调用 | 不调用 | ✅ `test_unsealed_candidate_is_rejected_before_scoring` |
| `fast_mode` | COARSE_ONLY | 不调用 | 不调用 | ✅ `test_fast_mode_stops_before_semantic_and_tier3`、API `test_fast_mode_never_calls_models` |
| Tier0 UNKNOWN | NEEDS_REVIEW | 调用（保留证据） | 不调用 | ✅ `test_hard_unknown_runs_semantic_but_never_tier3` |
| 粗排未过 | COARSE_ONLY | 不调用 | 不调用 | ✅ `test_below_coarse_stops_before_any_model_work` |
| Tier2 PARTIAL / 覆盖率<1 | NEEDS_REVIEW | 调用 | 不调用 | ✅ `test_partial_semantic_coverage_routes_to_review_without_tier3` |
| Tier2 POLICY_ANCHOR_MISSING | NEEDS_REVIEW | 调用 | 不调用 | ✅ `test_policy_anchor_missing_cannot_be_masked_by_high_scores` |
| Tier2 DATA/ASSET_INCOMPLETE | NEEDS_REVIEW | 调用 | 不调用 | 逻辑分支（语义服务返回状态） |
| 全条件满足 | DEEP_SCORED + 路由建议 | 调用 | 调用 | ✅ `test_full_pass_runs_tier3_and_records_same_binding`、API 流程 |
| Tier3 技术失败 | TECHNICAL_FAILED；任务 FAILED；仅 attempt | — | 调用失败 | ✅ `test_tier3_technical_failure_leaves_no_formal_result` |
| 重复请求/并发 | 复用同一正式结果，不重复调用模型 | 0 次 | 0 次 | ✅ `test_repeated_requests_reuse_the_formal_result_without_model_calls`、`test_concurrent_scoring_creates_one_formal_result`、API `test_repeated_scoring_reuses_formal_result_without_model_calls` |

身份一致性：`preliminary / semantic / tier3` 与候选人绑定使用同一 `policy_id/version/hash` 与 `resume_content_hash`；仓储层保存前重算加权分、路由带、证据路径与阈值快照。

## 5. 三岗位后端端到端演示

见 `reports/phase1_7_sprint2/e2e_demo_steps.md`。自动化验收覆盖：三岗位隔离（维度 4/5/4、互不串扰）、跨岗位 404、批次锁定版本、异步评分、审核与联系分离。

## 6. 全量测试结果（Definitive run）

全量 `unittest discover -s tests -t .`：**Ran 545 tests — OK**（0 失败、0 error、0 skip）。构成：

| 套件 | 数量 | 结果 |
| --- | --- | --- |
| 既有 Stage 1–6 回归（含 Batch 5 门控） | 500 | 全部通过（3 处历史发布调用改为带审计原因的显式豁免，断言未改） |
| `tests.test_pipeline_service`（新增） | 13 | ✅ |
| `tests.test_pipeline_repository`（新增） | 15 | ✅ |
| `tests.test_api_pipeline`（新增） | 9 | ✅ |
| `tests.test_policy_llm_reviewer`（+5） | 17 | ✅ |
| `tests.test_policy_compiler`（+3 审计门控） | 20 | ✅ |
| `tests.test_config_guard`（主库已迁移 v9） | 3 | ✅ |

日志：`reports/phase1_7_sprint2/sprint2_tests.log`（五段）。

## 7. 尚未验证（未验收）的能力

1. **真实 Qwen Tier 3**：`--live` 路径与 API 真实供应商调用未执行（离线 Mock 注入）。模型名解析逻辑有单测（默认/覆盖/缺失），但网络/供应商行为未验收。
2. **真实 BGE Tier 2**：API 默认使用真实 `SemanticScoringService`，但本轮未加载 1.3GB 模型；离线验收注入确定性 stub。真实语义资产准备（`semantic-prepare`）尚无 API 端点（见 §8）。
3. **真实前端联调**：API 契约与 OpenAPI 就绪，但 `frontend/` 仍是旧 V2 原型（含假数据回退），未对接新接口。
4. **多进程/多实例部署**：当前异步执行器为进程内线程池；多 worker 部署需外部队列（任务表已支持状态机）。
5. **真实认证**：采用可信头注入（`X-Org-Id`/`X-User-Id`），真实登录/网关联调未验收。

## 8. 已知问题与进入 Sprint 3 的条件

### 已知问题

1. **语义资产准备无 API**：deep_mode 需要候选人/策略 embedding 资产；当前只能通过既有 CLI `semantic-prepare` 预先准备，否则 Tier2 返回 `ASSET_INCOMPLETE` → NEEDS_REVIEW（诚实但不完整）。Sprint 3 需加 API/后台任务或明确运维前置步骤。
2. **正式结果表分叉**：通用结果在 `pipeline_evaluations`，旧 V2 结果仍在 `evaluations`；二者语义不同、互不影响。是否统一/保留期策略待产品决策。
3. **候选人列表 N+1 查询**：`list_job_candidates` 逐候选查最新结果；数据量大时需批量查询/物化视图。
4. **排名并列无稳定次级排序**：同分按创建时间；可按需加 `candidate_key`。
5. **任务无超时/取消**：线程池任务无 lease 超时；进程崩溃会留下 RUNNING 任务（可用 FAILED 重试，但需人工/清理策略）。
6. **`uv.lock` 未刷新**：pyproject 新增 fastapi/uvicorn/httpx 声明，但为避免依赖版本漂移未执行 `uv lock`（环境已安装，测试通过）；进入发布前需锁定。
7. **API 未提供删除/关闭岗位**：MVP 未要求；Repository 有状态方法可扩展。

### 进入 Sprint 3 的条件（建议门槛）

1. 完成真实 Qwen Tier3 Live Smoke（三岗位各 1 次，显式 `--live`）并记录 metrics。
2. 完成真实 BGE 语义验收（三岗位×N 候选人），或在 API 中提供语义资产准备端点。
3. 前端切换到新 API（删除静默假数据回退），完成一次人工端到端联调。
4. 明确认证网关集成方式与部署拓扑（单进程/多进程）。
5. 通过 Sprint 2 回归（本节 §9）+ 人工验收本报告。
