# Sprint 4 Batch 2A.1 交付报告（CDP 身份隔离与安全收尾）

日期：2026-11-15
范围：7 项审计问题的逐项修复、回归测试与安全收尾。
约束执行：未 `git clean`/`reset --hard`；未改 `config.json`、001–010 迁移、正式 Policy/数据库/备份；**未新增 Schema**；未连接真实 BOSS、未发送任何真实招呼语。

---

## 1. 实际修改文件

### 1.1 新增

| 文件 | 说明 |
| --- | --- |
| `browser/sessions.py` | `BrowserSessionRegistry`：按 `(org_id, actor_user_id)` 分配独立 CDP 端口 + 独立专用 Profile；`browser_account_id` 与系统 `actor_user_id` 显式分离；支持受控映射；`describe()`/`close()`。 |
| `tests/test_browser_sessions.py` | 6 项：双 HR 端口/Profile/账号隔离、同 HR 复用、配置映射、boss_readonly 未映射拒绝、描述/关闭、端口占用跳过。 |
| `tests/test_candidate_identity.py` | 6 项：解析器防页面 meta 覆盖、稳定身份必填、浏览器键规范、跨岗位键拒绝、手工键忽略 meta。 |
| `reports/sprint4/sprint4b2a1_security_closeout.md` | 7 项问题的修复证据与安全边界说明。 |
| `reports/sprint4/sprint4b2a1_delivery_report.md` | 本文件。 |
| `reports/sprint4/sprint4b2a1_tests.log` | 本批次测试日志。 |

### 1.2 修改

| 文件 | 说明 |
| --- | --- |
| `browser/runtime.py` | 强制回环 host；外部 CDP Profile 实证核验（`Browser.getBrowserCommandLine` + 专用目录哨兵）；启动参数加 `--enable-automation`；断线重连保持 `_owned`；健康信息增加 `profile/profile_verified`。 |
| `browser/cdp_adapter.py` | Dry-Run 目标身份二次确认：面板/卡片身份核验、卡片作用域打开详情、面板内唯一招呼控件；全局无作用域查找被移除；列表/详情/招呼 DOM 异常统一结构化。 |
| `browser/playwright_adapter.py` | 同样收紧 Dry-Run 身份作用域（与 CDP 适配器一致）。 |
| `browser/collector.py` | 浏览器导入显式 `source="browser"`；非 `BrowserError` 兜底为 `UNEXPECTED_STATE`；新增 fatal 错误码（BROWSER_CLOSED/UNAVAILABLE/CDP_CONNECT_FAILED）。 |
| `browser/identity.py` | 解析器强制覆盖 `_meta.encrypt_geek_id/encrypt_job_id/candidate_key`，页面元数据不能污染绑定键。 |
| `browser/contracts.py` | `BrowserHealth` 增加 `profile/profile_verified`；错误码补充 `CDP_PROFILE_MISMATCH`/`CDP_PROFILE_UNVERIFIED`/`BROWSER_ACCOUNT_UNMAPPED` 等。 |
| `storage/repositories/candidates.py` | 新增 `_validate_binding_identity`：跨岗位键拒绝；browser 来源必须使用 `job:geek` 规范键；手工来源忽略页面 meta。 |
| `storage/repositories/pipeline.py` | 新增 `ensure_pending_dry_run`：单事务 `BEGIN IMMEDIATE` 内 get-or-create，读后写原子化。 |
| `server/app.py` | 接入 `BrowserSessionRegistry`；lifespan 关闭全部 session；`/api/runtime-mode` 输出 session 列表；`UNEXPECTED_STATE` 映射为 503。 |
| `server/api.py` | Dry-Run 改调原子 `ensure_pending_dry_run`；扫描响应增加 `browser_account_id`；适配器创建纳入失败捕获（连接失败也写 `FAILED` 状态）。 |
| `server/responses.py` | 扫描响应增加 `browser_account_id`。 |
| `frontend/src/views/ReviewBoardView.vue` | 联系状态单一事实源 `contact_summary`；fast/deep 评定切换改为显式按钮；`detailToken` 详情竞态拦截；已联系/预演按钮禁用与提示。 |
| `frontend/src/types/index.ts` | `BrowserScanResult.browser_account_id`。 |
| `tests/test_browser_runtime.py` | 新增回环拒绝、Profile 未验证/不匹配/匹配、哨兵、断线重连所有权、外部所有权保持等。 |
| `tests/test_browser_cdp_e2e.py` | 新增 A 面板→B Dry-Run 拒绝、面板篡改、控件缺失、外部 Profile 拒绝/同 Profile 接受、真实断线重连所有权、双 HR 真实 Edge 隔离。 |
| `tests/test_api_browser.py` | 新增 8 线程并发 Dry-Run（仅 1 条 PENDING）、DOM 崩溃结构化 503、跨岗位 candidate_key 400。 |
| `tests/test_api_browser_cdp.py` | 新增 HTTP 层双 HR Session 隔离（独立端口/Profile/账号）。 |
| `tests/test_browser_adapter.py` | 新增 DOM 列表/详情/招呼异常结构化 3 项。 |
| `tests/test_contact_summary.py` | 测试助手为浏览器式键补齐一致的 `_meta` 身份字段（配合权威身份校验）。 |
| `tests/frontend_e2e.py` | 扩展至 30 项：数据源、徽章、幂等、fast/deep 联系一致性、按钮禁用、快速切换竞态。 |

---

## 2. 7 项问题的关键修复证据

### P0-1 多 HR 会话隔离
- `BrowserSessionRegistry` 按 `(org_id, actor_user_id)` 惰性创建 runtime；每个 session 独立端口（跳过占用）+ 独立专用 Profile。
- `browser_account_id` 与 `actor_user_id` 分离；Mock 阶段为 `mock-account-<hash>`（模拟映射），也可通过 `bindings` 受控配置；boss_readonly 未映射直接拒绝。
- 证据：`test_browser_sessions`、`test_api_browser_cdp::ApiCdpSessionIsolationTests`（两个 HR 的 `browser_account_id`/`page_url` 不同，registry 显示 2 个独立端口/Profile）、`test_browser_cdp_e2e::test_two_hr_sessions_use_isolated_browsers`（真实 Edge 双实例）。

### P0-2 Dry-Run 身份作用域
- 面板 `data-geek-id` 与请求 identity 不一致 → `IDENTITY_MISMATCH`，不点击；通过目标卡片内 `.open-detail` 重新打开并复核面板身份/姓名；按钮只在核验面板内查找且必须唯一；缺失 → `PAGE_UNCERTAIN`。
- 证据：`test_browser_cdp_e2e::test_greeting_scope_rejects_another_candidates_open_panel`（A 面板→B 拒绝；篡改面板拒绝；移除按钮 → PAGE_UNCERTAIN）。

### P0-3 外部 CDP Profile 核验
- 仅回环地址；接管外部端口时读取真实 `--user-data-dir` 与专用目录比对；不一致拒绝；读不到时接受专用目录哨兵，否则拒绝（除非显式 `BROWSER_ALLOW_UNVERIFIED_CDP=1`）。
- 证据：`test_browser_runtime`（未验证/不匹配/匹配/哨兵）、真实 Edge `test_external_profile_must_match_or_connection_is_refused`。

### P1-1 断线重连所有权
- `was_owned` 在重连时保留；自有进程无法被误判为外部；`close()` 正确终止。
- 证据：单元 mock 2 项 + 真实 Edge `test_owned_process_survives_a_cdp_reconnect_and_is_closed`；外部实例不误杀由既有测试覆盖。

### P1-2 并发 Dry-Run 幂等
- `ensure_pending_dry_run` 在单写事务内 get-or-create；API 仍先做完整 DOM 校验。
- 证据：`test_concurrent_dry_runs_create_exactly_one_pending_row`（8 线程 → 1 条 PENDING，事件 id 全部一致）。

### P1-3 DOM 结构化错误
- 适配器全流程捕获；collector 兜底；API 记录 `FAILED` 并返回 503。
- 证据：`CdpDomErrorTests` 3 项；`test_dom_failure_returns_a_structured_browser_error`（HTTP 503 `UNEXPECTED_STATE` + 状态 FAILED）。

### P1-4 前端联系状态统一与竞态
- 全部徽章/禁用/提示读 `contact_summary`；联系事件表使用 summary.events；`detailToken` 阻断旧详情响应；fast/deep 显式切换。
- 证据：UI E2E 30/30，含“已联系（HR：owner）”在 fast/deep 均一致、Dry-Run 按钮禁用、快速切换最终保持候选人 2。

### P1-5 候选人权威身份
- 解析器强制规范键；仓储拒绝跨岗位键/browser 键冲突；手工键不受页面 meta 影响。
- 证据：`test_candidate_identity` 6 项 + API 跨岗位键 400。

---

## 3. 测试结果

原始日志：`reports/sprint4/sprint4b2a1_tests.log`。

| 测试 | 结果 |
| --- | --- |
| 后端全量 `unittest discover -s tests -t .` | **Ran 656 tests — OK**（0 失败 / 0 error / 0 skip，约 245s） |
| 浏览器会话隔离（单元） | **6 / 6 OK** |
| 候选人身份契约 | **6 / 6 OK** |
| 浏览器运行时/数据源 | **20 / 20 OK** |
| CDP E2E（真实 Edge） | **11 / 11 OK** |
| HTTP→Edge CDP 全链路 + Session 隔离 | **4 / 4 OK** |
| 既有 API 扫描/Dry-Run/并发/DOM 错误 | **11 / 11 OK** |
| 浏览器适配器 DOM 错误 | **13 / 13 OK** |
| 前端 Node | **7 / 7** |
| `vue-tsc` / `vite build` | **0 错误 / 成功** |
| 前端 UI E2E | **30 / 30**（退出码 0） |
| `main.py db-check` | **ok / v10 / issues=[]** |

---

## 4. 遗留问题

1. **真实 BOSS 账号绑定未落库**：Mock 阶段为受控模拟映射；真实绑定需 011+ 迁移（见下）。
2. **扫描状态仍为进程内**：重启/多 worker 不共享；持久化需新迁移。
3. **boss_readonly 仅连接诊断**：真实 DOM 契约未核验前不采集、不发送。
4. **正式发送并发/幂等**：Dry-Run 已原子化；真实发送仍需 SENDING 意图日志 + 部分唯一索引（Schema 变更，待批准）。
5. **CDP 外部 Profile 核验依赖 `--enable-automation`/哨兵**：无证据时默认拒绝；受控环境可用显式豁免，但 `profile_verified=false`。
6. **UI 评估切换改为显式按钮**：已移除下拉选择，避免自动化不稳定并提升可见性。

---

## 5. Batch 2B 前置条件

1. 用户显式授权 `BROWSER_ALLOW_REAL_BOSS=1` + 已人工登录的专用 Edge（不代填、不绕验证码）。
2. 真实 DOM/接口契约核验：推荐页 iframe、卡片/详情选择器、`encrypt_geek_id/securityId` 来源、分页/滚动。
3. 真实招呼按钮/确认弹窗/上限提示复测，保持“页面不确定不记成功”。
4. **011+ 迁移批准**：真实 BOSS 账号绑定表 + 发送意图日志/唯一索引/幂等键；带数据升级与并发测试。
5. 扫描任务持久化（可选）与多 worker 一致性方案。
6. 发送频控、风控熔断与审计留存策略。

---

## 6. 复现命令

```powershell
# 新增/重点
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_sessions tests.test_candidate_identity -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_runtime -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_browser_cdp_e2e -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_api_browser_cdp -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_api_browser -v

# 前端
cd frontend
node --test tests/requestGate.test.ts
npx vue-tsc --noEmit
npx vite build
cd ..
.\.venv\Scripts\python.exe -X utf8 -m tests.frontend_e2e
```
