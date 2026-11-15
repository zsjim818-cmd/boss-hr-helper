# Sprint 2 — 三岗位后端端到端演示步骤（Mock 环境）

前置：`.venv` 已安装 fastapi/uvicorn/httpx（见 pyproject）。所有步骤均为离线 Mock（Mock 编译器、确定性语义 stub 由测试注入；真实运行需 BGE+Qwen，见 §7）。

## 0. 启动服务

```powershell
.\.venv\Scripts\python.exe main.py db-init      # 首次
.\.venv\Scripts\python.exe main.py db-migrate   # 增量迁移到 009
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
# OpenAPI: http://127.0.0.1:8000/docs
```

身份头（本地联调）：`X-Org-Id: <org>`、`X-User-Id: <user>`（须为组织成员；ORG_ADMIN 才能豁免发布门控）。

## 1. 创建岗位

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/jobs -Headers $h -ContentType application/json -Body (@{title="AI自动化应用开发工程师"} | ConvertTo-Json)
```

对三个岗位重复（Java 后端 / 企业客户销售经理），分别得到 `job_id`。

## 2. 编译 Policy（真实为 Qwen；离线联调用测试注入 Mock）

```
POST /api/jobs/{job_id}/policies/compile
{"job_title": "...", "jd_text": "...", "hr_requirements": "..."}
```

返回 `draft`、`compilation_run`、`quality`。用 `GET .../drafts/{draft_id}/quality` 查看 Stage 5.5 报告（覆盖率/原子性/结构/引用完整性）。

## 3. 确认 → 版本化 → 发布

```
POST .../drafts/{draft_id}/confirm        {"run_id": "...", "confirmation_hash": "..."}
POST .../drafts/{draft_id}/versions       {"run_id": "...", "confirmation_id": "..."}
POST .../policies/{policy_id}/publish     {"confirmation_hash": "..."}
```

BLOCKED 质量默认 409（附 `quality_issues`）。如确需发布已审阅的 BLOCKED 策略，仅 ORG_ADMIN 可带 `quality_override_reason`，系统写审计事件（操作人/策略版本/hash/原因/时间）。

## 4. 批次 + 导入候选人

```
POST /api/jobs/{job_id}/batches                                  # 锁定当前发布 Policy 版本
POST /api/jobs/{job_id}/batches/{batch_id}/candidates
{"candidate_key": "...", "resume": { ...标准简历... }, "source": "manual"}
```

重复导入同一 candidate_key 去重；不完整简历不封存（后续评分会被 409 拒绝）。

## 5. 启动评分（异步）

```
POST /api/jobs/{job_id}/candidates/{job_candidate_id}/score
{"scoring_mode": "deep_mode", "idempotency_key": "optional-key"}
→ 202 {"task": {...}}
GET /api/jobs/{job_id}/tasks/{task_id}       # 轮询至 SUCCEEDED/FAILED
```

`deep_mode` 会依序执行 Tier0→Tier1→（必要时）Tier2→（满足条件才）Tier3；`fast_mode` 只做 Tier0/1。
Tier0 明确失败 / 输入不完整 / 粗排未过 / 语义覆盖不足 都不会调用 Qwen。

## 6. 查询结果 → HR 审核 → 排名

```
GET /api/jobs/{job_id}/candidates?limit=20&offset=0&status=DEEP_SCORED
GET /api/jobs/{job_id}/candidates/{jc_id}                 # 最新结果 + 历史 + attempts
GET /api/jobs/{job_id}/candidates/{jc_id}/evaluations
POST /api/jobs/{job_id}/evaluations/{eval_id}/reviews     {"action":"APPROVE","reason":"..."}
POST /api/jobs/{job_id}/evaluations/{eval_id}/contact     {"action":"CONTACTED","note":"手工私信"}
GET  /api/jobs/{job_id}/ranking?limit=100
```

审核历史与联系事件分离；审核人、时间、评估版本均落库；任何路由建议都不触发发送动作。

## 7. 离线验收（自动化）

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_api_pipeline tests.test_pipeline_service `
    tests.test_pipeline_repository -v
```

`tests/test_api_pipeline.py` 覆盖：认证/越权/跨岗位 404、Policy 门控与审计豁免、批次/导入/分页/过滤、异步评分与幂等复用、审核/联系、fast_mode 零模型调用、无封存输入 409。
