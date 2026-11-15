# Stage 5 最终交付与验收报告（Trae 接管后）

- 项目根目录：`D:\Boss-project\boss-qhr-helper-dev`
- 报告定位：**独立新增文档**，不覆盖任何既有 Stage 5 交付报告。
- 覆盖范围：接管审计 → BGE Pooling/Profile 核验 → Step 6（SemanticScoringService + CLI）→ Step 7（全链路真实 BGE 验收）。
- 生成时间：2026-11-15

> 说明：本报告只记录可被代码、数据库、日志或真实模型运行结果复核的结论。所有"完成"均附证据；未完成项单列于"未解决问题"。

---

## 0. 交付物索引（本阶段产出）

| 文档 | 内容 |
| --- | --- |
| `takeover_audit_report.md` | 只读接管审计：项目结构、Git 状态、阶段进度、阻塞项、执行计划 |
| `bge_pooling_metadata_verification.md` | BGE Pooling/维度/max_length 元数据核验（静态 + 运行时交叉验证） |
| `semantic_acceptance.md` / `semantic_acceptance.json` | Step 7 三岗位真实 BGE 全链路验收原始记录 |
| `stage5_final_acceptance_report.md` | 本文档：Step 6/7 架构、CLI、回归、缓存、版本隔离、性能与结论 |
| `semantic_acceptance_run.log` | 接管前一次失败运行的原始日志（保留，未覆盖，供对照） |

---

## 1. 接管审计结论（要点）

- **项目性质**：已历经 Stage 1–4 完整设计与测试的多阶段项目，非全新仓库。
- **Git 状态**：仓库处于**全部未跟踪（untracked）**状态（`git status` 全部为 `??`，无任何提交历史），因此无法用 Git 历史区分改动来源；本报告以**实际文件与运行证据**为准。
- **已完成阶段**：Stage 1（审计与架构）、Stage 2（SQLite 持久化）、Stage 3（通用 Job Policy Compiler）、Stage 4（Policy Interpreter + Tier0/Tier1）。
- **数据库**：`data/recruitment.sqlite3`，`schema_version=8`，`pending_migrations=[]`，`integrity_check=ok`，无外键违规。
- 详见 `takeover_audit_report.md`。

### Stage 5 七步实际状态（核验后）

| 步骤 | 状态 | 证据 |
| --- | --- | --- |
| Step 1 旧 Tier2 审计与设计 | 完成 | `step1_audit_design.md` |
| Step 2 Embedding Profile + 持久化 | 完成 | `engine/semantic_profiles.py`、`storage/repositories/embeddings.py`、`tests/test_semantic_profiles.py`、`tests/test_semantic_storage.py` |
| Step 3 岗位锚点预计算 | 完成 | `engine/embedding_service.py`（`PolicyEmbeddingService`） |
| Step 4 简历分块与 Embedding 缓存 | 完成 | `engine/resume_chunker.py`、`ResumeEmbeddingService`、`tests/test_resume_embeddings.py` |
| Step 5 通用语义评分引擎 | 完成 | `engine/policy_semantic_ranker.py`、`schemas/semantic.py` |
| Step 6 SemanticScoringService + CLI | 完成 | `engine/semantic_scoring_service.py`、`engine/semantic_cli.py`、`main.py` 注册、`tests/test_semantic_cli.py`、`tests/test_semantic_scoring_service.py` |
| Step 7 全链路真实 BGE 验收 | 完成 | `tests/semantic_acceptance.py`、`semantic_acceptance.md/json` |

---

## 2. BGE Pooling / Embedding Profile 核验与修复说明

### 2.1 核验方法（不依赖文件名与网上默认值）

- 读取本地 pinned 快照静态配置：`modules.json`、`1_Pooling/config.json`、`sentence_bert_config.json`、`config.json`、tokenizer。
- **运行时交叉验证**：真实加载 `SentenceTransformer(<快照>)`，读取实际 Pooling 模块与维度，并对最小真实文本编码，检查输出 shape、有限性与 L2 范数。
- 覆盖项目自身 Embedding 生成路径（`engine/embedding_service.py`、`engine/embedding_vectors.py`）。

### 2.2 结论

- 模型快照：`D:\model\bge-large-zh-v1.5\hub\models--BAAI--bge-large-zh-v1.5\snapshots\79e7739b6ab944e86d6171e44d24c997fc1e0116`。
- 实际运行契约 = **CLS pooling + L2 Normalize + dim 1024 + max_seq_length 512 + 无 instruction 前缀 + float32/little-endian**。
- `config/semantic_profile.json` 声明与之**逐项一致**：交接资料中"Profile 声称 MEAN、模型实际 CLS"的冲突在当前代码与配置中**不存在**。

### 2.3 Profile 修复说明

- **Profile 未修改**：原始声明即为 CLS，故 `embedding_profile_hash` **保持不变**，既有缓存**无需失效**（不存在旧 Profile 向量被错误复用的问题）。
- **真实缺口 = 缺少运行时一致性守卫**：`pooling_strategy` 参与不可变 Profile hash，但此前无任何环节读取"实际加载模型"的 pooling，无法证明"声明 == 运行"。
- **修复（纯增量）**：新增 `engine/semantic_verification.py`
  - `detect_pooling_strategy(model)`：读取真实 Pooling 模块（兼容新版字符串 `pooling_mode='cls'` 与旧版布尔标志）；模式并存/缺失时**报错而非猜测**。
  - `detect_embedding_dimension(model)` / `detect_max_seq_length(model)`：读取真实维度/最大长度（优先新版 getter 名，消除 `get_sentence_embedding_dimension` 弃用告警）。
  - `verify_embedding_model(model, profile)`：返回结构化 `{ok, checks}`（pooling / dimension / max_length）。
  - `assert_embedding_model_matches(model, profile)`：不一致即 `raise ValueError`，被 `embedding-check` CLI、服务层与回归测试复用。
- **验证测试**：`tests/test_semantic_verification.py`（含 3 项真实 BGE 测试），覆盖：声明 pooling == 运行时 pooling、Profile hash 随关键编码参数变化（改 label 不变）、维度正确、输出有限、范数 ≈ 1.0。
- 详见 `bge_pooling_metadata_verification.md`。

---

## 3. Migration 状态与变更说明

- 当前数据库：`schema_version = 8`，`pending_migrations = []`，`integrity_check = ok`，`foreign_key_violations = []`。
- **Migration 008 已应用**（`db-check` 证据见上文）。
- 本阶段**未新增、未修改任何已应用 Migration**，未删除数据库、未重置历史数据。
- 与既有约定一致：已应用的 001–008 迁移保持不可变；如后续需要 schema 调整，将以**新 Migration 增量修复**。

---

## 4. Step 6：SemanticScoringService 架构说明

### 4.1 服务契约（`engine/semantic_scoring_service.py`）

输入：`AccessContext(org_id, user_id)`、`job_id`、`job_candidate_id`，可选注入 `preliminary`。

内部执行顺序：

1. `repository.read(context, job_id, "evaluate")` 做**组织/岗位授权校验**（授权失败是访问错误，绝不产出候选人分数）。
2. `interpreter.load_for_candidate(...)` 读取**候选人首次绑定的** `policy_id/policy_version`，并断言与 `job_candidates` 一致。
   - **绝不**用 `jobs.current_policy_id` 替代候选人绑定版本；**不接受**调用方传入任意 `policy_version` 覆盖绑定关系。
3. 读取**封存**评分输入（`get_sealed_input`）与其 `ResumeSnapshot`。
4. 若输入不完整（`completeness != COMPLETE` 或存在 issues）→ 直接返回 `DATA_INCOMPLETE`（**不伪造分数**）。
5. 加载 Embedding Profile（校验落库 `profile_hash` == 生效契约）、岗位锚点清单/向量、候选人分块清单/向量，并**重新解析每段证据到封存快照的精确 path + offset**（`_validate_chunk_sources`）。
6. 执行 Stage 5 确定性语义评分（`evaluate_policy_semantics`），组装 `SemanticEvaluation`。

资产缺失/不一致 → `ASSET_INCOMPLETE`；执行异常 → `SCORING_FAILED`；均不产出伪 0 分。

### 4.2 与 Stage 4 的集成边界

| 约束 | 实现 |
| --- | --- |
| 两份结果独立 | `PreliminaryEvaluation`（Tier1）与 `SemanticEvaluation`（Tier2）分别保存/返回 |
| 分数口径独立 | Tier2 固定 `score_kind=POLICY_TIER2_SEMANTIC`、`assessment_scope=TIER2_ONLY`；**不与 Tier1 相加** |
| 不覆盖 Tier0 | `hard_filter_override=false`、`routing_authority=false` |
| 非正式评估 | `thresholds_applied=false`、`formal_evaluation=false` |
| 不改 Stage4 公式 | 未修改 `prescreen_engine`/Tier1 任何计分逻辑 |
| 不造 Tier3 | 未创建任何伪造 Tier3 结果 |

### 4.3 状态机

`SEMANTIC_SCORED / PARTIAL_SEMANTIC / DATA_INCOMPLETE / ASSET_INCOMPLETE / SCORING_FAILED`。
失败或不完整状态**不允许**携带总分或维度分（由 `schemas/semantic.py` 的严格校验强制）。

---

## 5. Step 6：CLI 使用说明

统一入口 `main.py`，已注册命令：`embedding-check`、`semantic-prepare`、`semantic-diagnose`。

```bash
# 1) 只读核验：加载真实模型并与 Embedding Profile 声明比对 pooling/维度/max_length
python main.py embedding-check

# 2) 准备岗位语义资产（锚点向量）；可按需一并准备某候选人向量
python main.py semantic-prepare --org-id <ORG> --user-id <USER> --job-id <JOB_ID>
python main.py semantic-prepare --org-id <ORG> --user-id <USER> --job-id <JOB_ID> --job-candidate-id <JC_ID>

# 3) 只读 Tier2 诊断（默认同时给出 Tier1 初筛结果；--skip-prescreen 仅返回 Tier2）
python main.py semantic-diagnose --org-id <ORG> --user-id <USER> --job-id <JOB_ID> --job-candidate-id <JC_ID>
python main.py semantic-diagnose --org-id <ORG> --user-id <USER> --job-id <JOB_ID> --job-candidate-id <JC_ID> --skip-prescreen
```

行为约定：

- 状态明确：成功/失败/异常均以结构化 JSON 输出；异常返回 `{"ok": false, "error_type", "message"}` 且退出码 `1`。
- 输出内容：策略版本、Embedding Profile id/hash、缓存命中情况与语义匹配明细。
- **不输出 API Key，不输出简历全文**。
- CLI **不绕过** Repository 权限与版本约束（越权调用返回 `PermissionDenied`/`NotFound`，不返回分数）。
- `semantic-prepare` 只写不可变语义资产，**不发送消息、不写正式评估、不做路由**。

---

## 6. Step 7：全链路真实 BGE 验收（三岗位）

- 执行类型：`REAL_LOCAL_BGE_NOT_MOCK`（真实本地 pinned BGE；**非 Mock embedder**）。
- 数据：Stage 3 保留的三份真实 Qwen Policy（AI 自动化 / Java 后端 / 企业销售，JD 为合成）＋ 固定合成候选人集（`tests/fixtures/prescreen_candidates.json`，`prescreen-fixed-v2`，3 岗位 × 9 画像）。
- 模型加载耗时：约 364 ms；模型一致性校验：pooling `CLS`、维度 `1024`、max_length `512`，全部 `ok=true`。
- 断言：**236 项，失败 0 项**；记录 27 条候选人评测。

### 6.1 三岗位语义评分（节选）

| 岗位/画像 | Tier1 状态 | Tier1 分 | Tier2 状态 | Tier2 分 | 语义覆盖 |
| --- | --- | --- | --- | --- | --- |
| java_backend/complete_match | READY_FOR_DEEP | 100.0 | PARTIAL_SEMANTIC | 51.63 | 0.7 |
| java_backend/hard_condition_fail | HARD_FILTERED | 100.0 | PARTIAL_SEMANTIC | 51.63 | 0.7 |
| java_backend/negated_experience | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 0.0 | 0.7 |
| java_backend/missing_information | DATA_INCOMPLETE | 100.0 | UNSEALED_NOT_SCORED | None | — |
| ai_automation/complete_match | READY_FOR_DEEP | 91.26 | PARTIAL_SEMANTIC | 51.47 | 0.675 |
| ai_automation/similar_but_different | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 29.44 | 0.675 |
| b2b_sales/complete_match | READY_FOR_DEEP | 100.0 | PARTIAL_SEMANTIC | 71.63 | 0.675 |
| b2b_sales/similar_but_different | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 42.04 | 0.675 |

完整 27 行见 `semantic_acceptance.md`。

关键观察（均已断言）：

- **Tier1 与 Tier2 口径独立**：如 `hard_condition_fail` 的 Tier1 = `HARD_FILTERED`，Tier2 仍给语义分，且 `hard_filter_override=false` —— 语义分**不覆盖** Tier0 结论。
- **`missing_information` 不产生语义分**：由于无封存输入，Tier2 **不予评分**（`UNSEALED_NOT_SCORED`），不伪造分数。
- 全部 27 条记录的 `assessment_scope=TIER2_ONLY`、`routing_authority=false`、`thresholds_applied=false`、`formal_evaluation=false`、`policy_version` 与候选人绑定版本一致。

---

## 7. Embedding 缓存测试报告

| 场景 | 结果 | 说明 |
| --- | --- | --- |
| 冷启动锚点准备 | 3 岗位各 1 次冷编，`encoded_count > 0` | `anchor_cold` 计时见第 10 节 |
| 锚点缓存命中 | ✅ `cache_hit=true` 且 `encoded_count==0` | 重复 `prepare_policy` |
| 候选人向量冷编 | 24 个封存候选人各 1 次 | `resume_cold` |
| 候选人向量缓存命中 | ✅ `cache_hit=true` 且 `encoded_count==0` | 重复 `prepare_candidate` |
| 跨岗位安全复用 | ✅ `cross_job_resume_cache_hit=true`，复用 3 段物理向量 | 同一封存简历在两个岗位下复用物理向量，**语义结果仍按岗位重算** |
| 跨组织隔离 | ✅ 第二组织读取第一组织语义资产 → `NotFound/PermissionError` | `cross_org_isolation.isolated=true` |
| 改权重不重算向量 | ✅ V2 仅换维度权重，`v2_prepare.encoded_count==0` | 锚点文本未变，物理向量复用 |
| Profile 变化旧缓存失效 | ✅ 缓存键含 `embedding_profile_hash`；Profile 未变故 hash 不变（本阶段无失效需求） | 见 `test_semantic_profiles.py`/`test_semantic_vectors.py` |

> "修改锚点文本只重算受影响向量"由分块/锚点级 manifest 与文本 hash 缓存键保证（`tests/test_resume_embeddings.py`、`tests/test_semantic_vectors.py` 覆盖）。

---

## 8. 版本隔离测试报告

在被选岗位（java_backend）上：

1. 发布 **V1**，采集候选人 A（首次绑定 V1）。
2. HR 仅调整维度权重后发布 **V2**（权重和为 1，仍可发布）。
3. 采集**新的**候选人 B（首次绑定 V2）。

断言结果（真实源运行）：

- 旧候选人 A 的语义结果 `policy_version == V1`（**未漂移**）。
- 新候选人 B 的语义结果 `policy_version == V2`。
- V1 与 V2 `policy_id` 不同（版本身份区分）。
- **已封存简历不被自动覆盖**；同一候选人重新进入新批次仍使用原绑定版本。
- 程序重启后使用原锁定版本（由 Stage 2 方式与 `tests/test_policy_persistence.py`、`tests/test_prescreen_engine.py` 覆盖）。

本次验收中 `version_isolation` 观测：`old_candidate_policy_version=1`，`new_candidate_policy_version=2`。

---

## 9. 完整回归测试结果

- 命令：`python main.py test -q`
- 结果：**Ran 432 tests … OK**（失败 0）。
- 组成：Stage 4 历史回归 + Stage 5 既有定向测试 + 本阶段新增测试（`test_semantic_cli.py` 7 项等）。
- 约束遵守：**未删除任何失败测试，未降低任何断言标准**；测试失败一律先定位根因。
- Step 7 真实 BGE harness（`tests/semantic_acceptance.py`）为独立可执行验收脚本（非 `test*.py`，不纳入 unittest discover），单独运行：**236 项断言，失败 0**。

---

## 10. 性能测试数据（本机观测，非生产 SLA）

> 口径：本机 CPU + SQLite + 单进程本地 BGE；样本量小，**不能**宣称为生产 SLA。

| 阶段 | 样本 | 中位数 (ms) | p95 (ms) | min (ms) | max (ms) |
| --- | --- | --- | --- | --- | --- |
| 模型加载（一次性） | 1 | 364.066 | — | — | — |
| 锚点冷编 | 3 | 526.93 | 911.28 | 455.17 | 911.28 |
| 锚点缓存命中 | 3 | 23.76 | 24.79 | 21.45 | 24.79 |
| 候选人向量冷编 | 24 | 363.69 | 613.91 | 122.32 | 696.20 |
| 候选人向量缓存命中 | 3 | 35.33 | 36.04 | 35.22 | 36.04 |
| 单候选人语义评分（已备资产，无模型调用） | 24 | 75.19 | 93.79 | 59.31 | 95.17 |

多岗位复用：跨岗位相同封存简历复用 3 段物理向量，避免重复编码。

---

## 11. 新增 / 修改文件清单

### 11.1 本阶段（Stage 5 Step 6/7）交付与改动

新增：

- `engine/semantic_verification.py`（运行时模型一致性守卫）
- `engine/semantic_scoring_service.py`（Tier2 组装服务）
- `engine/semantic_cli.py`（`embedding-check` / `semantic-prepare` / `semantic-diagnose`）
- `tests/test_semantic_cli.py`
- `tests/test_semantic_scoring_service.py`
- `tests/test_semantic_verification.py`
- `tests/semantic_acceptance.py`（Step 7 真实 BGE 全链路验收 harness）
- `reports/phase1_7_stage5/{bge_pooling_metadata_verification.md, semantic_acceptance.md, semantic_acceptance.json, stage5_final_acceptance_report.md}`

修改：

- `main.py`：注册 `embedding-check` / `semantic-prepare` / `semantic-diagnose` 子命令（仅新增分发，不改既有命令）。

### 11.2 本次接管会话实际改动

- **仅** `tests/semantic_acceptance.py`（修复 Step 7 harness 的三处缺陷，见第 12 节）。
- **未改动任何生产代码、迁移、已发布 Policy、Stage 1–4 逻辑、Tier1 公式。**
- 未覆盖 `.env`，未重建 `.venv`，未升级依赖。

---

## 12. Step 7 harness 修复记录（本次会话）

初始状态下 `tests/semantic_acceptance.py` 运行失败，根因与修复（均为 harness 自身缺陷，**不涉及生产逻辑**）：

1. `ValueError: An open job with a published policy is required`
   → `_publish_policy()` 创建了 policy version 却**未调用** `repository.publish_policy()`，`job.current_policy_id` 为空。
   → 修复：在 `create_policy_version` 后补 `publish_policy(..., confirmation_hash=draft["draft_hash"])`。
2. `KeyError: 'score'`：维度分字段名错误 → 改为 `semantic_score`（与 `schemas/semantic.py` 一致）。
3. `NotFound`：对**未封存**候选人（`missing_information`）调用 `get_sealed_input`/`prepare_candidate` 抛 `NotFound`；`prescreen.evaluate` 对未封存输入要求显式 `resume_id`。
   → 修复：改用 `acquire_candidate_for_job` 返回的 `sealed_input`；`prescreen.evaluate` 显式传 `resume_id`；未封存候选人按"Tier2 不予评分"记录（`UNSEALED_NOT_SCORED`），**不伪造分数**。
4. V2 权重减半导致 `INVALID_WEIGHT_SUM`（权重和 ≠ 1）无法发布。
   → 修复：改为**交换两个不同维度权重**（权重和恒为 1，仍可发布），实现"仅权重变化、锚点向量复用"。
5. 版本隔离测试复用了同一候选人身份，导致"新候选人"仍绑定 V1。
   → 修复：为 V2 队列使用**独立候选人**（different profile / external_id）。

修复后：`job-limit 1` 冒烟 84/84 通过；全量 3 岗位 **236/236** 通过。

---

## 13. 未解决问题 / 观察项

1. `SemanticScoringService` 对**未封存输入**（无 `job_candidate_inputs`）当前以 `NotFound` 异常表达，而非返回 `DATA_INCOMPLETE` 结构体；`main.py semantic-diagnose` 会将其转为 `{"ok": false, "error_type": "NotFound"}` 且退出码 1。
   - 现状：语义正确（不伪造分数），但诊断体验偏"硬失败"。
   - 影响面：仅影响对未封存候选人的 Tier2 诊断调用；真实链路中 Tier2 只在通过 Tier0/Tier1 的封存候选上执行。
   - 建议：如确需更友好诊断，可在后续**新增**分支将未封存输入映射为 `DATA_INCOMPLETE`（属 Stage 5 范围内的可选增强，需补充对应测试）。
2. 性能数据为**本机小样本**观测，不构成生产 SLA。
3. 本报告涉及的三份 Policy 由 Stage 3 真实 Qwen 输出（输入 JD 为合成）；候选人集为人工合成固定集，**不等同真实招聘数据**，不得据此宣称招聘效果。
4. 仓库无 Git 提交历史，改动来源无法经由版本控制追溯，仅能以文件与运行证据核对。

---

## 14. 下一阶段建议（等待人工验收）

- **本阶段（Stage 5）到此停止**，不擅自进入项目整体 Stage 6（Tier3：动态 Rubric + Qwen 候选人深度评估）。
- 建议在进入项目 Stage 6 前：
  1. 人工复核本报告与 `semantic_acceptance.md/json`；
  2. 若需改进未封存诊断体验，按第 13.1 以**增量 + 测试**方式实施；
  3. 为仓库建立首个 Git 基线提交，便于后续阶段可追溯；
  4. 在真实（非合成）岗位与候选人样本上再做一轮离线评估，但**不得**将合成验收数据外推为生产效果承诺。

---

### 附：证据命令

```bash
.venv\Scripts\python.exe -X utf8 main.py test -q                      # 432 tests OK
.venv\Scripts\python.exe -X utf8 -m tests.semantic_acceptance          # 真实 BGE：236 断言 / 27 记录 / 0 失败
.venv\Scripts\python.exe -X utf8 main.py db-check                      # schema_version=8, pending=[], integrity=ok
.venv\Scripts\python.exe -X utf8 main.py embedding-check               # pooling CLS / dim 1024 / max_len 512 一致
```
