# 验收矩阵（2026-11-15，UTC+8）

基线：HEAD `2b1a373` + 本次未提交修复。
真实 BOSS 兼容性：**BLOCKED（无授权账号/无真机契约）**。

| 编号 | 场景 | 结果 | 证据 / 缺口 |
| --- | --- | --- | --- |
| T01 | 重复浏览器连接 | PASS（本地） | `test_boss_readonly_adapter::test_reconnect_reuses_open_tab_without_navigation`；工厂不再重复 connect；`test_capture_workflow::test_one_connect_per_http_scan` |
| T02 | `boss_readonly` 误开启 | PASS | `test_capture_workflow::test_boss_readonly_remains_hard_disabled_even_when_allowed`：`REAL_CONTRACT_UNVERIFIED`，0 候选人写入 |
| T03 | 内部 ID vs 平台 ID | PARTIAL | DTO/解析/仓储已分离（`test_capture_workflow::test_parser_separates_platform_job_id_and_drops_security_id`）；批次级平台 ID 持久化需 012，BLOCKED |
| T04 | 首屏/延迟/三页/重复/重排 | PASS（Mock） | 既有 `test_browser_adapter` 分页/去重/重排；本次 Collector listed/deduplicated/max_details；真实分页 BLOCKED |
| T05 | 同名不同 ID / 详情身份冲突 | PASS | `test_response_parser` 同名保留；`test_browser_cdp_e2e`/`test_browser_adapter` IDENTITY_MISMATCH |
| T06 | 明确不符 vs 字段缺失 | PASS | `test_capture_workflow::PreviewTests`：仅明确低学历/年限跳过，缺失 FETCH_DETAIL |
| T07 | 预筛节约详情 | PASS | `test_capture_workflow::test_preview_skips_confirmed_mismatch_without_detail_call`（open_detail 调用数=2） |
| T08 | 完整/缺项目/空详情/协议变化 | PASS（本地） | `test_boss_readonly_adapter` 完整/残缺/失败；`test_response_parser` 空详情 PARTIAL + CONTRACT_MISMATCH；真实未知 |
| T09 | 扫描导入后评分 | PASS（后端 + UI） | `test_capture_workflow` 扫描评分链路；UI E2E 断言扫描后评分按钮可用；ScannerView 选中/批量评分，vue-tsc/build |
| T10 | 批次途中发布新 Policy | PASS | `test_batch_policy_binding`（v1 批次 → v2 新批次、旧结果复用隔离）；UI 显示 `batch.policy_version` |
| T11 | 重扫幂等 / 新详情补全 | PARTIAL | 重扫幂等 PASS；新 sealed 输入版本受不可变触发器限制，显式 `INPUT_VERSIONING_REQUIRES_012`（BLOCKED） |
| T12 | 同 actor/batch 并发扫描 | PASS | `test_capture_workflow::test_concurrent_scans_are_rejected_not_overwritten`：409 `SCAN_ALREADY_RUNNING` |
| T13 | 计数/时限/无新增/取消/异常 | PASS（取消/上限） | max_details、max_duration 停止原因、取消接口 → `CANCELLED`、EMPTY_UNVERIFIED；暂停/恢复 BLOCKED |
| T14 | 审核/联系状态 | PASS | `test_contact_summary` + UI 徽章；联系不自动发送；NEEDS_RELOCATE 提醒已加入 |
| T15 | PII/期限/日志 | PARTIAL / BLOCKED | `security_id` 解析与入库剥离；runtime-mode 诊断中的本地 Profile 路径已脱敏；真实 PII 加密/到期需 012，真实采集关闭 |
| T16 | 无账号本地全链路 | PASS（本地） | 后端 700/700、UI E2E 31/31、采集/预筛/评分测试；真实账号 BLOCKED |
| T17 | 真实 BOSS 兼容性 | BLOCKED | 无授权 HR 账号、无真机 DOM/API 证据；代码硬门禁 |

> 当前仅通过离线模拟/本地 CDP 验证，真实 BOSS 招聘端兼容性尚未得到证明。
