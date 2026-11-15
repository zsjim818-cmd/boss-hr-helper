# Stage 5.5 Batch 1 — Policy Quality Gate 交付报告

执行时间：2026-11-15
范围：只读 Policy 解析 + 正向语义锚点覆盖检查（Batch 1，不含后续批次的任何功能）
前序文档：`batch1_audit.md`（接管审计）、`batch1_validation_design.md`（设计）
机器可读结果：`batch1_policy_validation.json`
测试日志：`batch1_tests.log`

---

## 1. 最终文件清单

### 1.1 Trae 已有成果及保留情况（全部保留，未清理未重置）

| 文件 | 状态 | 本批处理 |
| --- | --- | --- |
| `engine/policy_validator.py` | Trae 新增（未跟踪） | 保留全部逻辑，扩展报告字段与策略身份 |
| `engine/policy_anchor_validator.py` | Trae 新增（未跟踪） | 保留 5 个函数名与行为，新增分类/引用检查/维度覆盖 |
| `schemas/policy_validation.py` | Trae 新增（未跟踪） | 保留全部既有字段（向后兼容），只增字段 |
| `tests/test_policy_validator.py` | Trae 新增（未跟踪） | **6 项测试逐字保留**，追加 15 项新测试 |
| `config/policy_cli.py` | Trae 修改（+23/-3） | 保留两种模式结构，scoped 模式补策略身份透传 |

### 1.2 本批新增/修改

| 文件 | 说明 |
| --- | --- |
| `schemas/policy_validation.py` | +`PolicyDimensionCoverage`；`PolicyQualityIssue`+`suggested_action`；报告 + policy_id/version/hash、coverage_status、4 类计数、dimension_coverage |
| `engine/policy_anchor_validator.py` | +`UNCLASSIFIED_REQUIREMENT_SCOPE`/`UNKNOWN_PROJECTION_REQUIREMENT`/`UNKNOWN_ANCHOR_REQUIREMENT` 检查、要求层分类、维度覆盖率 |
| `engine/policy_validator.py` | 聚合扩展 + `policy_content_hash`（与 `policy_interpreter.content_hash` 同规范）+ 身份参数校验 |
| `config/policy_cli.py` | scoped 模式透传 `policy_id/policy_version/policy_hash`（来自 Repository 行，权威身份） |
| `tests/test_policy_validator.py` | 15 项新测试（覆盖任务清单第 1–15 项要求） |
| `tests/test_policy_cli.py` | `run_cli` 助手 +job_id/org_id 参数（默认值不变）；6 项新 CLI 集成测试（覆盖任务清单第 16–17 项及 CLI 要求） |
| `reports/phase1_7_stage5_5/scan_policy_validation.py` | 三岗位只读扫描脚本（可复现） |
| `reports/phase1_7_stage5_5/batch1_*.md/.json/.log` | 5 份交付文档 |

未修改：`main.py`（CLI 命令注册不动，`policy-validate` 入口已存在）、任何 Schema 定义文件
（`schemas/job_policy.py` 未动）、任何迁移文件、任何业务数据库、任何 Policy 内容。

**未触碰的外部并发产物**：`server/`（`app.py` 等）、`storage/repository.py` 为 Batch 1 会话期间
**外部并发开发者**新增的未跟踪文件（13:01–13:02 创建，非 OpenCode、非 Trae），`frontend/` 在接管
初始即存在且会话期间被外部继续修改——三者 Batch 1 均只观察、未改动（并发活动时间线与业务库污染
证据见 `batch1_audit.md` 附录 A）。

补充说明：接管审计确认 Trae 在 `tests/test_policy_cli.py` 的 14 行修改**从未落盘**（文件与基线提交
逐字节一致）。本批以完整覆盖的 6 项新 CLI 测试重建了该部分意图，未覆盖既有测试。

## 2. Policy Validator 实现说明

- **入口**：`engine.policy_validator.validate_policy_quality(policy, *, policy_id=None, policy_version=None, policy_hash=None) -> PolicyQualityReport`。纯函数：只 `model_validate`（复制输入）、无 I/O、无模型、无数据库、不缓存。
- **语义分母**：`union(scoring_dimensions[*].requirement_ids)` 中可解析到已定义原子要求的引用。
  依据：`engine/policy_semantic_ranker.evaluate_policy_semantics` 的语义总分与 `semantic_coverage`
  只通过维度投影聚合（L351–409），投影之外的 requirement 不产生语义贡献——分母与引擎真实行为一致。
- **覆盖分类**（对分母内每个要求，确定性、按声明顺序）：
  - DIRECT：存在 `POSITIVE` 且 `requirement_ids == [该要求]` 的锚点；
  - BROAD_ONLY：有正向锚点但全部关联 ≥2 个要求（WARNING，与引擎 `AMBIGUOUS_MAPPING` 一致）；
  - MISSING_POSITIVE：无正向锚点（ERROR → BLOCKED；与引擎 `POLICY_ANCHOR_MISSING` 一致；NEGATIVE 不替代）。
- **排除项**：仅 keyword_groups 引用（Tier 1 RULE）、仅 hard_filters 引用（Tier 0）、以及孤儿（UNCLASSIFIED，
  WARNING，不计分母）。HUMAN_REVIEW 路由在 Schema 不存在，不伪造（见 §6 缺口）。
- **无效引用**：维度投影/锚点引用未定义 requirement → ERROR；该维度 `direct_coverage_rate=None`（INDETERMINATE）。
- **INDETERMINATE 总覆盖**：分母为 0 时 `coverage_status=INDETERMINATE`、`positive_coverage_rate=None`
  ——绝不输出虚假 0%/100%。
- **身份**：库内模式 policy_id/version/hash 取自 `job_policy_versions` 行；文件模式 hash 为规范化内容
  指纹（与 storage `digest`/interpreter `content_hash` 同一规范化形式）。
- Issue 含 `code/severity/requirement_id/anchor_ids/dimension_ids/message/suggested_action`。
- **只读**：不修复、不写库、不改 Policy、不触发 Qwen/BGE、不重评分。`status=BLOCKED` 不影响
  `publishable` 与退出码（发布门控属后续批次）。

## 3. 三个岗位的真实覆盖率（真实 Qwen 编译策略，revision 1 草稿）

扫描口径：三个案例在主业务库**无已发布策略**（`job_policy_versions`/`policy_publications` 均 0 行，
Repository 探测 `get_current_policy → NotFound`）；验证对象为 Stage 3 live 验收库中与
`reports/phase1_7_stage3/policies/*.json` 最终验收副本**逐字节一致**（JSON 语义相等）的 revision 1 草稿。
所有 ID 从数据库实查（源快照与 fixture 输入精确匹配），未猜测。原始库零写入（发现阶段 `mode=ro`，
Repository 探测在字节副本上执行）。

| case | job_id | draft (rev 1) | 状态 | 语义要求 | 直接覆盖 | 仅宽泛 | 缺正向 | 未分类 | 直接覆盖率 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ai_automation | `feea4ce1…` | `f24303ed…` | BLOCKED | 10 | 0 | 7 | 3 | 0 | **0.0** |
| java_backend | `b6a07a6d…` | `00698530…` | BLOCKED | 5 | 0 | 2 | 3 | 0 | **0.0** |
| b2b_sales | `c9c1f96d…` | `2d4cc8a1…` | BLOCKED | 6 | 1 | 4 | 1 | 2 | **0.166667** |

层分布（详见 JSON `requirement_layers`）：
- ai：Tier2 10 项 / Tier1 关键词 11 项 / Tier0 硬过滤 2 项（edu、sal）
- java：Tier2 5 项 / Tier1 7 项 / Tier0 3 项（edu、exp、salary）
- b2b：Tier2 6 项 / Tier1 6 项 / Tier0 2 项（work_years、salary）+ **2 项孤儿**

对照：离线人工 fixture（`tests/policy_fixtures.py`）java 案例为 6 语义要求、3 直接覆盖、3 缺失、
覆盖率 0.5——真实编译输出的锚点质量明显低于人工编写的 fixture，这正是质量门的价值所在。

## 4. 三个岗位的问题清单（完整问题见 `batch1_policy_validation.json`）

### ai_automation（draft `f24303ed…`，与 `policies/ai_automation.json` 一致；同源历史草稿另存于 full_v1_1 / live_ai_v1）
- ERROR `MISSING_POSITIVE_ANCHOR`：`req_route_a_rag_01`（dim_delivery/dim_efficiency）、
  `req_api_test_01`（dim_reliability）、`req_n8n_01`（dim_efficiency）——共 3 项被声明为 Tier 2 评价
  却无正向锚点。
- WARNING `BROAD_ONLY_POSITIVE_ANCHOR`：7 项。`sa_pos_delivery` 一个锚点同时关联
  5 个要求；`sa_pos_reliability` 同时关联 `req_exc_01`+`req_route_b_retry_01`。

### java_backend（draft `00698530…`，与 `policies/java_backend.json` 一致；历史同源自 java_v1_2）
- ERROR：`req_spring_boot`、`req_quarkus`（dim_service_design）、`req_auto_test`（dim_test_quality）缺正向锚点。
- WARNING：`req_java_dev`、`req_sql_txn` 仅有 `sa_pos_order_payment`/`sa_pos_perf_tuning`
  这类多要求共享锚点。

### b2b_sales（draft `2d4cc8a1…`，与 `policies/b2b_sales.json` 一致；历史同源自 full_v1_1）
- ERROR：`req_skill_interview`（dim_customer_dev/dim_solution_comm）缺正向锚点。
- WARNING BROAD_ONLY：`req_exp_direct_sales`、`req_exp_channel_sales`、`req_evidence_deal`、`req_skill_funnel`；
  仅 `req_skill_negotiation` 有直接覆盖。
- WARNING `UNCLASSIFIED_REQUIREMENT_SCOPE`：**`req_pref_crm`、`req_pref_english` 两个加分要求
  未被任何评价层引用**（无维度投影/关键词组/硬过滤）——编译器生成了要求却未接线，
  是 Schema 缺口的真实案例（offline fixture 中对应的 docker/n8n 是接进了 extra 关键词组的）。

主业务库（`data/recruitment.sqlite3`）补充事实：1 个岗位（job `3addf716…`，title 在库中即存储为字面
`AI???????/??`，早期写入残留）、无已发布策略。该库在**接管时是干净的**（12:51 只读检查：41 表、
schema v8、无遗留表），但在 Batch 1 会话期间 **13:05:13 被外部并发启动的 uvicorn
`server.app:app --reload` 进程污染**：其 `storage/repository.py` 构造函数在业务库自动创建了 3 张
迁移集之外的空遗留表（`job_policies`/`candidate_records`/`daily_quotas`，均 0 行），引发 schema
drift，`Database.check()` 由此不通过，所有 policy CLI 命令按既有守卫正确拒绝该库。该 uvicorn 进程
至本报告撰写时仍在运行（详见 `batch1_audit.md` 附录 A）。Batch 1 全部命令对该库仅使用 `mode=ro`
连接与字节副本，业务库文件 mtime 自 13:05:13 起未因本批变化。修复属业务数据变更，超出本批只读范围，
建议单独决策（停服 + 移除遗留表/隔离其数据库文件）。

## 5. CLI 使用方法

```powershell
# 文件模式：验证独立 Policy JSON（publish 诊断 + quality 质量报告；不触达任何数据库）
.\.venv\Scripts\python.exe main.py policy-validate --policy path\to\policy.json

# 库内模式：验证指定岗位当前发布 Policy（经 AccessContext 授权，只读）
.\.venv\Scripts\python.exe main.py policy-validate --database data\recruitment.sqlite3 `
    --org-id <org> --user-id <user> --job-id <job>
```

输出 JSON：`pydantic_valid` / `diagnostics`（既有发布诊断，不变） / `publishable`（不变） /
`quality`（新增：`policy_id`/`policy_version`/`policy_hash`、`status`、`coverage_status`、
`semantic_requirement_count`、`directly_covered_count`、`broad_only_count`、`missing_positive_count`、
`unclassified_requirement_count`、`positive_coverage_rate`、`dimension_coverage[]`、`issues[]`）。
错误路径（缺参数/未知岗位/无发布策略/未授权/库未初始化）返回 `{"ok":false,"error_type":...}` 与退出码 1。
quality BLOCKED **不改变** `publishable` 与退出码（发布门控属后续批次）。

## 6. Schema 缺口（如实上报，未擅自补字段）

1. `PolicyRequirement` 无显式评估范围字段（无 SEMANTIC/RULE/HUMAN_REVIEW 枚举）。
   本批以维度投影推导 Tier 2 范围（引擎行为背书）；后续若引入需求级路由字段需 Schema 演进 + 迁移评审。
2. HUMAN_REVIEW 路由不可表达：无法确定评价层的要求只能报 UNCLASSIFIED（b2b 已出现 2 例真实数据）。
3. `needs_confirmation` 是编译期确认标记，不是评估路由，本批未据其分类。
4. 一个正向锚点可关联多个 requirement：真实编译策略高度依赖共享锚点（13/21 语义要求仅宽泛覆盖）。
   本批仅记录 BROAD_ONLY，完整治理属后续批次。
5. 主业务库会话期间被外部 uvicorn 进程污染（3 张空遗留表，见 §4 与审计附录 A），在其被处理前
   阻塞该库上一切 policy CLI 操作，需单独修复决策。

## 7. 下一批次建议

1. **Batch 2（原子性 + ALL/ANY 结构）**：真实策略中的复合要求与组结构是下一检查目标；
   建议同时检查"编译器生成的每个 requirement 至少被一层引用"的编译期不变量（b2b 孤儿案例），
   以及 preferred 要求是否应强制接入关键词组（与 offline fixture 行为对齐）。
2. **主业务库修复决策**：外部 uvicorn 进程在会话期间向业务库写入了 3 张空遗留表（先停服，再移除
   遗留表或隔离其数据库文件；需业务确认 + 备份；本批不动，见审计附录 A）。
3. **发布门控接入**：Batch 1 已产出 BLOCKED/WARNING 事实；后续批次可决定 `publish` 是否
   要求 quality PASS，以及 batch 扫描是否纳入 `policy-diagnose` live 验收流程。
4. **锚点提示词改进**：真实编译输出 0 直接覆盖（ai/java）说明编译器倾向生成多要求大锚点；
   可在 prompt/normalization 层引导"每个 Tier 2 要求一条独立正向锚点"（属 Stage 3 编译器侧后续工作）。

## 8. 测试结果

### 新增测试

- `tests/test_policy_validator.py`：21 项（Trae 6 项保留 + 新增 15 项），覆盖任务清单 1–15：
  直接覆盖/缺失/仅负向/多锚点/多要求锚点/无效投影/无效锚点引用/RULE 排除/HARD_FILTER 排除/
  HUMAN_REVIEW 等价（孤儿 UNCLASSIFIED）/INDETERMINATE/多维度覆盖率/空输入异常/V1V2 隔离+不可变/
  身份与哈希 + subprocess 断言无 sentence_transformers/httpx/numpy/torch 加载。
- `tests/test_policy_cli.py`：13 项（既 7 项全保留 + 新增 6 项），覆盖任务清单 16–17 与 CLI 要求：
  scoped 读取已发布策略并输出正确覆盖率与缺锚点问题、发布策略身份透传、未知岗位 NotFound、
  未授权用户/异组织 PermissionDenied、无发布策略 NotFound、缺参数 ValueError、
  **全库业务数据前后快照一致（只读性）**、无 metrics 字段 + 不可路由模型端点（无 Qwen/BGE 调用）。

### 结果（详见 `batch1_tests.log`）

- `tests.test_policy_validator`：21/21 OK
- `tests.test_policy_cli`：13/13 OK
- 全量回归（`unittest discover`，与项目既有 `python main.py test` 相同发现规则）：
  **Ran 459 tests — OK**（接管基线 438 = 432 项 Stage 1–5 历史 + 6 项 Trae validator；
  本批新增 21 项：validator +15、CLI +6；438 + 21 = 459，全部通过）
- 历史 432 项 Stage 1–5 测试无删除、无断言降低、无预期修改，全部继续通过。
- 日志中若出现 "qwen"/"Tier3 call metrics" 字样：来自历史 Tier 3 观测性单测的**注入式 Mock 客户端**
  （"开始载入模拟模型 (injected)"、api_elapsed 秒级 0.000x、状态为预期的失败路径），**不是真实模型调用**；
  Batch 1 新增代码无任何模型依赖（并有 subprocess 断言 `sentence_transformers`/`httpx`/`numpy`/`torch`
  不被加载）。
- 追加事实核查：全部测试在隔离的临时库上运行，未受外部 uvicorn 进程影响；回归期间业务库文件 mtime
  无变化（证据见 `batch1_audit.md` 附录 A）。
