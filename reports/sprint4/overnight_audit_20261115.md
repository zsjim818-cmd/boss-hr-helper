# 夜间源码审计与修复对照（2026-11-15，UTC+8）

基线：Git HEAD `2b1a373`（工作树含本次未提交修改）；后端基线 681 项通过、UI E2E 30/30。
本次结果：后端 **700/700 PASS**，UI E2E **31/31 PASS**，前端 Node 7/7，`vue-tsc`/`vite build` 通过；真实 BOSS 仍 BLOCKED。
证据等级：**【已证实】**=本地代码/测试可复现；**【推断】**=源码一致但缺少真机；**【未知】**=需真实 BOSS 授权采样。

## 1. 初始事实核查（不以提示词假设为准）

| 疑点 | 结论 | 关键位置 |
| --- | --- | --- |
| 工厂与路由重复 `connect()` | **已证实**：`_browser_factory` 连接后又由 `browser_scan` 再连；Mock/cdp 会重复初始化 | `server/app.py::_browser_factory`、`server/api.py::browser_scan` |
| 已打开标签被 `goto()` 重置 | **已证实**：`_open_page_impl` 命中页面仍 `goto`，会清掉人工筛选 | `browser/runtime.py::_open_page_impl` |
| `boss_readonly` 声明与门禁不一致 | **已证实**：`BROWSER_ALLOW_REAL_BOSS=1` 时可走真实适配器 | `server/app.py`、`browser/source.py` |
| 真实适配器选择器为本地假设 | **已证实**：`iframe#recommendFrame`、`.job-card-box` 等来自本地夹具 | `browser/boss_readonly_adapter.py`、`browser/boss_fixture.py` |
| `encrypt_job_id` 被内部 `job_id` 冒充 | **已证实**：解析器 `encrypt_job_id = detail.job_id or job_id`；仓储还要求等于内部 ID | `browser/identity.py`、`storage/repositories/candidates.py` |
| Collector 逐人开详情 | **已证实**：没有列表预筛阶段 | `browser/collector.py` |
| 扫描后无法顺滑评分 | **已证实**（上一轮已部分修复）：评分按钮曾依赖手动导入变量；本轮继续修复批次 Policy 展示/确认流程 | `frontend/src/views/ScannerView.vue` |
| sealed 简历补全不更新 | **已证实**：`job_candidate_inputs` 不可变；`_seal` 永远复用旧行 | `storage/repositories/candidates.py`、迁移 002/005 |
| 扫描 upsert 覆盖运行态 | **已证实**：`start_browser_scan` 直接 UPSERT RUNNING | `storage/repositories/browser.py` |
| PII 存储 | **部分证实**：原始/归一化简历明文入库；无到期清理；真实采集未开放 | `storage/repositories/candidates.py`、迁移 005 |

## 2. 本次修复（P0/P1 摘要）

- **连接责任**：工厂只构造适配器；HTTP 路由仅 `connect()` 一次；重复 `connect()` 不导航、不清除人工筛选。`browser/runtime.py::_open_page_impl(navigate=False)`。
- **真实源硬门禁**：`boss_readonly` 即使 `BROWSER_ALLOW_REAL_BOSS=1` 也返回 `REAL_CONTRACT_UNVERIFIED`，不访问真实站点、不写候选人；诊断仍可看配置。
- **空列表语义**：读取 0 张卡不再算成功，扫描状态记 `FAILED/EMPTY_UNVERIFIED`。
- **筛选确认**：扫描请求新增 `filters_confirmed/platform_job_id/filter_summary/max_details`；未确认返回 409 `FILTERS_UNCONFIRMED`；前端扫描前必须勾选人工确认。
- **ID 分层**：`CandidateCard/CandidateDetail.platform_job_id` 与内部 `job_id` 分离；解析器 `encrypt_job_id` 只接受平台值（未知为空），不再塞内部 ID；仓储不再要求两者相等。
- **列表预筛**：新增 `browser/preview.py`，仅当列表明确学历/年限不符时 `SKIP_CONFIRMED`，缺失一律 `FETCH_DETAIL`；Collector 增加 listed/deduplicated/preview_skipped/detail_* /sealed/review_pending/stop_reason 指标。
- **被动响应解析**：新增 `browser/response_parser.py`（主机/路径/状态/JSON shape 检查、401/403 分类、列表/详情解析），仅用独立合成 JSON；真实响应接线未启用。
- **扫描并发**：同一 `(org,job,batch,actor)` 运行中再次扫描返回 409 `SCAN_ALREADY_RUNNING`；超过 60 分钟视为陈旧可重试。
- **取消与上限**：新增 `POST .../browser/scan/cancel`；Collector 在页/候选人检查点停止；`max_details` 上限；`stop_reason` 可查询；状态返回 `CANCELLED`。
- **结果复用**：`reusable_result` 加入 sealed `resume_id` 与 pipeline 版本过滤，避免新简历沿用旧分数。
- **隐私**：解析器与 `prepare_resume` 丢弃 `_meta.security_id`；运行诊断标注 `real_pii_storage_ready=false`。

## 3. 明确未完成 / BLOCKED

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 真实 BOSS 列表/详情/招呼 | **BLOCKED** | 无授权账号、无真机契约；代码硬门禁 |
| 平台岗位 ID/筛选指纹批次持久化 | **BLOCKED** | 011 无对应列；需 012 加性迁移，未擅自应用 |
| 已密封简历的新输入版本（补全后重算） | **BLOCKED** | `job_candidate_inputs` 不可变；需 012 输入版本表；当前显式报 `INPUT_VERSIONING_REQUIRES_012` |
| 真正中途暂停/恢复 | **PARTIAL** | 已实现取消/上限/幂等重试；暂停恢复需持久运行游标（012） |
| 被动响应监听器接线到真实适配器 | **PARTIAL** | 解析/分类/合成测试已完成；真实监听需契约与授权 |
| 真实 PII 加密/期限清理 | **BLOCKED** | 需 012 + 密钥管理；当前真实采集关闭，Mock 仅合成数据 |
| 批次平台条件持久化 | **BLOCKED** | 同上 012；当前仅请求级校验 |

## 4. 已知风险

- 真实 BOSS DOM/API 字段、iframe、分页、登录/风控均未验证；本地夹具不能证明兼容。
- 平台岗位 ID 目前只在请求/DTO 层校验，未落库；批次与平台职位的强绑定需 012。
- 已审核候选人在简历变化后的重评需要输入版本迁移。
- `browser_scan_runs` 仅持久化基础计数，扩展指标（listed/preview/detail）当前只在即时响应返回。
