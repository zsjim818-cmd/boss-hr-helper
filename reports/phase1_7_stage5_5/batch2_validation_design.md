# Stage 5.5 Batch 2 — 设计文档（Requirement 原子性 / ALL-ANY 结构 / 引用完整性不变量）

日期：2026-11-15
前置：Batch 1（正向锚点覆盖检查）已验收；基线恢复完成（config.json git 恢复、主库 3 张外挂表清除、`db-check` ok、459/459 全绿）。
范围声明：本批**只新增只读检查**。不修改 Schema 定义（`schemas/job_policy.py`）、不做发布门控、不自动修复 Policy、不改迁移、不改已发布版本。

---

## 1. 事实基础（全部来自当前磁盘真实验证）

### 1.1 Schema 可用的结构信息

`PolicyRequirement`：`requirement_id/label/source_type/source_text/priority/requirement_type/needs_confirmation`。
`RequirementGroup`：`group_id/label/operator(ALL|ANY)/priority/members/source_type/source_text/needs_confirmation`。
`KeywordGroup.requirement_ids`、`ScoringDimension.requirement_ids`、`HardFilter.requirement_id` 是三个评价层引用入口（Batch 1 已建立该推导与引擎行为的一致性）。

### 1.2 引擎语义（决定检查严重度的依据）

| 事实 | 证据 |
| --- | --- |
| Tier 0 对**非 REQUIRED 的组节点整棵子树跳过**（`if item.priority != "REQUIRED": skipped; return None/UNKNOWN`） | `engine/policy_hard_filter.py` evaluate() L365–370 |
| Tier 1 关键词池只通过「维度目标 ∩ 组图可达叶子」进入计算，所以组的 ALL/ANY 组合语义在没有任何维度引用其叶子时永不执行 | `engine/policy_rule_matcher.py` evaluate_policy_rules L318–323、`_pool` L222 |
| 编译期只对 requirement **label** 中的 ASCII「X或Y」做 ANY 降级；**source_text 的内嵌选择不处理** | `config/policy_normalization.py` L18、L71–80（`alternative_analysis` 只读 `requirement.label`） |
| 一个原子 requirement 的锚点按 `requirement_ids` 精确关联；req 文本捆绑两个能力时，单一锚点无法分别验证 | `engine/policy_semantic_ranker.py` L121–175 |

### 1.3 真实策略结构（Batch 2 检查的标定样本）

`reports/phase1_7_stage3/policies/*.json`（真实 Qwen 编译产物）：

- ai_automation：6 组（含 normalizer 生成的 `req_route_a_tool_01`/`req_route_b_web_01` 两个 ANY 组），全部组叶子均被评价层引用。
- java_backend：`Spring Boot或Quarkus`、`Kafka或Kubernetes` 均已在 ANY 组内（选择语义被结构正确表达）。
- b2b_sales：**`grp_bonus_items`（ANY/PREFERRED，成员 crm/english）的叶子没有被任何关键词组/维度/硬过滤引用** —— 该组组合语义永不执行，且两成员已是 Batch 1 的 UNCLASSIFIED。

## 2. 检查设计（9 个新 issue 代码，确定性、只读、可解释）

### 2.1 Requirement 原子性

**`EMBEDDED_ALTERNATIVE_REQUIREMENT`（WARNING）**
条件：requirement **不是任何 ANY 组的直接成员**、`requirement_type != RESPONSIBILITY`，且 `source_text` 含内嵌选择：
- 出现「或者」；或
- 单「或」两侧邻域（各 ≤16 字符、在标点/句界截断）各含 Latin 技术 token；
- 排除数量习语（一个或多个 / 一种或多种 / 任一或多个…）。
理由：ANY 组已是选择语义的合法表达载体（normalizer 只处理 label 层）；RESPONSIBILITY 天然描述活动范围，不是能力门槛。fixture 验证：b2b「签约或回款案例」两侧为中文、java/ai 的「或」全部位于 ANY 组 → 不误报。

**`CONJOINED_TECH_REQUIREMENT`（WARNING）**
条件：`source_text` 中 `和/与/以及` 连接的两侧各含**不同的** Latin 技术 token（邻域同样按标点截断；`和/与` 前一字符黑名单剔除「饱和/参与/给与」等词内用法）。
不检查 `并/并且/同时`（中文动词串联惯用）与 `、/，` 列举（关键词组已提供部分评分）。fixture 验证：`SQL与事务设计能力`（右侧中文）不报；`异常处理和接口测试`（两侧中文）不报。

### 2.2 ALL/ANY 结构

**`SINGLE_MEMBER_GROUP`（WARNING）**：组只有 1 个成员，组合语义为空。
**`REDUNDANT_GROUP_WRAPPER`（WARNING）**：组唯一成员是另一个组（透传包装）。
**`DUPLICATE_GROUP_MEMBERS`**：两组成员集完全相同 —— operator/priority 一致时 WARNING（冗余），不一致时 **ERROR**（语义歧义）。
**`PREFERRED_GROUP_DEMOTES_REQUIRED`（ERROR）**：PREFERRED/OPTIONAL 组包含 REQUIRED 成员（原子或子组）。依据 §1.2：Tier 0 会跳过非必需组整个子树，硬约束静默丢失。与既有 `GROUP_PROMOTES_PREFERENCE`（反向提升）对称。
**`ALL_SEMANTICS_MISMATCH`（WARNING）**：`operator=ANY` 但组 `source_text` 含同时性短语（同时满足/全部都要/都必须/均需/都要满足/缺一不可/以上全部/两者都要/都需要）。fixture 的「不要求同时具备」「至少满足一条」不匹配 → 不误报。

### 2.3 评价层引用完整性（编译期不变量）

不变量定义（与 Batch 1 的层归类一致）：**每条由编译器产出的 requirement 必须被至少一个评价层引用；每个组的叶子闭包必须与至少一个评价层引用相交。**

- requirement 级：Batch 1 已有 `UNCLASSIFIED_REQUIREMENT_SCOPE`（WARNING，不计入语义分母）——不重复实现、不改变其语义。
- **`ORPHAN_REQUIREMENT_GROUP`（WARNING，本批新增）**：组的所有可达叶子 ∩（硬过滤 ∪ 关键词组 ∪ 维度投影）= ∅ → 该组 ALL/ANY 组合语义从未被任何评分路径引用。真实 b2b 的 `grp_bonus_items` 即此情形。报告新增 `orphan_group_count` 字段。
- **`PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED`（WARNING，本批新增）**：priority=PREFERRED 的 requirement 未接入任何关键词组且不属于已 UNCLASSIFIED 集合（避免双重报告）。理由：加分项在 Tier 1 只能通过关键词组挣分；只进维度会让 Tier 1 完全失去信号。offline fixture 的 preferred 全部接入 extra 组（对齐预期）。
- **编译期执行点**：`PolicyCompilerService.compile_draft` 与 `update_draft` 的返回值新增 `"quality"`（完整 `PolicyQualityReport`）。**报告性、不阻断**——`publishable`、草稿 diagnostics、版本/发布路径完全不变（发布门控属后续批次，需另行批准）。HR 手工编辑走 `update_draft` 同样被复核，防止人工回归。

### 2.4 明确不做（本批边界）

Requirement 自动拆分、Policy 自动修复、ALL/ANY 自动改写、锚点治理（多要求锚点风险=后续批次）、发布门控、Schema 变更（增加需求级评估范围字段需另行批准方案）、关键词池可达性的深度检查（已知观察见 §5）。

## 3. 报告契约增量（向后兼容）

- `PolicyQualityIssue` 新增 `group_id: str | None = None`。
- `PolicyQualityReport` 新增 `orphan_group_count: int = 0`。
- `validator_version` → `policy-quality-validator-v1.2`。
- 既有字段与语义不变（Batch 1 全部测试原样通过）。

## 4. 测试设计

- `tests/test_policy_validator.py` 新增 18 项：
  - `AtomicityTests`（6）：ANY 组外内嵌选择触发/ANY 组内豁免/RESPONSIBILITY 豁免/数量习语豁免/技术捆绑触发/中文连接不误报；
  - `GroupStructureTests`（9）：单成员组/透传包装/重复成员（同 operator=WARNING、异 operator=ERROR）/PREFERRED 组降级 REQUIRED=ERROR/ANY+同时性短语告警/fixture 正确 ANY 不误报/孤儿组计数/加分项未接关键词告警；
  - `Batch2CompatibilityTests`（3）：三 fixture 保持零 Batch 2 发现且 issue 集仍精确等于 `{MISSING_POSITIVE_ANCHOR}`/fully-covered 保持 PASS/真实三策略结构事实钉住（b2b 恰 1 孤儿组，ai/java 零）。
- `tests/test_policy_compiler.py` 新增 1 项：compile_draft/update_draft 返回 quality 且不门控。
- `tests/test_config_guard.py`（P0-1）3 项：根 config 通过 validate_config、与 git 基线一致、主库 db-check 无漂移（库不存在则 skip）。
- CLI 回归：`policy-validate` 文件模式对三真实策略（`batch2_policy_validation.json`）。
- 全量回归：保持历史 432 项 + Batch 1 的 27 项 + 本批新增 22 项。

## 5. 已知观察与后续候选（不在本批实施）

1. **关键词池可达性**：Tier 1 关键词组只有在「其声明的 requirement 与某维度目标（经组图展开）相交」时才进入维度池。offline fixture 的 `extra` 组（Docker/n8n）从未被任何维度目标触及，因此其关键词命中只记录在 `keyword_evidence`、不进入任何维度分数。这是 Stage 4 既有语义，本批未改变；是否应作为质量问题检查留待后续批次决定（会影响 fixture 预期，需单独评审）。
2. **组 ID 命名**：真实 ai 策略中 normalizer 产物组名沿用 `req_*` 前缀（`req_route_a_tool_01`），可读性可优化但非结构缺陷。
3. 多 Requirement 共享锚点治理（Batch 4 范围）与发布门控（Batch 5 范围）仍未实施。
