# Sprint 4 Batch 2A — Edge CDP 接管与 Browser Adapter 集成（设计与实现）

日期：2026-11-15
范围：浏览器运行时（CDP）、数据源解耦、CDP Mock 全链路、扫描状态隔离、候选人级联系汇总、Dry-Run 幂等。
安全边界：**不向真实 BOSS 候选人发送消息**；`boss_readonly` 未授权时自动禁用；不读取/持久化 Cookie、Token；不自动登录/滑块/验证码；不关闭非本程序启动的浏览器。

---

## 1. 浏览器运行时架构

```
FastAPI 请求线程池 ──submit──▶ CDP worker thread（唯一）
                                   │  sync_playwright
                                   ▼
                          chromium.connect_over_cdp("http://127.0.0.1:9222")
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
            外部已启动 Edge（owned=False）   本程序启动 Edge（owned=True）
            只断开，绝不关闭                close() 时 terminate 专用进程
```

### 1.1 `browser/runtime.py`

| 类 / 方法 | 职责 |
| --- | --- |
| `CDPConnectionManager` | 单线程执行器（`ThreadPoolExecutor(max_workers=1)`）+ 单次 Playwright 实例；所有 Playwright 调用经 `run(fn)` 调度到该线程，解决 FastAPI 多线程与 sync API 的线程亲和性。 |
| `connect()` | 已有 9222 则直接 `connect_over_cdp`（`owned=False`）；端口空闲且 `auto_launch=True` 时用专用 profile 启动 Edge（`owned=True`）。连接失败/超时/进程提前退出 → `BrowserError`（`BROWSER_UNAVAILABLE` / `CDP_CONNECT_FAILED` / `TIMEOUT`）。 |
| `health()` | 连接状态、页面数、endpoint；连接被浏览器关闭时返回 `connected=False` 并清理句柄。 |
| `list_pages()` | 仅返回 `{context,url,title}`；**不读取 Cookie/Token/存储**。 |
| `write_mock_page(html)` | 将 Mock HTML 写入 `<profile 同级>/mock-pages/mock-<hash>.html`，返回 `file://` URL（内容寻址，重复扫描复用同一文件）。 |
| `open_page(url, prefix)` | 多标签页目标选择：优先复用 URL 前缀匹配的页，否则新建；不影响用户其它标签页。 |
| `close()` | 仅断开 CDP 连接；`owned=True` 时额外 `terminate()` 专用 Edge 进程；外部浏览器保持运行。 |

### 1.2 专用 Profile 隔离

- 默认：`%LOCALAPPDATA%\boss-hr-helper\edge-profile\port-<port>`（非 Windows 用 `XDG_DATA_HOME`/`TEMP`）。
- 构造函数硬校验：若 `user_data_dir` 等于日常 Edge `User Data`，直接 `ValueError`。
- 启动参数包含 `--remote-debugging-port=<port>`、`--user-data-dir=<专用目录>`、`--no-first-run`、`--no-default-browser-check`、`--disable-extensions`、`--disable-background-networking`；测试默认 `--headless=new`。

### 1.3 故障语义

| 场景 | 结果 |
| --- | --- |
| 端口无监听且 `auto_launch=False` | `BROWSER_UNAVAILABLE`（可恢复），HTTP 503 |
| 端口被非 CDP 服务占用 | `CDP_CONNECT_FAILED`（不尝试再启动） |
| Edge 启动后未开端口 / 提前退出 | `TIMEOUT` / `BROWSER_UNAVAILABLE` |
| 连接被浏览器关闭 / 标签页失效 | `BROWSER_CLOSED` / `PAGE_UNAVAILABLE` / `PAGE_CHANGED` |
| 采集 0 人且存在 fatal issue | HTTP 503，状态查询记录 `FAILED` + `error.code` |
| Mock 页面加载失败 / 登录失效 | `PAGE_UNAVAILABLE` / `LOGIN_REQUIRED`（不可恢复），不伪装成功 |

---

## 2. 数据源与模型模式解耦

新增 `browser/source.py`，`BROWSER_SOURCE` 取值：

| 值 | 行为 |
| --- | --- |
| `disabled` | 不挂载浏览器路由（404）；运行时诊断标记禁用原因 |
| `mock` | 内存 `MockBossAdapter`（无浏览器进程） |
| `cdp_mock` | 真实 Edge + CDP + 本地 Mock DOM（默认端口 9222，可配置） |
| `boss_readonly` | 真实 BOSS 只读；**必须** `BROWSER_ALLOW_REAL_BOSS=1`，否则自动降级为 `disabled` 并记录诊断 |

默认：deterministic → `mock`；live → `disabled`。模型模式与浏览器源互不强制：
`deterministic + cdp_mock` 与 `live + mock/cdp_mock` 均受支持（有测试）。
`GET /api/runtime-mode` 返回 `browser_source` 诊断（source/enabled/cdp_endpoint/profile/diagnostics）。

环境变量：

```
BROWSER_SOURCE=disabled|mock|cdp_mock|boss_readonly
BROWSER_CDP_HOST=127.0.0.1
BROWSER_CDP_PORT=9222
BROWSER_CDP_AUTOLAUNCH=1
BROWSER_USER_DATA_DIR=<专用目录，禁止默认 Edge Profile>
BROWSER_HEADLESS=1
BROWSER_CHANNEL=msedge
BROWSER_EDGE_PATH=<可选，默认自动查找>
BROWSER_ALLOW_REAL_BOSS=1   # boss_readonly 的显式授权
```

`boss_readonly` 当前仅允许**连接与页面列表诊断**：真实 DOM 契约未核验，扫描返回结构化 `UNSUPPORTED`，不会读取或发送任何真实候选人消息。

---

## 3. CDP Mock 全链路

```
Vue（ScannerView / ReviewBoardView）
  → FastAPI（扫描 / Dry-Run / 联系汇总）
  → CDPConnectionManager（单线程）
  → Edge（专用 profile，CDP）
  → file:// Mock DOM（分页、详情弹窗、招呼按钮、失败/登录失效态）
  → CandidateListCollector（真实 DOM 选择器）
  → Repository（批次 Policy 绑定、去重导入）
  → Tier0/1/2/3 → HR 审核 → Greeting Dry-Run（DOM 重新定位招呼控件，不点击）
```

- `browser/cdp_adapter.py::CdpMockBossAdapter` 所有 DOM 操作经 `manager.run`，多次 HTTP 请求可复用同一 9222 浏览器与同一 Mock 标签页（有页面数不增长断言）。
- 扫描响应新增 `source` / `browser` / `page_url`，前端展示"数据源：cdp_mock · edge-cdp · file://…"。
- `browser_adapter_factory` 可由 `create_app(..., browser_adapter_factory=...)` 注入；`browser_runtime` 参数允许测试注入独立端口的运行时。

---

## 4. 扫描状态隔离

- 状态键：`(org_id, job_id, batch_id)`，写入 `app.state.browser_scan_results`。
- 路由：`POST/GET /api/jobs/{job_id}/batches/{batch_id}/browser/scan`（批次来自路径；body 中冲突的 batch_id 返回 400）。
- 同岗位不同批次完全隔离（测试：A 批次 `imported=3`，B 批次 `imported=0/reused=3`，状态互不覆盖）。
- 重复扫描候选人去重（`acquire_candidate_for_job` 复用）。
- 失败也写入状态（`FAILED` + `error.code/message/recoverable`），GET 可查询，不伪装成功。
- **未新增数据库迁移**：状态为进程内内存。若需持久化/多 worker 共享，需新迁移（见 §7）。

---

## 5. 联系规则与候选人级汇总

业务规则（已实现）：

| 维度 | 规则 |
| --- | --- |
| 跨岗位 | 不同 `job_id` 可分别联系同一候选人（去重查询按 job 过滤） |
| 多 HR | 同一岗位内不同 `actor_user_id` 可各自联系；仅**同一 HR** 重复发送被阻断 |
| 跨评估 | 同一 `job_candidate` 的 fast/deep、任意 Policy 版本共享同一去重口径 |
| 审批 | 最新非 `NOTE` 审核状态必须为 `APPROVE` |

- `GreetingExecutor` 去重改为 `has_pipeline_contact_event(..., actor_id=ctx.user_id)`。
- `PipelineRepository.pipeline_contact_summary` 返回：`contacted`、`contacted_by`（去重排序的 HR）、`current_user_contacted`、`latest_contact`、`latest_pending_dry_run`、`current_user_pending_dry_run`、`pending_dry_run_count`、`events`（含 `scoring_mode`）。
- API：`GET /api/jobs/{job_id}/candidates/{job_candidate_id}/contact-summary`；候选人详情响应内也包含 `contact_summary`。
- 前端：审核看板按候选人级徽章展示"已联系（HR：owner、hr1）/ 当前账号已联系 / 当前账号已预演（PENDING）/ 团队预演：hr2 / 预演记录 N 条"；切换 fast/deep 时徽章保持一致。

---

## 6. Dry-Run 幂等与正式联系事件演进

### 6.1 当前实现（无 Schema 变更）

- 仍然 `dry_run=True` 物理锁死，真实发送返回 `UNSUPPORTED`。
- 幂等键：`(job_candidate_id, pipeline_evaluation_id, actor_user_id)` + `action='PENDING'` + `note LIKE 'DRY_RUN:%'`。
- 重复点击：先重新执行真实 DOM 校验（确认招呼控件仍可定位），再返回既有 PENDING 行，响应 `idempotent=true`，**不再 INSERT**。
- 响应/前端提示"已复用既有预演记录（幂等，未重复写入）"；联系表只保留 1 条预演行。
- 不同 HR 各自保留自己的预演行（符合多 HR 规则）。

### 6.2 正式联系事件演进（建议，未实施）

1. **状态机**：新增 `SENDING` / `PENDING_RECONCILIATION` 状态与发送意图日志，先登记意图再执行不可逆动作，确证后落 `CONTACTED`；页面不确定 → 人工核对，绝不重试。
2. **并发唯一性**：`pipeline_contact_events` 增加 `job_candidate_id` 列并建部分唯一索引
   `UNIQUE(org_id, job_id, job_candidate_id, actor_id) WHERE action IN ('SENDING','CONTACTED')`，
   在数据库层保证"同岗位同 HR 至多一次"，跨进程安全。
3. **幂等键**：HTTP 层引入 `Idempotency-Key`（或 attempt id），重放返回首次结果。
4. **跨岗位/多 HR**：唯一键包含 `org_id/job_id/actor_id`，天然允许不同岗位、不同 HR 各自联系。
5. 迁移方式：新增 `011_*.sql`，沿用 010 的"暂存表 → 重建 → 回填 → 重建索引/触发器"范式；**需批准后实施**。

以上第 1–3 项涉及 Schema，**本批次未实施**。

---

## 7. 安全与合规清单

- [x] 不读取/持久化 Cookie、Token、localStorage；页面列表只含 URL/标题。
- [x] 不自动登录、不处理滑块/验证码。
- [x] 不关闭外部启动的浏览器（`owned=False` 仅断开）；有测试覆盖。
- [x] 专用 `--user-data-dir`，拒绝日常 Edge Profile。
- [x] Mock 页面均为 `file://`，不访问 `zhipin.com`（测试断言 URL 不含该域）。
- [x] 真实发送物理禁用；`boss_readonly` 未授权时禁用，授权时也只读诊断。
- [x] 未修改 001–010 迁移、`schema_migrations`、正式业务数据。

---

## 8. Batch 2B 前置条件

1. **真实账号与授权**：用户明确授权 `boss_readonly`（`BROWSER_ALLOW_REAL_BOSS=1`）并提供已人工登录的专用 Edge 会话；不代填账号、不绕验证码。
2. **真实 DOM 契约核验**：推荐页 iframe、卡片/详情选择器、`encrypt_geek_id/securityId` 来源、分页/滚动行为；参考仓库为 v1.2.2（2026-10），需重新验证。
3. **发送语义**：打招呼按钮文案、确认弹窗、上限提示；保持"页面不确定不记成功"。
4. **Schema 批准**：发送意图日志/唯一索引方案（§6.2）经批准后以 `011` 迁移落地，并提供带数据升级与并发测试。
5. **扫描状态持久化**（可选）：如需多 worker/重启保留，新增扫描任务表（同样走 011 审批）。
6. **风控与频控**：发送间隔、每日上限、异常熔断策略需产品确认。
7. **凭据边界**：继续禁止读取/落库 Cookie/Token；如需登录态检测，仅探测页面可见状态，不读取存储。
