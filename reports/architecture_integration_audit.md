# Boss HR Helper — 架构一致性审计报告

审计日期:2026-11-15(只读审计,不实施修复)
姊妹文档:`project_status_audit.md`(进度盘点)、`next_execution_plan.md`(执行计划)

---

## 1. 核心架构事实

项目当前实际存在**两套平行架构**,均处于可运行状态,且**共享同一个 SQLite 文件**:

```
新体系(Phase 1.7,算法主线,已验收)         旧体系(前后端原型线,运行中)
─────────────────────────────────────      ─────────────────────────────────────
schemas/job_policy.py (JobScoringPolicy)   frontend/src/types (旧V2形状,类名相同!)
config/policy_compiler.py (Qwen→泛型)      config/job_compiler.py (Qwen→旧V2固定结构)
config/policy_service.py (草稿→确认→版本)   server/app.py (直接save_policy覆盖)
storage/repositories/ (AccessContext+审计)  storage/repository.py (无权限无审计可UPDATE)
engine/policy_interpreter.py               engine/hybrid_engine.py (config.json驱动)
  ├─ policy_hard_filter (Tier0)              ├─ hard_filter (Tier0)
  ├─ policy_rule_matcher (Tier1)             ├─ rule_matcher (Tier1)
  ├─ policy_semantic_ranker (Tier2)          ├─ semantic_ranker (Tier2)
  └─ (Tier3 未通用化)                        └─ llm_reviewer (Tier3, AI岗硬编码)
storage/database/ (migrations 001-008)      data/recruitment.sqlite3 上自建3表
data/recruitment.sqlite3 (41迁移表)         ↑ 同一个文件!
```

两套体系没有任何一层共享业务代码;唯一的物理交点是数据库文件——而这正是事故源(下§2)。

## 2. 已发生的事故链(证据闭环)

**事故:** 2026-11-15 13:28:01,根目录 `config.json` 被覆写,V2 引擎全链路测试 42 项失败。

```
uvicorn server.app:app --reload 启动(13:05:11, 所有者本人)
 → storage/repository.py::RecruitmentRepository() 构造函数
 → _init_tables(): 在业务库创建 job_policies/candidate_records/daily_quotas 3 张迁移集之外的表(13:05:13)
 → 主库 schema drift,database.check() 从此 false,新体系 policy/semantic CLI 全部拒绝主库
GET /api/bootstrap(13:28:01,读操作!)
 → repo.get_all_policies() 发现 job_policies 表为空
 → _adapt_config_to_policy(根config.json) 将 V2 配置"翻译"成旧 policy 形状并入库
 → save_policy() → _sync_to_root_config(): 用白名单键重建根 config.json
 → 丢弃 config_version / model_versions / cache 三个必需键
 → config/validation.py:22 拒绝 ("V2 config_version is required")
 → HybridScoringEngine 构造失败 → 42 项测试挂(41 errors + 1 fail)
```

**破坏性设计三连(旧体系):**
1. **读操作带写副作用**:GET `/api/bootstrap`(幂等语义)在空库时静默种子化+回写配置文件(`storage/repository.py:52-64`)。
2. **文件系统越权**:数据库层直接覆写全局配置文件 `config.json`(`repository.py:130-145`),该文件同时是新旧引擎的共同依赖——旧体系把新体系(及自身)的地基拆了。
3. **无漂移防护的 schema 外挂**:`CREATE TABLE IF NOT EXISTS` 到受迁移校验保护的库,触发 `connection.py:86` schema_hash 校验失败。

**可恢复性:** `config.json` 完整存在于 git 基线(`git checkout -- config.json`);三张旧表无数据价值(§5.4);无真实业务数据损失。**但修复动作本轮未执行**(超出只读授权)。

## 3. 契约与命名冲突(联调前必须消除)

| 冲突 | 新体系 | 旧体系/前端 | 后果 |
| --- | --- | --- | --- |
| 类名 `RecruitmentRepository` | `storage/repositories/__init__.py:12` | `storage/repository.py:12` | 同名异构,import 一字之差;`from storage.repository import` 静默拿到旧版 |
| 类名 `JobScoringPolicy` | `schemas/job_policy.py:127`(三维投影/锚点/Rubric) | `frontend/src/types/index.ts`(旧V2 config形状) | 前后端"对上名对不上义",联调试通也是错位联调 |
| 编译器类名 | `JobPolicyCompiler`(config/policy_compiler.py) | `JobProfileCompiler`(config/job_compiler.py) + server 实际引用后者 | 极易接错 |
| Policy 版本概念 | `policy_version: int`(1,2,3...)+ 不可变哈希链 | `policy_version: str`("v2.1"+"时间戳") | 语义完全不同 |

## 4. 权限模型断层

- 新体系:`AccessContext(org_id, user_id)` 由可信服务端构造;每次读写经 `_authorize`(org 活跃成员→岗位级 grant→权限位列);写操作全留 `audit_events`;`ProvisioningContext` 仅部署引导(`storage/models/context.py`、`base.py:72-136`)。
- 旧后端:**零认证、零授权、零审计**;CORS `allow_origins=["*"]` + `allow_credentials=True`(`server/app.py:15-21`)——任何本机页面可直接读改全部业务数据。审核写路径 `/api/candidates/approve` 无身份即改状态+计数。
- 断层结论:现 server **不能**作为新体系的前置暴露层直接暴露;需要完整的鉴权中间层,而非补丁。

## 5. 数据库一致性

1. **文件双开**:uvicorn(旧连接,不带 PRAGMA 校验)与 CLI/测试(新 Database.connect,校验 WAL/外键/recursive_triggers)并发访问同文件;旧连接绕过 `busy_timeout` 约定,存在锁竞争风险(当前未见死锁,但设计上已违规)。
2. **迁移校验被架空的机制**:`Database._verify_history`(connection.py:77-90)用全库 schema_hash 防漂移——这是新体系的保护机制而不是障碍;正确方向是清走外挂表,而不是放宽校验。
3. **不可变模型 vs 可变模型**:新体系 evaluations/policy_versions 有 `004/005_business_guards/immutable_guards` 迁移的 TRIGGER 级防改;旧表靠 `ON CONFLICT DO UPDATE` 天然覆盖历史。同一候选人两条真相记录是迟早的事。
4. **当前 4 条 audit_events、1 job/draft/run(11-14 Stage 3 残留)+ job_policies 1 行(种子)**,无真实业务数据(详见 status 报告 §5.4)。

## 6. 引擎一致性(回答"Tiers 是否同一 Policy")

- **新体系:是。** `PolicyInterpreter.compile_policy` 产出的单个 `ExecutablePolicy`(含 policy_hash+profile_hash)是 Tier0/1/2 唯一输入源;缓存身份键含双哈希,内容与身份不符即抛错(`policy_interpreter.py:126-133,184-195`)。服务层再校验候选人绑定一致性。这是本审计确认的最强一致性设计。
- **旧体系:弱。** 单配置快照+锁+reload 语义(`hybrid_engine.py:64-77`),但配置本身可被旧 API 覆写(本次事故即此),reload 是全局热替换,无版本绑定概念。
- **Tier 3 是断层**:新体系的 Rubric/权重/维度在每个 `JobScoringPolicy.scoring_dimensions` 里动态携带,输出协议 `DynamicPolicyAssessment` 已定义并测试,但没有生产实现;旧 Tier 3 四维硬编码且 prompt 携带旧 config job_target。**"通用 Policy 驱动四级全链"差这最后一环。**

## 7. 工程一致性

1. **依赖声明断裂**:fastapi/uvicorn 实际在用但不在 `pyproject.toml/uv.lock`(锁文件外依赖,环境不可复现);前端依赖独立于 node 生态(正常),但服务启动方式(uvicorn 命令行)未入任何脚本/文档。
2. **文档一致**:README(Stage 4 止)、`开发文档/项目预设结构.md`(Phase 1.5 止,规划中 server/web 与现状不符)。新协作者(及 AI 代理)无法从文档得知:v2 新体系入口、server 是旧体系、config.json 被动覆写风险。**无 AGENTS.md**——本仓库多次出现"工具接力开发现象"(Codex→Trae→OpenCode),缺一份强约束文档是反复踩坑的环境因素之一。
3. **测试防护网**:432→459 项离线回归是资产,但它假设根 config.json 健康。本次事故证明:**没有"配置完整性"守门测试**(没有人断言 `validate_config(根config.json)` 通过 / config.json 与 git 一致 / db-check ok)。这层测试本可在 uvicorn 一启动就报警。
4. **进程治理**:dev server 以 `--reload` 常驻并持库连接,与离线回归/CLI 共写一库;缺"运行中服务状态下禁止 schema 实验变更"的约定。

## 8. 架构级结论

1. **算法主线是内聚、可验证的**——契约(→Schema)→编译(→Qwen+确定性校验)→执行(→Interpreter 统一四级输入)→持久化(→不可变+版本绑定)→质量门(Batch 1)环环闭环,并有 459 项测试和真实 BGE/Qwen 验收史。
2. **前后端线是一套"平行 MVP 骨架"**,在旧架构上自洽(UI 完整、能跑通旧闭环),但它以三种方式侵蚀主线:同文件建表(架构违规)、覆写共享配置(已发生事故)、同名词误导(联调隐患)。
3. **当前不存在"统一系统",存在"一个已验收内核 + 一个旧架构外壳"**。MVP 的正确路径不是修补外壳,而是把外壳接到内核上(见 `next_execution_plan.md` P0/P1)。
