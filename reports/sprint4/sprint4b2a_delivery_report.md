# Sprint 4 Batch 2A 交付报告（Edge CDP 接管与 Browser Adapter 集成）

日期：2026-11-15
范围：浏览器运行时 / CDP Mock 全链路 / 数据源解耦 / 扫描状态隔离 / 候选人级联系汇总 / Dry-Run 幂等。
约束执行：未 `git clean`/`reset --hard`；未改 001–010 迁移、`schema_migrations`、正式业务数据；未新增 Schema；未向真实 BOSS 候选人发送任何消息；未接管日常 Edge Profile。

---

## 1. 实际修改文件

### 1.1 新增

| 文件 | 说明 |
| --- | --- |
| `browser/source.py` | `BROWSER_SOURCE=disabled|mock|cdp_mock|boss_readonly` 解析、默认值（deterministic→mock / live→disabled）、`boss_readonly` 授权门禁与降级、启动诊断。 |
| `browser/runtime.py` | `CDPConnectionManager`（单 worker 线程执行 Playwright、专用 profile、自动拉起/接管 9222、健康检查、页面列表、断线/超时错误、owned/external 区分）+ `BrowserRuntime` 门面。 |
| `browser/cdp_adapter.py` | `CdpMockBossAdapter`：真实 Edge + CDP + `file://` Mock DOM，多标签页选择、DOM 采集、详情解析、Dry-Run 控件重定位（不点击）。 |
| `tests/test_browser_runtime.py` | 13 项：数据源矩阵、profile 隔离、启动参数、线程亲和、不可用/断线、端口被非 CDP 服务占用、外部浏览器不被杀、owned 进程清理。 |
| `tests/test_browser_cdp_e2e.py` | 7 项：真实 Edge CDP 连接/健康/页面列表、多标签页、采集入仓、断线重连、外部连接不杀浏览器、失败态、真实 DOM Dry-Run。 |
| `tests/test_api_browser_cdp.py` | 3 项：HTTP→Edge CDP→DOM 全链路、批次状态隔离与标签复用、浏览器不可用 503 + FAILED 状态。 |
| `tests/test_contact_summary.py` | 5 项：候选人级汇总、跨 fast/deep、同岗多 HR、跨岗位独立、Dry-Run 幂等（按 actor）。 |
| `reports/sprint4/browser_runtime_and_cdp_integration.md` | 运行时/数据源/CDP Mock/联系规则/幂等设计与 2B 前置条件。 |
| `reports/sprint4/sprint4b2a_delivery_report.md` | 本文件。 |
| `reports/sprint4/sprint4b2a_tests.log` | 本批次测试原始日志。 |

### 1.2 修改

| 文件 | 说明 |
| --- | --- |
| `browser/contracts.py` | `BrowserHealth` 增加 `pages/source/page_url`；错误码增加 `BROWSER_UNAVAILABLE/BROWSER_CLOSED/CDP_CONNECT_FAILED/TARGET_MISSING`。 |
| `browser/greeting.py` | 去重按 `actor_id` 过滤：同岗不同 HR 各自可联系；同 HR 跨 fast/deep/Policy 版本仍阻断。 |
| `storage/repositories/pipeline.py` | `has_pipeline_contact_event(actor_id=...)`；新增 `pipeline_contact_summary`（团队汇总）与 `find_pending_dry_run`（幂等查询）。 |
| `server/app.py` | 数据源解析、`BrowserRuntime` 注入、按 source 挂载路由、`browser_adapter_factory`、lifespan 关闭运行时、`BrowserError`→503/409 结构化响应、`/api/runtime-mode` 增加浏览器诊断。 |
| `server/api.py` | 扫描/状态改为批次路径（`/batches/{batch_id}/browser/scan`）；状态键 `(org,job,batch)`；失败写入 `FAILED` 后抛结构化错误；Dry-Run 幂等复用；新增候选人联系汇总；候选人详情内嵌汇总。 |
| `server/schemas.py` | `BrowserScanRequest` 批次改为可选（路径为准，冲突 400）。 |
| `server/responses.py` | 扫描响应增加 `source/browser/page_url`；状态增加 `batch_id/error`；Dry-Run 增加 `idempotent`；新增 `ContactSummaryOut`。 |
| `frontend/src/api/index.ts` | 批次级扫描/状态、`contactSummary`；Dry-Run 结果/汇总类型。 |
| `frontend/src/types/index.ts` | `BrowserScanResult/Status` 扩展、`GreetingDryRunResult.idempotent`、`ContactSummary`。 |
| `frontend/src/views/ScannerView.vue` | 批次级扫描调用、数据源/page_url 展示。 |
| `frontend/src/views/ReviewBoardView.vue` | 候选人级联系徽章（团队/当前账号/PENDING 预演）、切换评估保持一致、幂等提示。 |
| `tests/test_api_browser.py` | 批次路径、批次状态隔离、活跃/禁用数据源、幂等预演、多 HR 预演。 |
| `tests/frontend_e2e.py` | 后端以 `cdp_mock`（端口 9335、临时专用 profile）启动；新增数据源、候选人徽章、重复 Dry-Run 幂等断言。 |

---

## 2. 关键行为

### 2.1 数据源矩阵（与模型模式解耦）

| 组合 | 结果 |
| --- | --- |
| deterministic + mock（默认） | 内存 Mock 扫描 |
| deterministic + cdp_mock | 真实 Edge/CDP Mock DOM |
| live + mock / cdp_mock | 允许；模型与浏览器互不强制 |
| live + 未配置 | `disabled`，浏览器路由 404 |
| boss_readonly 未授权 | 自动降级 `disabled`，诊断记录原因 |
| boss_readonly 已授权 | 仅连接/页面诊断；扫描返回 `UNSUPPORTED`，不读取真实候选人 |

### 2.2 浏览器运行时

- 所有 Playwright 调用固定在同一 worker 线程，FastAPI 多次请求稳定复用同一 CDP 浏览器。
- 默认专用 profile：`%LOCALAPPDATA%\boss-hr-helper\edge-profile\port-<port>`；拒绝日常 Edge Profile。
- 自动拉起参数含 `--remote-debugging-port` 与 `--user-data-dir`，支持有头/无头。
- 只断开外部浏览器；仅终止自己启动的进程。
- `list_pages` 仅返回 URL/标题，不读取 Cookie/Token。

### 2.3 扫描隔离与错误

- 状态按 `(org_id, job_id, batch_id)` 隔离，同岗位多批次互不覆盖；重复扫描去重。
- 连接失败/0 采集且 fatal issue → HTTP 503 + `FAILED` 状态 + `error.code`，不伪装成功。

### 2.4 联系规则与汇总

- 同岗同 HR：跨 fast/deep、跨 Policy 版本去重（`ALREADY_SENT`）。
- 同岗不同 HR：各自可联系、可并发（无跨 actor 阻塞）。
- 跨岗位：互不影响。
- 汇总 API/前端：`contacted`、`contacted_by`、当前账号状态、最新/当前 PENDING 预演、预演条数；fast/deep 切换徽章一致。

### 2.5 Dry-Run 幂等

- `(job_candidate_id, evaluation_id, actor_id)` 已有 `DRY_RUN:` PENDING → 重新做 DOM 校验后复用，`idempotent=true`，不新增行。
- 不同 HR 各自保留一条预演记录。
- 正式联系事件演进（SENDING/唯一索引/幂等键）见设计文档 §6，**需新迁移批准**。

---

## 3. 测试结果

原始日志：`reports/sprint4/sprint4b2a_tests.log`（10 个分区，626 项全量回归 + 前端检查）。

| 测试 | 结果 |
| --- | --- |
| 后端全量 `unittest discover -s tests -t .` | **Ran 626 tests — OK**（0 失败 / 0 error / 0 skip，约 218s） |
| 浏览器运行时/数据源单测 | **13 / 13 OK** |
| CDP E2E（真实 Edge，专项） | **7 / 7 OK** |
| HTTP→Edge CDP→DOM E2E | **3 / 3 OK** |
| 联系汇总 / 多 HR / 跨岗位 / 幂等 | **5 / 5 OK** |
| 既有 API 扫描 + Greeting 安全 + 内存 Mock 适配器 | **31 / 31 OK** |
| 前端 Node 竞态测试 | **7 / 7** |
| 前端 `vue-tsc --noEmit` | **0 错误 / 0 告警** |
| 前端 `vite build` | **成功**（dist 已清理） |
| 前端 UI E2E（Vue→HTTP→Edge CDP→DOM） | **19 / 19 检查通过** |
| `main.py db-check` | **ok=true / schema_version=10 / issues=[]** |

CDP E2E 关键证据（`test_browser_cdp_e2e` / `test_api_browser_cdp` / UI E2E）：
- 真实 Edge 以专用 `--user-data-dir` 启动，`health.pages`、`list_pages` 可查询；Mock 页面 URL 为 `file://`，页面列表不含 Cookie/Token 字段；
- 多标签页：先打开 decoy 标签，适配器仍定位到 Mock DOM 标签；
- 断线后 `health.connected=false`、适配器返回 `BROWSER_CLOSED`；重新连接后采集恢复正常；
- 外部连接的 manager 关闭后，owner 浏览器仍存活且页面可列；
- HTTP 全链路扫描报告 `source=cdp_mock`、`browser=edge-cdp`、`page_url=file://…`，候选人经真实 DOM 进入 Repository，随后 deep 评分、APPROVE、Dry-Run `DRY_RUN_READY`；
- 重复 HTTP 扫描复用同一 CDP 标签（页面数不增长）；同岗两批次状态互不覆盖；
- 浏览器不可用时 HTTP 503 + `BROWSER_UNAVAILABLE`，状态查询为 `FAILED`，候选人数保持 0；

---

## 4. 遗留问题

1. **扫描状态仅进程内**：重启/多 worker 不共享；持久化需新迁移方案（未实施）。
2. **boss_readonly 未实现真实采集**：授权后仅连接/页面诊断；真实 DOM 契约未核验前不采集、不发送。
3. **正式发送并发控制**：当前 `dry_run=True` 物理禁用；真实发送需 SENDING 意图日志 + 唯一索引（新迁移，需批准）。
4. **Dry-Run 幂等依赖 note 前缀**：`DRY_RUN:` 为约定；若未来引入正式事件表，应迁移为结构化字段。
5. **UI E2E 依赖本机 Edge**：CDP 端口默认 9335（避免占用用户 9222）；CI 需安装 Edge 与 Playwright。
6. **多标签页策略**：按 Mock 页面 URL 前缀复用；真实 BOSS 需按 iframe/标签页规则重新核验。

---

## 5. 复现命令

```powershell
# 运行时/数据源单测
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_runtime -v
# 真实 Edge CDP E2E
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_cdp_e2e -v
# HTTP -> CDP -> DOM
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_api_browser_cdp -v
# 联系汇总 / 多 HR / 幂等
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_contact_summary -v
# UI 全链路（自动起 8010 后端 + 5174 前端 + 9335 CDP）
.\.venv\Scripts\python.exe -X utf8 -m tests.frontend_e2e
```
