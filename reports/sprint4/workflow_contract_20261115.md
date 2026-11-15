# 工作流与契约（2026-11-15，UTC+8）

## 1. 三层身份

| 层 | 字段 | 权威来源 | 约束 |
| --- | --- | --- | --- |
| 项目岗位 | `jobs.job_id` / `scan_batches.job_id` | 本地数据库 | 仅内部业务键，绝不冒充平台 ID |
| 平台岗位 | `encryptJobId` → `CandidateCard.platform_job_id` / `CandidateDetail.platform_job_id` | 页面/响应（本地仅合成） | 未知时为空；不得写内部 `job_id`；真机未验证 |
| 平台候选人 | `encryptGeekId` → `CandidateCard.identity` / `ResumeData._meta.encrypt_geek_id` | 页面稳定属性 | 唯一去重键；同名不同 ID 不合并 |
| 本地候选人绑定 | `candidate_key = "<internal job_id>:<encrypt_geek_id>"` | 服务端计算 | 仓储强制浏览器来源使用规范键 |
| 临时安全上下文 | `securityId` | 页面 | **不持久化、不入日志、不返回** |

## 2. 批次与 Policy

```
创建批次 → 读取岗位当前发布 Policy → 批次锁定 (policy_id, policy_version)
采集 → candidate.binding 首次写入同一版本；批次 B 可再采集同一候选人
评分 → batch_id 优先解析批次 Policy；结果 UNIQUE 含 policy_id/version/resume/mode
运行中发布新版本 → 旧批次继续旧版本；新批次取新版本
```

前端“批次锁定 Policy”只读 `batch.policy_version`；当前发布版本变化会提示但不改变运行批次。

## 3. 扫描状态机（当前持久化映射）

| 业务状态 | 存储 | 说明 |
| --- | --- | --- |
| RUNNING | `browser_scan_runs.status=RUNNING` | 同 actor+batch 并发第二次 → 409 `SCAN_ALREADY_RUNNING` |
| COMPLETED | `COMPLETED` | 至少读到/处理数据 |
| COMPLETED_WITH_ERRORS | `COMPLETED_WITH_ERRORS` | 部分 issue，仍保留成功项 |
| FAILED | `FAILED` + `error_code` | `AUTH_REQUIRED/CAPTCHA_BLOCKED/EMPTY_UNVERIFIED/...` |
| CANCELLED | `FAILED` + `error_code=CANCELLED` | API 对外映射为 `CANCELLED` |
| PAUSED/RESUME | 未实现（012 游标） | 明确 PARTIAL，不伪装支持 |

终止原因：`PAGE_END/MAX_PAGES/MAX_DETAILS/CANCELLED`；认证/风控/协议错误立即终止，不做无限重试。

## 4. 列表预筛 vs 正式评分

```
list page → stable ID dedupe → PreviewDecision
  SKIP_CONFIRMED   仅当列表明确显示硬条件不符（学历/年限）
  FETCH_DETAIL     缺失/未知（默认，不当作失败）
  NEEDS_VERIFICATION → 保留人工核验
→ open_detail（受 max_details/取消约束）→ ResumeData
→ COMPLETE 才 seal + 正式 Tier0–3；PARTIAL/FAILED 仅待核验，不跑深评
```

`PreviewDecision` 不写正式结果、不调用付费模型；正式评分语义未改变。

## 5. 采集/详情字段映射（合成证据）

| 合成响应字段 | 内部字段 | 证据等级 |
| --- | --- | --- |
| `zpData.geekList[].encryptGeekId` | `identity` | 开源观察 + 合成夹具，真机未验证 |
| `geekCard.geekName/jobTitle/geekDegree/geekWorkYear` | name/position/degree/work_years | 本地假设 |
| `geekCard.encryptJobId` | `platform_job_id` | 本地假设 |
| `geekDetailInfo.geekBaseInfo` | ResumeData 基本信息 | 本地假设 |
| `geekWorkExpList/geekProjExpList/geekEduExpList` | work/project/education | 本地假设 |

## 6. 幂等与重算

- 扫描重复：稳定 ID 去重；同 candidate_key 复用 candidate/job_candidate。
- 评分幂等：任务幂等键按 `(batch, job_candidate, mode)`；结果复用需同 Policy 版本 + sealed `resume_id` + pipeline 版本。
- 简历变化：新快照可追加，但 sealed 输入不可变；改封需 012 输入版本表（当前显式 BLOCKED）。
- 审核/联系：审核事件与联系事件独立；重扫不重置 `CONTACTED`；不自动发送消息。

## 7. 证据矩阵

| 能力 | 开源参考 | 独立本地 Mock | 真实未验证 |
| --- | --- | --- | --- |
| 推荐页 iframe/卡片 | 有观察 | DOM 夹具 + 合成 JSON | BLOCKED |
| 列表分页/懒加载 | 有观察 | Mock/Collector 分页与上限 | BLOCKED |
| 详情字段 | 有观察 | 两套夹具（DOM/JSON） | BLOCKED |
| 登录/风控 | 文档说明人工 | 登录/验证码/滑块夹具 | BLOCKED |
| 招呼 Dry-Run | 有行为语义 | 面板作用域 + 不点击断言 | BLOCKED |
| 平台岗位 ID 绑定持久化 | 未知 | 请求/DTO 校验 | 需 012 |
| PII 加密/期限 | 无 | 仅合成数据 | 需 012 |
