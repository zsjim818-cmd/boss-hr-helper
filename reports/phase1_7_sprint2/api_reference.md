# Sprint 2 — REST API 参考（Vue3 前端对接用）

服务入口：`uvicorn server.app:app --host 127.0.0.1 --port 8000`
**OpenAPI 文档**：`http://127.0.0.1:8000/docs`（Swagger UI） / `http://127.0.0.1:8000/openapi.json`（机器可读）

## 认证（可信身份注入）

所有业务接口要求两个请求头（由上游网关/本地控制台在真实登录后注入；API 不接受请求体里的 user_id）：

| Header | 说明 |
| --- | --- |
| `X-Org-Id` | 组织 ID |
| `X-User-Id` | 用户 ID |

缺少 → `401`；用户不在组织/无岗位权限 → `403`。所有 Repository 调用再校验组织成员、岗位授权与权限位（view/edit/publish/collect/evaluate/review）。

## 错误格式

| 状态码 | 场景 | Body |
| --- | --- | --- |
| 400 | 业务参数/状态非法 | `{"error_type","message"}` |
| 401 | 缺少可信身份头 | `{"detail"}` |
| 403 | 越权（非成员/无岗位权限/非管理员豁免） | `{"error_type":"PermissionDenied","message"}` |
| 404 | 资源不存在（含跨岗位访问） | `{"error_type":"NotFound","message"}` |
| 409 | 无封存评分输入；质量门控拒绝 | 门控：`{"error_type":"PolicyQualityBlocked","message","quality_issues":[...]}` |
| 422 | 请求体 Schema 校验失败 | `{"error_type":"ValidationError","errors":[...]}` |

## 岗位

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/jobs` | `{title}` → 岗位 |
| GET | `/api/jobs` | 当前用户可见岗位列表 |
| GET | `/api/jobs/{job_id}` | 岗位详情（含 current_policy_id） |

## Policy 管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/jobs/{job_id}/policies/compile` | `{job_title,jd_text,hr_requirements}` → 草稿+编译记录+quality（真实 Qwen；测试注入 Mock） |
| GET | `/api/jobs/{job_id}/policies/drafts/{draft_id}` | 草稿详情（revision/hash/diagnostics） |
| PATCH | `/api/jobs/{job_id}/policies/drafts/{draft_id}` | `{policy,expected_revision,parent_run_id,source_input?}` → 新 revision + 校验 + quality |
| GET | `/api/jobs/{job_id}/policies/drafts/{draft_id}/quality` | Stage 5.5 质量报告（status/覆盖率/issues） |
| POST | `/api/jobs/{job_id}/policies/drafts/{draft_id}/confirm` | `{run_id,confirmation_hash}` → 人工确认 |
| POST | `/api/jobs/{job_id}/policies/drafts/{draft_id}/versions` | `{run_id,confirmation_id,execution_profile?}` → 不可变版本 |
| POST | `/api/jobs/{job_id}/policies/{policy_id}/publish` | `{confirmation_hash,quality_override_reason?}`；BLOCKED 默认 409；`quality_override_reason` 仅 ORG_ADMIN 生效并写审计 |
| GET | `/api/jobs/{job_id}/policies/current` | 当前发布版本 |
| GET | `/api/jobs/{job_id}/policies/history?version=N` | 历史版本 |

## 候选人与批次

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/jobs/{job_id}/batches` | 创建批次（锁定当前发布 Policy 版本） |
| GET | `/api/jobs/{job_id}/batches/{batch_id}` | 批次详情（policy_id/version 固定） |
| POST | `/api/jobs/{job_id}/batches/{batch_id}/candidates` | `{candidate_key,resume,source?,external_id?}` 导入标准化简历（去重/快照/封存） |
| GET | `/api/jobs/{job_id}/candidates?limit&offset&status&min_score` | 列表（去重分页过滤，附最新正式结果） |
| GET | `/api/jobs/{job_id}/candidates/{job_candidate_id}` | 详情（绑定信息+最新结果+历史+attempts） |

## 评分

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/jobs/{job_id}/candidates/{job_candidate_id}/score` | `{scoring_mode:"fast_mode"|"deep_mode",idempotency_key?}` → 202/任务；已有正式结果直接复用；无封存输入 409；后台线程执行，不阻塞查询 |
| GET | `/api/jobs/{job_id}/tasks/{task_id}` | 任务状态（PENDING/RUNNING/SUCCEEDED/FAILED）+ 结果 |
| GET | `/api/jobs/{job_id}/candidates/{job_candidate_id}/evaluations` | 最新正式结果 + 完整历史 |
| GET | `/api/jobs/{job_id}/ranking?limit` | 岗位级排名（按最新正式总分降序，NULL 置后） |

## 审核与联系

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/jobs/{job_id}/evaluations/{evaluation_id}/reviews` | `{action:"APPROVE"|"REJECT"|"REQUEST_REVIEW"|"NOTE",reason}`（审核权限） |
| GET | `/api/jobs/{job_id}/evaluations/{evaluation_id}/reviews` | 审核历史（含审核人/时间） |
| POST | `/api/jobs/{job_id}/evaluations/{evaluation_id}/contact` | `{action:"CONTACTED"|"DECLINED"|"PENDING",note}`（**仅记录 HR 手工事实，不触发任何自动发送**） |
| GET | `/api/jobs/{job_id}/evaluations/{evaluation_id}/contact` | 联系事件历史 |

## 评分结果结构（关键字段）

`pipeline_evaluations` 暴露：`scoring_status`（HARD_FILTERED / DATA_INCOMPLETE / NEEDS_REVIEW / COARSE_ONLY / DEEP_SCORED）、`total_score`（仅 DEEP_SCORED）、`routing_suggestion`（仅 DEEP_SCORED）、`tier0_json`（Tier0/1 初筛）、`tier1_json`（规则维度分）、`tier2_json`（语义评估：维度分/覆盖率/证据）、`tier3_json`（Tier3：每维分数/权重/置信度/原文证据）、`review_reasons_json`。所有身份字段（policy_id/version/hash、resume_id/content_hash）随结果返回。

前端对接建议：先 `GET /openapi.json` 生成类型；错误码 409/403 需显式提示（不可静默回退假数据）。
