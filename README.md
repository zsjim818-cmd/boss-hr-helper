# Scoring Engine V2（Phase 1.7 Stage 4）

保留 Tier 0 → Tier 1 → Tier 2 → Tier 3。当前交付仅涉及评分，不执行浏览器采集或发送动作。

当前目录按 `开发文档/项目预设结构.md` 的职责划分整理：

```text
boss-qhr-helper-dev/
├── main.py                   # 统一CLI入口
├── config.json               # 旧V2岗位与评分参数，保留兼容
├── .env.example              # 环境变量模板
├── pyproject.toml / uv.lock   # 依赖与锁文件
├── config/                   # 配置、legacy编译器、通用policy编译/校验/服务/Prompt
├── schemas/                  # 简历/评分契约、job_policy.py、prescreen.py
├── engine/                   # 旧四级引擎，以及Policy Interpreter/通用初筛
├── storage/                  # JSON缓存、database/、models/、repositories/、database_cli.py
├── tests/                    # 测试、mock_data.py、mock_models.py及诊断/基准工具
├── reports/                  # 评分快照、诊断日志、测试与交付记录
└── 开发文档/                 # PRD、计划、结构规范、数据格式和参考截图
```

预设结构中的 `browser/`、`server/`、`web/` 等属于后续阶段，尚未实现。当前仅建立已有功能的目录。

Stage 2 已新增独立 SQLite 业务持久化层。初始化、检查及迁移统一通过原 CLI：

```powershell
.\.venv\Scripts\python.exe -X utf8 main.py db-init
.\.venv\Scripts\python.exe -X utf8 main.py db-check
.\.venv\Scripts\python.exe -X utf8 main.py db-migrate
.\.venv\Scripts\python.exe -X utf8 -m tests.database_acceptance
```

默认数据库为 `data/recruitment.sqlite3`，可通过 `.env` 中的 `RECRUITMENT_DB_PATH` 或命令行 `--database 路径` 指定；相对路径以项目根目录解析。`RECRUITMENT_DB_BUSY_TIMEOUT_MS` 默认5000。每个连接验证 foreign_keys、recursive_triggers、WAL 和 busy_timeout。`db-check` 不会创建不存在的数据库。

业务访问入口为 `storage.repositories.RecruitmentRepository`，须由可信后端传入 `AccessContext(org_id, user_id)`；该对象本身不证明登录身份。组织与用户的首次创建使用独立部署上下文 `ProvisioningContext`。没有向前端提供数据库文件、连接或身份初始化接口。

扫描批次创建时锁定已发布策略。新岗位候选人绑定批次版本，已有候选人保留首次版本；首份完整可评分简历封存后不能自动替换。正式 fast/deep 结果分别最多一份，失败保存在 attempts，历史策略、快照和结果由数据库拒绝修改、删除及冲突替换。详细 ER、API、验收与限制见 [Stage 2 实施报告](reports/phase1_7_stage2/implementation.md)。

现有评分入口继续使用原 `config.json` 与 JSON 缓存；没有把它们自动接入业务库，也未把缓存迁成正式评分。Embedding 资产、前端及浏览器接入留在后续阶段。

Stage 3 新增通用 `JobScoringPolicy`、`JobPolicyCompiler` 与 `PolicyCompilerService`。稳定Schema支持动态维度/Rubric、ALL/ANY要求图、来源引用、硬条件、加分项、关键词及语义锚点。Qwen输出必须经过严格JSON Schema、Pydantic及确定性业务校验才能形成可确认草稿；编译本身不会发布。

编译还执行保守别名处理与明确二选一的ANY转换，保留原始响应、转换原因和编译前后哈希。无来源证明的别名会被剔除，歧义关系阻塞发布；这些处理不改变评分权重和四级算法。

```powershell
python main.py policy-schema --output reports/policy_schema.json
python main.py policy-validate --policy reports/phase1_7_stage3/policies/java_backend.json
python -X utf8 -m tests.policy_acceptance
# 以下显式访问真实Qwen，编译三份合成JD并保存到独立诊断库，只创建草稿：
python main.py policy-diagnose --live --label my_review
```

已有组织/岗位可使用 `policy-compile --live --input input.json --org-id ... --user-id ... --job-id ...`；输入字段为 `job_title/jd_text/hr_requirements`。草稿编辑、完整哈希确认、版本创建和发布分别通过 `policy-update/policy-confirm/policy-version/policy-publish`，每个命令支持 `--help`。这些是可信本地管理命令，org/user参数不代替HTTP认证。

默认业务库现为Migration 7，旧库用 `python main.py db-migrate` 追加升级，001–005没有修改。发布必须关联精确草稿revision/hash的成功编译记录与HR确认。旧 `config/job_compiler.py` 保留为AI岗位V2兼容入口，通用编译使用 `config/policy_compiler.py`；通用配置尚未交给现有固定四维评分器执行。完整交付与真实输出见 [Stage 3 报告](reports/phase1_7_stage3/implementation.md)。

Stage 4 已接入通用初筛：`JobScoringPolicy → PolicyInterpreter → Tier0/Tier1 → PreliminaryEvaluation`。候选人通过 `job_candidate_id` 读取首次绑定的策略版本；封存输入不会被后续重复采集替换。初筛不调用Qwen/BGE，不写正式评分，不授予发送资格。

```powershell
python -X utf8 -m tests.prescreen_acceptance
python main.py prescreen-benchmark
python main.py prescreen --database data/recruitment.sqlite3 --org-id ORG --user-id USER --job-id JOB --job-candidate-id JC --output reports/result.json
```

先通过已有Repository/策略管理入口创建真实组织岗位、发布通用策略并采集候选人。未封存的部分简历预览须显式添加 `--resume-id`；`--profile` 可选择独立的运行参数文件。默认 `config/prescreen_profile.json` 与岗位Policy分开，完整生效参数及哈希写入报告。

新分数是0–100规则初筛，不能当作旧Tier1+Tier2粗排或深度分。硬FAIL不会被高分覆盖，UNKNOWN交人工核验；深度阈值只传递。动态维度、原文偏移、否定/学习/提及和图分支均保留。完整审计、公式、42项固定集与限制见 [Stage 4 报告](reports/phase1_7_stage4/implementation.md)。现有固定四维终审仍属旧兼容路径，未在本阶段改造。

在项目根目录运行（Windows PowerShell）：

```powershell
.\.venv\Scripts\python.exe -X utf8 main.py test
.\.venv\Scripts\python.exe -X utf8 main.py benchmark
.\.venv\Scripts\python.exe -X utf8 main.py diagnose --mode deep_mode
```

以上命令均使用确定性 Mock，不访问模型 API。基准工具写入 `reports/benchmark_v2_mock.json` 和 `reports/benchmark_comparison.md`。Mock 深度四维固定为 90/85/80/90，仅验证数据契约、公式和路由，不衡量招聘效果。

各工具也支持模块方式运行：`python -m tests.benchmark_runner`、`python -m tests.test_benchmark`、`python -m tests.test_engine`。统一入口及默认配置路径支持从其他工作目录调用。

仅诊断一份原始简历的真实 Tier 3 请求（不加载BGE，不跑五份简历）：

```powershell
.\.venv\Scripts\python.exe -X utf8 main.py llm-diagnose --live --candidate 1 --label candidate1
```

可用 `--resume 文件.json` 替代内置样本，`--thinking on/off` 单独覆盖本次思考开关，或用 `--thinking on --thinking-budget 1024` 测试有限推理预算。`--max-tokens` 仅在显式指定时发送。每次诊断只发一次请求，按原配置45秒超时，记录到 `reports/llm_live/<label>.json`。该入口要求显式 `--live`，固定为人工核验，不授予AUTO资格；成功状态仅表示Tier3成功，Tier1/Tier2未运行。

Qwen真实超时诊断见 `reports/llm_live_diagnosis.md`。当前配置通过 `llm_review.enable_thinking=false` 关闭默认思考，Rubric与严格Schema、原文证据校验保持。Prompt额外明确顶层对象结构；供应商偶发返回数组仍会被拒绝。请求用量、耗时及失败阶段可从 `engine.last_llm_diagnostics` 查看，运行日志也会记录安全指标，不记录API密钥或模型思考原文。供应商未返回的 `reasoning_tokens` 保留未知。

显式本地模型检查：

```powershell
.\.venv\Scripts\python.exe -X utf8 main.py diagnose --live --mode fast_mode
```

五类/单例诊断脚本仅在显式传入 `--live --mode deep_mode` 时调用 `.env` 配置的 LLM；`llm-diagnose --live` 则直接调用单份简历的Tier3。Qwen 服务需支持 OpenAI `json_schema` 输出；不支持时记录 `SCORING_FAILED`，不会回退放行。`.env` 支持原有 `EMBEDDING_MODEL_PATH`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL_NAME`。`python-dotenv` 是原项目已经使用、此前补入依赖声明的包；新增算法、缓存和测试未增加第三方依赖。

调用入口与评分口径：

```python
from engine.hybrid_engine import HybridScoringEngine

engine = HybridScoringEngine("config.json")
results = engine.evaluate_batch(resume_dicts, force_mode="deep_mode")
# results[i].coarse_score：Tier1 + Tier2（0–100），仅粗排
# results[i].total_score：成功深评（0–100）；其他情况为 None
```

`evaluate` 仍接受原始字典或 `ResumeData`，原有模型构造参数继续有效。测试可注入 `embedder`、`llm_client`、`cache`。外层卡片独立为 `CandidateCardData`，传入完整评分入口会记录字段映射失败；不会隐式扩充为完整简历。

`AUTO_GREET` 仅表示评分侧候选队列资格：必须成功 deep_mode、通过粗排及明确硬条件、完整且无映射问题、达到配置阈值、四维证据及置信度满足要求、开启配置开关。fast_mode、未知硬条件、LLM/JSON/证据校验失败均不能进入该队列。`NOT_RECOMMENDED` 仅是不推荐，最终判断由 HR 作出。兼容契约保留旧 `REJECT` 字面值，V2 引擎不生成它。

算法与配置：

| 阶段 | V2 行为 | 主要配置 |
| --- | --- | --- |
| Tier 0 | 学历/薪资 PASS、FAIL、UNKNOWN；周期不同不擅自折算；历史职位仅作核验线索 | `hard_filters` |
| Tier 1 / 45分 | 技能别名全局去重；每词仅最强来源计分；类别预算和平滑饱和 | `keyword_rules`、`keyword_aggregation`、`evidence_source_weights`、`evidence_quality` |
| Tier 2 / 55分 | Agent/RPA 分路线 OR；同块负向惩罚；每经历一槽的 Top-K；辅助来源独立降权 | `semantic_anchors.positive_anchor_groups`、`semantic_scoring` |
| Tier 3 / 100分 | Rubric + 原文引用 + 缺失项 + 置信度；Pydantic校验；四维40/30/20/10 | `dimension_weights`、`llm_review` |

Tier 1 每类别贡献为 `cap * (1-exp(-raw/scale))`；类别预算默认为实战28、工程12、提效5。原有每词基础分保留，别名只取最高基础分，跨来源只取最高来源权重。来源分别为工作职责、项目、毕设、工作标签、技能与个人优势。格式化技术列表（包括“熟悉/精通+列表”）即使映入职责字段也降权；这一启发式不能验证经历真实性。

Tier 2 每块正相似度先按配置区间映射至0–55，负向惩罚只比较该块的正负值，再乘来源权重。每条经历只占一个 Top-K 槽，按净分排序加权并按实际选中权重归一化。实战与辅助来源独立聚合取较高者，最后两路线取较高者；不会要求同时具备 Agent 和 RPA。完整原文窗口、各路线正相似度、负相似度、扣分和入选路线均保留于 `debug_trace`。

四个深评维度为 AI应用与自动化实战、工程基础、自动化提效能力、客观岗位适配，旧“主观稳定性”维度已移除。高分实战/工程/提效必须引用具体职责或实现原文，短技术名和技能标签不够；引用长度阈值配置为 `llm_review.min_practice_quote_chars`。简历以不可信 JSON 数据放在 user 消息，规则在 system 消息；姓名、年龄和寻址ID不进入评分提示词。

配置结构详见 `config.json`。旧平铺锚点配置会显式报错，需转换为 `positive_anchor_groups.agent/rpa`；`config/job_compiler.py` 已同步生成 V2 配置。`config_version` 是人工版本标签，实际缓存还包含整个配置的 SHA256，改配置而不改标签仍会失效。

缓存键为 `candidate_key + resume_content_hash + job_config_version + embedding_model_version + llm_model_version + prompt_version + scoring_mode + engine_version`。下载时间和寻址元数据不参与内容哈希。失败和不完整结果不缓存；缓存命中会重新校验结构、证据、置信度、深度加权和路由。磁盘缓存可通过 `cache.enabled/directory` 配置，注入 Mock 时默认只用内存。缓存异常可查看 `engine._score_cache.issues`，不会把缓存错误当作候选人分数。

单引擎使用固定配置快照；`engine.config` 返回副本。修改配置后显式 `engine.reload_config()`，批次运行时禁止内部重载，其他线程重载会等批次结束。模型对象或实际模型修订变化应重建引擎，并更新 `model_versions`。当前版本标签包含 `unpinned`，远端模型别名不能证明供应商模型未变；temperature=0 也不能保证跨供应商版本完全一致。

输入已知字段严格校验；未知字段保留原值并记录 `data_issues`，阻止自动发送资格。未包含采集区块与显式空数组分开处理；旧样本按五个区块是否存在推导完整性，无法证明页面已完整展开。Phase 2 接入时应明确上报 `data_completeness` 和 `collection_errors`。学历采用最高明确声明学历，不自动证明已经毕业；日薪、年薪与月薪不同周期时交人工核验，13薪记录但不擅自折算成年包。

数据契约统一从 `schemas` 导入，也可直接使用 `schemas.candidate`、`schemas.card`、`schemas.evaluation`。简历文本提取函数集中在 `engine/resume_text.py`，Schema 仅负责数据及校验。

交付与证据见 `reports/delivery_v2.md`、`reports/phase_validation.md`。五类原始样本集中在 `tests/mock_data.py`，真实模型校准及后续浏览器/发送阶段等待人工审查。
