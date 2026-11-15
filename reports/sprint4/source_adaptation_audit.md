# Sprint 4 Batch 1 — 参考源码审计与适配策略

日期：2026-11-15
范围：`browser/` 抽象层与 Mock 招聘端。**本批次不连接真实 BOSS 页面、不发送任何真实招呼语。**

## 1. 参考仓库核实（已找到真实 clone）

| 项 | 值 |
| --- | --- |
| 路径 | `D:\Boss-project\HR\boss-hr-agent-toolkit` |
| 来源 | `https://github.com/1xiaoyueryuer/boss-hr-agent-toolkit.git`（git remote origin） |
| 分支 | `stable/v1.2` |
| HEAD | `22626327006c667dfcdd6d7707298068721cff02`（2026-10-24，fix: generate complete and readable screening reports） |
| 版本 | `pyproject.toml` version `1.2.2`；`git describe` = `v1.2.2-1-g2262632` |
| 许可证 | **MIT License**（Copyright (c) 2026 yuyu） |
| 运行时 | Windows + Python 3.10+ + Microsoft Edge；唯一依赖 `patchright==1.59.1`（仅作 CDP 客户端，不下载 Chromium） |
| 入口 | `python -m boss_hr`（`boss_hr/cli.py`），命令：start / fetch / score / report / confirm / greet / status / doctor |

工作区同级另有 `boss-helper`、`boss-hr-desktop`、`boss-zhipin-job-automation`、`Bosszhipin-job-bot`、`dom-test` 等目录；本次以 `boss-hr-agent-toolkit` 为准（用户确认其为官方 clone），其余未作为契约依据。

## 2. 参考实现的关键事实（逐项，含文件路径）

### 2.1 浏览器启动与 CDP 连接
- `boss_hr/adapters/browser_environment.py`：`ensure_browser_ready(auto_launch=True, login_wait_seconds=20)` 统一供 start/fetch/greet 使用。
- 流程：查找 Edge → 检查 `127.0.0.1:9222` → 未监听时用**专用 profile** 启动 Edge → 等待端口 → 连接 CDP → 检查 BOSS 登录态 → 检查招聘者页面。
- 显式拒绝：使用用户日常 Edge profile、删除专用 profile、自动填写账号/密码/验证码、硬编码用户路径、启动多个 9222 实例。
- `shared/cdp_preflight.py` + `boss_hr/adapters/browser_preflight.py` 提供端口/登录态探测；`CDP_HOST/CDP_PORT` 常量。

### 2.2 推荐列表定位
- `boss-recommend-downloader/scripts/recommend_list.py`：`page.goto('https://www.zhipin.com/web/chat/recommend', wait_until='networkidle', timeout=60000)`；
- 推荐页位于 **iframe**：最多等待 15 秒轮询 `page.query_selector('iframe')`，`iframe.content_frame()` 后操作；滚动使用 `human_scroll(page, iframe_box, min_delta=1200, max_delta=1800)`；
- 列表数据落盘 `recommend_geek_ids.json`，条目含 `encryptGeekId`、`geekCard.encryptJobId`、`securityId` 等。

### 2.3 候选人详情展开与提取
- `boss-recommend-downloader/scripts/recommend_download.py`：在页面上下文（`page.evaluate`）请求 `/wapi/zpjob/view/geek/info?encryptGeekId=…&encryptJobId=…`，读取 `zp.geekDetailInfo`：
  - `geekBaseInfo`、`anonymousGeekExpect|geekExpect`、`geekWorkExpList`、`geekProjExpList`、`geekEduExpList`、`geekCertificationList`；
- 与我们 `schemas/candidate.py` 的字段一一对应（expectation / work_experience / project_experience / education / certifications），并生成 `candidate_key = f"{encrypt_job_id}:{encrypt_geek_id}"`。
- 其它失败：缺少 securityId、限流（`mark_limit_hit`）、单条失败（`mark_failed`，保留原因）。

### 2.4 候选人稳定标识
- 以 **`encrypt_geek_id` 为唯一键**；姓名仅作 fallback（`auto_greet.py` 注释与 `_build_geek_id_index`）。
- 打招呼前用 `encrypt_geek_id` 在实时 DOM 中定位 `li`；若卡片上的 ID 与目标不一致 → `not_found`（reason `geekId mismatch`），**不做猜测**。
- 位置回退：`state/geek_positions.json` 保存 `doc_x/doc_y`，在卡片刷新/滚动后重新定位。

### 2.5 招呼语发送定位与结果确认
- `boss-hr-greet/scripts/auto_greet.py`：
  - 目标卡片内 `locator("button.btn-greet:has-text('打招呼')")` 精确点击；
  - 点击后等待约 2 秒，按钮文本应变为 `"继续沟通"` 才视为成功；
  - 扫描 iframe 内是否出现 `"今日沟通次数已达上限"` 弹窗 → 点击 `"知道了"`；
  - 写 `runs/<run_id>/process/greet_log.json` + `run_log.txt`；
  - 汇总状态：`complete` / `partial_success`（greeted≥1 且 not_found≥1）/ `no_candidates`；dry-run 有 `dry_run_complete` / `dry_run_review`。
- `boss_hr/application/greet_service.py`：dry_run 参数、`confirmed` 校验（未确认则 `AWAITING_CONFIRMATION`）、`not_found` 统计、`partial_success_warnings`。
- 错误码（`boss_hr/contracts/errors.py`）：`MISSING_ENCRYPT_JOB_ID`、`JOB_MISMATCH`、`RUN_NOT_FOUND`、`AWAITING_CONFIRMATION`、`EDGE_NOT_FOUND`、`CDP_NOT_RUNNING`、`CDP_CONNECT_FAILED`、`BOSS_LOGIN_REQUIRED`、`BOSS_PAGE_REQUIRED` 等；结果统一 `CommandResult{ok,status,data,error{code,recoverable,next_action,remediation}}`。

### 2.6 页面变化后的失败恢复
- 端口/登录态预检与自动恢复（`browser_environment`），失败给 `remediation` 指令；
- DOM 位置变化 → `geek_positions.json` 回退 + 全量扫描（`not_found_after_full_scan`）；
- 单条失败不影响其它候选人，最终以 `partial_success` 明示；
- 登录验证/人机校验：文档要求人工完成，不实现绕过（README 明确"登录由你完成"）。

## 3. 与本项目契约的对齐结论

1. `schemas/candidate.py` 的 `_meta`（`candidate_key` / `encrypt_job_id` / `encrypt_geek_id` / `dom_data_lid` / `dom_card_index`）与参考仓库产出的主线数据完全兼容；本项目 `CandidateMeta` 已要求 `encrypt_geek_id` 非空。
2. `schemas/card.py` 的 `_dom_anchor`（`dom_card_index`、`dom_data_lid`）对应卡片定位线索，可承载位置回退。
3. 参考实现使用 iframe + 页面内接口（`/wapi/...`）取详情；真实适配阶段需在**已登录、用户授权**的 Edge 会话中实现，且必须验证接口返回与 Schema；本批次只用 Mock 页面复刻同样的数据契约。
4. 参考实现的招呼发送具有"点击后文本变化校验 + 上限弹窗检测 + 不猜测成功"的保守语义，完全符合本项目"页面状态不确定时不假定发送成功"的要求。

## 4. 本批次适配策略（避免未核实假设）

- 不复制参考仓库的私有实现；只复用**已核实的行为语义**与错误分类。
- `browser/` 只提供接口与 Mock 实现：`BrowserAdapter`（连接/健康/列表/详情/发送预演）、`MockBossAdapter`（内存站点）、`CandidateListCollector`、`CandidateDetailParser`、`CandidateIdentityResolver`、`GreetingExecutor`。
- Mock 页面（Playwright + 本机 Edge `channel="msedge"`）复刻：分页/滚动、已沟通/未沟通、详情弹窗、加载失败/超时、卡片刷新与消失、招呼确认与 dry-run。
- **未核实项**（真实对接前必须验证，不在本批实现）：真实 BOSS DOM 选择器、真实 `/wapi` 接口字段稳定性、真实风控/滑块行为、发送成功判定的真实文案（参考仓库基于 v1.2 时期页面，可能已变化）。
- 真实对接条件见交付报告 §"Sprint 4 Batch 2 前置条件"。
