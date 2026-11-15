# Phase 1.7 Stage 5 — Step 1 审计与技术设计

日期：2026-11-15  
范围：旧 Tier 2、Stage 3 Policy、Stage 4 绑定链、SQLite v7。本文只固化设计，不把设计结果冒充实现或真实 BGE 验收。

## 1. 已核实基线

- 当前实际入口是 `HybridScoringEngine`，旧 Tier 2 位于 `engine/semantic_ranker.py`；项目不存在本阶段可直接复用的通用 Policy Tier 2。
- 现有数据库 schema 为 v7，尚无 Embedding 业务表。
- 本地模型路径实际指向 Hugging Face 快照 `BAAI/bge-large-zh-v1.5@79e7739b6ab944e86d6171e44d24c997fc1e0116`，包含模型、Tokenizer 和 Pooling 文件，因此该次真实验收可以标为 `PINNED`，不能继续沿用旧配置中的 `local-unpinned` 别名作为资产身份。
- Step 1 后实际运行 `python -m unittest discover -s tests -p "test_*.py" -v`：373 项，全部通过，耗时 72.703 秒。
- Stage 3 三份 Policy 是保存的真实 Qwen 编译产物；Stage 4 固定候选画像是合成测试数据。真实 BGE 运行只能证明模型调用、资产与算法链可执行，不能证明招聘准确率或真实性。

## 2. 旧 Tier 2 审计

### 2.1 分块和来源

旧实现从 `work / project / thesis / work_tags / skills / advantage` 投影文本，按换行及中英文句末标点切句，再按 320 字符无重叠窗口切块；短于 8 字符的块丢弃。去标点和 casefold 后相同的片段只保留来源权重最高者。

旧来源权重：

| 来源 | 权重 | 语义角色 |
| --- | ---: | --- |
| work | 1.00 | 实践 |
| project | 1.00 | 实践 |
| thesis | 0.90 | 实践 |
| work_tags | 0.15 | 辅助 |
| skills | 0.20 | 辅助 |
| advantage | 0.30 | 辅助 |

可复用的是来源分级与重复片段抑制。不能复用的是拼接后的 `skills` / `work_tags` 作为“连续原文”以及开发岗位专属动作词；新分块必须保存原字段、列表项和字符偏移。

### 2.2 旧相似度、惩罚和 Top-K

旧向量在模型侧 L2 归一化，点积作为余弦相似度：

```text
mapped55 = clip((positive_similarity - 0.42) / (0.80 - 0.42), 0, 1) * 55
```

同一块内，负向最大相似度满足 `neg > 0.62` 且 `neg >= pos - 0.04` 时：

```text
penalty55 = min(12, max(0, (neg - 0.55) * 80))
net55 = max(0, mapped55 * source_weight - penalty55 * source_weight)
```

每条固定路线先按经历归并，一个经历只留最高块；实践与辅助分别 Top-K（K=3，权重 0.65/0.25/0.10），各自按已使用权重重新归一化，最后取两组较高值。Agent/RPA 两路线最后再取较高值。

可复用：L2、余弦、同块局部惩罚、惩罚上限、同经历一槽、实践/辅助分流、Top-K、稳定排序和完整诊断。必须替换：固定 Agent/RPA 键、所有负锚点对所有路线可见、路线内全锚点最大值、构造引擎时重复编码锚点、0–55 与 Tier 1 直接相加。

## 3. Policy v1 的已确认缺口

Policy v1 足以驱动通用执行，但不能保证全维度完整语义覆盖：

1. `SemanticAnchor` 只含 `requirement_ids`，没有 dimension、锚点权重或“一条锚点覆盖多个要求时的逻辑”。
2. AI、Java、销售三份 Policy 都存在维度引用的 requirement 没有正向锚点。
3. Java `dim_test_quality` 只有负向锚点；销售 `dim_solution_comm` 完全没有正向锚点。
4. 同一锚点可引用多个 requirement；一次高相似不能证明全部原子要求成立。
5. 同一 requirement 可进入多个维度；例如 Java 技术锚点经 `req_java_dev` 也能间接关联“业务沟通”维度，但文本本身并不能证明沟通能力。

兼容规则：

- 只使用 Policy 明示的 `anchor.requirement_ids`，不从 label、Rubric 或 Qwen 临时补造。
- 多 requirement 锚点标为 `MULTI_REQUIREMENT_BROAD`，只提供共享软信号，不宣称每个原子要求已独立验证。
- 无正向锚点标为 `POLICY_ANCHOR_MISSING`，分数为 `null`；不能把配置缺口记成候选人 0 分。
- 只有负向锚点不能独立生成“不具备能力”的结论。
- 三份现有 Policy 的真实验收预期为 `PARTIAL_SEMANTIC`，除非未来发布锚点覆盖完整的新版本。

## 4. 新架构与身份边界

```text
AccessContext + job_id + job_candidate_id
  -> Repository 授权并读取候选人首次绑定 policy_id/version
  -> PolicyInterpreter 校验不可变 Policy
  -> 读取封存 resume_id/content_hash
  -> Policy anchor manifest + Resume chunk manifest
  -> 同 embedding_profile 的合法向量资产
  -> requirement 局部相似度
  -> requirement_groups 的 ALL/ANY 软聚合
  -> Policy 动态维度
  -> SemanticEvaluation（TIER2_ONLY）
```

Stage 4 与 Stage 5 只通过下列不可变身份元组关联：

```text
org_id + job_id + job_candidate_id + candidate_id
+ policy_id + policy_version + policy_hash
+ resume_id + resume_content_hash
```

Tier 2 不读取 `jobs.current_policy_id` 替换历史绑定，不写正式 `evaluations`，不改变 Stage 4 状态，不触发 Qwen/AUTO_GREET，也不与 Tier 1 相加。

## 5. Embedding Profile 与缓存键

`embedding_profile_id` 对应一个不可变、严格校验并取 canonical JSON hash 的编码契约，至少包含：

- model name、实际 revision 及 `PINNED/UNPINNED`；
- tokenizer revision、pooling；
- 文本 normalizer、输入前缀；
- max length、截断策略；
- 是否 L2 normalize；
- dimension、dtype=float32、byte_order=little。

逻辑锚点引用键：

```text
org_id + job_id + policy_id + policy_version + anchor_id + embedding_profile_id
```

物理向量复用键：

```text
org_id + embedding_profile_id + encoded_text_hash
```

候选块逻辑键：

```text
org_id + candidate_id + resume_id + chunker_version + chunk_id + embedding_profile_id
```

`encoded_text_hash` 针对实际送模字符串（归一化后文本加输入前缀），不含 Policy 权重、阈值或整份 Policy hash。不同组织不共享资产；同组织相同文本和编码契约可以在不同岗位间复用，但语义评分结果不复用。

## 6. Migration 008 方案

只追加 `008_semantic_embeddings.sql`，不修改 001–007：

| 表 | 作用和关键约束 |
| --- | --- |
| embedding_profiles | 组织内不可变编码契约；PK(org, profile_id)，UNIQUE(org, profile_hash) |
| embedding_assets | float32 BLOB；UNIQUE(org, profile_id, encoded_text_hash)；BLOB 长度=dimension*4；不可改删 |
| policy_anchors | Policy JSON 的不可变投影；复合 FK 到精确 policy/version；触发器核对 JSON 中 ID、文本、极性和 refs |
| policy_embeddings | 锚点逻辑引用；复合 FK 同时锁定 anchor text hash 与 asset/profile/text hash |
| policy_embedding_manifests | 完整 READY 清单；插入前检查该 Policy 全部锚点恰有合法映射 |
| resume_chunks | 封存 Resume 的稳定分块、路径、字段内偏移、经历键、质量类型和文本 hash；不可改删 |
| resume_chunk_manifests | 一次完整分块的不可变清单，避免部分块被当成完整输入 |
| candidate_embeddings | Resume chunk 到 asset 的逻辑引用，允许同组织跨岗位复用物理向量 |
| resume_embedding_manifests | 指定 Resume/chunker/profile 下全部 chunk 都有合法向量后才生成 READY 清单 |
| embedding_build_attempts | 追加式成功/失败诊断；失败记录不会产生 READY 清单 |

SQLite 只能检查 BLOB 长度等结构，不能原生证明 float32 无 NaN/Inf、非零范数或 SHA-256 正确。可信 Python 序列化器在写入和读取时都校验维度、有限性、范数、内容 hash；数据库保存验证器版本。模型推理在写事务外，短 `BEGIN IMMEDIATE` 内按物理键查询/验证/插入，禁止 `INSERT OR REPLACE` 和未经验证的 `INSERT OR IGNORE`。

历史发布门控不追改。已发布但无向量的 Policy 通过显式幂等资产准备生成独立 manifest；不回写 `policy_publications` 或 Policy JSON。

## 7. Resume Chunker v1

- 从工作职责/成果、项目描述/成果、毕设、每个技能标签、每个工作标签和个人优势逐字段投影。
- 实践来源与辅助来源分别标记；技能标签不能升级为实践。
- 句级边界后采用确定性字符窗口；窗口参数进入 `chunker_version/config_hash`。
- 每个 chunk 保存 `source_path`、`source_type`、字段内 start/end、原文 text、text hash、experience_key、evidence_quality_type。
- Top-K 以 `experience_key` 去重；同一经历拆成多个块只能占一个槽。
- 仅使用封存的评分输入；不以新采集 Resume 替换旧输入。

## 8. 确定性语义公式（独立 0–100）

参数置于版本化 semantic profile，不从全局岗位 config 读取。为保留旧算法比例而非针对固定集调分，v1 采用：

```text
mapped = clip((positive_similarity - 0.42) / (0.80 - 0.42), 0, 1) * 100

if negative_similarity > 0.62
and negative_similarity >= positive_similarity - 0.04:
    penalty = min(21.818182,
                  max(0, (negative_similarity - 0.55) * 145.454545))
else:
    penalty = 0

chunk_net = max(0, mapped - penalty) * source_weight
```

其中 `21.818182 = 12/55*100`，`145.454545 = 80/55*100`，只是把旧 55 分口径等比例换成 100 分，不是样本拟合。

原子 Requirement：

1. 只读取显式关联该 requirement 的正/负锚点。
2. 每块取相关正锚点最高值；负向只在同块、同 requirement 范围内局部比较。
3. 每个 experience 只留最高 `chunk_net`。
4. 实践与辅助分别 Top-K，`K=3`、权重 `0.65/0.25/0.10`，再取两组较高者；辅助来源因 source weight 上限不能达到实践满分。
5. 有正锚点但无有效相似证据时可合法得到 0；没有正锚点时为 `null/POLICY_ANCHOR_MISSING`。

ALL/ANY：

- ALL：保留全部成员；对可评分成员取算术平均，同时单独输出 `scored_leaf_count / total_leaf_count`。不可评分成员不被偷偷当候选人 0 分，高分也不能覆盖其缺口。
- ANY：计算并保留全部分支，选择可评分软分最高者；同分按稳定 ID；未选分支仍进诊断。选中分支的 coverage 决定该 ANY 的 coverage。
- 每个动态维度先把自身 requirement_ids 投影到 Policy DAG，再执行 ALL/ANY；不能把 Spring Boot 与 Quarkus、Agent 与 RPA 等替代路线误作同时必需。

维度与总分：

```text
dimension_score = 该维度投影图的软分
dimension_coverage = 已有正锚点且可评分的叶 / 维度投影叶

semantic_score =
  sum(dimension_score * dimension_weight for 可评分维度)
  / sum(dimension_weight for 可评分维度)

semantic_coverage =
  sum(dimension_coverage * dimension_weight for 全部维度)
```

这使配置缺锚点保持“未知/部分覆盖”，而不是伪造成候选人不匹配。`semantic_score` 只描述已配置锚点覆盖部分的软相似度；只有输入完整且 coverage=1 才能标 `SEMANTIC_SCORED`，否则有可执行部分时标 `PARTIAL_SEMANTIC`。资产损坏、模型失败或完全不可执行时不返回伪 0 分。

## 9. SemanticEvaluation 契约

新增严格 `schemas/semantic.py`，顶层包含完整身份、profile/chunker/semantic 版本及 hash、`score_kind=POLICY_TIER2_SEMANTIC`、`assessment_scope=TIER2_ONLY`、状态、可空总分、coverage、动态维度、原子 requirement、ALL/ANY 分支、正负证据、缺口、review reasons 和 debug trace。

状态：`SEMANTIC_SCORED / PARTIAL_SEMANTIC / DATA_INCOMPLETE / ASSET_INCOMPLETE / SCORING_FAILED`。失败或资产不完整不能产生成功状态或伪 0 分。所有证据必须重新解析到原始 snapshot 的 exact path + offsets；结果明确 `routing_authority=false`、`hard_filter_override=false`、`formal_evaluation=false`。

## 10. Step 1 结论和下一步门槛

该设计不需要修改已发布 Policy、Stage 4 公式或旧 Migration。Step 2 只实现严格 Profile、float32 序列化、Migration 008 与 Repository，并用临时真实 SQLite 验证幂等、不可变、哈希、重启和并发；通过后才进入岗位锚点预计算。
