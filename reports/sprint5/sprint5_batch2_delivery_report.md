# Sprint 5 Batch 2 交付报告（012 持久化 + PARTIAL 重采版本化 + 服务端分页）

日期：2026-11-16
基线：v11 → v12（新增 012；001–011 未修改）
真实 BOSS 采集：继续硬禁用（无授权账号/真机契约）
真实消息发送：物理禁用（仅有 Dry-Run）

## 一、数据库 012 增量迁移

文件：`storage/database/migrations/012_workbench_persistence.sql`

### 1. 批次预检确认持久化（scan_batches）
新增可空字段：
- `platform_job_id`
- `filter_summary_json`（JSON object/array，默认 `[]`）
- `filters_confirmed_at`
- `confirmed_by`

约束/触发器：
- 确认时间与确认人必须同时存在或同时为空；
- 确认人必须是当前组织的 ACTIVE 成员；
- 页面岗位/筛选摘要/确认状态更新受专门触发器校验。

效果：HR 勾选确认后写入批次；F5/服务重启直接读取批次确认，不再要求重复预检。页面岗位变化（precheck `job_mismatch` 或确认时页面岗位不一致）仍会拒绝确认并要求重新核对。

### 2. 扫描运行扩展指标（browser_scan_runs）
新增：
`pages_read`、`cards_seen`、`cards_deduped`、`details_opened`、`details_completed`、`details_partial`、`prescreen_skipped`、`stop_reason`、`run_token`。

- 扩展计数全部非负约束；
- `run_token` 每次启动生成新值，唯一索引 `ux_browser_scan_runs_run_token`；
- 进度/完成写库均带 `run_token` 条件，旧异步回调不能覆盖新扫描状态；
- 服务重启后 status 接口从 DB 还原完整阶段统计。

### 3. 候选人输入快照多版本（job_candidate_input_versions）
新增 append-only 版本表：
- 主键 `(org_id, job_id, job_candidate_id, input_version)`；
- 每条记录绑定 `resume_id`、Policy 版本、封存人与时间；
- `is_current` 标记当前输入，部分唯一索引保证每候选人仅一个当前版本；
- `superseded_at/by/reason` 记录版本演进；
- v11 既有 `job_candidate_inputs` 在迁移时自动回填为 version 1/current。

不可变审计：
- 版本只允许 `current -> superseded` 的受控 UPDATE，其余字段不可改写；
- 禁止 DELETE；
- 版本号必须连续；
- 新版本必须对应 COMPLETE/可评分且已采集到该候选人/岗位的 Resume 快照。

读侧新增视图 `job_candidate_inputs_current`：优先当前版本，旧库无版本表时回退 legacy 表。评分、语义 embedding、旧评估任务查询均切换到该视图，因此 PARTIAL 补齐后新版本可重新评分，历史评估仍指向旧 `resume_id`。

### 4. 敏感字段数据库级防线
- `resume_snapshots` INSERT 触发器递归检查 `raw_resume_json` / `normalized_resume_json`：
  - 拒绝非空的 `cookie`、`authorization`、`access_token`、`refresh_token`、`session_token`、`security_id` 等键；
  - 空的 `security_id: null` 允许（Pydantic 默认字段），非空 session id 一律拒绝。
- `browser_account_bindings.profile_id` 禁止包含 cookie/authorization/token 片段。
- 应用层 `prepare_resume` 继续剥离临时 `security_id`；API 简历响应再次剥离。

### 5. v11→v12 升级测试
新增 `tests/test_migration_012.py`：
- 带历史数据 v11（Policy/批次/候选人/封存输入/扫描运行）升级到 v12；
- 验证 legacy 输入回填为 v1/current，legacy 表与评估不变；
- 验证旧扫描运行计数保留、新计数/run_token 默认安全；
- 验证版本链、唯一 current、不可变 UPDATE/DELETE；
- 验证 cookie/非空 security_id 落盘被拒绝；
- 验证 `PRAGMA foreign_key_check` / `integrity_check`；
- 验证 013 失败时整个 012 回滚，恢复后可正常升级；
- 验证全新 v12 构建。

## 二、服务端分页与重采闭环

### 1. 候选人列表服务端分页
`GET /api/jobs/{job_id}/candidates` 新增下推参数：
- `batch_id`
- `tab`：`all | unscored | fast | deep | needs_review_or_failed`（兼容 `review`）
- `keyword`：candidate_key、简历姓名、学历
- `total + limit + offset`

实现：SQL CTE 计算每候选人最新评估（Deep 优先），过滤、计数、分页全部在 SQLite 内完成；仅对当前页做简历摘要/联系状态 enrich。

响应新增 `tab/keyword/limit/offset` 元信息；非法 tab 返回 400。

前端 ScannerView：
- 页签与关键字直接请求后端；
- 分页器联动 `limit/offset`，支持 10/20/50 页大小；
- 切换页签/页大小自动回到第 1 页；
- 表格不再做客户端切片。

### 2. 单人重新提取详情
新增：`POST /api/jobs/{job_id}/candidates/{job_candidate_id}/re-extract`

流程：
1. 从 `candidate_key` 解析稳定平台身份；
2. 通过当前受控 adapter 重新打开详情并做 identity 校验；
3. 保存新 Resume 快照与 acquisition；
4. COMPLETE 时追加新的 Sealed 输入版本；PARTIAL 只留诊断快照，不解锁评分；
5. 返回 `input_version / sealed / completeness / message`。

评分侧：
- `get_sealed_input` 读取当前版本；
- 评分幂等键自动带 `input version` 后缀，新版本不会被旧任务复用；
- 历史评估与旧 Resume 快照保持不可变。

前端简历抽屉新增“重新提取详情并更新快照”，成功后刷新列表/抽屉，可继续 Fast/Deep。

## 三、前端与会话/预检联动

- 预检确认勾选后立即 POST 持久化，并显示“确认已持久化（F5/重启后保留）”；
- 批次切换/F5 自动读取 `filters_confirmed_at` 恢复确认态；
- 预检仍会比对页面岗位；不一致时禁止确认与扫描；
- 扫描进度展示 pages/cards/dedup/details/prescreen/stop_reason；服务重启后由 DB 指标回填；
- Scanner 批次候选人页签、关键字、服务端分页与总数展示；
- 原有 ReviewBoard 批次/Policy 过滤、待核验过滤、简历抽屉保持不变。

## 四、测试与验收

- 后端全量：**712 / 712 OK**（0 失败 / 0 error / 0 skip）
- 前端 Playwright UI E2E：**42 / 42 通过，退出码 0**
  - 新增：预检确认持久化 → F5 保留
  - 新增：服务端分页显示 total=11、翻页到第 2 页
  - 新增：单人重新提取详情（封存快照保持不变）
- 前端 Node：7 / 7
- `vue-tsc --noEmit`：0 错误
- `vite build`：成功（dist 已清理）
- 主库 db-check：ok / **schema_version=12** / pending=[] / FK=[] / integrity=ok

新增/更新测试文件：
- 新增 `tests/test_migration_012.py`（2 项）
- 新增 `tests/test_workbench_persistence.py`（4 项：确认持久化、扫描指标/run_token、分页与页签、PARTIAL→COMPLETE 重采评分）
- 更新 `tests/test_migration_011.py`（v10→v12）
- 更新 `tests/test_capture_workflow.py`（版本 2 追加而非覆盖）
- 更新 `tests/test_browser_bindings.py`（schema 12）
- 更新 `tests/frontend_e2e.py`（42 项）

### 主库升级
- 备份：`data/recruitment.sqlite3.bak_sprint5_012`
- 已执行 `python main.py db-migrate`，应用 `012_workbench_persistence.sql`
- 升级后 `db-check`：ok / v12 / pending=[] / FK=[]。

## 五、安全与约束

- 001–011 未修改；
- 未访问真实 BOSS 页面，未登录真实账号；
- 未发送真实消息；
- `boss_readonly` 仍返回 `REAL_CONTRACT_UNVERIFIED` 并拒绝导航/扫描/重采；
- Cookie/Token/非空 security_id 在数据库边界被拒绝；
- 候选人输入历史不可改写，评分历史保持审计。

## 六、剩余风险

1. 真实 BOSS DOM/API 契约仍未核验；`boss_readonly` 仍 BLOCKED。
2. 真实 PII 字段级加密、保留期限与删除任务尚未实现。
3. 扫描运行中进程崩溃后，DB 会保留最后进度与 `RUNNING` 状态；下一次启动扫描会覆盖为新 token（前端有 SCAN_ALREADY_RUNNING 恢复提示）。
4. 输入版本循环回退到历史相同 resume 内容时，受 `UNIQUE(...,resume_id)` 限制；当前 UI 的重采只追加不同内容，未提供“回滚到历史版本”操作。
5. 服务端分页为 limit/offset；超大数据量可后续升级为游标分页。
6. 前端 E2E 的 PARTIAL→COMPLETE 主路径由后端测试覆盖；UI E2E 验证了重采按钮与封存快照不变路径（CDP Mock 详情内容固定）。
