# Sprint 4 Batch 2B 交付报告（011 持久化 + boss_readonly 只读适配器）

日期：2026-11-15
范围：新增 011 增量迁移（绑定/扫描状态/任务批次/结构化联系事件）、boss_readonly 真实页面只读适配器与本地夹具 E2E、人工真机联调工具。
约束：001–010 未修改；根 `config.json` 未改；未连接真实 BOSS（自动化测试）；未发送任何真实招呼语；未执行 `git clean`/`reset --hard`。

---

## 1. 011 Schema 设计说明（实施）

| 对象 | 设计 |
| --- | --- |
| `browser_account_bindings` | PK `(org_id,user_id)`；`browser_account_id`、`cdp_port`、`profile_id`、`status`；唯一账号/端口；**不含任何 Cookie/Token 列**。 |
| `browser_scan_runs` | PK `(org_id,job_id,batch_id,actor_user_id)`；状态 `RUNNING/COMPLETED/COMPLETED_WITH_ERRORS/FAILED`；采集/导入/复用/失败计数、`issues_json`、`error_code/message`；外键指向批次与成员。 |
| `pipeline_tasks.batch_id` | 可空列（历史任务 NULL）；索引 `(org,job,batch_id)`；新增不可变触发器；幂等键按 `(candidate,mode,batch)` 校验。 |
| `pipeline_contact_events` | 重建并新增 `job_candidate_id`、`mode(DRY_RUN/REAL_SEND/LEGACY)`、`browser_account_id`；历史行逐列回填（`DRY_RUN:` 前缀 → `DRY_RUN`，其余 `LEGACY`）。部分唯一索引 `ux_contact_real_send_once(org,job,job_candidate_id,actor_id) WHERE mode='REAL_SEND'`。 |
| 触发器 | 保留 010 的评估/审核/联系不可变保护；新增“联系人与评估归属一致”触发器、任务 batch 不可变触发器。 |

迁移方式：增量 `011_browser_persistence.sql`，沿用 010 的“暂存→重建→回填→重建索引/触发器”范式；v10 历史数据 100% 保留。

**升级记录**：开发主库已从 v10 升级到 v11；升级前备份 `D:\Boss-project\recruitment.sqlite3.bak_sprint4_011`（802,816 bytes）；`db-check` = ok / v11 / issues=[] / pending=[]。

---

## 2. 实际修改文件

### 2.1 新增

| 文件 | 说明 |
| --- | --- |
| `storage/database/migrations/011_browser_persistence.sql` | 绑定、扫描运行、任务批次列、结构化联系事件与唯一索引。 |
| `storage/repositories/browser.py` | 绑定读取/upsert；扫描运行 start/finish/get。 |
| `browser/boss_readonly_adapter.py` | `BossReadonlyCdpAdapter`：iframe 定位、登录/风控预检、只读列表/详情、限定面板 Dry-Run。 |
| `browser/boss_fixture.py` | 本地 BOSS 结构夹具（iframe、登录、验证码/滑块、详情切换、残缺/失败简历、按钮上限）。 |
| `scripts/launch_boss_edge.ps1` | 专用 Profile 启动 Edge（含 `--enable-automation`）与健康检查；拒绝日常 Profile。 |
| `scripts/bind_browser_account.py` | 持久化 HR→BOSS 账号/端口/profile_id 绑定（不存凭据）。 |
| `reports/sprint4/boss_readonly_manual_guide.md` | 人工真机只读联调指南。 |
| `tests/test_migration_011.py` | v10→v11 历史保留、触发器、唯一互斥、回滚。 |
| `tests/test_browser_bindings.py` | 绑定持久化与重启复用、多 HR 独立、无凭据列。 |
| `tests/test_boss_readonly_adapter.py` | 真实 Edge + 本地夹具 6 项 E2E。 |
| `reports/sprint4/sprint4b2b_delivery_report.md` / `sprint4b2b_tests.log` | 交付与日志。 |

### 2.2 修改

| 文件 | 说明 |
| --- | --- |
| `server/api.py` | 扫描状态持久化（start/finish/get）；Dry-Run 写 `mode=DRY_RUN` 与 `browser_account_id`；评分任务携带 `batch_id`。 |
| `server/app.py` | 注册表接入持久化 store；`boss_readonly` 授权时构造真实只读适配器；诊断不变。 |
| `storage/repositories/pipeline.py` | 任务 `batch_id` 创建/幂等校验；联系事件结构字段；Dry-Run 查询兼容 `mode` 与历史前缀。 |
| `storage/repositories/__init__.py` | 组合 `BrowserRepository`。 |
| `browser/sessions.py` | 绑定持久化加载/保存、`profile_id` 脱敏、禁用状态拒绝。 |
| `browser/contracts.py` | 新增 `AUTH_REQUIRED`/`CAPTCHA_BLOCKED`/账号绑定错误码。 |
| `browser/runtime.py` | （2A.2 基础上）保持动态 Profile 核验。 |
| `frontend/src/api/index.ts`、`views/ScannerView.vue` | 评分按批次传 `batch_id` 与批次级幂等键。 |
| `tests/test_migration_010.py` | 固定 v10 迁移集，种子改用 run+save 兼容 v10 表。 |
| 其它测试 | 适配 v11 持久化扫描状态与结构化事件。 |

---

## 3. boss_readonly 适配器行为

- 仅当 `BROWSER_SOURCE=boss_readonly` 且 `BROWSER_ALLOW_REAL_BOSS=1` 时启用；未授权时源被降级为 `disabled`。
- 连接：复用该 HR 的专用 CDP Session；打开推荐页；定位 `iframe#recommendFrame`。
- 预检：页面或 iframe 出现登录/扫码/验证码/滑块标记 → `AUTH_REQUIRED` / `CAPTCHA_BLOCKED`，不自动处理。
- 列表：`.job-card-box[data-geek-id]`，缺少稳定身份 → `IDENTITY_MISMATCH`；受控延时 `delay_ms`。
- 详情：卡片内打开详情，核验 `#detail-panel[data-geek-id]` 与姓名；解析 JSON 为严格 `ResumeData`（完整/残缺/失败三态由现有解析器产出）。
- Dry-Run：仅当面板身份与目标一致时，在面板内定位唯一 `.btn-greet`；禁用/达到上限/全局点击标记 → `PAGE_UNCERTAIN`；返回 `DRY_RUN_READY`，**从不点击**；`dry_run=False` 恒为 `UNSUPPORTED`。

本地夹具 E2E（真实 Edge、`file://`，不访问外网）覆盖：iframe 列表、登录、验证码/滑块、详情面板切换、残缺/失败简历、按钮上限、A 面板→B 请求拒绝。

---

## 4. 测试结果

> 待最终全量回归完成后填入。

| 测试 | 结果 |
| --- | --- |
| 后端全量 `unittest discover -s tests -t .` | **Ran 679 tests — OK**（0 失败 / 0 error / 0 skip，约 265s） |
| 010/011 迁移验收（含回滚） | **4 / 4 OK** |
| HR 浏览器账号绑定持久化 | **2 / 2 OK** |
| boss_readonly 本地夹具 E2E（真实 Edge） | **6 / 6 OK** |
| HTTP API（扫描持久化/批次评分/Dry-Run/runtime-mode） | **20 / 20 OK** |
| 浏览器运行时 + Session + CDP E2E | **41 / 41 OK** |
| 前端 Node / `vue-tsc` / `vite build` | **7 / 7、0 错误、成功** |
| 前端 UI E2E | **30 / 30（退出码 0）** |
| `main.py db-check` | **ok / v11 / issues=[] / pending=[]** |

011 验收证据：v10 历史 evaluations/reviews/tasks 逐列一致；联系事件回填 `job_candidate_id` 与 `mode`；绑定/扫描表可写；`REAL_SEND` 同 HR 重复被唯一索引拒绝、不同 HR 允许；任务 `batch_id` 不可变；联系与评估归属触发器生效；失败 012 回滚后仍为 v10 且无半成品表。

---

## 5. 遗留问题 / 2B 收尾说明

1. 真实 BOSS DOM 选择器仍需真机核对（夹具按审计结构实现；真实页面变化时更新顶部选择器常量）。
2. 真实发送仍物理禁用；`REAL_SEND` 唯一索引已就绪，等待未来发送实现写入 `mode='REAL_SEND'`。
3. `boss_readonly` 的账号必须先在 `browser_account_bindings` 绑定；未绑定时拒绝（不假设 user_id 即账号）。
4. 扫描状态已持久化；多 worker 共享同一 SQLite 数据库即可（注意并发写锁）。
5. 评分任务 `batch_id` 为空的历史任务保持兼容；同幂等键跨批次会返回 `PipelineConflict`，前端已按批次生成键。

---

## 6. 人工真机只读联调步骤（摘要）

见 `reports/sprint4/boss_readonly_manual_guide.md`：
1. `scripts/bind_browser_account.py` 绑定当前 HR 的 BOSS 账号/端口/profile_id；
2. `scripts/launch_boss_edge.ps1 -Port 9222 -ProfileDir <专用目录>` 启动 Edge（`-Check` 健康检查）；
3. 人工扫码登录 BOSS 招聘端并进入推荐页；
4. 服务设置 `APP_RUN_MODE=live`、`BROWSER_SOURCE=boss_readonly`、`BROWSER_ALLOW_REAL_BOSS=1`、`BROWSER_CDP_AUTOLAUNCH=0`；
5. 前端建批次 → 只读扫描 → live BGE+Qwen 评分 → HR 审核 → Dry-Run 重定位校验（不发送）。
