# Sprint 4 Batch 2A.2 交付报告（跨批次 Policy 绑定与 CDP 安全收尾）

日期：2026-11-15
范围：3 项 P0 + 2 项 P1 修复、跨批次 Policy 版本绑定、扫描状态按 HR 隔离、runtime-mode 最小权限脱敏。
约束：未改 `config.json`、001–010 迁移、正式 Policy/数据库/备份；**未新增 Schema**；未连接真实 BOSS、未发送真实招呼语。

---

## 1. 实际修改文件

### 1.1 新增

| 文件 | 说明 |
| --- | --- |
| `tests/test_batch_policy_binding.py` | 3 项：批次 A(v1) 评分 → 发布 v2 → 批次 B(v2) 独立评分；批次必须归属候选人；HTTP 层同候选人双版本评分。 |
| `tests/test_runtime_mode_privacy.py` | 3 项：匿名无会话、已认证只见自己、未知身份无详情；不含绝对路径。 |
| `reports/sprint4/sprint4b2a2_delivery_report.md` | 本文件。 |
| `reports/sprint4/sprint4b2a2_tests.log` | 测试日志。 |

### 1.2 修改

| 文件 | 说明 |
| --- | --- |
| `browser/runtime.py` | 废除静态哨兵信任；新增 DevToolsActivePort 动态握手核验；外部 Profile 仅接受真实命令行或动态握手证据，否则 `CDP_PROFILE_UNVERIFIED`。 |
| `browser/source.py` | `describe()` 不再返回绝对 `user_data_dir`，改为 `profile_isolated` 布尔。 |
| `browser/sessions.py` | `describe()`/`describe_for()` 脱敏为 `profile_id`（哈希标识）；新增当前调用者专用摘要；`_default_runtime` 继承 `source.auto_launch`（不再硬编码 True）。 |
| `server/auth.py` | 新增 `get_optional_context`（缺失头返回 None）。 |
| `server/app.py` | `/api/runtime-mode` 改为可选身份：匿名仅全局摘要；认证后仅返回自己的 session；不再返回 session 列表/绝对路径。 |
| `server/api.py` | 扫描状态键增加 `actor_user_id`；评分/语义准备支持 `batch_id`；批次评分任务与结果校验按批次 Policy。 |
| `server/schemas.py` | `ScoreRequest.batch_id`、`SemanticPrepareRequest.batch_id`。 |
| `server/semantic_runtime.py` | `prepare`/语义服务支持 `batch_id`，按批次锁定 Policy 准备资产。 |
| `storage/repositories/candidates.py` | 新增 `resolve_batch_policy`、`expected_candidate_policy`（批次上下文优先，不覆写初始绑定）。 |
| `storage/repositories/pipeline.py` | `_binding_truth` 支持指定 Policy 版本并修复同名列歧义；`save_pipeline_result(batch_id=...)` 校验批次归属/Policy 锁；`pipeline_result_for_mode` 支持 Policy 过滤。 |
| `engine/policy_interpreter.py` | `load_for_candidate(batch_id=...)` 按批次锁定版本编译计划。 |
| `engine/prescreen_engine.py` | Tier 0/1 支持 `batch_id` 并按批次校验绑定。 |
| `engine/semantic_scoring_service.py` | Tier 2 支持 `batch_id`。 |
| `engine/policy_llm_reviewer.py` | Tier 3 `_identity/evaluate/evaluate_and_persist` 支持 `batch_id`。 |
| `engine/policy_scoring_pipeline.py` | `run/run_and_persist/reusable_result` 支持 `batch_id`；复用仅匹配同 Policy；保存携带 batch 上下文。 |
| `frontend/src/api/index.ts` | `semanticPrepare`/`startScoring` 支持 `batch_id`。 |
| `frontend/src/views/ScannerView.vue` | 评分与语义准备传入当前批次 ID。 |
| `tests/test_browser_runtime.py` | 新增静态哨兵拒绝、伪造 DevToolsActivePort 拒绝、握手匹配、连接所有权等回归。 |
| `tests/test_browser_sessions.py` | 断言脱敏字段 `profile_id`、auto_launch 继承。 |
| `tests/test_browser_cdp_e2e.py` | 真实 Edge 双 Session/Profile 与身份作用域回归（沿用）。 |
| `tests/test_api_browser.py` | 新增同批次双 HR 扫描状态隔离。 |
| `tests/test_api_browser_cdp.py` | 更新 runtime-mode 最小权限断言、双 HR 扫描状态各自保留。 |
| `tests/pipeline_support.py` | Tier2/Tier3 测试桩接受 `batch_id`。 |

---

## 2. 5 项问题修复验证证据

### P0-1 静态哨兵不再被信任

- `_sentinel_valid()` 已删除；外部连接只接受：
  - CDP 读取的真实 `--user-data-dir` 与专用目录一致；
  - 或活跃 `DevToolsActivePort`（端口 + WebSocket 路径）与当前 `/json/version` 严格匹配。
- 静态文件（含旧哨兵）仅存在时：`CDP_PROFILE_UNVERIFIED` 拒绝。
- 证据：`test_browser_runtime::test_static_sentinel_file_is_not_trusted`、`test_planted_devtools_active_port_without_live_browser_is_not_trusted`、`test_devtools_active_port_handshake_is_verified_against_the_endpoint`（本地握手服务器：匹配通过、伪造路径/端口拒绝）。

### P0-2 runtime-mode 最小权限与脱敏

- 匿名：`browser_source.session = null`，无 `sessions` 列表、无 actor/org/端口/路径。
- 已认证：仅 `registry.describe_for(ctx)` 返回当前 HR 自己的一条摘要；其他 HR/组织不可见。
- `profile` 绝对路径从 API 移除，仅返回 `profile_id`（sha256 前 12 位）与 `profile_isolated`。
- 证据：`test_runtime_mode_privacy`（3 项）、`test_api_browser_cdp::test_two_hr_accounts_get_isolated_cdp_sessions`（匿名/owner/hr2 三种视图断言）。

### P0-3 跨批次 Policy 绑定

- `resolve_batch_policy` 以 `(job_id, batch_id, job_candidate_id)` 解析批次锁定 Policy，并校验该候选人由该批次采集（`candidate_acquisitions` 或 first_batch）。
- Pipeline/Interpreter/Tier0-3 全链路接受 `batch_id`；结果保存校验批次归属与 Policy 版本；旧 `job_candidates.policy_id/version` 永不覆写。
- 复用查询按 `(policy_id, policy_version)` 过滤，批次 A 复评复用 v1，批次 B 生成独立 v2 正式结果。
- HTTP：`ScoreRequest.batch_id`、`SemanticPrepareRequest.batch_id`。
- 证据：`test_batch_policy_binding`（3 项），覆盖 v1/v2 两条不可变结果与默认无 batch 的向后兼容。

### P1-1 同批次多 HR 扫描状态

- 键从 `(org, job, batch)` 细化为 `(org, job, batch, actor_user_id)`；状态查询仅返回调用者自己的记录。
- 证据：`test_api_browser::test_scan_status_is_isolated_per_actor_in_one_batch`（owner collected=1，hr2 collected=3，各自状态保留）；CDP session 测试同样断言。

### P1-2 auto_launch 继承

- `BrowserSessionRegistry._default_runtime` 使用 `self.source.auto_launch`，不再硬编码 True。
- 证据：`test_browser_sessions::test_runtime_inherits_auto_launch_from_the_source`。

---

## 3. 测试结果

> 待最终全量回归完成后填入（见 `sprint4b2a2_tests.log`）。

| 测试 | 结果 |
| --- | --- |
| 后端全量 `unittest discover -s tests -t .` | **Ran 667 tests — OK**（0 失败 / 0 error / 0 skip，约 279s） |
| 跨批次 Policy 绑定 | **3 / 3 OK** |
| runtime-mode 最小权限 | **3 / 3 OK** |
| 浏览器运行时 + Session 隔离 | **30 / 30 OK** |
| CDP E2E + HTTP→CDP | **15 / 15 OK** |
| HTTP API 扫描/Dry-Run | **12 / 12 OK** |
| 前端 Node / `vue-tsc` / `vite build` | **7 / 7、0 错误、成功** |
| 前端 UI E2E | **30 / 30（退出码 0）** |
| `main.py db-check` | **ok / v10 / issues=[]** |

---

## 4. 遗留问题与 2B 前置条件

1. **真实 BOSS 账号绑定仍需 011+ 迁移**（当前受控模拟映射）；发送意图日志/唯一索引/幂等键同样需要 Schema 批准。
2. **DevToolsActivePort 兼容性**：Edge 固定 `--remote-debugging-port` 时可能不写该文件；自有实例依赖 `--enable-automation` 命令行证据；无任何证据的外部实例默认拒绝（安全优先）。
3. 扫描状态仍为进程内且按 actor 隔离；多 worker/重启持久化需新迁移。
4. `boss_readonly` 仍仅连接/页面诊断，真实 DOM 契约未核验前不采集、不发送。
5. 批次评分任务表没有 `batch_id` 列（未新增 Schema）；同 task 跨版本重放会以 `PipelineConflict` 拒绝，前端应按批次使用独立幂等键。
6. Batch 2B 前需完成：显式真实账号授权、真实 DOM/接口核验、发送语义/风控复测、011 迁移批准与并发/升级测试。

---

## 5. 复现命令

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_batch_policy_binding -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_runtime_mode_privacy -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_runtime tests.test_browser_sessions -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_cdp_e2e tests.test_api_browser_cdp -v
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -t .
```
