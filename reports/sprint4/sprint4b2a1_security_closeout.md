# Sprint 4 Batch 2A.1 — CDP 身份隔离与安全收尾（修复证据）

日期：2026-11-15
范围：7 项审计问题的逐项修复与回归验证。
约束：未改 `config.json`、001–010 迁移、正式 Policy/数据库/备份；未新增 Schema；未连接真实 BOSS、未发送真实招呼语。

---

## P0-1 多 HR 浏览器会话隔离

**问题**：`app.state.browser_runtime` 为全局单实例，所有 HR 共用同一 CDP 浏览器。

**修复**：新增 `browser/sessions.py::BrowserSessionRegistry`

- 键为 `(org_id, actor_user_id)`；每个 HR 惰性获得一个独立 `BrowserRuntime`。
- 每个 Session 分配**独立 CDP 端口**（从 `port_base` 向上探测空闲端口）与**独立专用 Profile**
  （`<profile_root>/<org>-<user>-<account>/profile-<port>`）。
- 明确区分 `actor_user_id`（系统身份）与 `browser_account_id`（BOSS 账号身份）：
  - 本地/Mock 阶段由服务端派生 `mock-account-<sha256[:12]>`（`mapping_source="simulated"`）；
  - 可通过构造参数 `bindings={(org,user): {browser_account_id, cdp_port, user_data_dir}}` 做受控映射（`mapping_source="configured"`）；
  - `boss_readonly` 未显式映射账号时抛 `BROWSER_ACCOUNT_UNMAPPED`，不做“user_id 即 BOSS 账号”的假设。
- `server/app.py`：`create_app(..., browser_session_registry=...)`；未注入时按 cdp_mock 自动创建注册表；lifespan 统一关闭所有 session；`/api/runtime-mode` 输出 `browser_source.sessions`。
- 扫描响应新增 `browser_account_id`，前端可区分实际使用的 BOSS 账号。

**测试**
- `test_browser_sessions`：每个 HR 端口/Profile/账号不同；同 HR 复用同一 runtime；配置映射覆盖；boss_readonly 未映射拒绝；描述与关闭；端口占用跳过。
- `test_browser_cdp_e2e::test_two_hr_sessions_use_isolated_browsers`：真实 Edge 双实例，端口/Profile/页面互不串。
- `test_api_browser_cdp::ApiCdpSessionIsolationTests`：HTTP 层两个 HR 扫描返回不同 `browser_account_id` / `page_url`，注册表 2 个 session、端口/Profile 均唯一。

---

## P0-2 Dry-Run 目标身份二次确认与 DOM 作用域闭合

**问题**：`_send_greeting_impl` 用页面全局 `#detail-panel #greet-button` 查找控件，A 的面板可能被当作 B。

**修复**（`browser/cdp_adapter.py`，`browser/playwright_adapter.py` 同步）：

1. 卡片必须唯一命中 `data-geek-id`，且其属性值等于请求 identity。
2. 若当前详情面板可见，其 `data-geek-id` 必须等于目标；属于 A 而请求 B → `IDENTITY_MISMATCH`，**不做任何点击**。
3. 否则仅通过**目标卡片内部**的 `.open-detail` 打开详情，并再次校验面板 `data-geek-id` 与卡片/面板姓名一致。
4. 招呼按钮只在**已核验面板根节点**内 `panel.locator("#greet-button")` 查找，且必须唯一；缺失/重复 → `PAGE_UNCERTAIN`。
5. 所有 DOM/超时异常 → 结构化 `BrowserError` / `PAGE_UNCERTAIN`；真实发送仍 `UNSUPPORTED`。

**测试**（`test_browser_cdp_e2e::test_greeting_scope_rejects_another_candidates_open_panel`）：
- 打开 A 面板后对 B 直接 Dry-Run → `IDENTITY_MISMATCH`；
- 经 executor 重新打开 B 面板后允许；
- 篡改面板 `data-geek-id` 为 A → `IDENTITY_MISMATCH`；
- 移除面板内招呼按钮 → `PAGE_UNCERTAIN`。

---

## P0-3 外部 CDP Profile 实证核验与回环限制

**修复**（`browser/runtime.py`）：

- **回环强制**：`host` 仅接受 `127.0.0.1/localhost/::1`，其它值构造即 `ValueError`。
- **真实 Profile 核验**：
  - 自有启动：写入专用目录哨兵 `boss-hr-helper-profile.json`，启动参数加 `--enable-automation`（Edge 才允许读取命令行）。
  - 接管已有端口：通过 browser-level CDP 会话执行 `Browser.getBrowserCommandLine`，解析真实 `--user-data-dir` 与配置的专用目录比对：
    - 不一致 → `CDP_PROFILE_MISMATCH`（拒绝，不可用环境变量绕过）；
    - 读不到命令行 → 校验专用目录哨兵；无哨兵 → `CDP_PROFILE_UNVERIFIED`（拒绝）；
    - 仅当显式 `BROWSER_ALLOW_UNVERIFIED_CDP=1` 时允许“未验证”连接（`profile_verified=false`），且 `owned=false`。
- 健康信息新增 `profile` / `profile_verified`，便于诊断。
- 不读取 Cookie/Token，不做登录自动化。

**测试**
- 单元：非回环拒绝；未验证拒绝 + 显式豁免；Profile 不匹配拒绝；匹配通过；哨兵生效。
- 真实 Edge：外部不同 Profile 接管 → `CDP_PROFILE_MISMATCH` 且 owner 浏览器不受影响；相同 Profile → 连接成功、`profile_verified=true`、`owned=false`。

---

## P1-1 CDP 断线重连的进程所有权

**修复**：`_connect_impl` 在重连前计算 `was_owned = _owned and _process 存活`；重连成功后
`_owned = launched or was_owned`，不再无条件覆盖为 `False`。真正外部实例保持 `owned=false`，`close()` 只断开、不杀进程。

**测试**
- 单元（mock）：自有进程存活 + 断线 → 重连后 `owned=true` 且不重复做外部 Profile 校验；外部连接 → `owned=false`。
- 真实 Edge（`test_owned_process_survives_a_cdp_reconnect_and_is_closed`）：连接 → 主动断开 CDP → 自动重连 → `owned=true` → `close()` 后底层进程被终止。
- 既有 `test_external_connection_closes_without_killing_the_browser` 保证外部实例不被误杀。

---

## P1-2 Dry-Run 并发幂等原子化

**修复**：
- 仓储新增 `PipelineRepository.ensure_pending_dry_run(...)`：在**单个 `BEGIN IMMEDIATE` 事务**内完成“校验 evaluation 归属 → 查询既有 `DRY_RUN:` PENDING（按 actor）→ 插入或复用”，并用审计记录区分新建。
- `server/api.py` 每次请求仍先执行完整 DOM 身份/控件校验，通过后只调用该原子 get-or-create，不再分离查询与插入。

**测试**
`test_api_browser::test_concurrent_dry_runs_create_exactly_one_pending_row`：8 线程同时 Dry-Run，全部 200，数据库仅 1 条 PENDING，事件 id 全部一致，仅 1 个请求 `idempotent=false`。

---

## P1-3 DOM 提取结构化错误

**修复**：
- `CdpMockBossAdapter._list_cards_impl` 全流程 try/except：任何 Playwright/渲染异常 → `PAGE_UNAVAILABLE`（`BrowserError`）。
- `_open_detail_impl` / `_send_greeting_impl` → `PAGE_CHANGED` / 结构化 Outcome。
- `CandidateListCollector` 增加兜底：适配器抛出的非 `BrowserError` 统一转为 `UNEXPECTED_STATE` issue，不向 HTTP 层泄漏 500。
- API：0 采集且存在 fatal issue → 记录批次 `FAILED` + `error.code`，返回 503 `BrowserError`。

**测试**
- `test_browser_adapter::CdpDomErrorTests`：列表/详情/招呼 DOM 崩溃均结构化。
- `test_api_browser::test_dom_failure_returns_a_structured_browser_error`：注入渲染崩溃适配器 → HTTP 503 `UNEXPECTED_STATE`，状态查询 `FAILED`。

---

## P1-4 前端联系状态统一与详情竞态

**修复**（`frontend/src/views/ReviewBoardView.vue`）：
1. **单一事实源**：所有候选人级徽章、按钮禁用/提示统一读取 `contact_summary`；删除基于单条 evaluation `contacts` 的 `contacted` 判断。
2. 联系事件表直接展示 `contactSummary.events`，fast/deep 切换不再出现矛盾。
3. “已联系”按钮与 Dry-Run 按钮在 `current_user_contacted=true` 时禁用，并提示“同岗位跨评估去重”。
4. **详情竞态**：新增 `detailToken`；`selectRanking` 每次点击自增 token，`candidateDetail`/`reviews`/`contactSummary` 每个 await 后校验 token + 同 job_candidate + epoch，旧响应直接丢弃。
5. epoch 切换同时递增 token 并清空汇总。

**测试**（UI E2E，`tests/frontend_e2e.py`）：
- 同一 HR 在 fast 评估标记 CONTACTED → UI 在 fast 与 deep 下均显示“已联系（HR：owner）”，Dry-Run 按钮禁用。
- 快速点击候选人 1 → 候选人 2，最终详情头保持候选人 2（旧响应被 token 拦截）。
- 重复 Dry-Run 仍只保留 1 条 PENDING。

---

## P1-5 候选人权威身份契约（补充核查）

**核查结论**：
- `schemas/candidate.py` 仅校验 `_meta` 格式；`CandidateRepository.acquire_candidate_for_job` 始终使用显式 `candidate_key` 参数作为绑定键，页面 `_meta.candidate_key` 从未直接决定绑定。
- 但 `CandidateDetailParser` 原先用 `setdefault`，页面返回的恶意 `_meta.candidate_key/encrypt_geek_id` 可污染采集导入键。

**修复**：
- 解析器强制覆盖：`encrypt_geek_id = 适配器稳定身份`、`encrypt_job_id = job`、`candidate_key = "job:geek"`；并校验解析结果与之一致。
- 仓储 `_validate_binding_identity`：
  - 任何来源：`job_id:geek` 键的前缀必须等于绑定岗位（拒绝跨岗位键）；
  - 浏览器来源（`source="browser"`）：必须有稳定 geek id，键必须等于 `job_id:encrypt_geek_id`，且 `encrypt_job_id` 不得指向其它岗位；
  - 手工来源：简单键保持不变，页面 `_meta.candidate_key` 不参与绑定（兼容性保留）。

**测试**（`test_candidate_identity`）：解析器防覆盖；浏览器键不一致/缺 geek 拒绝；跨岗位键拒绝；手工键忽略页面 meta；API 跨岗位键返回 400。

---

## 未新增 Schema

- 未新增表/列/索引；账号绑定与扫描状态仍为进程内（受控映射/内存）。
- 真实 BOSS 账号绑定若需落库，需 011+ 迁移（见 Batch 2B 前置条件与既有设计文档）。

---

## 安全边界复核

- [x] 未连接真实 BOSS、未发送真实招呼语（`dry_run=True` 锁死，真实发送 `UNSUPPORTED`）。
- [x] CDP 仅限回环；外部未知 Profile 默认拒绝。
- [x] 不读取/持久化 Cookie、Token；不自动登录/滑块/验证码。
- [x] 不关闭非本程序启动的浏览器；自有进程断线重连后仍正确回收。
- [x] 未修改 `config.json`、001–010 迁移、正式业务数据；未提交任何 Schema 变更。
