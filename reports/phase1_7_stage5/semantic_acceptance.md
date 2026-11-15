# Phase 1.7 Stage 5 — Step 7 真实 BGE 全链路验收

执行时间：2026-11-15T04:03:15.576045+00:00
执行类型：`REAL_LOCAL_BGE_NOT_MOCK`（真实本地 BGE，非 Mock embedder）
模型：`D:\model\bge-large-zh-v1.5\hub\models--BAAI--bge-large-zh-v1.5\snapshots\79e7739b6ab944e86d6171e44d24c997fc1e0116`
Embedding Profile：`bge-large-zh-v1.5-79e7739b-cls-v1` / hash `16cf0e6fe94e2cfa0c6a81415a69d4e6a0f965ec683f67558e9f641a5d54f278`
模型加载耗时：364.066 ms

本报告只记录本次真实模型运行观测；合成 JD/候选人不等同真实招聘数据，本机耗时不是生产 SLA。

## 模型一致性校验

```json
{
  "ok": true,
  "checks": {
    "pooling": {
      "declared": "CLS",
      "actual": "CLS",
      "ok": true
    },
    "dimension": {
      "declared": 1024,
      "actual": 1024,
      "ok": true
    },
    "max_length": {
      "declared": 512,
      "actual": 512,
      "ok": true
    }
  }
}
```

## 三岗位语义评分结果

| 岗位/画像 | Tier1 状态 | Tier1 分 | Tier2 状态 | Tier2 分 | 语义覆盖 |
| --- | --- | --- | --- | --- | --- |
| ai_automation/complete_match | READY_FOR_DEEP | 91.26 | PARTIAL_SEMANTIC | 51.47 | 0.675 |
| ai_automation/partial_match | BELOW_COARSE | 46.87 | PARTIAL_SEMANTIC | 40.34 | 0.675 |
| ai_automation/hard_condition_fail | HARD_FILTERED | 91.26 | PARTIAL_SEMANTIC | 51.47 | 0.675 |
| ai_automation/missing_information | DATA_INCOMPLETE | 91.26 | UNSEALED_NOT_SCORED | None | None |
| ai_automation/alternative_route | READY_FOR_DEEP | 81.95 | PARTIAL_SEMANTIC | 57.05 | 0.675 |
| ai_automation/keyword_only | BELOW_COARSE | 41.67 | PARTIAL_SEMANTIC | 3.68 | 0.675 |
| ai_automation/negated_experience | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 2.14 | 0.675 |
| ai_automation/repeated_keywords | READY_FOR_DEEP | 91.26 | PARTIAL_SEMANTIC | 51.47 | 0.675 |
| ai_automation/similar_but_different | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 29.44 | 0.675 |
| java_backend/complete_match | READY_FOR_DEEP | 100.0 | PARTIAL_SEMANTIC | 51.63 | 0.7 |
| java_backend/partial_match | BELOW_COARSE | 19.51 | PARTIAL_SEMANTIC | 40.7 | 0.7 |
| java_backend/hard_condition_fail | HARD_FILTERED | 100.0 | PARTIAL_SEMANTIC | 51.63 | 0.7 |
| java_backend/missing_information | DATA_INCOMPLETE | 100.0 | UNSEALED_NOT_SCORED | None | None |
| java_backend/alternative_route | READY_FOR_DEEP | 100.0 | PARTIAL_SEMANTIC | 51.63 | 0.7 |
| java_backend/keyword_only | BELOW_COARSE | 41.67 | PARTIAL_SEMANTIC | 3.86 | 0.7 |
| java_backend/negated_experience | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 0.0 | 0.7 |
| java_backend/repeated_keywords | READY_FOR_DEEP | 100.0 | PARTIAL_SEMANTIC | 51.63 | 0.7 |
| java_backend/similar_but_different | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 27.97 | 0.7 |
| b2b_sales/complete_match | READY_FOR_DEEP | 100.0 | PARTIAL_SEMANTIC | 71.63 | 0.675 |
| b2b_sales/partial_match | BELOW_COARSE | 21.49 | PARTIAL_SEMANTIC | 53.14 | 0.675 |
| b2b_sales/hard_condition_fail | HARD_FILTERED | 100.0 | PARTIAL_SEMANTIC | 71.63 | 0.675 |
| b2b_sales/missing_information | DATA_INCOMPLETE | 100.0 | UNSEALED_NOT_SCORED | None | None |
| b2b_sales/alternative_route | READY_FOR_DEEP | 100.0 | PARTIAL_SEMANTIC | 69.36 | 0.675 |
| b2b_sales/keyword_only | BELOW_COARSE | 41.67 | PARTIAL_SEMANTIC | 8.53 | 0.675 |
| b2b_sales/negated_experience | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 5.12 | 0.675 |
| b2b_sales/repeated_keywords | READY_FOR_DEEP | 100.0 | PARTIAL_SEMANTIC | 71.63 | 0.675 |
| b2b_sales/similar_but_different | BELOW_COARSE | 0.0 | PARTIAL_SEMANTIC | 42.04 | 0.675 |

Tier1（Stage 4）与 Tier2（Stage 5）分口径独立，不直接相加；语义分不覆盖 Tier0 结论。

## 缓存与版本隔离

```json
{
  "cache": {
    "anchor_manifest_reused": true,
    "resume_manifest_reused": true,
    "cross_job_resume_cache_hit": true,
    "cross_job_reused_chunk_count": 3,
    "note": "Cross-job reuse shares physical vectors; semantic results are still recomputed per job."
  },
  "version_isolation": {
    "job": "java_backend",
    "old_candidate_policy_version": 1,
    "new_candidate_policy_version": 2,
    "old_candidate_status": "PARTIAL_SEMANTIC",
    "new_candidate_status": "PARTIAL_SEMANTIC",
    "note": "Weights changed in V2; both candidates still use their own first-bound immutable version."
  },
  "cross_org_isolation": {
    "checked": true,
    "isolated": true
  }
}
```

## 本机性能观测

```json
{
  "scope": "Local CPU SQLite and single-process local BGE; not a production SLA",
  "runtime_ms": {
    "anchor_cold": {
      "samples": 3,
      "median_ms": 526.9321,
      "p95_observed_ms": 911.2811,
      "min_ms": 455.1653,
      "max_ms": 911.2811
    },
    "anchor_cache_hit": {
      "samples": 3,
      "median_ms": 23.756,
      "p95_observed_ms": 24.789,
      "min_ms": 21.4501,
      "max_ms": 24.789
    },
    "resume_cold": {
      "samples": 24,
      "median_ms": 363.69325,
      "p95_observed_ms": 613.9141,
      "min_ms": 122.3215,
      "max_ms": 696.1968
    },
    "resume_cache_hit": {
      "samples": 3,
      "median_ms": 35.3331,
      "p95_observed_ms": 36.0408,
      "min_ms": 35.2192,
      "max_ms": 36.0408
    },
    "semantic_evaluate": {
      "samples": 24,
      "median_ms": 75.19335,
      "p95_observed_ms": 93.7945,
      "min_ms": 59.3107,
      "max_ms": 95.174
    }
  }
}
```

## 自动断言

执行 236 项断言，失败 0 项。
