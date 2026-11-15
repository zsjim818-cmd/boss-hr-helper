# Phase 1.7 Stage 5 — Trae 接管审计报告

日期：2026-11-15
性质：只读接管审计。本报告只记录已通过代码、数据库、日志或真实模型运行验证的事实；不把计划当作实现，不把 Mock 当作真实模型验收。
工具：TraeCode（接管前由 Codex 开发）。

---

## 1. 项目实际目录与运行环境

- 项目根目录：`d:\Boss-project\boss-qhr-helper-dev`（与指令一致）。
- 虚拟环境：项目自带 `.venv`（未重建、未升级依赖）。
- 实测解释器与依赖：Python 3.11.14；sentence-transformers 6.3.0；torch 2.15.0+cpu；numpy 2.4.6；pydantic 2.14.3。
- 业务数据库：`data/recruitment.sqlite3`（文件实际存在）。
- 模型资产：`.env` 的 `EMBEDDING_MODEL_PATH` 指向本地 pinned 快照
  `D:\model\bge-large-zh-v1.5\hub\models--BAAI--bge-large-zh-v1.5\snapshots\79e7739b6ab944e86d6171e44d24c997fc1e0116`。
  本报告不输出任何 API Key 或 `.env` 敏感内容。

## 2. 当前 Git 状态

- `.git` 已初始化，分支 `master`，**尚无任何 commit**；所有文件均处于 untracked 状态。
- 影响：无法用 git 历史做差异比对，也没有可回退的基线。
- 处理策略：接管期间**不**擅自提交、**不**做破坏性操作。若需要版本保护，建议在用户确认后再决定是否创建首个提交。本次仅做增量、可回退的文件修改。

## 3. 已完成阶段及证据

| 阶段 | 证据目录 | 关键产物 |
| --- | --- | --- |
| Stage 1 代码审计与架构设计 | `reports/phase1_7_stage1/` | `code_audit.md`、`database_design.md`、`source_manifest.json`、`baseline_tests.log`（旧基线 373 项） |
| Stage 2 SQLite 持久化 | `reports/phase1_7_stage2/` | `implementation.md`、`database_migrate.json`、迁移 001–005、Repository 层 |
| Stage 3 通用 Policy Compiler | `reports/phase1_7_stage3/` | `implementation.md`、`policies/{ai_automation,java_backend,b2b_sales}.json`、`live/*` 真实 Qwen 编译产物 |
| Stage 4 Policy Interpreter + Tier0/1 | `reports/phase1_7_stage4/` | `implementation.md`、`PreliminaryEvaluation.schema.json`、`execution_design.md`、`benchmark_*` |
| Stage 5 通用 Tier2 语义 | `reports/phase1_7_stage5/` | **仅 `step1_audit_design.md`** |

实测回归基线：在本机运行 `python main.py test -q` → **408 项测试全部通过（0 失败 / 0 错误），耗时 114.3 秒**。
（Stage 4 交接记录为 373 项；Stage 5 已新增约 35 项定向测试。）

## 4. Stage 5 七个步骤的实际完成状态

| 步骤 | 内容 | 实际状态 | 判定依据 |
| --- | --- | --- | --- |
| Step 1 | 旧 Tier2 审计与设计 | 完成 | `reports/phase1_7_stage5/step1_audit_design.md` |
| Step 2 | Embedding Profile 与 DB 持久化 | 完成 | `storage/database/migrations/008_semantic_embeddings.sql`、`storage/repositories/embeddings.py`、`schemas/semantic.py`、`engine/embedding_vectors.py` |
| Step 3 | 岗位语义锚点预计算 | 完成 | `engine/embedding_service.py` 的 `PolicyEmbeddingService` |
| Step 4 | 简历分块与 Embedding 缓存 | 完成 | `engine/embedding_service.py` 的 `ResumeEmbeddingService`、`engine/resume_chunker.py` |
| Step 5 | 通用语义评分引擎 | **实现完成**（返回 `SemanticComputation`） | `engine/policy_semantic_ranker.py::evaluate_policy_semantics` |
| Step 6 | SemanticScoringService 与 CLI 接入 | **未开始** | 全仓库**不存在** `SemanticScoringService`；`main.py` 无 `embedding-check/semantic-prepare/semantic-diagnose` 命令 |
| Step 7 | 全链路测试与真实 BGE 验收 | **未开始** | `reports/phase1_7_stage5/` 无 Step5/6/7 交付日志 |

### 4.1 关键细节：Step 5 只到「计算」，未到「评估契约」

- `schemas/semantic.py` 定义了严格的 `SemanticEvaluation` 顶层契约，但全仓库**从未构造** `SemanticEvaluation`（grep 仅有定义处命中）。
- 现有 Step 5 只产出内部 `SemanticComputation` dataclass。
- 因此 Step 6 的核心缺口之一是：把 `SemanticComputation` + 不可变身份元组组装为合法 `SemanticEvaluation`，并封装服务层与 CLI。

### 4.2 交接清单与实际文件名的偏差（重要）

指令中列出的 Stage 5 文件（`baseline_v1.json`、`benchmark_comparison.md`、`benchmark_v2_mock.json`、`delivery_v2.md`、`llm_live_diagnosis.md`、`local_bge_fast.json`、`phase_validation.md`、`single_resume_mock.log`、`tests_final.log`、`policy_schema.json` 等）**实际位于 `reports/` 根目录**，且经核对属于 **Phase 1.5 Scoring Engine V2** 阶段产物（例如 `delivery_v2.md` 标题即「Phase 1.5 Scoring Engine V2 交付说明」），并非 Stage 5 产物。
结论：**Stage 5 没有任何 Step5/6/7 的持久化交付报告**，仅有一份 `step1_audit_design.md`。交接描述中的 "Step 5 测试日志、Step 6 审计、Step 7 计划" 均无对应文件可核验（可能仅存在于 Codex 会话中）。

## 5. Migration 008 当前状态

- 实测 `python main.py db-check`：
  - `schema_version = 7`
  - `pending_migrations = ["008_semantic_embeddings.sql"]`
  - `integrity_check = ok`，`foreign_key_violations = []`
- 结论：**Migration 008 尚未应用到任何真实数据库**（`data/recruitment.sqlite3` 仍为 v7）。
- 依据指令，008 尚未应用时**允许在应用前修正设计**；已应用的 001–007 不得修改。
- 迁移框架要求名称/校验和/`schema_hash`/`user_version` 严格一致（`storage/database/connection.py`），因此修改 008 是安全且可复现的。

## 6. BGE Pooling 元数据核验结论（最高优先级项）

### 6.1 配置文件证据

模型快照 `79e7739b...`：

- `1_Pooling/config.json`：`pooling_mode_cls_token = true`，`pooling_mode_mean_tokens = false`，`word_embedding_dimension = 1024`。
- `modules.json`：`Transformer` → `Pooling` → `Normalize`（即：CLS 池化 + L2 归一化）。
- `sentence_bert_config.json`：`max_seq_length = 512`，`do_lower_case = true`。

### 6.2 真实运行时交叉验证（不是仅凭配置推断）

实际加载 `SentenceTransformer(pinned_snapshot)`（CPU、离线）并编码真实中文文本，结果：

- 运行时模块顺序：`[(0,'Transformer'), (1,'Pooling'), (2,'Normalize')]`
- Pooling 模块 `pooling_mode = 'cls'`，`embedding_dimension = 1024`
- 输出 `shape=(1024,)`，`dtype=float32`
- 输出向量原始范数 `= 1.0`（说明模型内 `Normalize` 模块已做 L2 归一化）
- 与手工池化对比余弦相似度：
  - `cos(模型输出, CLS池化)` = **1.0000001**（≈1）
  - `cos(模型输出, MEAN池化)` = **0.8611**

### 6.3 结论

- **模型实际使用 CLS 池化 + L2 Normalize，维度 1024，max_length 512。**
- 当前 `config/semantic_profile.json` 声明 `pooling_strategy = "CLS"`，`profile_id = bge-large-zh-v1.5-79e7739b-cls-v1`，`l2_normalize = true`，`embedding_dimension = 1024`，`max_length = 512`。
- **因此 Profile 声明与运行时模型行为一致**（交接说明中「Profile 声称 MEAN、模型实际 CLS」的冲突**在当前代码状态中不存在**；Profile 已是 CLS）。

### 6.4 仍然真实存在的可追溯性缺口（需要修复/加固）

- `pooling_strategy` 目前**只作为元数据被存储与参与 profile hash**，运行时**从未被校验**。`encode_texts()` 直接调用 `embedder.encode()`，不读取该字段。
- 风险：若模型被替换或 profile 被改动，`profile_hash` 会变化（缓存会失效，正确），但**没有任何守卫能证明"声明的 pooling == 实际模型的 pooling"**，也无法在运行前发现模型侧漂移。
- 这正是指令第五节要求解决的问题（"Profile 描述的参数 ≠ 实际生成参数" 的可追溯性），因此需要：
  1. 增加**运行时 pooling/维度一致性校验**（读取已加载模型的 Pooling 模块，与 profile 声明比对）；
  2. 增加**回归测试**，避免未来配置漂移；
  3. 将其接入即将实现的 `embedding-check` CLI。

## 7. 当前阻塞问题

- **无硬阻塞**：现有 408 项测试全绿，核心 Step 5 计算引擎可用，数据库可用。
- 需优先处理的**加固项**：第 6.4 节的 pooling 运行时一致性守卫（不是算错，而是缺可追溯性证明）。
- 需补齐的**功能缺口**：Stage 5 Step 6（`SemanticScoringService` + `SemanticEvaluation` 组装 + CLI）与 Step 7（全链路/真实 BGE/缓存/版本隔离/性能）。

## 8. 下一步准备修改/新增的文件

遵循「不推倒重写、不改 Stage 1–4 业务逻辑、不改已发布 Policy、不改已封存简历、不改 Tier1 公式」：

拟**新增**：
- `engine/semantic_verification.py`（或并入 `engine/semantic_profiles.py`）：运行时模型 pooling/维度一致性校验器。
- `engine/semantic_scoring_service.py`：Step 6 服务层（`SemanticScoringService`），复用 `PolicyInterpreter`、`PolicyEmbeddingService`/`ResumeEmbeddingService`、`evaluate_policy_semantics`。
- `engine/semantic_cli.py`：`embedding-check / semantic-prepare / semantic-diagnose` 命令实现。
- 新测试文件：pooling 运行时一致性、profile hash 敏感性、语义服务层、CLI。

拟**修改**：
- `main.py`：注册 3 个新子命令（保持既有风格，延迟 import）。
- `config/semantic_profile.json`：**仅在需要**时改动；当前 CLS 已正确，预计不改 pooling 值（若改动会改变 profile hash → 使缓存失效）。
- `storage/database/migrations/008_semantic_embeddings.sql`：**仅在发现设计缺陷时**改；当前设计完整，预计不改。

**不修改**：`engine/policy_semantic_ranker.py` 的 ALL/ANY 与评分公式、`engine/policy_interpreter.py`、`engine/prescreen_*`、`schemas/prescreen.py`、001–007 迁移、三份已发布 Policy。

## 9. 预计新增测试

1. 运行时 pooling 与 profile 声明一致（真实模型，标记为可选/慢测，模型不可用时 skip）。
2. `embedding_profile_hash` 随 pooling / max_length / l2_normalize / dimension 等**有效编码参数**变化，且不受 `embedding_profile_id`/`profile_version` 影响。
3. 模型输出维度=1024、为有限值、范数≈1。
4. 旧 profile 的缓存不被错误复用（跨 profile 隔离）。
5. `SemanticScoringService`：权限/绑定版本/封存快照/缓存命中/失败状态。
6. CLI：`embedding-check / semantic-prepare / semantic-diagnose` 的成功/失败/异常与退出码，且不泄露敏感信息。

## 10. 明确不做（本阶段边界）

不重写 Stage 1–4；不改 Tier1 公式；不融合 Tier1 与 Tier2 分数；不让语义分覆盖 Tier0 FAIL；不引入岗位分支；不开发 Qwen Tier3 / Vue3 / Playwright / Boss 发送；不升级依赖；不删除或重置数据库；不覆盖既有报告。

---

（本报告为只读接管审计，随后按「先修复 Pooling/Profile 可追溯性 → 再实施 Step 6 → 最后 Step 7」推进。）
