# Migration 010 重基线审计（只读核实）

日期：2026-11-15（Sprint 4 Batch 1.1 收尾）
范围：`storage/database/migrations/010_pipeline_scoring_mode.sql` 的历史重基线事实、校验和、备份与测试记录，以及后续冻结约束。
本文件由**只读**核查生成：本批次未修改 001–010 SQL、未修改 `schema_migrations`、未修改任何正式业务数据。

---

## 1. 事实时间线

| 阶段 | 事件 |
| --- | --- |
| Sprint 3 早期 | 010 首次实现为「重建 `pipeline_evaluations` + 依赖重命名/重建子表」的初版，并已应用到开发主库 `data/recruitment.sqlite3`（`user_version=10`）。应用时 `schema_migrations` 记录 010 的校验和为 `e0af8e806fe94d7e67d3dbd39ffd855e7b7e6675ad5ccb26bc698e098eaeda69`。 |
| Sprint 3.1 P1-3 | 新增迁移验收测试时发现：SQLite 在 `ALTER TABLE ... RENAME` 时会改写子表 FK 引用，初版在**带数据**升级路径上会产生 FK 违规。遂在同一次开发会话内把 010 改写为「暂存表 → 删旧三表 → 按最终名重建 → 回填 → 重建索引/触发器」，且不再依赖重命名。 |
| 重基线 | 主库早已按初版应用，`db-check` 因**每迁移校验和**与改写后的文件不一致而失败。处理方式：先做**字节级备份到项目目录外**，再临时移除 `schema_migrations` 的不可变触发器，仅更新 version=10 的 `name`/`checksum`，随即用原 SQL 重建触发器。**未修改任何业务表、业务行或其它迁移行**。 |
| Sprint 4 Batch 1.1 | 本批次对 010 **零改动**，仅做只读核实（本文件）。 |

关键结论：主库当前 `schema_migrations` 中 010 的 `schema_hash` 仍为 `332808b95b7ca955b9370e418df7fdb3bf1d6dbded38e242684317c8e5aa1e11`（与重基线前完全一致），只有 `checksum` 从初版值变为修正文件值。

---

## 2. 校验和与文件事实（本次只读实测）

| 项 | 值 |
| --- | --- |
| 迁移文件 | `storage/database/migrations/010_pipeline_scoring_mode.sql` |
| 文件 SHA-256（当前） | `d590941a7a99bd835370002b47830c3ae0c4cb6a2236be600e9221cdda552c12` |
| 文件大小 | 9,459 bytes |
| 行尾 | 纯 LF（0 个 CRLF；此前 CRLF 归一化是导致 `schema_hash` 文本差异的原因之一，已消除） |
| 运行时加载校验和 | `d590941a…52c12`（`load_migrations`，与文件 SHA-256 一致） |
| 主库 010 记录 | `checksum=d590941a…52c12`、`schema_hash=332808b9…a1e11`、`applied_at=2026-11-15T08:30:04.490Z` |
| 备份 010 记录（重基线前） | `checksum=e0af8e80…eda69`、`schema_hash=332808b9…a1e11`（schema_hash 与主库相同） |

> 说明：初版文件内容已被同会话内的修正版替换，其文件本身不可再取；`e0af8e80…eda69` 是主库在重基线前**实际记录的历史校验和**，可从备份库只读复核（见 §3）。

---

## 3. 备份事实（项目目录外，保留不动）

| 项 | 值 |
| --- | --- |
| 路径 | `D:\Boss-project\recruitment.sqlite3.bak_sprint3_010` |
| 大小 | 802,816 bytes（与主库当前大小一致） |
| `PRAGMA user_version` | 10 |
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA foreign_key_check` | 无违规行 |
| `schema_migrations` 行数 | 10（001–010） |
| 010 行 | `(10, '010_pipeline_scoring_mode.sql', 'e0af8e80…eda69', '332808b9…a1e11', '2026-11-15T08:30:04.490Z')` |

该备份是重基线前的完整字节副本，可作为回滚/取证依据；**未纳入仓库，且本批次未改写或删除**。

---

## 4. 结构一致性核实

用当前迁移文件在临时目录**从零构建** v10，与主库逐对象比对 `sqlite_master`：

- 对象总数：fresh 235 / main 235；`only fresh = []`、`only main = []`。
- 唯一文本差异：`pipeline_evaluations` 表名引号——主库为
  `CREATE TABLE "pipeline_evaluations" (...)`（初版经 rename 生成），
  fresh 为 `CREATE TABLE pipeline_evaluations (...)`（修正版按最终名直接创建）。
  其余列、约束、索引、触发器 SQL 完全一致。
- 因此 fresh 的 `schema_hash`（`baea345e…cdd92`）与主库记录的 `schema_hash`（`332808b9…a1e11`）在**文本**上不同，但语义结构一致；主库 `Database.check()` 以记录值与主库实际 schema 比对，结果：

```
db-check ok: True | version: 10 | issues: []
```

主库实测：`integrity_check = ok`、`foreign_key_check` 无违规。

---

## 5. 迁移验收测试记录（本批次重跑）

`tests/test_migration_010.py`（2 项，均通过）：

1. `test_v9_to_v10_preserves_all_pipeline_data_and_guards`
   - v9 库写入正式评分 + HR 审核 + 联系事件 → 升级 v10 后 evaluations/attempts/reviews/contacts **逐列一致**；
   - 审核/联系仍指向同一 `pipeline_evaluation_id`；
   - `foreign_key_check` 空、`integrity_check=ok`；
   - 不可变触发器仍生效（UPDATE/DELETE/无引用的 INSERT 均被拒）；
   - 升级后同一绑定可保存 fast + deep 两条结果，重复同模式保存返回原行（唯一键语义正确）。
2. `test_failed_migration_rolls_back_without_partial_state`
   - 注入坏迁移（011 非法 SQL）→ `MigrationError`、`user_version` 仍为 9、无残留表、数据快照不变；
   - 随后用正确迁移集可正常升级到 10，数据不变。

---

## 6. 后续冻结约束（强制）

1. **001–010 永久冻结**：不得再修改、重排、重命名或删除这 10 个迁移文件；`schema_migrations` 的 10 条记录（尤其 version=10 的 `checksum`/`schema_hash`）不得再变更。
2. **禁止直接改正式数据**：不得以维护为名 UPDATE/DELETE 业务表；`schema_migrations` 与 `pipeline_*` 的不可变触发器必须保持启用。
3. **结构演进只走新迁移**：任何 Schema 变更（含本批次 §设计文档中的并发去重方案）必须新增 `011_*.sql`，经审批后执行，并提供升级/回滚验收测试。
4. **若未来必须纠正历史迁移**（仅在发现数据损坏级缺陷且经明确批准时）：
   - 先做项目目录外字节备份并记录 SHA-256；
   - 记录变更前后 `checksum` 与 `schema_hash`；
   - 只允许调整历史元数据，不得改动业务行；
   - 形成同等规格的审计文档，并重跑 `test_migration_010` 与 `db-check`。
5. **禁止 `git clean` / `git reset --hard`**：备份、数据库与未提交工作必须原样保留。
6. 010 的修正版实现（暂存表三段式重建）是本项目后续同类表结构升级的参考范式：**避免 RENAME 改写子表 FK**，重建后必须重建索引、触发器并校验 FK/integrity。
