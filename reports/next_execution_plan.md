# Boss HR Helper — 下一阶段执行计划

制定日期:2026-11-15(基于同日只读审计:`project_status_audit.md`、`architecture_integration_audit.md`)
范围声明:P0 为止血/恢复项,P1 为合并主线与最小闭环项,P2 为增强项。**本轮审计未实施任何修复;以下均为待人工确认后的执行计划。**

---

## P0-1 恢复根目录 config.json 并加配置守门测试

- **要解决的问题**:旧后端种子化逻辑已覆写 `config.json`(丢失 `config_version`/`model_versions`/`cache`),V2 引擎构造失败,42/452 测试失败(证据:audit §2 事故链;`git diff config.json`)。
- **涉及模块/文件**:`config.json`(git 基线恢复)、新增 `tests/test_config_guard.py`。
- **实施步骤**:
  1. `git checkout -- config.json`(或 `git restore config.json`)。
  2. 新增守门测试:断言 `validate_config(json.load(根config.json))` 通过;断言工作区 config.json 与 git HEAD 一致(允许显式白名单变更)。
  3. 可选加固:`storage/repository.py` 的 `_sync_to_root_config` 在 P0-3 废弃前先行加"禁止写入仓库根 config.json"的保护(或直接跳到 P0-3)。
- **依赖**:无(其他一切的前置)。
- **验收标准**:`python main.py test` 全量恢复至 459/459 通过(0 errors);守门测试随套件常驻。
- **预期测试**:全量回归;新 `test_config_guard` 2–3 项。

## P0-2 停止旧服务对主库的 schema 污染(与所有者协调后执行)

- **要解决的问题**:uvicorn(`server.app:app --reload`)常驻并在业务库持有 3 张外挂表,造成 `db-check` schema drift,阻塞新体系一切 policy/semantic CLI 与 7 项 Stage 5.5 测试。
- **涉及模块/文件**:运行中进程;`server/app.py:23`(模块级 repo 实例);`storage/repository.py:8`(DB_PATH 指向主库)。
- **实施步骤**(按最小破坏排序):
  1. 与所有者确认停服窗口,停止当前 uvicorn(计划内操作)。
  2. 备份 `data/recruitment.sqlite3`(冷拷贝)。
  3. 将旧体系指向**独立 SQLite 文件**(如 `data/legacy_console.sqlite3`),一行改动:`repository.py` 的 `DB_PATH`;或直接实施 P0-3 的接口下线。
  4. 处理主库 3 张空表(见 P0-3;在 P0-3 完成前主库 db-check 仍会失败——**若需立即恢复 db-check**,在备份确认后执行一次性清理:3 张表均 0/1 行无价值数据,`DROP TABLE job_policies/candidate_records/daily_quotas` + WAL checkpoint;该动作属 schema 变更,需所有者显式批准后执行)。
- **依赖**:P0-1(先恢复测试基线再动库);所有者批准。
- **验收标准**:`python main.py db-check` 返回 `ok:true`;全量回归 459/459(含此前 error 的 7 项 CLI 子进程用例)。
- **预期测试**:`db-check`;全量回归;`test_database_infrastructure`。

## P0-3 拆除旧体系的写副作用与暴露面(决策项)

- **要解决的问题**:旧后端"GET 带写、无鉴权、CORS *、可覆写配置"四连(architecture 报告 §2/§4)。在 P1 重写 server 之前,旧 server 不应再以现状运行。
- **涉及模块/文件**:`server/app.py`、`storage/repository.py`。
- **实施步骤**(二选一,由所有者决策):
  - 方案 A(推荐,若接受短期无前端):下线 `server/` 与 `storage/repository.py` 的运行入口(移出 import 路径或标记 deprecated 门卫),保留代码供 P1 参考;前端暂时只做静态演示(api 回退层本就会兜住,且 P1 将替换它)。
  - 方案 B(需保留演示):最小补丁——`/api/bootstrap` 只读(空库返回空,不种子化)、删除 `_sync_to_root_config` 调用、CORS 收紧到 localhost:5173、/evaluate_benchmark 换 Mock embedder 开关。**仍不解决权限/审计缺失,仅降险**。
- **依赖**:P0-2(旧体系先行隔离)。
- **验收标准**:方案 A:8000 端口无服务或仅 health;任何路径均不触碰根 config.json 与主库。方案 B:GET 请求零写副作用(diff config.json + audit_events 不变);CORS 非 `*`。
- **预期测试**:新增接口级回归(bootstrap 幂等:连续两次 GET,库与文件零变化)。

## P1-1 新版 FastAPI 服务层:把新体系核心能力暴露为 HTTP(前后端合并主线)

- **要解决的问题**:最小闭环 8 环节中 1–7 的引擎/持久化已就绪但无 HTTP 层;前端必须走旧 API 才能拿到任何数据(architecture §1 断层)。
- **涉及模块/文件**:新增 `server/v2/`(或重构 `server/app.py`,建议前者并存过渡期):`deps.py`(HTTP 身份→`AccessContext` 构造,Session/Token 由所有者定)、`routers/jobs.py`、`routers/policies.py`、`routers/candidates.py`、`routers/evaluations.py`;复用无改动:`config/policy_service.py`、`storage/repositories/`、`engine/prescreen_engine.py`、`engine/semantic_scoring_service.py`。
- **实施步骤**:
  1. 鉴权中间层:登录态→(org_id,user_id) 映射;部署引导沿用 `ProvisioningContext`(不入 HTTP)。
  2. 岗位/编译/确认/发布端点:thin wrapper over `PolicyCompilerService`(+ `policy-validate` 的 quality 面板,复用 `engine/policy_validator.validate_policy_quality`)。
  3. 批次+候选人导入端点:`create_scan_batch` + `acquire_candidate_for_job`(JSON 简历上传;Mock 数据来源=tests/mock_data,先无浏览器)。
  4. 初筛/语义评分端点:`PreliminaryScreeningEngine.evaluate`、`SemanticScoringService.evaluate`(BGE 懒加载单例,进程内复用;显式开关 Mock embedder 以便无模型联调)。
  5. 结果/审核端点:evaluations 仓库读取 + HR 审核事件写入(`hr_review_events`)。
  6. OpenAPI 契约生成,作为 P1-2 前端契约。
- **依赖**:P0-2(db-check 必须恢复)、组织/用户引导决策。
- **验收标准**:所有端点负例(未授权/跨组织/未知 ID)返回预期错误且 `audit_events` 有痕;评分端点无模型时优雅降级;swagger 可用。
- **预期测试**:新增 `tests/test_server_v2_*.py`(权限隔离、幂等、只读性快照、与 CLI 结果一致性对照——同一候选人 HTTP 评分 == CLI 评分)。

## P1-2 前端切换到新契约(替换 mock 回退层)

- **要解决的问题**:前端类型/页面均为旧 V2 形状;api 层 catch→假成功回退掩盖故障,联调不可信(status §4.1)。
- **涉及模块/文件**:`frontend/src/types/index.ts`(对齐 P1-1 OpenAPI,`JobScoringPolicy` 改名或重定义为新 Schema)、`api/index.ts`(删除全部静默回退,失败必须可见)、三个 View(策略页改为新三级结构:requirements/anch础/维度覆盖率+quality 报告;看板改为 Preliminary+Semantic+审核状态;Scanner 对接批次/导入 API)。
- **实施步骤**:类型生成→api 层重写(无回退,错误显式 toast)→页面逐个迁移→保留 Mock 环境开关(读环境变量,不再隐式)。
- **依赖**:P1-1(OpenAPI 契约)。
- **验收标准**:`vue-tsc` 0 错误、build 成功;后端关闭时 UI 显示"不可用"而非假数据;走完 P1-3 场景全部真实数据。
- **预期测试**:前端最小冒烟(可手工 checklist;UI 无测试框架,P2 再补)。

## P1-3 端到端 Mock 业务联调(最小闭环验证)

- **要解决的问题**:验证 8 环节闭环真实可跑通(这是"具备完整 Mock 联调条件"的实证)。
- **依赖**:P1-1、P1-2。
- **实施步骤/场景脚本**:创建组织/用户(引导)→建 2 个岗位(AI/Java)→policy-compile(真实 Qwen 或 RecordedCompilerClient mock 注入)→人工确认→发布→quality 报告(BLOCKED 项如实展示)→建批次→导入 5 份 mock 简历→跑 Prescreen→READY_FOR_DEEP 者跑 Semantic→看板查看分数/证据/coverage/partial 原因→HR 批准/淘汰→audit 全程有痕。
- **验收标准**:全流程零人工 SQL;两次重放结果一致(除时间戳);跨岗位数据隔离可见。
- **预期测试**:一条 E2E 回归测试(临时库+mock 编译器/嵌入器)。

## P2-1 Tier 3 通用化(补齐四级末环)

- **要解决的问题**:`llm_reviewer` 四维/Rubric/权重 AI 岗硬编码;新 Policy 的 `scoring_dimensions[*].rubric/weight` 无法驱动深评。
- **涉及模块/文件**:新增 `engine/policy_llm_reviewer.py`(按 `ExecutablePolicy` 构造请求:维度=ScoringDimension 列表、Rubric 带 per-band、输出=`DynamicPolicyAssessment`,复用 `validate_dynamic_assessment` 与证据引用校验);`engine/llm_observability.py` 观测复用;旧 `llm_reviewer.py` 保留为 legacy。
- **依赖**:P1-1(端点形态);Stage 5.5 Batch 5(可选:发布门控保证 Rubric 质量)。
- **验收标准**:任意岗位 Policy 驱动 Tier 3,schema 校验+quote 校验+缺失证据降置信全部生效;三套现有岗位 fixture 回归。
- **预期测试**:mock LLM 的确定性 Tier3 套件 + 1 次真实 Qwen 验收(显式 --live)。

## P2-2 Stage 5.5 Batch 2 启动(见问答 B)

- 内容:Requirement 原子性检查 + ALL/ANY 结构深度检查 + 编译期不变量"每个 requirement 至少被一层引用"(b2b 孤儿实证)。设计已就绪(`reports/phase1_7_stage5_5/batch1_validation_design.md` §7 建议),Schema 缺口(评估范围字段)决策可并行。
- **依赖**:P0-1/P0-2(测试与主库恢复);无 P1 依赖,可与 P1 并行。

## P2-3 工程收尾项

- fastapi/uvicorn 入 `pyproject.toml` 锁定;README 重写(双架构说明→合并后单架构);新增 `AGENTS.md`(工具接力协作的最小守则:禁改区、测试入口、常见陷阱);`server/`+`storage/repository.py` 于 P1-1 完成后正式归档删除(决策时点)。

---

## 明确回答五个问题

**A. 现在最应该优先修复哪三个问题?**
1. **恢复 `config.json`(P0-1)**——一行 git restore 即可解除 42 项测试失败,一切验证的前提;同时加守门测试防复发。
2. **隔离旧后端与主库(P0-2/P0-3)**——停 uvicorn → 旧库路径切走或下线写副作用接口 → 清 3 张空外挂表恢复 `db-check`;不解决它会持续腐蚀新体系(CLI/嵌入/评分全被挡)。
3. **启动 P1-1 新 HTTP 层**——这是消除"平行架构"的根治动作;在它落地前,禁止再对旧 server 做功能增强(只许 P0-3 的降险补丁)。

**B. Stage 5.5 Batch 2 是否具备启动条件?**
**代码与设计上:是。** Batch 1 交付物完整(31 项测试中 21 validator+CLI 部分当前仅受 config.json/主库连带影响,非自身缺陷);Batch 2(原子性+ALL/ANY)依赖的 Schema 结构(requirement_groups、scoring_dimensions)已就绪;设计文档已预研;三岗位真实策略的孤儿/共享锚点实证了检查的必要性。
**运行环境上:需先完成 P0-1**(否则回归基线是红的,无法验证"未破坏历史");**P0-2 建议同办**(CLI/主库路径验证需要 db-check 绿)。结论:完成 P0-1(+建议 P0-2)后即可启动,无算法侧阻塞。

**C. 前后端是否已经具备开展完整 Mock 业务联调的条件?**
**否。** 依据(status §4、§7):①后端只有 6 个旧架构接口,新体系的岗位管理/批次/候选人导入/初筛/语义评分/审核**零接口**;②前端契约是旧 V2 形状(类名同名异构),改造成本≈重写 api+types+两个页面;③api 层静默假成功回退使联调结果不可信;④当前唯一"真实评估"端点因 config.json 损坏必然失败,且会触发真实 BGE/Qwen(非 Mock);⑤无鉴权层,连"HR 是谁"都无法表达。
**具备条件的最短路径**:P0-1/2/3 → P1-1(mock 编译器/嵌入器注入开关)→ P1-2(去回退+新契约)→ 即可 P1-3 联调。复用面很大(引擎/仓库/校验全部现成),缺的主要是 HTTP 薄层与前端契约。

**D. 是否存在应该合并或废弃的重复实现?(仅建议,不执行)**
| 项 | 建议 | 理由/时点 |
| --- | --- | --- |
| `storage/repository.py`(旧 Repo) | **废弃** | 与新 Repo 同名冲突、无权限/审计、有写副作用;P1-1 落地后删除,期间标记 deprecated |
| `server/app.py` 现有 6 接口 | **废弃并由 P1-1 重写** | 架构错位(直连旧 Repo+旧引擎);仅 health 可保留 |
| `config/job_compiler.py`(旧编译器) | **废弃** | prompt 硬编码 agent/rpa、产出旧 config 形状;P1-1 后删除(Stage 3 已判其为"AI 岗 V2 兼容入口") |
| 旧引擎 Tier0-2(`hard_filter/rule_matcher/semantic_ranker`) | **保留为 legacy 兼容路径,不投入新特性** | 与新 policy_* 系列功能重叠,但被 benchmark/历史报告/旧 V2 CLI 依赖;待 Tier 3 通用化(P2-1)后评估整体下线 |
| 前端 `types/index.ts::JobScoringPolicy` | **重命名并对齐新 Schema**(P1-2 一并做) | 与后端新契约同名冲突 |
| `tests/mock_data.py` BENCHMARK_CANDIDATES | **保留** | 唯一 Mock 简历来源,P1-3 联调复用 |
| 3 张旧表 | **主库 DROP(需批准)** | 0 数据价值,阻塞 db-check;见 P0-2 |

**E. 距离"多岗位动态配置→候选人匹配→HR 审核"MVP 还有哪些具体缺口?**
按闭环环节列(编号同 status §7):
1. **HTTP 服务层缺失**(环节 1,3,4,5,7 的入口):新体系 100% CLI/Repository,无鉴权 API——P1-1。
2. **前端契约错位+假回退**(环节 8):页面结构在但数据面全是旧形状/Mock 兜底——P1-2。
3. **通用 Tier 3 未实现**(环节 6'):`DynamicPolicyAssessment` 协议在、`validate_dynamic_assessment` 在、无生产调用;Rubric/权重/维度仍 AI 岗硬编码——P2-1(MVP 若可接受"Tier0-2+人工终审",此项可后置,但需产品决策)。
4. **简历导入无业务入口**:Repo 方法在(`acquire_candidate_for_job`)但无 API/UI(浏览器采集是 Phase 2,P1-3 先用 mock 简历 JSON 导入)。
5. **HR 审核留痕闭环**:evaluations+hr_review_events 表与仓库在,无端点;旧 `/api/candidates/approve` 不写新表——并入 P1-1 第 5 步。
6. **发布质量门未生效**:quality BLOCKED 不阻塞 publish(有意设计,Batch 5 范围);MVP 至少应在发布 UI 展示 quality 报告(已随 Batch 1 可用)。
7. **主库健康**:P0-2 未完成前,主库上无法执行任何 policy/semantic 操作——MVP 的隐性硬依赖。
8. 环境/工程类:依赖锁缺失、README/AGENTS 缺失、dev server 与测试库共享(P2-3)。

---

## 建议执行顺序(时间线)

```
第 1 步(当天): P0-1 恢复 config + 守门测试   [~0.5h,阻塞解除]
第 2 步(当天): P0-2 停服→隔离旧库→(批准后)清表→db-check 绿  [~1h+审批]
第 3 步(同步): P0-3 决策方案 A/B(下线或降险旧 server)
第 4 步(本周): P1-1 新 HTTP 层(鉴权+6 组端点)  ←──┐ 可并行: P2-2 Stage5.5 Batch 2
第 5 步:        P1-2 前端新契约(去回退)              │
第 6 步:        P1-3 端到端 Mock 联调(闭环验收)  ←──┘
之后:           P2-1 Tier 3 通用化 → P2-3 工程收尾(含旧实现归档删除)
```

完成本报告后停止,等待人工确认下一阶段开发任务。
