# Scoring Engine V2 五类基准对比（离线 Mock）

**这些分数仅验证算法公式、数据契约和路由，不代表真实 BGE/Qwen 匹配质量。**

同一确定性 Mock Embedding；LLM 固定四维 90/85/80/90，权重 40/30/20/10 得 86.5。固定深度分没有区分候选人能力，不能据此判断匹配质量提升。

旧基线在修改前实际执行并保存在 `baseline_v1.json`；V2 由 `python -m tests.benchmark_runner` 生成。旧版 total 字段包含粗排返回值，V2 只有成功深度评分才有正式分，空值显示为 —。V1 未记录独立 scoring_status，状态列仅展示 V2 实际输出。

| 原始样本 | Tier1 原始 V1→V2 | Tier1 折算 V1→V2 | Tier2 V1→V2 | 粗排 V1→V2 | 旧 total → V2 深度分 | V2 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1. Agent 实战 | 134.10 → 122.00 | 45.00 → 35.51 | 55.00 → 53.31 | 100.00 → 88.82 | 86.50 → 86.50 | DEEP_SCORED |
| 2. 工作流/RPA | 93.30 → 89.80 | 45.00 → 32.86 | 55.00 → 55.00 | 100.00 → 87.86 | 86.50 → 86.50 | DEEP_SCORED |
| 3. 传统 Python | 33.75 → 30.50 | 33.75 → 11.73 | 48.32 → 48.32 | 82.07 → 60.05 | 86.50 → 86.50 | DEEP_SCORED |
| 4. 文案运营 | 3.75 → 2.00 | 3.75 → 1.11 | 0.00 → 0.00 | 3.75 → 1.11 | 3.75 → — | COARSE_ONLY |
| 5. 超预算 | 0.00 → 0.00 | 0.00 → 0.00 | 0.00 → 0.00 | 0.00 → 0.00 | 0.00 → — | HARD_FILTERED |

| 样本 | V1 fast 路由 | V2 fast 路由 | V1 deep 路由 | V2 deep 路由 |
| --- | --- | --- | --- | --- |
| 1 | AUTO_GREET | PENDING_REVIEW | AUTO_GREET | AUTO_GREET |
| 2 | AUTO_GREET | PENDING_REVIEW | AUTO_GREET | AUTO_GREET |
| 3 | AUTO_GREET | PENDING_REVIEW | AUTO_GREET | AUTO_GREET |
| 4 | REJECT | NOT_RECOMMENDED | REJECT | NOT_RECOMMENDED |
| 5 | REJECT | NOT_RECOMMENDED | REJECT | NOT_RECOMMENDED |

所有 V2 fast 结果均无正式深度分，且不进入 AUTO_GREET。NOT_RECOMMENDED 仅表示不推荐，最终招聘判断交 HR。

## 边界场景

| 场景 | 状态 | 正式分 | 路由 |
| --- | --- | ---: | --- |
| llm_timeout | SCORING_FAILED | — | PENDING_REVIEW |
| llm_invalid_json | SCORING_FAILED | — | PENDING_REVIEW |
| partial_resume | DATA_INCOMPLETE | — | PENDING_REVIEW |
| collection_failed | SCORING_FAILED | — | PENDING_REVIEW |
| unknown_degree | DEEP_SCORED | 86.50 | PENDING_REVIEW |
| labels_only | COARSE_ONLY | — | NOT_RECOMMENDED |
| history_customer_service_with_agent_project | DEEP_SCORED | 86.50 | AUTO_GREET |
| daily_salary | DEEP_SCORED | 86.50 | PENDING_REVIEW |
| missing_salary | DEEP_SCORED | 86.50 | PENDING_REVIEW |
| decimal_over_budget | HARD_FILTERED | — | NOT_RECOMMENDED |

重复输入与新引擎复算：五类样本 全部稳定。该结论仅适用于确定性 Mock；temperature=0 不能保证远端模型跨版本的确定性。

完整参数快照、版本、每块相似度、惩罚、证据、路由原因及边界输出见 `benchmark_v2_mock.json`。

## 验证边界

本报告的 Mock 运行未加载真实 BGE，也未调用 Qwen；另外独立完成的真实 BGE fast 检查见 local_bge_fast.json 和 delivery_v2.md。当前阈值和映射区间仍需人工标注样本及真实模型校准。开发文档中的历史分数未按本次环境复现，不能与此 Mock 分数直接比较。
