# Boss HR 智能筛选算法说明

本文描述当前代码实际执行的候选人初筛与深评算法。目标不是“用一个模型给简历打一个黑箱总分”，而是把招聘判断拆成可审计、可复现、可人工复核的 Tier 0～3：

- **Tier 0**：客观硬条件三态过滤；
- **Tier 1**：Policy 规则初筛与粗排；
- **Tier 2**：BGE 向量语义证据与覆盖率；
- **Tier 3**：Qwen 结构化终审；
- 最后由 HR 在审核看板做最终判断。

所有阈值、维度、Rubric、关键词组、语义锚点都来自候选人绑定的**不可变 Policy 版本**，算法本身不写死具体岗位。

---

## 1. 总体设计原则

### 1.1 单一不可变 Policy

每个候选人通过 `job_candidates.policy_id + policy_version` 绑定一个已发布 Policy；切换批次时，使用该批次锁定的 Policy 版本。

Tier 0/1/2/3 读取同一个 Policy，避免出现：

- 初筛按 v1 规则，终审按 v2 规则；
- 页面显示的 Policy 与实际评分 Policy 不一致；
- 重评时静默换规则。

### 1.2 三态而不是二态

客观条件不只有“通过/淘汰”，还包括“未知”：

- `PASS`：明确满足；
- `FAIL`：明确不满足；
- `UNKNOWN`：信息缺失、规则无法执行、或规则本身是推断/待确认。

当前策略是：**未知不等于不合格**。未知会进入人工复核，而不是直接被淘汰。

### 1.3 模型只做证据提取与辅助评分

- Tier 0/1 不调用大模型；
- Tier 2 使用本地 BGE，不联网；
- Tier 3 才调用 Qwen；
- Qwen 输出必须通过结构化 Schema、维度完整性、原文引用一致性校验；
- 任何模型输出都不能直接授权联系候选人。

### 1.4 输入快照不可变

候选人评分输入来自 sealed résumé snapshot：

- `resume_snapshots` 保存原始与归一化简历；
- `job_candidate_inputs` / `job_candidate_input_versions` 记录当前封存输入；
- 评分结果绑定 `resume_id`、`resume_content_hash`、`policy_id`、`policy_version`；
- PARTIAL 简历不会被当成 COMPLETE 强制评分。

---

## 2. 一次评分的完整生命周期

### 2.1 手动导入或浏览器采集

候选人进入系统时：

1. 生成或复用稳定候选人身份；
2. 写入 `resume_snapshots`；
3. 校验简历完整性：
   - `COMPLETE`；
   - `is_scorable=1`；
   - 无 collection errors；
   - 无 mapping data issues；
4. 写入 `job_candidate_inputs` / `job_candidate_input_versions`；
5. 候选人状态变为 **Sealed**。

PARTIAL 简历可以保存用于诊断，但不会成为正式评分输入。

### 2.2 点击 Fast 初筛

执行：

```text
Tier 0 硬条件
  -> Tier 1 规则粗排
  -> 输出 COARSE_ONLY / HARD_FILTERED / NEEDS_REVIEW / DATA_INCOMPLETE
```

不调用 BGE，不调用 Qwen。

### 2.3 点击 Deep 深评

执行：

```text
Tier 0 硬条件
  -> Tier 1 规则粗排 + coarse_pass
  -> Tier 2 BGE 语义评分
  -> Tier 2 完整性门槛
  -> Tier 3 Qwen 终审
  -> DEEP_SCORED
```

其中 Tier 2/3 任一步技术失败，不会生成正式分数，而是记录技术失败并保留可重试状态。

---

## 3. Policy 编译与执行计划

Policy 包含：

- `job`：岗位标题与摘要；
- `requirements`：原子要求；
- `requirement_groups`：ALL / ANY 关系；
- `hard_filters`：客观字段硬条件；
- `keyword_groups`：Tier 1 关键词信号；
- `semantic_anchors`：Tier 2 正向/负向语义锚点；
- `scoring_dimensions`：评分维度、权重与 Rubric；
- `thresholds`：
  - `coarse_pass`：Tier 1 粗排门槛；
  - `deep_review`：Deep 人工复核线；
  - `deep_recommend`：Deep 推荐线。

`PolicyInterpreter` 会把 Policy 编译成：

- requirement 图根节点；
- 关键词到原子要求/维度的映射；
- 可执行硬条件；
- 每维度的关键词预算；
- 语义锚点与维度关系。

编译时会检查：

- Policy hash 是否一致；
- 维度 ID 是否与 Policy 一致；
- requirement/group 引用是否存在；
- 是否存在环；
- 语义锚点、关键词、维度是否完整。

---

## 4. Tier 0：客观硬条件

### 4.1 哪些条件可以成为硬条件

只有满足以下条件的 requirement 才可执行硬拒绝：

- `priority=REQUIRED`；
- `source_type != INFERENCE`；
- `needs_confirmation=false`；
- 绑定至少一个 typed hard filter。

偏好、可选、模型推断、待确认的要求**不能**用于硬淘汰。

### 4.2 支持的客观字段

当前 typed hard filter 支持：

- 学历 `education_level`；
- 工作年限 `work_years`；
- 期望薪资 `expected_salary`；
- 城市 `city`；
- 工作授权 `work_authorization`；
- 求职状态 `job_status`。

### 4.3 三态计算

每个原子条件得到：

```text
PASS / FAIL / UNKNOWN
```

组合规则：

- `ALL`：任一 `FAIL` => `FAIL`；否则任一 `UNKNOWN` => `UNKNOWN`；否则 `PASS`；
- `ANY`：任一 `PASS` => `PASS`；否则任一 `UNKNOWN` => `UNKNOWN`；否则 `FAIL`。

保留 UNKNOWN 的原因：

- 简历没写薪资，不等于薪资超预算；
- 学历写法无法识别，不等于学历不达标；
- 工作授权缺失，不等于没有授权。

### 4.4 薪资与年限解析

不是做字符串比较，而是解析成区间：

- 工作年限：`5年`、`0.4年`、`3年以上`；
- 薪资：`12K/月`、`9-11K`、`年薪20万`、`8千/月`。

薪资会保留：

- 原始周期：月/年/天/小时；
- 下限/上限；
- 年薪月数。

周期不一致会进入 UNKNOWN 或人工复核，而不是静默换算。

### 4.5 Tier 0 输出

```text
PASS          -> 继续 Tier 1
FAIL          -> HARD_FILTERED
UNKNOWN       -> HARD_UNKNOWN / NEEDS_REVIEW
```

---

## 5. Tier 1：规则初筛与粗排

### 5.1 证据来源

Tier 1 从简历中读取：

- 工作职责；
- 项目描述；
- 项目成果；
- 论文/学术描述；
- 工作标签；
- 技能；
- 证书；
- 个人优势。

不同来源有不同权重，当前执行配置：

```text
work        1.20
project     1.20
thesis      1.00
work_tags   0.35
skills      1.15
advantage   0.40
```

技能权重提高后，学生项目型简历不会因为缺少正式工作经历而被过度惩罚，但项目实践仍然高于纯技能自述。

### 5.2 关键词匹配

Policy 中的关键词组会聚合成 canonical keyword：

```text
canonical + aliases
```

匹配时：

1. 在简历各字段中找关键词/别名；
2. 判断上下文：
   - `PRACTICE`：有具体动作、实现、交付；
   - `ASSERTED`：自述“熟悉/掌握”；
   - `LEARNING`：正在学习/计划学习；
   - `MENTION`：仅提及；
   - `NEGATED` / `NO_PRACTICE`：否定或“没有实践”；
3. 每个 canonical keyword 只保留**最强证据**；
4. 只有能与 Policy 原子要求唯一对应的关键词，才计入 requirement matched。

### 5.3 单维度得分公式

对每个评分维度：

```text
budget          = 该维度所有关键词权重之和
matched_budget  = 已命中关键词权重之和
raw             = Σ(命中关键词权重 × 证据来源权重)
quality         = min(1, raw / (matched_budget × max_source_weight))
coverage        = (1 - exp(-matched_budget / budget)) / (1 - exp(-1))
dimension_score = 100 × coverage × quality
```

解释：

- `coverage`：该维度关键词覆盖得够不够；
- `quality`：命中的证据是否来自高可信来源、是否有实践上下文；
- 只有“覆盖高 + 质量高”才拿高分；
- 纯技能堆砌 coverage 可能高，但 quality 低。

### 5.4 总分公式

```text
preliminary_score = Σ(dimension.weight × dimension.normalized_score)
```

维度权重来自 Policy，而不是代码写死。

### 5.5 粗排判断

```text
preliminary_score >= policy.thresholds.coarse_pass
```

- 是：进入 Tier 2；
- 否：`COARSE_ONLY`，Deep 不调用 BGE/Qwen。

这正是 Fast 初筛的速度优势：只看规则，不做模型推理。

---

## 6. Tier 2：BGE 向量语义评分

### 6.1 嵌入模型

当前配置：

```text
model: BAAI/bge-large-zh-v1.5
pooling: CLS
dimension: 1024
max_length: 512
normalization: L2
dtype: float32
text normalization: NFKC + whitespace
```

文本先归一化，再编码；向量写入 SQLite 前会校验：

- float32；
- 非 NaN/Inf；
- 维度正确；
- L2 范数；
- 向量 hash。

### 6.2 简历切块

简历按字段与句子边界切成 chunk：

```text
max_characters: 320
min_characters: 8
overlap: 0
strategy: FIELD_THEN_SENTENCE
```

每个 chunk 保存：

- source_path；
- source_type；
- start/end offset；
- 原始文本；
- 文本 hash；
- 证据质量类型。

评分前会重新验证每个 chunk 仍是 sealed snapshot 的逐字子串，防止旧向量对应到被替换的简历文本。

### 6.3 语义锚点

Policy 中每个要求可以有：

- POSITIVE anchor：该要求的能力描述；
- NEGATIVE anchor：反例/弱相关描述。

正负锚点都编码成向量。Tier 2 不采用自由生成，而是固定 Policy 锚点检索，保证可复现。

### 6.4 相似度映射

对每个 chunk 与锚点计算 cosine similarity。

相似度映射到 0～1：

```text
mapped = clip((similarity - similarity_min) / (similarity_max - similarity_min), 0, 1)
```

当前：

```text
similarity_min = 0.42
similarity_max = 0.80
```

低于 0.42 视为没有有效语义证据，而不是 0 分事实。

### 6.5 Top-K 证据聚合

每个 requirement 会：

1. 选择最相关的 chunk；
2. 按来源权重计算净信号：

```text
net_signal = max(0, mapped_similarity - negative_penalty) × source_weight × 100
```

3. 去重：
   - 相同文本只保留一个；
   - 同一段经历只保留最强 chunk；
4. 在实际实践证据与辅助自述证据之间选更强的一组；
5. 取 Top-K：

```text
top_k = 3
weights = [0.65, 0.25, 0.10]
```

requirement semantic score：

```text
Σ(top_k_signal × top_k_weight) / Σ(top_k_weight)
```

### 6.6 局部负向惩罚

负向锚点不是一票否决，而是局部降权：

```text
negative_threshold = 0.62
negative_margin = 0.04
negative_baseline = 0.55
penalty_weight = 1.45454545
penalty_cap = 0.21818182
```

只有当负向相似度足够高，且接近正向相似度时才惩罚；惩罚上限受控，避免把“缺少某技能”误判为“绝对不胜任”。

### 6.7 维度得分与覆盖率

维度得分是维度内被选中 requirement 得分的算术平均：

```text
dimension_semantic_score = mean(selected_requirement_scores)
```

覆盖率：

```text
dimension_coverage =
    covered_requirements / selected_requirement_leaves
```

总语义分按维度权重加权：

```text
semantic_score =
    Σ(dimension.weight × dimension.semantic_score)
    / Σ(dimension.weight)
```

总覆盖率：

```text
semantic_coverage = Σ(dimension.weight × dimension.coverage)
```

### 6.8 Tier 2 状态

- `SEMANTIC_SCORED`：覆盖率达到 1.0，所有维度 `SCORED`；
- `PARTIAL_SEMANTIC`：部分锚点缺失或部分维度不可用；
- `DATA_INCOMPLETE`：输入不完整；
- `ASSET_INCOMPLETE`：向量资产未准备；
- `SCORING_FAILED`：语义执行失败。

Deep 模式下，只有 `SEMANTIC_SCORED` 且覆盖率 = 1 才继续 Tier 3。

---

## 7. Tier 3：Qwen 结构化终审

### 7.1 调用方式

- OpenAI-compatible 网关；
- 模型来自 `LLM_MODEL_NAME`，当前为 Qwen；
- timeout 120 秒；
- `enable_thinking=false`，关闭慢思考；
- 如网关不支持 `extra_body`，自动回退后再重试；
- 结构化输出 `json_schema`；
- 失败重试一次后仍超时/连接失败，进入本地兜底。

### 7.2 输入

只传入与岗位相关、可引用的简历字段：

- 工作职责；
- 项目描述/成果；
- 教育相关信息；
- 技能；
- 期望薪资；
- 求职状态。

不传姓名、年龄、性别、婚育、籍贯、照片等敏感信息。

### 7.3 Prompt 约束

Tier 3 要求模型：

- 只按 Policy 维度与 Rubric 打分；
- 每个维度输出 0～100；
- evidence_quotes 必须是简历原文的逐字连续片段；
- 禁止改写、拼接、编造引用；
- 无证据时低分、低置信度；
- 存在 missing_evidence 时置信度受限；
- 只返回指定 JSON Schema。

### 7.4 输出校验

模型输出不是直接采信，而是逐项校验：

1. 维度 ID 必须与 Policy 完全一致；
2. 维度顺序一致；
3. 引用必须是 sealed 简历字段的逐字子串；
4. 无证据时：
   - score ≤ 60；
   - confidence ≤ 0.5；
5. 存在 missing evidence 时：
   - confidence ≤ 0.8；
6. 标签与权重必须来自 Policy；
7. 总分必须等于 Policy 权重加权和；
8. 路由必须符合 Policy thresholds。

不满足任一项，该次 LLM 结果不能成为正式分数。

### 7.5 Tier 3 总分与路由

```text
tier3_total = Σ(dimension.score × dimension.weight) / Σ(dimension.weight)
```

路由：

```text
total >= deep_recommend  -> DEEP_RECOMMEND
total >= deep_review     -> HUMAN_REVIEW
否则                      -> NOT_RECOMMENDED
```

注意：`routing_suggestion` 只是排序/复核建议，**不授权联系候选人**。

### 7.6 超时与本地兜底

网络超时、连接失败或重试后仍失败时：

- 使用已经存在的 Tier 1 规则命中与 Tier 2 语义证据；
- 从 sealed 简历中选取可核验原文（quote + source_path）；
- 生成结构化维度评分；
- 标记 `fallback=true`、`status=DEGRADED`；
- 仍然产出 `DEEP_SCORED`，避免网络抖动卡死整条流水线。

兜底不是伪造引用：引用的仍是封存简历原文。

---

## 8. 完整决策矩阵

| Tier 0 / Tier 1 状态 | Fast 模式 | Deep 模式 |
| --- | --- | --- |
| HARD_FILTERED | HARD_FILTERED | HARD_FILTERED，不调用 BGE/Qwen |
| DATA_INCOMPLETE / SCORING_FAILED | DATA_INCOMPLETE | DATA_INCOMPLETE，不调用 BGE/Qwen |
| HARD_UNKNOWN | NEEDS_REVIEW | NEEDS_REVIEW，可生成 Tier 2 证据但不调用 Qwen |
| BELOW_COARSE | COARSE_ONLY | COARSE_ONLY，不调用 BGE/Qwen |
| READY_FOR_DEEP + Tier 2 不完整 | — | NEEDS_REVIEW，不调用 Qwen |
| READY_FOR_DEEP + Tier 2 完整 | — | DEEP_SCORED（Tier 3） |
| 任一阶段技术异常 | TECHNICAL_FAILED | TECHNICAL_FAILED，不写入正式评估 |

---

## 9. Fast 与 Deep 的区别

### Fast 初筛

- 只执行 Tier 0 + Tier 1；
- 不加载 BGE；
- 不调用 Qwen；
- 输出 `COARSE_ONLY` 或提前终止状态；
- 用于大批量快速排序。

### Deep 深评

- 必须通过 Tier 0 + Tier 1 coarse 门槛；
- 加载已准备的 BGE 向量；
- 校验 Tier 2 覆盖率和维度完整性；
- 调用 Qwen 结构化终审；
- 输出 `DEEP_SCORED` 和最终加权分。

**最终 Deep 分数来自 Tier 3，不是 Tier 1 + Tier 2 + Tier 3 简单相加。**

---

## 10. 存储与可复现性

每次正式评分记录：

- org/job/job_candidate/candidate；
- policy_id/version/hash；
- resume_id/content_hash；
- scoring_mode/status/total_score/routing；
- Tier 0 硬条件结果；
- Tier 1 维度、证据、关键词命中；
- Tier 2 锚点、chunk、相似度、覆盖率；
- Tier 3 维度评分、引用、missing evidence、LLM metrics。

正式评估不可变；重评会生成新记录，而不是覆盖历史。

同一个任务通过 idempotency key + policy version + resume snapshot 复用；删除候选人后重新导入会生成新的 job_candidate，旧评分不会带入。

---

## 11. 当前已知边界

1. 真实 BOSS 页面 DOM 选择器仍需真机契约验证；
2. Tier 3 依赖外部 Qwen 网关，网络质量会影响时延；
3. Tier 2 覆盖率不足时不会强行给高分，而是进入 `NEEDS_REVIEW`；
4. 关键词同义词、Policy 标签与语义锚点质量直接决定初筛区分度；
5. 硬条件只对已明确结构化字段执行，未知信息保留人工复核；
6. HR 最终判断与联系动作不由模型自动完成。
