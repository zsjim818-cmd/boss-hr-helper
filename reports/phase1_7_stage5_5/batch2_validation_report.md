# Stage 5.5 Batch 2 — 交付报告

日期：2026-11-15
基线：P0 恢复完成后的干净状态（`git restore config.json`、主库 3 张外挂表已由所有者 DROP + WAL checkpoint、`db-check` ok、Batch 1 时代 459/459）。
范围：Requirement 原子性检查、ALL/ANY 结构深度检查、Requirement 评价层引用完整性（编译期不变量）。**未做**：Schema 变更、发布门控、自动修复、迁移、Policy 修改。
安全约束遵守：保留全部既有未提交/未跟踪成果；**未**使用 `git clean`/`git reset --hard`/批量删除；备份文件未删除（已移出仓库并验证有效性，见 §1.2）；未修改任何正式 Policy/迁移/已发布版本。

---

## 1. P0-1 收尾（配置与主库守门）

### 1.1 新增 `tests/test_config_guard.py`（3 项）

| 测试 | 断言 |
| --- | --- |
| `test_root_config_passes_v2_validation` | 根目录 `config.json` 必须通过 `config.validation.validate_config`（`config_version`/`model_versions`/`cache`、权重、阈值、锚点组等全部校验） |
| `test_root_config_matches_committed_baseline` | 工作区 `config.json` 必须与 `git show HEAD:config.json` 逐字节一致（"不被篡改"守门；git 不可用时 skip——非 git 副本不误判） |
| `test_main_business_database_stays_drift_free` | 主业务库存在时，`Database.check()` 必须 ok（与 `python main.py db-check` 同语义：schema 漂移/待迁移/完整性问题都会失败）；库不存在时 skip |

失败信息直接给出可执行指令（`git restore config.json` / 查 db-check）。该守门可分别复现 2026-11-15 事故的两个半面（config 被覆写、外挂表导致漂移），一启动测试即报警。

### 1.2 备份文件处置（遵循补充约束 3）

`data/recruitment.sqlite3.bak_20261115` **未删除**，已移出仓库至项目外同盘安全目录：

- 位置：`D:\Boss-project\recruitment.sqlite3.bak_20261115`
- 移动前检查：存在、716800 字节
- 移动后有效性验证（只读）：`PRAGMA integrity_check = ok`；保留修复前状态（含 `job_policies`/`candidate_records`/`daily_quotas` 三张表）；`jobs=1`、`audit_events=4`，与修复前业务数据一致。
- `data/` 目录现仅剩主库；`git status` 不再出现 `data/` 条目。

---

## 2. Batch 2 实现内容

### 2.1 新增模块 `engine/policy_structure_validator.py`

9 个确定性、只读检查代码（设计依据与 fixture 标定详见 `batch2_validation_design.md`）：

**原子性（2）**
- `EMBEDDED_ALTERNATIVE_REQUIREMENT`（WARNING）：非 ANY 组直接成员、非 RESPONSIBILITY 的原子要求，source 内嵌「或者」或带技术词两侧的「或」；数量习语（一个或多个…）排除。
- `CONJOINED_TECH_REQUIREMENT`（WARNING）：`和/与/以及` 连接两侧各含不同 Latin 技术 token；`并/同时`（动词串联）与顿号列举不查；词内用法（参与/饱和…）黑名单剔除。

**ALL/ANY 结构（6）**
- `SINGLE_MEMBER_GROUP`（WARNING）；`REDUNDANT_GROUP_WRAPPER`（WARNING）；
- `DUPLICATE_GROUP_MEMBERS`（operator/priority 相同 → WARNING；冲突 → **ERROR**）；
- `PREFERRED_GROUP_DEMOTES_REQUIRED`（**ERROR**）——依据 `policy_hard_filter.evaluate()` L368–370：Tier 0 跳过非 REQUIRED 组的整棵子树，REQUIRED 硬约束会被静默丢弃；
- `ALL_SEMANTICS_MISMATCH`（WARNING）：ANY 组但 source 含同时性短语（「不要求同时具备」不误报）；
- `ORPHAN_REQUIREMENT_GROUP`（WARNING）：组叶子闭包与（硬过滤 ∪ 关键词组 ∪ 维度投影）无交集 → 组合语义从未执行。

**引用完整性/接线（1）**
- `PREFERRED_REQUIREMENT_NOT_KEYWORD_WIRED`（WARNING）：加分要求未接任何关键词组（已 UNCLASSIFIED 的跳过，避免双报）。

### 2.2 契约与聚合（向后兼容）

- `schemas/policy_validation.py`：`PolicyQualityIssue +group_id`；`PolicyQualityReport +orphan_group_count`；既有字段/语义零改动。
- `engine/policy_validator.py`：聚合三类检查；`validator_version → policy-quality-validator-v1.2`。
- `config/policy_service.py`：`compile_draft` 与 `update_draft` 返回值新增 `"quality"`（完整报告）。**报告性、不门控**：`publishable`、草稿 diagnostics、版本/发布路径均不变（发布门控属后续批次，需另行批准）。HR 手工编辑同样被复核。

## 3. CLI 回归（真实策略，文件模式，无数据库/无模型）

`python main.py policy-validate --policy <policy.json>` × `reports/phase1_7_stage3/policies/*.json`：

| case | returncode | status | 语义要求 | 直接覆盖 | 宽泛 | 缺锚点 | 未分类 | 孤儿组 | Batch 2 发现 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ai_automation | 0 | BLOCKED | 10 | 0 | 7 | 3 | 0 | 0 | 无 |
| java_backend | 0 | BLOCKED | 5 | 0 | 2 | 3 | 0 | 0 | 无 |
| b2b_sales | 0 | BLOCKED | 6 | 1 | 4 | 1 | 2 | **1** | `ORPHAN_REQUIREMENT_GROUP`（`grp_bonus_items`） |

machine-readable：`batch2_policy_validation.json`。结论：真实三策略的结构质量良好（原子性与 ALL/ANY 无误报），唯一结构问题是 b2b 的加分组从未接线（与其两个加分要求 UNCLASSIFIED 同源，属同一根因的组级呈现）。

## 4. 测试统计

| 套件 | 数量 | 结果 |
| --- | --- | --- |
| `tests.test_config_guard`（新增） | 3 | 3/3 OK |
| `tests.test_policy_validator`（Batch 1 21 + Batch 2 18） | 39 | 39/39 OK |
| `tests.test_policy_compiler`（含 1 项编译期 quality 接线） | 14 | 14/14 OK |
| `tests.test_policy_cli` | 13 | 13/13 OK |
| 全量回归 `unittest discover -s tests -t .` | **481** | **481/481 OK**（459 基线 + 22 新增），无失败、无 error、无 skip |

测试增量：validator +18（原子性 6、结构 9、兼容性/真实策略钉住 3）、compiler +1、config_guard +3。历史 432 项与 Batch 1 全部断言原样保留、未削弱。

## 5. 未解决问题与局限（不以全绿宣称算法质量合格）

1. **检查均为报告性，不构成任何门控**：`quality BLOCKED` 不影响 `publishable`、CLI 退出码、`publish_policy`；真实 b2b 的孤儿组当前仍可发布（符合本批边界）。发布门控需 Batch 5 单独评审。
2. **原子性启发式是确定性文本规则，不是语义理解**：可证明的假阴性场景（同义改写、无连接词的隐性捆绑、纯中文并列被有意豁免）与假阳性边界（词内连接词黑名单为有限集合）已写入设计文档 §2.1/§5；本批给出的真实策略"零发现"只说明这三个标定样本上无文本级信号，**不等于**其策略在语义上完美原子。
3. **关键词池可达性未检查**（已知观察）：Tier 1 关键词组只有在其声明要求与某维度目标相交时才进入维度池；offline fixture 的 `extra` 组（Docker/n8n）因此从不为任何维度加分（Stage 4 既有语义）。是否将"关键词组从未可达"纳入质量检查会影响 fixture 预期，需单独评审后另行实施。
4. **引用完整性目前是 WARNING 级不变量**：requirement 级 `UNCLASSIFIED_REQUIREMENT_SCOPE` 与组级 `ORPHAN_REQUIREMENT_GROUP` 均不阻断；升级为编译期强制（阻断编译或标记 draft 不可确认）属于发布/编译策略变更，需批准。
5. **未实施**：Requirement 自动拆分、Policy 自动修复、ALL/ANY 自动改写、多要求共享锚点治理（Batch 4）、发布门控（Batch 5）、Schema 增加需求级评估范围字段（需方案批准）。
6. **compile-time quality 只存在于返回值**，不落库（与既有 diagnostics_json 只存阻断项的设计一致）；后续若需在 HR 界面历史回看质量报告，需要版本化存储方案（另行批准）。
7. CLI 库内模式（`--org-id/...`）本批未做真实库端到端演示（主库只有 Stage 3 验收残留、无已发布策略）；该路径的权限/只读性由 Batch 1 的 13 项 CLI 测试持续覆盖。

## 6. 实际修改文件清单

| 文件 | 类型 | 变更 |
| --- | --- | --- |
| `tests/test_config_guard.py` | 新增 | P0-1 守门 3 项 |
| `engine/policy_structure_validator.py` | 新增 | Batch 2 检查模块（9 代码） |
| `schemas/policy_validation.py` | 修改（未跟踪文件中） | +`group_id`、+`orphan_group_count` |
| `engine/policy_validator.py` | 修改（未跟踪文件中） | 聚合结构检查、版本 v1.2 |
| `config/policy_service.py` | 修改 | compile/update 返回 quality（报告性） |
| `tests/test_policy_validator.py` | 修改（未跟踪文件中） | +18 项 Batch 2 测试 |
| `tests/test_policy_compiler.py` | 修改 | +1 项编译期 quality 接线测试 |
| `reports/phase1_7_stage5_5/batch2_validation_design.md` | 新增 | 设计文档 |
| `reports/phase1_7_stage5_5/batch2_policy_validation.json` | 新增 | CLI 回归机读结果 |
| `reports/phase1_7_stage5_5/batch2_validation_report.md` | 新增 | 本报告 |
| `reports/phase1_7_stage5_5/batch2_tests.log` | 新增 | 六段测试日志 |

工作区：Stage 5.5 Batch 1/2 成果、frontend/、审计报告等全部保留；备份文件在项目外；未提交 Git（未获指示）。
