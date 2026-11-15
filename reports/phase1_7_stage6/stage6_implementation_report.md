# Stage 6 — 通用 Tier 3（Qwen 终审）实现报告

日期：2026-11-15
基线：P0 恢复后干净状态 + Stage 5.5 Batch 1/2 已交付 + 发布质量门控（Batch 5，同日封口）。
范围：通用 Policy 驱动的 Tier 3 终审（动态 Rubric/权重/证据核验/加权路由/不可变持久化）+ 离线 Mock 与 `--live` 两种模式。
安全约束：未使用 `git clean`/`reset --hard`；未修改根 `config.json`；未修改迁移；未破坏既有测试；真实 Qwen 未被调用（本轮全部离线）。

---

## 1. 交付物

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `schemas/pipeline.py` | 新增 | `PolicyTier3Evaluation` / `PipelineDimensionReview` / `PipelineEvidence` 契约（含身份绑定、阈值快照、路由建议） |
| `engine/policy_llm_reviewer.py` | 新增 | 动态请求构造、证据核验规则、加权/路由、`PolicyReviewService`（evaluate / evaluate_and_persist）、单次调用观测指标 |
| `engine/pipeline_cli.py` | 新增 | `python main.py tier3-review ...`（默认离线 Mock；`--live` 真实 Qwen；`--persist` 写不可变 attempts） |
| `storage/repositories/evaluations.py` | 修改 | `_validate_pipeline_result` + `append_pipeline_attempt`（复用 append-only 守卫） |
| `main.py` | 修改 | 注册 `tier3-review` 命令 |
| `tests/test_policy_llm_reviewer.py` | 新增 | 12 项单元测试 |
| `tests/test_pipeline_e2e.py` | 新增 | 3 项三岗位全链路测试 |
| `reports/phase1_7_stage5_5/stage6_pipeline_results.json` | 新增 | 三岗位真实 CLI 冒烟结果 |
| `reports/phase1_7_stage6/stage6_tests.log` | 新增 | 六段测试日志 |

## 2. 架构与数据流

```
HTTP/CLI 调用
  → PolicyReviewService.evaluate(context, job_id, job_candidate_id)
      → Repository 授权（read "evaluate"）
      → PolicyInterpreter.load_for_candidate  ← 与 Tier0/1/2 同一个 ExecutablePolicy（policy_hash 校验）
      → job_candidate_inputs（封存输入）+ resume_snapshots（规范化简历/hash 校验）
      → engine.llm_reviewer.llm_source_map（排除姓名/年龄/ID/推断标签）
      → build_pipeline_request(policy, source_map)   ← 维度/Rubric/权重全部来自 policy.scoring_dimensions
      → 一次 LLM 调用（Mock 或 Qwen）→ DynamicPolicyAssessment（既有 wire 协议）
      → validate_dynamic_assessment（维度集合严格一致）
      → 证据规则校验（逐字原文定位、缺失证据降置信、无证据不得高分）
      → weighted_total（policy 权重）→ routing_suggestion（policy 阈值）
      → PolicyTier3Evaluation（身份+结果+指标）
  → evaluate_and_persist：create_evaluation_task("deep_mode") → append_pipeline_attempt（SUCCEEDED/FAILED）
```

关键不变量：Tier 3 结果中的 `policy_hash / policy_version / resume_content_hash` 必须与候选人首绑版本和封存快照一致；`append_pipeline_attempt` 在仓库层**复算**维度集合、权重、加权分、路由、证据路径，任何不一致拒绝入库（服务端输出不可信）。

## 3. 动态性证明（无任何 AI 岗硬编码）

- 请求 system 中的维度 JSON 由 `policy.scoring_dimensions` 直接序列化（dimension_id/label/weight/rubric 三档 min/max/description）。
- 单元测试 `test_request_dimensions_are_fully_dynamic_per_policy`：对三份异构策略逐维比对 id/label/weight/rubric 数量，并断言旧 V2 四维词汇（`ai_automation_practice`/`engineering_foundation`/`ai_efficiency_mindset`/`job_fit_and_stability`/`AI应用与自动化实战`）**不出现在 prompt 中**；JSON Schema 的 dimensions 项 required 恰为 5 个协议字段。
- 三岗位的维度集合互异（4/5/4），E2E 断言三者不全相同。

## 4. 证据与置信规则（确定性，已在代码内常量 + 测试锁定）

| 规则 | 值 | 违反后果 |
| --- | --- | --- |
| 证据 quote 最短长度 | 4 字符 | 拒绝（`PipelineReviewError`） |
| quote 必须逐字出现在封存简历某字段 | exact substring | 拒绝 |
| 无证据时分数上限 | 60.0 | 拒绝 |
| 无证据时置信度上限 | 0.5 | 拒绝 |
| 存在缺失证据时置信度上限 | 0.8 | 拒绝 |
| 维度集合 | 必须与 policy 完全一致（`validate_dynamic_assessment`） | 拒绝 |
| 截断/异常结束/拒绝回答 | finish_reason=length、非 stop、refusal | 拒绝并记录 stage |

服务层证据校验失败时 `last_llm_diagnostics` 记为 `FAILED / evidence_validation`；成功时 `SUCCEEDED / completed` 且 `evidence_valid=true`。指标含 request 元数据（prompt_sha256/schema_sha256/字符数/字节数）、耗时、usage、reasoning_tokens、finish_reason，**不含 prompt 正文、简历原文或密钥**（测试断言）。

## 5. 持久化与不可变守卫

- 通用结果写入 `evaluation_attempts`（`result_json`，JSON object 列）；`create_evaluation_task(..., "deep_mode")` 复用既有任务身份（候选/策略版本/简历/执行 profile）。
- 不可变由迁移 004/005 触发器保证：`no_update_evaluation_attempts` / `no_delete_evaluation_attempts` / `attempt_running_task`（顺序强制）；E2E 测试实测 UPDATE/DELETE 均被 `sqlite3.IntegrityError` 拒绝。
- **「正式结果」表 `evaluations` 仍是旧 V2 契约**（列语义 tier1≤45/tier2≤55 + 形状触发器绑定 `EvaluationOutput` 字段），通用结果**未**混写其中——避免伪造旧语义。通用结果的正式表集成需要单一迁移决策（新增列/新表或扩展状态枚举），未在本批动迁移。
- 失败路径：`evaluate_and_persist` 中任何异常会先追加 `FAILED` attempt（带 error_type/metrics）再抛出；任务可重试（attempt 序号由触发器强制）。

## 6. CLI

```powershell
# 离线确定性（默认）：Mock LLM，不联网；--persist 写入不可变 attempts
.\.venv\Scripts\python.exe main.py tier3-review --database data\recruitment.sqlite3 `
    --org-id ORG --user-id USER --job-id JOB --job-candidate-id JC --persist

# 真实 Qwen（显式）：
.\.venv\Scripts\python.exe main.py tier3-review ... --live [--model NAME] [--timeout 45]
```

输出 JSON：`evaluation`（完整 `PolicyTier3Evaluation`）+ `attempt`（persist 时）。错误路径：`{"ok": false, "error_type": ..., "message": ...}`，退出码 1。要求主库 `db-check` 通过（与 policy/semantic CLI 一致的既有守卫）。

## 7. 三岗位全链路结果摘要（真实 CLI 子进程，离线 Mock）

数据集：`stage6_pipeline_results.json`。说明：Tier 2 使用确定性单位向量（等价于"每个锚点都有完美匹配证据"），Tier 3 使用确定性 Mock 分数；**这些数字用于验证链路与公式，不代表真实招聘质量或真实 BGE/Qwen 效果**。

| case | Policy 维度(权重) | Tier0/1 | Tier2（分/覆盖） | Tier3（分 → 路由） | attempt |
| --- | --- | --- | --- | --- | --- |
| ai_automation | delivery .50 / reliability .25 / efficiency .15 / requirements .10 | PASS · BELOW_COARSE | 100.0 / 0.775 | **82.9 → HUMAN_REVIEW** | SUCCEEDED |
| java_backend | service_design .30 / data_correctness .30 / troubleshooting .20 / testing_quality .15 / communication .05 | PASS · READY_FOR_DEEP | 100.0 / 0.600 | **79.9 → HUMAN_REVIEW** | SUCCEEDED |
| b2b_sales | prospecting .25 / discovery_solution .20 / commercial_progress .35 / collections .20 | PASS · READY_FOR_DEEP | 100.0 / 0.550 | **79.0 → HUMAN_REVIEW** | SUCCEEDED |

- 三份策略的 Tier 3 维度集合分别为 4/5/4 且互异（同一套代码，无岗位分支）。
- 加权分与阈值带全部可由公式复算（E2E 断言 `weighted_total` 与路由带）。
- ai 的 BELOW_COARSE 来自 Tier1 规则分（Mock 简历与 AI 关键词覆盖有限），不影响 Tier 2/3 演示；Tier2 覆盖率 <1 正是 Batch 1 锚点缺口（真实策略宽泛/缺失锚点）在评分上的体现。

## 8. 测试统计

| 套件 | 数量 | 结果 |
| --- | --- | --- |
| `tests.test_policy_llm_reviewer`（新增） | 12 | 12/12 OK |
| `tests.test_pipeline_e2e`（新增） | 3 | 3/3 OK |
| `tests.test_policy_compiler`（含门控 3 项） | 17 | 17/17 OK |
| `tests.test_policy_cli`（含门控 1 项） | 14 | 14/14 OK |
| 全量回归 | **500** | **500/500 OK**（Batch 2 的 481 + 本批 19），0 失败/0 error |

## 9. 未解决问题与风险（不以测试全绿宣称算法质量合格）

1. **Mock ≠ 质量证明**：三岗位结果均来自确定性 Mock（分数固定、向量完美匹配）。真实 Qwen/BGE 质量必须用 `--live` 与真实 BGE 重新验收（未在本轮执行——需显式授权与真实账号/模型）。
2. **Tier 2 未走真实 BGE**：E2E 直接调用 ranker（确定性向量）。真实链路需要 `semantic-prepare` + 真实 BGE（Stage 5 已完成验收，但本轮未重复）。
3. **正式评估表未接入**：通用结果只进 `evaluation_attempts`；`evaluations` 表被旧契约触发器绑定。要做单表正式化，需要一个迁移决策（扩展状态/新表），未在本批动。
4. **证据规则是保守近似**：只校验"逐字出现 + 长度 + 无证据上限 + 缺失证据置信上限"，不判断证据是否"实践性"（旧引擎有 practice-path 深度校验）。通用 Policies 没有 practice 配置，是否引入 profile 化增强留待决策。
5. **Tier 3 失败不自动重试**：失败 attempt 已记录、任务可人工重跑；无自动重试/熔断策略。
6. **前端未接**：`server/frontend` 仍是旧 V2 原型（见 `architecture_integration_audit.md`）；新链路只有 CLI，前端联调仍是 MVP 下一缺口。
7. **阈值语义**：`deep_review/deep_recommend` 现用于 Tier3 路由建议；HR 工作台需要明确定义"未达 deep_review 是否仍可人工查看"等产品语义（当前输出仅建议，不授权任何发送）。

## 10. 修改/新增文件清单（本轮全部）

新增：
- `schemas/pipeline.py`
- `engine/policy_llm_reviewer.py`
- `engine/pipeline_cli.py`
- `tests/test_policy_llm_reviewer.py`
- `tests/test_pipeline_e2e.py`
- `reports/phase1_7_stage5_5/batch5_publish_gate.md`
- `reports/phase1_7_stage5_5/stage6_pipeline_results.json`
- `reports/phase1_7_stage6/stage6_implementation_report.md`
- `reports/phase1_7_stage6/stage6_tests.log`

修改：
- `config/policy_service.py`（`PolicyQualityBlocked` + 默认发布门控 + `enforce_quality_gate` 参数）
- `config/policy_cli.py`（`--allow-blocked-quality` + 门控错误诊断）
- `main.py`（注册 `tier3-review`）
- `storage/repositories/evaluations.py`（`_validate_pipeline_result` + `append_pipeline_attempt`）
- `tests/test_policy_compiler.py`（+3 门控测试；1 处历史发布显式豁免）
- `tests/test_policy_cli.py`（+1 CLI 门控测试；2 处历史发布显式豁免）
- `tests/test_policy_normalization_integration.py`（1 处历史发布显式豁免）

工作区其余 Batch 1/2 成果、frontend、审计报告全部保留；未提交 Git；备份仍在项目外。
