# Boss HR Helper — 全项目进度盘点审计报告

审计日期：2026-11-15
审计性质：**只读审计**。未修改任何代码/数据库/Policy/配置，未停止任何服务，未执行迁移，未调用真实 Qwen/BGE，未做任何 BOSS 网站操作，未提交 Git。
例外说明：为验证前端可构建性执行过一次 `npx vite build`（产物 `frontend/dist/` 已删除，恢复原状）；为验证后端真实性执行过只读 GET `/api/health`、`/api/bootstrap`、`/openapi.json`（详见 §5——`/api/bootstrap` 的 GET 具有写副作用，该副作用是本次审计的关键发现之一，由运行中的服务自身产生，非审计写入）。

依据：当前磁盘真实代码 + 真实只读数据库检查 + 真实运行测试。不以旧交付报告推测进度。

---

## 0. 执行摘要（最重要结论先行）

| # | 结论 | 证据 |
| --- | --- | --- |
| 1 | **根目录 `config.json` 已于 13:28:01 被运行中的旧版后端 (`server/app.py` + `storage/repository.py`) 覆写损坏**，V2 旧评分引擎全链路（Tier 0–3 Mock 回归）当前**不可用**：全量回归 **452 测试、42 项失败（41 errors + 1 fail）**，全部可归因于此 | `git diff config.json`（丢失 `config_version`/`model_versions`/`cache` 三键，新增 `job_id`/`policy_version`）；`config/validation.py:22` 拒绝；临时目录用 git 基线副本验证 VALID。昨夜 Batch 1 完成时为 459/459 全绿 |
| 2 | **两套 Repository 并存且操作同一个 SQLite 文件**：新体系 `storage/repositories/`（迁移 001–008，41 表）与旧体系 `storage/repository.py`（自建 `job_policies`/`candidate_records`/`daily_quotas` 3 表，构造函数即写库）。旧体系运行中（uvicorn PID 3632，`:8000`），已造成主库 schema drift | `storage/repository.py:23-49`；进程命令行；`db-check` 当前失败：`Database schema drift` + `migrations are pending` |
| 3 | 核心算法线（Phase 1.7 Stage 2–5.5）代码与测试**完整、真实、已验收**，但**被前后端线拖累**：任何依赖根 `config.json` 的测试当前失败；主业务库 `db-check` 失败阻塞其上一切 policy CLI 操作 | §2/§3 |
| 4 | 业务库**无真实业务数据**（候选人/评估/嵌入全 0 行），无数据丢失风险；仅 Stage 3 验收残留（1 org/user/job/draft）+ uvicorn 写入的 1 行 `job_policies`（同内容被镜像写入根 config.json，即损坏源） | `db_inventory.py` 全表盘点（§5.4） |
| 5 | **前后端是围绕"旧 V2 单岗位架构"构建的平行系统**，未接入 Stage 3–5 的通用 Policy 体系（JobScoringPolicy/PolicyCompilerService/PolicyInterpreter/通用 Tier0-2/语义资产）；前端可构建、后端可启动，但业务闭环只能在旧 V2 路径上跑 | §4/§5/§6 |

---

## 1. 仓库与工作区状态

- 分支 `master`,单基线提交 `1887468 chore: 初始化仓库，纳入 Stage 1-5 现有实现`（Stage 1–5 全量在库）。无其他分支、无 stash。
- 未提交修改:`config/policy_cli.py`、`tests/test_policy_cli.py`（Stage 5.5 Batch 1 成果）。
- 未跟踪:`engine/policy_anchor_validator.py`、`engine/policy_validator.py`、`schemas/policy_validation.py`、`tests/test_policy_validator.py`、`reports/phase1_7_stage5_5/`（Batch 1）;`frontend/`（Vue3 工程,含 node_modules）;`server/`、`storage/repository.py`(旧体系,会话期间由所有者生成)。
- **`config.json` 处于"已损坏但 git 可恢复"状态**（`git checkout -- config.json` 即可还原,本轮未执行）。
- AGENTS.md 不存在。README.md 停留在 Stage 4 时代（无 Stage 5/5.5、无前后端内容）。

## 2. 模块清单（按模块输出）

### 2.1 算法核心（Phase 1.7）

| 模块 | 功能 | 核心文件 | 已实现 | 测试情况 | 问题 | 完成判断 |
| --- | --- | --- | --- | --- | --- | --- |
| Stage 2 持久化+版本管理 | 组织/用户/AccessContext、岗位、Policy 草稿→确认→版本→发布、扫描批次版本锁定、封存输入、不可变守卫 | `storage/database/`(connection/migrations 001–008)、`storage/repositories/`(base/identity/policies/batches/candidates/evaluations/compiler/embeddings) | ✅ 全部 | `test_database_*.py` 5 文件+`test_prescreen_interpreter` 等,离线真实 SQLite,全绿(受 config.json 污染的测试除外,见注) | 主库被旧体系污染(§5) | **已实现且过测试** |
| Stage 3 通用 Policy 编译 | JD+HR→Qwen→JobScoringPolicy(动态维度/Rubric/ALL-ANY/硬过滤/关键词/语义锚点)、保守规范化、草稿-确认-版本-发布服务 | `config/policy_compiler.py`、`config/policy_service.py`、`config/policy_validation.py`、`config/policy_normalization.py`、`schemas/job_policy.py` | ✅ 真实 Qwen 三岗位验收(reports/phase1_7_stage3,19 项真实响应) | `test_policy_compiler/persistence/normalization*/schema/generality/cli` 全绿 | 无 | **已实现且过真实+离线测试** |
| Stage 4 Interpreter+Tier0/1 | 候选人首次绑定 Policy 版本、封存输入防替换、通用硬过滤/关键词规则初筛(0–100,分维度) | `engine/policy_interpreter.py`、`engine/policy_hard_filter.py`、`engine/policy_rule_matcher.py`、`engine/prescreen_engine.py`、`engine/prescreen_cli.py` | ✅ | `test_prescreen_*` 5 文件全绿;42 固定集 benchmark | 无 | **已实现且过测试** |
| Stage 5 语义引擎 | BGE-large-zh 嵌入、策略锚点+简历分块向量资产、跨岗位简历向量复用缓存、Tier 2 语义评分服务+CLI | `engine/embedding_service.py`、`engine/policy_semantic_ranker.py`、`engine/semantic_scoring_service.py`、`engine/semantic_cli.py`、`storage/repositories/embeddings.py`、migration 008 | ✅ 真实 BGE 验收(reports/phase1_7_stage5,236 断言全过,3 岗位×9 画像) | `test_semantic_*`/`test_policy_embeddings`/`test_resume_embeddings` 全绿 | 无 | **已实现且过真实验收** |
| Stage 5.5 Batch 1 质量门 | 只读正向锚点覆盖检查、维度覆盖率、UNCLASSIFIED/无效引用、policy-validate CLI 库内模式 | `engine/policy_validator.py`、`engine/policy_anchor_validator.py`、`schemas/policy_validation.py`、`config/policy_cli.py` | ✅(Batch 1 范围) | 新增 21 单测+6 CLI 集成,昨夜 459/459(今随 config.json 损坏,test_policy_cli 部分子进程用例间接受阻,见 §5) | Batch 2–5 未做(见任务 5) | **已实现且过测试(本批范围)** |
| 旧 V2 引擎(Tier0–3,单岗位) | 固定四维、config.json 驱动、JSON 磁盘缓存 | `engine/hybrid_engine.py`、`engine/hard_filter.py`、`engine/rule_matcher.py`、`engine/semantic_ranker.py`、`engine/llm_reviewer.py`、`engine/scoring_session.py` | ✅ 历史实现 | `test_p0`/`test_session`/`test_llm_review`/`test_additional_boundaries`/`test_benchmark_v2`/`test_entrypoints`(诊断CLI)/`test_database_evaluations` **当前 42 项失败——全部因 config.json 损坏**,非代码回归 | **P0:配置被覆写** | 代码已实现;**当前被配置损坏阻塞** |

注:上表"全绿"指昨夜 Stage 5.5 Batch 1 交付时全量 459/459 通过的记录(reports/phase1_7_stage5_5/batch1_tests.log);当前再跑为 452 ran / 42 failed(差额 7 = test_policy_cli 中 6 个子进程 CLI 用例因主库 db-check 失败而提前 error + 计数方式差异,另见 §5.3)。**42 项失败与 7 项 error 的根因都是 server/旧体系造成的写副作用,不是算法代码回归。**

### 2.2 前后端(新开平行线)

| 模块 | 功能 | 核心文件 | 已实现 | 测试情况 | 问题 | 完成判断 |
| --- | --- | --- | --- | --- | --- | --- |
| 后端 FastAPI | health/bootstrap/compile/publish/evaluate_benchmark/approve 六路由 | `server/app.py` | ✅ 可启动(运行中) | 无任何后端测试 | ①调用旧体系 `storage/repository.py`+旧 V2 引擎+旧 `config/job_compiler.py`,完全未接 Stage 2–5 新体系;②GET `/api/bootstrap` 空库时**静默种子化并覆写根 config.json**(已实际发生);③`/api/candidates/evaluate_benchmark` 需真实 BGE(当前因 config.json 损坏必失败);④无任何认证/AccessContext,违背新体系权限模型 | **旧架构原型,与主算法线脱节** |
| 前端 Vue3 | 三页面(策略/采集模拟/审核看板)+Pinia+ElementPlus+axios | `frontend/src/`(api/index.ts 22.7KB、三个 View、store、types、router) | ✅ 可构建(vue-tsc 0 错误;vite build 成功,1.32MB js) | 无前端测试 | ①策略页硬编码旧 V2 四维权重/关键词表(`JobPolicyView.vue:125-136`);②API 层带**假数据静默回退**(`api/index.ts:476-514` catch 后返回伪造成功,掩盖后端故障);③类型契约 `types/index.ts` 是旧 V2 形状(JobScoringPolicy 实为旧 config 形状,与新 `schemas/job_policy.py` 完全不同名同实冲突);④ScannerView 纯前端模拟(无后端调用) | **可构建的 UI 原型,数据面为旧架构 + 硬编码 Mock** |
| 浏览器自动化 | — | 无 `browser/` 目录 | ❌ 未实现 | — | 按 PRD 属 Phase 2;无 BOSS 账号,Mock 优先 | **尚未实现**(按计划) |

### 2.3 工程基建

| 项 | 状态 |
| --- | --- |
| 依赖 | `pyproject.toml` 声明 numpy/openai/pydantic/python-dotenv/sentence-transformers;**未声明 fastapi/uvicorn/axios 等前后端依赖**(server 的依赖装在 .venv 但游离于锁文件外) |
| 测试入口 | `python main.py test` = unittest discover,41 个 test_*.py |
| 文档 | README 停在 Stage 4;`开发文档/项目预设结构.md` 为 Phase 1.5 老蓝图(其中 server/web 规划与现状不符);无 AGENTS.md |

## 3. 算法完整性问答(任务 2 逐项)

1. **能否输入任意岗位 JD+HR 要求生成 Policy?** ✅ 能,且经真实 Qwen 三岗位验收。链路:`PolicyCompileInput{job_title,jd_text,hr_requirements}` → `JobPolicyCompiler.compile()`(`config/policy_compiler.py`)→ 严格 Schema/Pydantic/确定性业务校验+规范化 → `PolicyCompilerService.compile_draft()`(`config/policy_service.py:14-39`,先授权再调模型,失败也留痕)。CLI:`policy-compile --live`。证据:reports/phase1_7_stage3(19 份真实响应与策略副本)。
2. **Policy 能否正确持久化、版本化、发布?** ✅ 草稿(revision 乐观锁)→人工确认(draft_hash)→版本(关联成功编译+确认,单事务)→发布(确认哈希)。`storage/repositories/policies.py:24-151`、`config/policy_service.py:67-84`。测试:`test_policy_persistence.py`(版本隔离/确认失效/跨岗位边界/无法降级写)全绿。
3. **不同岗位完全隔离?** ✅ 所有表主键含 `org_id+job_id`;候选/评估/嵌入查询全部先 `_authorize(org,job)`(base.py:72-96);跨组织零共享已测(`test_semantic_storage`、`cross_org_isolation` 验收)。
4. **扫描批次锁定固定 Policy 版本?** ✅ `create_scan_batch` 锁定当时 `current_policy_id/version`(`batches.py:6`);候选人**首次**绑定批次版本且不随新发布漂移(`candidates.py:161-188`,`first_batch_id`);同输入重采不替换封存(`prescreen_engine.py:71-72`)。测试:`test_database_candidates`/`test_prescreen_engine`(v1/v2 隔离多处)。
5. **Tier 0/1/2 读同一份 Policy?** ✅ 统一经 `PolicyInterpreter.load_for_candidate()` 得到同一 `ExecutablePolicy`(policy_id+version+hash+执行 profile 双哈希缓存,内容冲突即抛错),Tier0=`policy_hard_filter.evaluate_hard_filters(plan)`、Tier1=`policy_rule_matcher.evaluate_policy_rules(plan)`、Tier2=`policy_semantic_ranker.evaluate_policy_semantics(plan,...)`,全部读同一 `plan.policy`。服务层再校验候选绑定与快照身份(`prescreen_engine.py:61-65`、`semantic_scoring_service.py:41-55`)。
6. **Tier 3 是否完全使用动态 Rubric/权重/岗位证据?** ❌ **未改造成通用模式**。旧 `llm_reviewer.py` 仍是:固定四维 DIMENSIONS(AI实战/工程/提效/契合,`llm_reviewer.py:10-15`,AI 岗硬编码中文释义)、固定全局 RUBRIC、固定四维 40/30/20/10 深度加权(hybrid_engine 旧路径)、评分对象是旧 config 的 `job_target`。新体系虽已备好**输出协议** `DynamicPolicyAssessment`(`schemas/job_policy.py:150-155`,维度是值不是字段)+校验器 `validate_dynamic_assessment`(`config/policy_validation.py:430`,已在测试中用),但**没有任何生产代码调用它构造真实 Tier 3 请求**。Stage 3 报告 §明确:"通用Policy……不直接驱动现有Tier 3"。**结论:Tier 3 改造=未实现(仅契约就绪)**。
7. **还存在 AI 岗硬编码?** 通用链路(Tier0–2 via Interpreter)无岗位硬编码;残留于:旧 V2 引擎全套(合理,是兼容路径)、旧 `config/job_compiler.py`(prompt 写死 agent/rpa 锚点组)、server/前端(旧 V2 四维)。
8. **完整候选人匹配链路最远能到哪?** 在新体系内:**岗位创建→Qwen 编译→确认→发布→批次创建(锁版本)→候选人采集→简历快照→封存→Tier 0→Tier 1→(BGE 资产准备)→Tier 2 语义评分**,止于 `SemanticEvaluation`(SEMANTIC_SUCCESS_STATUSES)。**Tier 3 深评(通用 Rubric 版)未实现;HR 审核落库(evaluations 表写入)在 Repository 层已实现(`storage/repositories/evaluations.py`)但只有 CLI/测试驱动,无 HTTP 入口;前端展示完全未接**。真实证据:Stage 5 验收 JSON 27 条记录全链 Tier0→1→2(236 断言)。

未验证项声明:①`policy-compile --live` 本次未实测(需真实 Qwen,禁止调用),以 11-14 真实交付产物为准;②BGE 资产准备/语义评分本次未实测(需加载 1.3GB 本地模型,且主库 db-check 失败也会拒绝),以 Stage 5 验收记录为准;③`/api/candidates/evaluate_benchmark` 的 BGE 路径未实测(同因)。

## 4. 前后端实现程度(任务 3)

### 4.1 前端(vue-tsc 0 错误,vite build ✅)

| 维度 | 状态 | 证据 |
| --- | --- | --- |
| 路由/页面 | `/policy`(JobPolicy)、`/scanner`(Scanner)、`/review`(默认重定向) | `router/index.ts:8-12` |
| 岗位/Policy 配置页 | 可输入 JD/HR 要求→调 `/api/jobs/compile`→编辑**旧 V2 结构**(Tier0 阈值、**固定四维权重滑杆**、关键词表、agent/rpa 锚点组)→`/api/jobs/publish` | `JobPolicyView.vue:125-136`(四维硬编码),`:246-266`(compile/publish 调用) |
| 候选人列表/评分展示 | ReviewBoard:列表/Tier1-3 分数/debug_trace/证据/批量批准,数据来自 store | `ReviewBoardView.vue:29,182,231-238,271-274` |
| 人工审核 | 批准(调 `/api/candidates/approve`)/前端本地淘汰(`rejectCandidates` 仅改本地状态,无 API) | `useAppStore.ts:77-103` |
| 静态 Mock 残留 | 大量:①`DEFAULT_JOB_POLICY`/`MOCK_CANDIDATES` 4 条完整假记录(前端 `api/index.ts:10-433`);②**所有 API 均带 catch→假成功回退**(compile 返回伪造版本/publish 返回假 vector_path/approve 假 greeted_count)——**后端挂掉时前端显示一切正常,联调不可信**;③store 初始 policies=2 硬编码岗位、funnelLogs 假日志、`initFromBackend` 失败时 candidates=MOCK | `api/index.ts:436-516`、`useAppStore.ts:11-53,61-67` |
| ScannerPage | 纯前端状态机模拟(开始/滑块熔断/完成),`startScannerSimulation` 只改 store 状态,无任何 API | `ScannerView.vue:139-143` |

### 4.2 后端(运行中,`openapi.json` 实测 6 路由)

| 页面功能 | 前端调用 | 后端路由 | 实际处理逻辑 | 数据来源 | 是否已联调 |
| --- | --- | --- | --- | --- | --- |
| 健康检查 | `GET /health` | `/health`,`/api/health` | 返回 db_path+时间 | 直连 `storage/repository.py` 实例 | ✅(实测连通) |
| 启动引导 | `fetchBootstrap` | `GET /api/bootstrap` | **读旧表 job_policies/candidate_records/daily_quotas;若 job_policies 空→用旧编译器把根 config.json 种子化进库并回写根 config.json**(本次 13:28:01 已实际发生) | 旧 3 表+根 config.json | ✅(实测:policies=1)——**即损坏源** |
| LLM 编译策略 | `compileJobPolicy` | `POST /api/jobs/compile` | 调旧 `config/job_compiler.py::JobProfileCompiler`(真实 Qwen,schema=旧 V2 CompiledScoringProfile)→存旧表+回写根 config.json;**异常时静默 fallback:不调模型,仅把标题/JD 文本塞进当前配置模板再保存**(`server/app.py:98-106`) | 旧编译器/旧表 | 部分(未验证 LLM 成功路径;fallback 必然"成功",掩盖故障) |
| 发布策略 | `publishJobPolicy` | `POST /api/jobs/publish` | `repo.save_policy`(旧表+回写根 config.json)+若引擎已加载则 `reload_config` | 旧表+根 config.json | 部分(逻辑直通,但每次发布都覆写根 config.json——与损坏同机制) |
| 真实评估基准 | `runRealBenchmark` | `POST /api/candidates/evaluate_benchmark` | 加载旧 V2 `HybridScoringEngine('config.json')`(**当前必抛 ValueError**)+`engine.evaluate()`逐个 5 个 mock 简历(**真实 BGE;llm client 为真实 OpenAI→真实 Qwen**)→存旧表 | 根 config.json+tests/mock_data | **当前必失败**(config.json 已损坏;未实测——会触发真实模型) |
| 批准打招呼 | `approveAndGreetCandidates` | `POST /api/candidates/approve` | 旧表状态更新+daily_quotas 计数 | 旧表 | ✅(逻辑直通) |
| 采集控制 | **前端无调用**(Scanner 纯模拟) | 无路由 | — | — | ❌ 不存在 |

无任何真实"导入单份简历"API;无组织/用户/岗位管理 API;无对应新体系的任何接口。

### 4.3 重复实现清单(任务 4 前置)

| 重复项 | 新体系 | 旧体系(前后端线) | 关系 |
| --- | --- | --- | --- |
| Repository | `storage/repositories/RecruitmentRepository`(AccessContext 权限、审计、不可变守卫) | `storage/repository.py::RecruitmentRepository`(同名类!无权限、无审计、可 UPDATE) | **同名冲突**,import 时极易混淆 |
| Policy 存储 | `job_policy_versions/policy_drafts/...`(不可变+哈希+版本) | `job_policies`(单行可覆盖,job_id 主键,无版本链) | 业务语义重复 |
| 候选人记录 | `job_candidates/candidates/resume_snapshots/evaluations`(版本绑定+封存) | `candidate_records`(candidate_key 主键,JSON 大字段,可 UPDATE 覆盖) | 业务语义重复 |
| 配额 | 无(quota 属未来发送阶段) | `daily_quotas` | 独有旧概念 |
| Policy 语义 | `JobScoringPolicy`(三维评价层投影) | 旧 V2 config 形状(`types/index.ts::JobScoringPolicy` 同名不同构!) | **前端类型名与后端新 Schema 同名冲突** |
| 编译器 | `config/policy_compiler.py`(JobPolicyCompiler,新) | `config/job_compiler.py`(JobProfileCompiler,旧) | 名称易混 |

## 5. 数据库冲突审计(任务 4,最高优先级)

### 5.1 两套体系确认

- 新:`storage/database/__init__.py::Database`(WAL/外键/递归触发器校验)+迁移 001–008(files in `storage/database/migrations/`)+`storage/repositories/`。**唯一合法业务入口**(README:35 明示)。
- 旧:`storage/repository.py`(行 23–49 `_init_tables` 构造即建表;`_get_conn` 每次裸连接,无 PRAGMA 校验、无事务边界、无权限、无审计;行 66–94 `save_policy` UPSERT 覆盖+**回写根 config.json**)。`server/app.py:23` 模块级实例化,uvicorn 启动即触发建表。

### 5.2 同一个文件确认

两者默认均指向 `data/recruitment.sqlite3`(旧:`repository.py:8` `ROOT_DIR/"data"/"recruitment.sqlite3"`;新:`database_cli.py:12` 默认 `data/recruitment.sqlite3`/`RECRUITMENT_DB_PATH`)。**当前正被两个进程体系并发打开**(uvicorn 持有连接+审计/CLI 进程按需连接)。

### 5.3 三张表的来源与冲突

- `job_policies/candidate_records/daily_quotas` 由 `storage/repository.py:23-49` 创建(2026-11-15 13:05:13,首次 uvicorn 实例化;`db_check` 与文件 mtime 证据链见 `reports/phase1_7_stage5_5/batch1_audit.md` 附录 A)。
- **与 Stage 2 表业务重复**:`job_policies`≈`job_policy_versions`(但无版本/哈希/来源链);`candidate_records`≈`job_candidates+evaluations`(但无 org 隔离/版本绑定/封存/不可变);`daily_quotas` 新体系无对应(未来发送阶段概念)。风险:同一候选人既可被旧 API 写进 `candidate_records`(无任何校验),又会被新流程写 `job_candidates`,双写导致真相分裂。
- **当前 `db-check` 失败**:实测输出 `ok:false`,issues=`["Database schema drift: tables, indexes or triggers were changed", "Database migrations are pending"]`(root cause:迁移集定义 schema=41 表,实际 44 表;`connection.py:86` schema_hash 漂移)。**后果**:新体系一切需要 `database.check()` 的入口(`config/policy_cli.py:73,84`、`engine/semantic_cli.py:77` 等)拒绝主库——即 **7 项 Stage 5.5 CLI 测试 error、policy/semantic CLI 在主库不可用**。
- **连锁破坏**:13:28:01 某次 `GET /api/bootstrap`(或等价旧表读路径)触发空表种子化:`get_all_policies()`(repository.py:52-64)发现 `job_policies` 空→`_adapt_config_to_policy(根config.json)`→入库+`save_policy`→`_sync_to_root_config`(repository.py:130-145)以**白名单重建**根 config.json,**丢弃** `config_version/model_versions/cache` 三键→V2 引擎 `validate_config` 拒绝→42 项测试失败。这是"旧体系读操作带写副作用"的直接事故,已用 git 基线对比+独立进程验证闭环(§0-1)。

### 5.4 数据现状与丢失风险

41 张迁移表全盘点:`audit_events=4、jobs=1、organizations=1、users=1、memberships=1、drafts=1、compilation_runs=1、grants=1、migrations=8`,其余业务表(候选人/评估/嵌入/批次/发布)**全 0**;audit 时间范围 2026-11-14 14:51–14:54(Stage 3 验收残留)。旧 3 表:`job_policies=1`(种子行)、`candidate_records=0`、`daily_quotas=0`。
**结论:无真实业务数据,无数据丢失风险;三张旧表可安全废弃,唯一有价值恢复物是根 config.json(git 有基线)。**

### 5.5 新前后端如何安全复用 Stage 2 体系(方向性建议,不实施)

1. 后端改经 `storage.repositories.RecruitmentRepository`+`AccessContext` 做**HTTP 身份→服务端构造 context**的鉴权层(现 server 完全无鉴权,CORS `*`)。
2. Policy 读写全部走 drafts→confirm→version→publish 流程;废弃 `job_policies`/回写 config.json 机制。
3. 候选人/评估走 `acquire_candidate_for_job`/evaluations 仓库方法;前端类型改名对齐 `JobScoringPolicy` 新契约。

## 6. Stage 5.5 待解决问题清单(任务 5)

| 检查项 | 状态 | 证据/说明 |
| --- | --- | --- |
| 缺失正向锚点(MISSING_POSITIVE_ANCHOR→BLOCKED) | ✅ 已实现 | `policy_anchor_validator.py:143-161`;三真实策略共 7 项 ERROR |
| 多 Requirement 共享锚点(BROAD_ONLY→WARNING) | ✅ 已实现(仅记录,不治理) | `:143-159`;真实策略 13 项 WARNING |
| Requirement 原子性 | ❌ 未实现(Batch 2 范围) | 无对应检查代码 |
| ALL/ANY 组合结构深度检查 | ❌ 未实现(Batch 3 范围;基础图校验在 Stage 3 `policy_validation` 已有:环/引用/提升) | `config/policy_validation.py:251-268` |
| 孤儿 Requirement(UNCLASSIFIED) | ✅ 已实现(本批新增) | `:163-196`;真实 b2b 发现 2 例 |
| 无效引用→维度 INDETERMINATE | ✅ 已实现 | `dimension_coverage` rate=None |
| quality BLOCKED 与 publishable/退出码关系 | ✅ 已实现且**刻意解耦** | `policy_cli.py:86,128`:quality 嵌入输出但 `publishable`/exit code 仍只由旧诊断决定 |
| 发布质量门控实际生效 | ❌ 未实现(Batch 5 范围) | `publish_policy`(policies.py:131-151)不读 quality 报告 |
| Schema 缺口:需求级评估范围字段 | ❌ 存在(已如实上报) | `PolicyRequirement` 无路由枚举,靠投影推导+UNCLASSIFIED 兜底 |

## 7. 最小业务闭环 8 环节判定(任务 6)

| # | 环节 | 判定 | 依据 |
| --- | --- | --- | --- |
| 1 | HR 创建岗位 | **已实现(新体系,CLI/Repo);无 HTTP** | `create_job`;server 无岗位管理 API |
| 2 | 输入 JD+HR 要求 | **已实现(CLI/policy-compile);前端表单已有但走旧编译器** | §3-1 |
| 3 | Qwen 编译 Policy | **已实现并真实验收** | §3-1 |
| 4 | 检查/确认/发布 | **已实现(full CLI 链)** | §3-2;`policy-confirm/version/publish` |
| 5 | 导入模拟简历 | **已实现(Repo `acquire_candidate_for_job`,测试/验收用过);无 HTTP;前端无入口** | `candidates.py:161` |
| 6 | Tier0→1→2 | **已实现并真实 BGE 验收(236 断言)** | §3-8 |
| 6' | Tier 3(通用 Rubric) | **缺少必要功能(未实现)** | §3-6 |
| 7 | 保存结果+绑定岗位/Policy 版本 | **已实现(Repository 层:evaluations+attempts,不可变);无 HTTP 驱动** | `evaluations.py` |
| 8 | 前端显示分数/证据/风险/审核状态 | **前后端未联调且架构错位**(§4:前端=旧 V2 契约+mock 回退;后端=旧体系直连) | §4 |

**闭环结论:1–7 在新体系后端侧全部已实现且经测试/真实验收,唯独卡在两处——(a)无 HTTP 服务层把新体系暴露给前端;(b)通用 Tier 3 未实现。8 完全未接。当前 server/frontend 平行闭环走的是旧 V2 单岗位路径,且该路径今天已被自身配置覆写事故弄断。**

## 8. 本次审计执行的操作清单(供复核)

只读检查:git 系列、全部源码阅读、`db-check`、`mode=ro` SQLite 盘点×3、进程/命令行、GET health/bootstrap/openapi、Stage 3/5 验收 JSON 解析。
执行过的测试(全部离线 mock、临时目录):①`tests.test_session/test_benchmark/test_engine` → 10 errors(定位损坏);②全量 `unittest discover` → 452 ran/42 failed(现状量化);③`vue-tsc --noEmit` → 0 错误;④`npx vite build` → 成功(产物已删);⑤独立进程 validate_config 双配置对比 → 因果闭环。
跳过项及原因:`policy-compile/policy-diagnose --live`(真实 Qwen)、`semantic-prepare/embedding-check`(真实 BGE+主库 db-check 失败)、`POST` 类 API(BGE/Qwen/写库)、一切迁移/修复(不在授权范围)。
