# 夜间实现摘要（2026-11-15，UTC+8）

## 修改文件

**浏览器/采集层**
- `browser/contracts.py`：新增平台岗位字段、采集指标、结构化错误码（EMPTY_UNVERIFIED/CANCELLED/REAL_CONTRACT_UNVERIFIED 等）。
- `browser/runtime.py`：`_open_page_impl(navigate=False)`；重复连接复用标签不导航。
- `browser/mock_adapter.py`、`browser/mock_data.py`、`browser/mock_page.py`：列表卡片提供平台岗位/学历/年限；详情携带 platform_job_id；DOM 属性同步。
- `browser/boss_readonly_adapter.py`：复用标签不刷新；平台字段读取；错误分类保持。
- `browser/boss_fixture.py`：夹具补充平台岗位/学历/年限。
- `browser/identity.py`：`encrypt_job_id` 仅来自平台值；丢弃 `security_id`。
- `browser/preview.py`（新增）：保守列表预筛 + 从 Policy 提取明确学历/年限条件。
- `browser/response_parser.py`（新增）：被动响应分类与合成 JSON 列表/详情解析。
- `browser/collector.py`：列表预筛、平台岗位校验、max_details、取消检查、指标与 stop_reason。

**服务端/仓储**
- `server/app.py`：工厂不再重复 connect；boss_readonly 硬门禁 `REAL_CONTRACT_UNVERIFIED`；ScanAlreadyRunning 409；取消集合；PII/契约未验证诊断。
- `server/api.py`：扫描确认字段/限制/取消端点；空列表 `EMPTY_UNVERIFIED`；取消 `CANCELLED`；greeting 显式 connect；预览接入。
- `server/schemas.py`：扫描确认/平台岗位/摘要/max_details。
- `server/responses.py`：扫描指标字段。
- `browser/source.py`：`real_contract_verified=false`、`real_pii_storage_ready=false`。
- `storage/repositories/browser.py`：运行中扫描 409、陈旧阈值、取消状态支持。
- `storage/repositories/candidates.py`：平台 ID 不比较内部 ID；`security_id` 入库剥离；`reseal_candidate_input` 明确 012 BLOCKED。
- `storage/repositories/pipeline.py` + `engine/policy_scoring_pipeline.py`：结果复用加入 sealed resume/pipeline 版本过滤。

**前端**
- `ScannerView.vue`：批次锁定 Policy 展示、扫描前人工确认/平台岗位/摘要、指标/终止原因、数据源标签、候选人选中/自动选中、单人与批量评分。
- `ReviewBoardView.vue`：NEEDS_RELOCATE 人工重新定位提醒。
- `api/index.ts`、`types/index.ts`：新扫描字段/指标。

**测试**
- 新增 `tests/test_capture_workflow.py`（预筛、指标、空列表、误开真实源、单次 connect、并发扫描、取消、身份/平台 ID）。
- 新增 `tests/test_response_parser.py`（合成 JSON 列表/详情、同名不同 ID、空详情、认证/契约错误）。
- 扩展 `tests/test_boss_readonly_adapter.py`（重复连接不重置页面）。
- 扩展 `tests/frontend_e2e.py`（扫描前确认、CDP_MOCK 标签）。

## 测试结果

- 后端全量：**700 / 700 OK**（0 失败 / 0 error / 0 skip）
- 前端 UI E2E：**31 / 31，退出码 0**
- 前端 Node：7 / 7；`vue-tsc` 0 错误；`vite build` 成功
- `db-check`：ok / v11 / pending=[]
- 新增专项：`test_capture_workflow.py` 11 项、`test_response_parser.py` 7 项；`test_boss_readonly_adapter` 增加重复连接不刷新
- 日志：`reports/sprint4/test_run_20261115.log`

## 迁移

- **未新增/应用 012**。平台岗位/筛选摘要持久化、输入版本化、PII 加密与期限均需 012 加性迁移，当前标 BLOCKED。
- 001–011 与现有数据库未修改。

## 本地 Mock 使用流程

1. 启动后端（确定性 + cdp_mock）与前端：
```powershell
$env:APP_RUN_MODE="deterministic"; $env:APP_ALLOW_TRUSTED_HEADERS="1"
$env:BROWSER_SOURCE="cdp_mock"; $env:BROWSER_CDP_PORT="9335"; $env:BROWSER_HEADLESS="1"
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
cd frontend; npm run dev -- --port 5173
```
2. 前端：岗位策略发布 → 批次导入页创建批次 → 勾选人工确认 → Mock 扫描 → 表格选中/批量 Fast/Deep → 审核看板批准 → Dry-Run（不发送）。
3. 自动验证：`tests/frontend_e2e.py` 覆盖同一链路（30 项）。

## 真实上线前人工确认

- 真实 BOSS 账号授权、DOM/API 契约采样、平台岗位 ID 绑定、登录/风控流程。
- 012 迁移（平台字段/输入版本/PII 加密与期限）评审与备份。
- 招呼按钮语义、频控与合规审批。
