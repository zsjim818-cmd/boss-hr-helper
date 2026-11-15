# Stage 5.5 封口 — 发布质量门控（Batch 5）

日期：2026-11-15
范围：只补"发布质量门控"最后一环。未做自动修复、未拆分母拆分等非 MVP 支线。至此 Stage 5.5（正向锚点 / 原子性 / ALL-ANY / 引用完整性 / 发布门控）正式封口。

---

## 1. 实现

### 1.1 服务层门控（`config/policy_service.py`）

- 新增异常 `PolicyQualityBlocked(ValueError)`：携带完整 `PolicyQualityIssue` 列表，消息含 ERROR 代码集合。
- `PolicyCompilerService.publish_version(..., enforce_quality_gate=True)`：
  - 默认**开启**：读取已封存的 `job_scoring_policy_v1` 版本 → `engine.policy_validator.validate_policy_quality`（带 policy_id/version/hash 身份）→ `status == "BLOCKED"` 时抛 `PolicyQualityBlocked`，不执行 `publish_policy`。
  - 仅对通用策略（`job_scoring_policy_v1`）门控；legacy snapshot 策略保持历史发布路径（其质量契约不适用）。
  - `enforce_quality_gate=False` 是**显式、可审计**的豁免参数（用于已审阅的测试/迁移场景），不是静默绕过。

### 1.2 CLI（`config/policy_cli.py`）

- `policy-publish` 新增 `--allow-blocked-quality`（默认关闭 = 门控开启）。
- 门控拒绝输出：`{"ok": false, "error_type": "PolicyQualityBlocked", "message": ..., "quality_issues": [...]}`，退出码 1；发布条目不会写入。

### 1.3 与历史测试的兼容（零破坏方式）

默认门控会拒绝 offline fixture（其质量状态本就是 BLOCKED，这是 Batch 1 的发现）。因此三处历史发布调用改为**显式豁免**（仅新增参数，断言未改、未删除）：

| 文件 | 位置 | 说明 |
| --- | --- | --- |
| `tests/test_policy_compiler.py` | `test_explicit_confirmation_can_create_immutable_version_and_publish` | 验证版本/发布机制 |
| `tests/test_policy_cli.py` | `test_actual_cli_draft_...` 与 `publish_java_policy` helper | 验证 CLI 发布机制 |
| `tests/test_policy_normalization_integration.py` | `test_full_compiler_retains_raw_response_...` | 验证规范化链路 |

所有 repo 层 `publish_policy` 调用（database_support / prescreen_support / 各数据库测试）不经 service，天然不受影响。

### 1.4 新增测试

- `test_publish_quality_gate_blocks_blocked_policy_by_default`（service）：BLOCKED → `PolicyQualityBlocked`，issues 含 `MISSING_POSITIVE_ANCHOR`；版本已存在但 `get_current_policy` NotFound（未发布）。
- `test_publish_quality_gate_passes_a_clean_policy`（service）：补齐 4 个正向锚点的 java 策略 quality=PASS → 发布成功。
- `test_publish_quality_gate_bypass_is_explicit_and_auditable`（service）：显式豁免 → 发布成功。
- `test_publish_quality_gate_blocks_blocked_policy_by_default`（CLI 子进程）：默认 `policy-publish` → 退出码 1 / `PolicyQualityBlocked` / `quality_issues` 可见 / 未发布。

## 2. 语义边界（诚实声明）

1. 只阻断 `status == "BLOCKED"`（存在 ERROR 级问题：缺失正向锚点、无效引用、REQUIRED 被降级、重复组语义冲突等）。`WARNING` 级（宽泛锚点、孤儿要求/组、原子性提示等）**不阻断**，仅随 quality 报告呈现——这是"可发布但有可见风险"，与 Batch 1-2 的"记录不治理"一致。
2. 门控是**内容级**：豁免参数不改变任何质量判断，只是允许已审阅的调用方越过；生产 CLI 默认不越过。
3. 旧 V2 引擎（legacy snapshot 策略）与通用策略的发布路径分离：门控不会误伤历史兼容路径。
4. 未实现：quality WARNING 的人工确认流程（类似 CONFIRM 语义）——如产品需要"WARNING 必须 HR 确认才能发布"，需另立需求。

## 3. Stage 5.5 总览（封口）

| 批次 | 内容 | 状态 |
| --- | --- | --- |
| Batch 1 | 正向锚点覆盖、维度覆盖率、UNCLASSIFIED、INDETERMINATE | ✅ 已交付（21 测试） |
| Batch 2 | Requirement 原子性、ALL/ANY 结构、引用完整性（requirement+group）、编译期 quality 接线 | ✅ 已交付（22 测试） |
| Batch 5（本批） | 发布质量门控 | ✅ 已交付（4 测试 + 3 处显式豁免） |
| （Batch 3/4 合并入 Batch 2 的范围：原子性与多要求锚点记录） | — | 见 §2 边界 |

> **Sprint 2 更新（同日）**：门控豁免已收紧——`enforce_quality_gate=False` 或 API/CLI 的豁免必须携带显式原因，且仅 ORG_ADMIN 可执行；每次豁免写入 `POLICY_QUALITY_GATE_OVERRIDDEN` 审计事件（操作人、policy_id/version/hash、原因、时间）。默认路径仍对 BLOCKED 抛 `PolicyQualityBlocked`。原测试中的 3 处豁免调用已补原因（断言未改）。

未实施且不在 MVP 关键路径：自动拆分/自动修复、多要求共享锚点的强制治理、WARNING 确认流、质量报告历史化存储。
