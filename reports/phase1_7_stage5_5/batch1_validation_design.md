# Stage 5.5 Batch 1 — Policy Quality Gate 设计文档（只读正向锚点覆盖检查）

## 0. 范围声明

本批次只实现：**只读 Policy 解析 + 正向语义锚点覆盖检查 + 三岗位只读扫描 + CLI 接入**。

明确不做（后续批次）：Requirement 原子性自动拆分、ALL/ANY 深度结构检查、多 Requirement 锚点完整风险治理、
Policy 自动修复、发布门控、Migration、新版本创建、三岗位 Policy 实际修改、BGE 重验收、Tier 3、FastAPI、Vue3。

## 1. 评估范围（Tier 2 语义 Requirement）的判定依据

### 1.1 事实：Schema 没有显式评估范围字段

`PolicyRequirement`（`job_scoring_policy_v1`）只有 `requirement_type`（业务分类）、`priority`、
`needs_confirmation`（编译期人工确认标记）。**不存在 SEMANTIC / RULE / HUMAN_REVIEW 评估路由枚举。**
这是必须上报的 Schema 缺口，本批次不伪造该字段。

### 1.2 复用的结构性等价物（有引擎行为背书）

| 评估层 | Schema 结构 | 引擎证据（实测代码行） |
| --- | --- | --- |
| Tier 0 硬过滤 | `hard_filters[*].requirement_id` | `engine/policy_hard_filter.py`（按谓词执行，不涉及语义） |
| Tier 1 规则/关键词 | `keyword_groups[*].requirement_ids` | `engine/policy_rule_matcher.py`（关键词池按组引用） |
| Tier 2 语义 | `scoring_dimensions[*].requirement_ids` | `engine/policy_semantic_ranker.py` L351–409：语义总分与 `semantic_coverage` **只由维度投影聚合**，投影之外的 requirement 不产生语义贡献 |

因此：

```
语义评价范围（分母） = union(scoring_dimensions[*].requirement_ids) 中能解析到已定义原子要求的引用
```

这不是猜测，而是 Tier 2 引擎真实聚合路径的镜像。已被诊断（`config.policy_validation`）禁止发布的
无效引用不进入分母，单独报告。

### 1.3 各类 Requirement 的归类规则

| Requirement 特征（实际存在的信息） | 归类 | 是否进入语义分母 |
| --- | --- | --- |
| 被维度投影引用 | SEMANTIC（Tier 2 评价对象） | 是 |
| 仅被 keyword_groups 引用 | RULE（Tier 1 评价对象） | 否 |
| 被 hard_filters 引用 | HARD_FILTER（Tier 0 评价对象） | 否 |
| 同时被投影和关键词引用 | SEMANTIC（Tier 2 会评分它；关键词是 Tier 1 辅助信号） | 是 |
| **不被任何结构引用（孤儿）** | **UNCLASSIFIED** | **否**（保守排除 + WARNING） |
| `needs_confirmation=True` | 不影响归类（编译期确认标记，非评估路由；Schema 缺口如实上报） | 按结构归类 |

HUMAN_REVIEW 路由在 Schema 中不存在——**UNCLASSIFIED 是对 Schema 缺口的诚实兜底**：
无法确定评价层的要求不进入分母、不发虚假覆盖率，只发 WARNING 等待人工归类。

## 2. 覆盖状态定义（每个进入语义分母的 Requirement）

| 状态 | 判定 | 后果 |
| --- | --- | --- |
| DIRECT_COVERAGE | 存在 `polarity=POSITIVE` 且 `requirement_ids == [该要求]` 的锚点 | 计入直接覆盖分子 |
| BROAD_ONLY | 存在正向锚点，但全部同时关联 ≥2 个 requirement | 不计分子；WARNING（共享相似度不能独立证明本要求，与 Tier 2 引擎 `AMBIGUOUS_MAPPING` 行为一致） |
| MISSING_POSITIVE | 无任何正向锚点（NEGATIVE 不能替代） | 不计分子；ERROR → BLOCKED（与 Tier 2 引擎 `POLICY_ANCHOR_MISSING` 行为一致） |
| UNCLASSIFIED（要求级） | 孤儿 requirement（未被任何评价层结构引用） | 不进分母；WARNING + 单独计数 |

无效引用（投影或锚点引用了不存在的 requirement）→ ERROR；所在维度覆盖率记 INDETERMINATE；
整体覆盖率分母只含可解析引用。

## 3. 覆盖率计算

```
semantic_requirement_count = |可解析的维度投影并集|
direct_coverage_rate       = directly_covered / semantic_requirement_count        （分母为 0 时 = None）
broad_only_count           = 仅宽泛正向锚点的要求数（单独报告，不进分子）
missing_positive_count     = 无正向锚点的要求数
unclassified_count         = 未被任何评价层引用的要求数（单独报告，不进分母）
```

分母为 0（无法确认任何 Tier 2 评价对象）时：`coverage_status = INDETERMINATE`，
`positive_coverage_rate = None`——**绝不输出虚假的 0% 或 100%**。

维度级覆盖率：`dimension.direct_covered / dimension.semantic_requirement_count`；
含无效引用的维度 `direct_coverage_rate = None`（INDETERMINATE）。

## 4. 报告契约（`schemas/policy_validation.py`）

保留 Trae 已实现的字段名（向后兼容既有测试断言），只增不改：

- `PolicyQualityIssue`：`code` / `severity` / `message` / `requirement_id` / `anchor_ids` / `dimension_ids` + 新增 `suggested_action`。
- `PolicyDimensionCoverage`（新增）：`dimension_id` / `requirement_ids` / `invalid_requirement_ids` /
  `semantic_requirement_count` / `direct_covered` / `broad_only` / `missing_positive` / `direct_coverage_rate(可 None)`。
- `PolicyQualityReport`：原字段 + `policy_id` / `policy_version` / `policy_hash`（策略身份，库内模式由 Repository 行提供，
  文件模式 policy_hash 为内容指纹）、`coverage_status`、`broad_only_count`、`missing_positive_count`、
  `unclassified_requirement_count`、`dimension_coverage`。
- `status`：`BLOCKED`（含 ERROR）/ `WARNING`（仅 WARNING）/ `PASS`（无问题）。**只读报告，不构成发布门控**。

Issue 代码（本批 5 种）：`MISSING_POSITIVE_ANCHOR`(ERROR)、`BROAD_ONLY_POSITIVE_ANCHOR`(WARNING)、
`UNCLASSIFIED_REQUIREMENT_SCOPE`(WARNING)、`UNKNOWN_PROJECTION_REQUIREMENT`(ERROR)、`UNKNOWN_ANCHOR_REQUIREMENT`(ERROR)。

`policy_hash`：库内模式使用 `job_policy_versions.policy_hash`（Repository 权威值）；文件模式用与
`engine.policy_interpreter.content_hash` 相同的规范化 SHA-256 内容指纹（与 storage `digest` 规范化一致）。

## 5. 只读性保证

- Validator 是纯函数：只 `JobScoringPolicy.model_validate`（Pydantic 复制，不改输入 dict），无 I/O、无模型、无数据库。
- CLI scoped 模式走 `RecruitmentRepository.get_current_policy(AccessContext, job_id)`，复用现有权限体系
  （`AccessContext` → `_authorize` 组织/岗位授权），不绕过、不新增权限路径；执行路径用 `read()`（BEGIN 只读事务，无审计写入）。
- 不触发 Qwen（不构造 `JobPolicyCompiler` 客户端）、不触发 BGE（不加载模型/embedding 向量栈）。
- 不修改 Policy、不创建版本、不写任何表（测试用全库前后快照片断）。

## 6. CLI

已有入口 `python main.py policy-validate`（不新建入口），两种模式：

```
# 文件模式：验证独立 Policy JSON（含 quality）
python main.py policy-validate --policy path/to/policy.json

# 库内模式：验证指定岗位当前发布 Policy（经过 AccessContext 授权）
python main.py policy-validate --database <db> --org-id <org> --user-id <user> --job-id <job>
```

输出 JSON：`pydantic_valid` / `diagnostics`（既有发布诊断，不变）/ `publishable`（不变）/
`quality`（本批新增，含 `policy_id` / `policy_version` / `policy_hash` / 覆盖率 / 维度覆盖 / issues）。
quality BLOCKED **不改变** `publishable` 与退出码——发布门控属后续批次。错误路径（无参数、 NotFound、
PermissionDenied、数据库未初始化）输出 `{"ok": false, "error_type": ...}` 并返回 1。

## 7. 三岗位扫描口径（诚实声明）

三个案例（ai_automation / java_backend / b2b_sales）在**主业务库无已发布策略**
（`jobs.current_policy_id` 均为空、`job_policy_versions`/`policy_publications` 为 0 行，只读 SQL 实测）。
真实编译产物为 Stage 3 live 验收库中的 revision 1 **草稿**：

| case | 库 | org_id | job_id | draft_id |
| --- | --- | --- | --- | --- |
| ai_automation | `reports/phase1_7_stage3/live/normalized_v1_2/drafts.sqlite3` | 实查 | 实查 | 实查 |
| b2b_sales | 同上 | 实查 | 实查 | 实查 |
| java_backend | `reports/phase1_7_stage3/live/compact_java_v1_3/drafts.sqlite3` | 实查 | 实查 | 实查 |

扫描流程：只读 SELECT 发现真实 org/user/job/draft ID（不猜测）→ 用 `RecruitmentRepository` +
`AccessContext` 验证 `get_current_policy`（证明发布状态）与 `get_policy_draft`（取得草稿内容）→
校验草稿与 `reports/phase1_7_stage3/policies/*.json` 最终验收副本一致 → `validate_policy_quality` 出报告。
所有连接 `mode=ro`，不写任何文件/库。结果写入 `reports/phase1_7_stage5_5/batch1_policy_validation.json`。

## 8. 测试设计（对应任务清单 17 项）

单测 `tests/test_policy_validator.py`（保留 Trae 6 项）：有效/无效投影、孤儿要求、宽泛/缺失/负向锚点、
多锚点、多维度覆盖率、INDETERMINATE、空输入异常、V1/V2 隔离、输入不可变、无模型模块加载（subprocess 断言
`sentence_transformers`/`httpx`/`numpy` 未导入）。

CLI 集成 `tests/test_policy_cli.py`（重建并补全丢失的修改）：scoped 读取已发布策略（真实子进程、真实库）、
覆盖率正确、缺锚点问题可见、未知 job → NotFound、未授权用户/错误组织 → PermissionDenied、
无发布策略 → NotFound、缺参数 → ValueError、**全库业务数据前后快照一致（只读性）**、
输出无 metrics 字段 + 不可路由端点（证明无 Qwen/BGE 调用）。
