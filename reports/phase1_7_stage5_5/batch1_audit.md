# Stage 5.5 Batch 1 — 接管审计报告

执行时间：2026-11-15
执行者：OpenCode（接替 Codex → Trae）
性质：只读审计，未清理、未重置、未覆盖任何既有修改。

## 1. 接管时的工作区状态

git 基线：仅 1 个提交 `1887468 chore: 初始化仓库，纳入 Stage 1-5 现有实现`。

工作区相对基线的全部差异（全部保留，未做任何清理）：

| 路径 | 状态 | 说明 |
| --- | --- | --- |
| `config/policy_cli.py` | 修改（+23/-3） | `policy-validate` 增加文件模式与库内 scoped 模式；输出新增 `quality` 字段 |
| `engine/policy_validator.py` | 未跟踪 | Stage 5.5 质量门入口 `validate_policy_quality`（Trae 新增） |
| `engine/policy_anchor_validator.py` | 未跟踪 | 正向语义锚点覆盖检查（Trae 新增） |
| `schemas/policy_validation.py` | 未跟踪 | `PolicyQualityIssue` / `PolicyQualityReport` 契约（Trae 新增） |
| `tests/test_policy_validator.py` | 未跟踪 | 6 项锚点覆盖测试（Trae 新增） |
| `frontend/` | 未跟踪 | Vue3 脚手架（含 node_modules）；属后续阶段，Batch 1 不触碰 |

未被执行过的危险操作：无 `git reset` / `git clean` / `git checkout .` / 覆盖式复制 / 删除测试文件。

## 2. Trae 已完成并经实测确认的成果

- 语义范围推导：`semantic_scope_requirement_ids(policy)` = `scoring_dimensions[*].requirement_ids` 的并集。
- 直接覆盖：存在 `polarity == "POSITIVE"` 且 `len(requirement_ids) == 1` 的锚点。
- 状态判定：`MISSING_POSITIVE_ANCHOR`（ERROR，导致 BLOCKED）、`BROAD_ONLY_POSITIVE_ANCHOR`（WARNING）。
- 报告：`semantic_requirement_count`、`directly_covered_count`、`positive_coverage_rate = direct / semantic_scope`。
- CLI：`policy-validate` 已接入 quality 输出，支持 `--policy <file>` 与 `--database/--org-id/--user-id/--job-id`。
- 测试：`tests.test_policy_validator` 6/6 通过；`tests.test_policy_cli` 7/7 通过（CLI 改动未破坏既有断言）。

## 3. 截图所述 `tests/test_policy_cli.py` +14 行的状态

该文件当前内容与基线提交逐字节一致（`git diff` 为空），磁盘上不存在任何与 quality 相关的 CLI 测试。
结论：这 14 行修改从未落盘（或已丢失）。Batch 1 需要重建这部分 CLI 集成测试，且按本批任务清单补全至完整覆盖（只读性、权限隔离、错误路径、不调用模型等）。

## 4. Policy Schema 审计与评估范围缺口

### 4.1 真实 Schema（`schemas/job_policy.py`，`job_scoring_policy_v1`）

`PolicyRequirement` 字段：`requirement_id` / `label` / `source_type` / `source_text` / `priority` /
`requirement_type`（SKILL 等 7 类）/ `needs_confirmation`。

**不存在**任何显式的评估范围（routing）字段：
- 没有 `SEMANTIC` / `RULE` / `HUMAN_REVIEW` 枚举；
- `needs_confirmation` 是编译期人工确认标记，不是评估路由；
- `requirement_type` 是业务分类（教育/薪资/技能），不是评估层。

### 4.2 结构性等价物（引擎行为核实）

| 评估层 | Schema 结构 | 引擎证据 |
| --- | --- | --- |
| Tier 0 硬过滤 | `hard_filters[*].requirement_id`（单引用） | `engine/policy_hard_filter.py` |
| Tier 1 规则 | `keyword_groups[*].requirement_ids` | `engine/policy_rule_matcher.py` |
| Tier 2 语义 | `scoring_dimensions[*].requirement_ids` | `engine/policy_semantic_ranker.py` |

关键证据：`evaluate_policy_semantics` 先为全部 requirement 计算 requirement_scores，
但**最终语义得分与 `semantic_coverage` 只由 `scoring_dimensions` 投影聚合**
（L351–409：`select_node(root, target=dimension.requirement_ids)`），投影之外的 requirement 不产生语义贡献。
因此"维度投影并集 = Tier 2 语义评价范围"是引擎实现背书的结构推导，属于复用现有等价字段，不是猜测。

### 4.3 仍然存在的缺口（Batch 1 保守处理）

1. **无需求级评估意图声明**：投影是编译器输出，不是 Requirement 自身的契约。
   编译器漏投影时 Tier 2 会静默跳过该要求。→ Validator 将无任何投影的孤儿 requirement 报
   `UNCLASSIFIED_REQUIREMENT_SCOPE`（WARNING），**不计入覆盖率分母**。
2. **HUMAN_REVIEW 路由不存在**：Schema 无法表达"此要求仅人工复核"。
   → Batch 1 不臆测路由，只在报告中声明该缺口。
3. **维度投影无效引用**：`dimension.requirement_ids` 引用不存在的 requirement 时，
   该维度覆盖率记 `INDETERMINATE`（`direct_coverage_rate=None`），不伪造 100%。
4. **多 Requirement 锚点**：一个正向锚点关联多个 requirement 时不能独立证明任何一项。
   → 仅记录 BROAD_ONLY 警告（Batch 1 不做完整风险治理，后续批次）。

## 5. 三个岗位的真实数据状态（只读核查）

### 5.1 主业务库 `data/recruitment.sqlite3`

**接管时（2026-11-15 12:51 首次只读检查）**：干净的 41 表 schema v8，无遗留表，
`job_policy_versions` / `policy_publications` / `job_candidates` 均为 0 行，仅 1 个岗位
（`org_id=ec5e129149214dccb2de98a2c52bd932`，`job_id=3addf716812f43078a6cd1638dca608a`，
AI 自动化方向，`current_policy_id=None`）——`db-check` 当时可以通过（Stage 3 检查记录亦为
ok=true、schema v7 时代）。

**会话期间被外部进程污染（13:05:13）**：详见附录 A。结论不变：主业务库中三个案例岗位均无已发布策略。

### 5.2 Stage 3 live 验收库（三岗位真实 Qwen 编译产物所在）

| case | 库 | org_id | job_id | 产物形态 |
| --- | --- | --- | --- | --- |
| ai_automation | `reports/phase1_7_stage3/live/full_v1_1/drafts.sqlite3` | `642ecf08…` | `b4a2ed9fcb494f1b94f02b64cbc50c18` | revision 1 草稿（从未发布） |
| b2b_sales | 同上 | `642ecf08…` | `d0cba22053ae45209844e0a64f563da8` | revision 1 草稿（从未发布） |
| java_backend | `reports/phase1_7_stage3/live/compact_java_v1_3/drafts.sqlite3` | `691ac60c…` | `b6a07a6d5f594aca98c52e69d85c0555` | revision 1 草稿（从未发布） |

最终验收 Policy JSON 副本：`reports/phase1_7_stage3/policies/{ai_automation,b2b_sales,java_backend}.json`。

Stage 5 BGE 真实验收曾在独立临时库中以
policy_id `3909859547bc…` / `04f2a811e490…` / `79cc58f1460a…`（均 version 1）重放这三份 Policy；
这些版本号属于当时的临时验收库，不在当前业务库中。

### 5.3 扫描口径（诚实声明）

- 三个岗位**均无当前发布策略**（`get_current_policy` 将返回 NotFound，实测属实）。
- Batch 1 扫描将验证它们的真实编译产物：Stage 3 live 库中 revision 1 草稿
  （与 `reports/phase1_7_stage3/policies/*.json` 验收副本一致性另行确认），
  并在报告中显式声明版本来源（草稿阶段、revision 1、draft_hash）。

## 6. 接管时测试状态（实测）

- `tests.test_policy_validator`：6/6 OK
- `tests.test_policy_cli`：7/7 OK
- 全量回归：见 `batch1_tests.log`（基线与完成后各一份记录）。

## 7. Batch 1 需补齐的工作

1. 报告契约扩展：`broad_only_count` / `missing_positive_count` / `unclassified_count` /
   各维度覆盖率 / `policy_id` / `policy_version` / `policy_hash` / `suggested_action`（只增不改既有字段）。
2. UNCLASSIFIED（孤儿 requirement）检测；无效投影/锚点引用检测（INDETERMINATE 维度覆盖率）。
3. 三个岗位只读扫描与问题清单。
4. CLI 集成测试重建与补全（含只读性、权限隔离、无模型调用断言）。
5. 单元测试补至任务清单 17 项要求。
6. `reports/phase1_7_stage5_5/` 交付文档。

## 附录 A：接管会话期间的外部并发活动（重要警示）

Batch 1 开发期间检测到**另一个开发者/进程在同一工作区并发工作**（非 OpenCode、非 Trae 遗留）。
证据链（全部来自文件 mtime、进程列表与进程命令行的只读检查）：

| 时间 | 事件 | 归属 |
| --- | --- | --- |
| 12:47–12:51 | 接管审计；业务库只读检查：41 表干净 schema，无 `server/`、无 `storage/repository.py` | OpenCode |
| 13:01:18–13:02:08 | `server/__init__.py`、`storage/repository.py`、`server/app.py` **被外部创建**（接管初始 git status 中均不存在） | 外部 |
| 13:03:10 | `frontend/src/api/index.ts` 被外部修改 | 外部 |
| 13:04:11 | `server/__pycache__/app.cpython-313.pyc` —— 一个 Python 3.13 解释器导入过 `server.app` | 外部 |
| 13:05:11 | 启动并持续运行：`.venv/Scripts/uvicorn.exe server.app:app --host 127.0.0.1 --port 8000 --reload`（PID 3632/49648/48584） | 外部 |
| 13:05:13 | **业务库被写入**：`storage/repository.py` 构造函数自动建表，`data/recruitment.sqlite3` 新增 3 张迁移集之外的空遗留表（`job_policies`/`candidate_records`/`daily_quotas`，各 0 行），体积 688128→712704，引发 schema drift，`db-check` 从此失败 | 外部 uvicorn |
| 13:08:45 | uvicorn `--reload` 监视器因项目内 .py 文件变化 fork 新工作进程（PID 43004） | 外部 uvicorn |

影响与处置：

1. **业务库 drift 不是接管前状态**：本报告 §5.1 的接管时状态为干净 schema；drift 为会话内外部进程引入。
2. **Batch 1 全部命令未向业务库写入**：所有连接为 `mode=ro` 或针对字节副本 / 临时库；
   业务库文件 mtime 自 13:05:13 起未再变化（证据）。
3. **uvicorn 至今仍在运行并持有业务库连接**（`-shm` 持续存在、`-wal` 为 0 字节）：在它造成的 drift
   被处理前，主业务库上的一切 policy CLI 操作都会被既有守卫正确拒绝。
4. **建议**：立即协调该并发开发者——要么停掉 uvicorn、移除/改造 `storage/repository.py` 的自动建表
   （改用迁移集或独立数据库文件），要么接受业务库被 Stage-1 风格外挂污染并另行决策修复方案
   （属业务数据变更，超出本批只读授权）。
5. 本批未触碰 `server/`、`storage/repository.py`、`frontend/`（外部并发工作成果，按"潜在他人工作"处理，
   只观察不修改；`frontend/` 在接管初始即存在，`server/` 与 `storage/repository.py` 为会话期间外部新增）。
