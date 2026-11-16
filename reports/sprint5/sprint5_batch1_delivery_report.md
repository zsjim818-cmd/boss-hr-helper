# Sprint 5 Batch 1 交付报告（HR 会话管控 + 扫描预检/进度 + 批次运营工作台）

日期：2026-11-16
基线：v11 数据库；零 Schema 变更；未修改 001–011；真实 BOSS 仍硬禁用。

## 一、已交付

### P0 浏览器会话与登录状态
- 新增受当前 `AccessContext` 隔离的会话 API：
  - `GET /api/browser/session`
  - `POST /api/browser/session/connect`
  - `POST /api/browser/session/navigate`
- 状态机：`DISCONNECTED / LOGIN_REQUIRED / CAPTCHA_BLOCKED / READY / REAL_CONTRACT_UNVERIFIED / DISABLED`。
- 返回数据源模式（mock/cdp_mock/boss_readonly）、连接健康、脱敏 profile_id、页面标题/URL、iframe 检测、平台岗位、可见卡片、动作建议、错误码。
- 前端 ScannerView 顶部会话卡：连接/召唤专用浏览器、打开/定位推荐页、重新检测登录、数据源能力横幅、真实契约/PII 未就绪提示；未 READY/未预检时禁用扫描。
- 会话状态按 `(org_id, user_id)` 隔离；`boss_readonly` 始终 `REAL_CONTRACT_UNVERIFIED`，不会访问真实页面。

### P0 页面岗位与筛选预检
- `GET /api/jobs/{job_id}/browser/precheck`：回读页面岗位、平台岗位 ID、筛选摘要、可见卡片、iframe，自动比对系统岗位并返回 `job_mismatch`/warnings。
- 前端三态：未预检 → 已读取 → HR 勾选确认；切换岗位/批次/身份自动重置；岗位不匹配禁止确认和扫描。

### P0 扫描参数、进度、取消与错误恢复
- 前端暴露 pages / max_details / max_duration_seconds，以及“仅扫描入库 / 自动 Fast 初筛”。
- 扫描进度：collector `progress_callback` → `app.state.browser_scan_progress`；GET scan status 返回 `progress`（阶段、页数、列表/去重、导入/复用/失败、详情/部分、预筛跳过、终止原因）。
- 前端进度条/计数、停止按钮（`POST .../browser/cancel`，兼容原 `/scan/cancel`）；已入库候选人保留。
- 统一错误说明：`AUTH_REQUIRED / CAPTCHA_BLOCKED / REAL_CONTRACT_UNVERIFIED / EMPTY_UNVERIFIED / BROWSER_CLOSED / SCAN_ALREADY_RUNNING / CANCELLED / MAX_DURATION / FILTERS_UNCONFIRMED` + 重连/重检/重试按钮。

### P1 批次与候选人运营
- `GET /api/jobs/{job_id}/batches`：按 `started_at` 倒序返回历史批次与锁定 policy_version；Scanner/ReviewBoard 批次下拉，F5 自动恢复最新批次及 scan 状态。
- 候选人列表增加 `batch_id` 过滤、`resume_summary`（姓名/学历/年限/期望薪资/完整度）、`sealed`、`contact_summary`。
- 前端候选人页签：全部 / 未评分 / Fast / Deep / 待核验或失败；表格列扩充；前端分页。
- `GET /api/candidates/{id}/resume`：返回封存/最新快照、完整度、诊断、_meta（已剥离 security_id）；Scanner/ReviewBoard 抽屉展示简历、工作/项目/教育、技能、采集元数据。
- 批量评分逐人结果（成功/跳过/失败原因）弹窗；单人语义/Fast/Deep 在抽屉与列表。
- ReviewBoard：批次/Policy 过滤排名（后端 `batch_id/policy_version` 查询）、`仅待核验` 开关、查看简历抽屉。

### 后端/前端文件
- 新增：`server/browser_session.py`、`tests/test_browser_session.py`
- 修改：`server/api.py`、`server/app.py`、`server/responses.py`、`server/schemas.py`、`browser/collector.py`、`storage/repositories/{batches,candidates,pipeline}.py`
- 前端：`ScannerView.vue`（会话卡/预检/进度/批次/候选人/抽屉/批量结果）、`ReviewBoardView.vue`（批次/待核验/抽屉）、`api/index.ts`、`types/index.ts`
- E2E：`tests/frontend_e2e.py` 扩展到 37 项（会话连接 -> 预检 -> 扫描/进度 -> F5 恢复 -> 简历抽屉 -> 审核批次控件）。

## 二、测试结果

- 后端全量：**706 / 706 OK**（0 失败 / 0 error / 0 skip）
- 前端 UI E2E：**37 / 37 通过，退出码 0**
- 前端 Node：7 / 7；`vue-tsc --noEmit` 0 错误；`vite build` 成功（dist 已清理）
- `main.py db-check`：ok / v11 / pending=[]
- 新增测试：`tests/test_browser_session.py` 5 项（会话生命周期/预检不一致/多 HR 隔离/DISABLED 与 BOSS 门禁/批次历史+简历数据+进度取消）。

## 三、剩余问题

1. 会话状态和扫描进度保存在 `app.state`，服务重启后会话需重新连接；DB 扫描状态仍保留。
2. `boss_readonly` 仍无真实契约与授权，真实采集硬禁用。
3. 暂停/恢复未实现；当前只有取消、上限与幂等重试。
4. 扫描扩展指标（listed/preview/detail）只存在运行时 progress，`browser_scan_runs` 重启后只有基础计数。
5. 平台岗位 ID/筛选摘要仅请求级校验，未落库；刷新后预检需重新读取页面。
6. 真实 PII 加密/保留期限未就绪。
7. 候选人列表为客户端分页（单次最多 200 条）；更大规模需服务端游标/筛选。
8. ReviewBoard 的批次过滤按候选人是否在该批次采集（latest evaluation 仍需属于该 Policy 版本），跨批次重评的历史展示需 012 输入版本。

## 四、012 提案（本批次未执行）

1. `scan_batches` 增加 `platform_job_id`、`filter_summary`、`filters_confirmed_at`、`confirmed_by`（可为空，历史批次兼容）。
2. `browser_scan_runs` 增加扩展计数（listed/deduplicated/preview_skipped/detail_attempted/completed/partial/sealed/review_pending）、`stop_reason`、`run_token`，支持旧回调防覆盖与重启后完整统计。
3. `job_candidate_inputs` 版本化：新表 `job_candidate_input_versions`（resume_id、version、sealed_at、superseded_at），允许补全简历后新建版本而不改不可变审计。
4. 真实 PII：字段级加密/密钥引用、retention policy、到期删除任务、`security_id` 永不落库的数据库约束。
5. 可选：`browser_sessions` 持久化当前页面/岗位/预检状态（仅元数据，不含 Cookie/Token）。
6. 迁移要求：011 后新增 012，禁止改 001–011；提供 v11→v12 带数据升级、回滚、触发器/FK/integrity 验收；仅在临时测试库执行。

## 五、安全与约束

- 未修改 001–011、未新增/应用迁移、未改 `config.json`、未访问真实 BOSS、未发送消息。
- 真实源硬门禁保留；所有自动化仅本地 Mock/CDP Mock。
