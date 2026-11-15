# Stage 4 固定候选集对照

本次仅运行真实规则和实际Repository，没有Qwen、BGE或Mock模型调用。
27份候选人为人工合成；原五份仓库样本来源本阶段未核实，按岗位分别形成15项。Policy来自Stage3真实Qwen输出，JD输入为合成数据。

旧分仅为纯函数0–45诊断值，旧Tier0失败时原生产链路本不会运行Tier1；新分为0–100初筛，不能相减当作匹配质量提升。

## 人工合成27画像

| 岗位/画像 | 旧硬状态 | 旧Tier1诊断/45 | 新硬状态 | 新初筛/100 | 新状态 |
| --- | --- | --- | --- | --- | --- |
| ai_automation/complete_match | FAIL | 27.0 | PASS | 91.26 | READY_FOR_DEEP |
| ai_automation/partial_match | FAIL | 21.48 | PASS | 46.87 | BELOW_COARSE |
| ai_automation/hard_condition_fail | FAIL | 27.0 | FAIL | 91.26 | HARD_FILTERED |
| ai_automation/missing_information | UNKNOWN | 27.0 | UNKNOWN | 91.26 | DATA_INCOMPLETE |
| ai_automation/alternative_route | FAIL | 22.31 | PASS | 81.95 | READY_FOR_DEEP |
| ai_automation/keyword_only | FAIL | 19.85 | PASS | 41.67 | BELOW_COARSE |
| ai_automation/negated_experience | FAIL | 31.27 | PASS | 0.0 | BELOW_COARSE |
| ai_automation/repeated_keywords | FAIL | 27.0 | PASS | 91.26 | READY_FOR_DEEP |
| ai_automation/similar_but_different | FAIL | 0.0 | PASS | 0.0 | BELOW_COARSE |
| java_backend/complete_match | FAIL | 0.0 | PASS | 100.0 | READY_FOR_DEEP |
| java_backend/partial_match | FAIL | 0.0 | PASS | 19.51 | BELOW_COARSE |
| java_backend/hard_condition_fail | FAIL | 0.0 | FAIL | 100.0 | HARD_FILTERED |
| java_backend/missing_information | UNKNOWN | 0.0 | UNKNOWN | 100.0 | DATA_INCOMPLETE |
| java_backend/alternative_route | FAIL | 0.0 | PASS | 100.0 | READY_FOR_DEEP |
| java_backend/keyword_only | FAIL | 0.0 | PASS | 41.67 | BELOW_COARSE |
| java_backend/negated_experience | FAIL | 0.0 | PASS | 0.0 | BELOW_COARSE |
| java_backend/repeated_keywords | FAIL | 0.0 | PASS | 100.0 | READY_FOR_DEEP |
| java_backend/similar_but_different | FAIL | 0.0 | PASS | 0.0 | BELOW_COARSE |
| b2b_sales/complete_match | FAIL | 0.0 | PASS | 100.0 | READY_FOR_DEEP |
| b2b_sales/partial_match | FAIL | 0.0 | PASS | 21.49 | BELOW_COARSE |
| b2b_sales/hard_condition_fail | FAIL | 0.0 | FAIL | 100.0 | HARD_FILTERED |
| b2b_sales/missing_information | UNKNOWN | 0.0 | UNKNOWN | 100.0 | DATA_INCOMPLETE |
| b2b_sales/alternative_route | FAIL | 0.0 | PASS | 100.0 | READY_FOR_DEEP |
| b2b_sales/keyword_only | FAIL | 0.0 | PASS | 41.67 | BELOW_COARSE |
| b2b_sales/negated_experience | FAIL | 0.0 | PASS | 0.0 | BELOW_COARSE |
| b2b_sales/repeated_keywords | FAIL | 0.0 | PASS | 100.0 | READY_FOR_DEEP |
| b2b_sales/similar_but_different | FAIL | 0.0 | PASS | 0.0 | BELOW_COARSE |

## 原五份样本×三岗位（来源未核实）

| 岗位/画像 | 旧硬状态 | 旧Tier1诊断/45 | 新硬状态 | 新初筛/100 | 新状态 |
| --- | --- | --- | --- | --- | --- |
| ai_automation/repository_original_1 | PASS | 35.51 | UNKNOWN | 40.7 | HARD_UNKNOWN |
| java_backend/repository_original_1 | PASS | 35.51 | UNKNOWN | 8.13 | HARD_UNKNOWN |
| b2b_sales/repository_original_1 | PASS | 35.51 | UNKNOWN | 0.0 | HARD_UNKNOWN |
| ai_automation/repository_original_2 | PASS | 32.86 | UNKNOWN | 66.11 | HARD_UNKNOWN |
| java_backend/repository_original_2 | PASS | 32.86 | FAIL | 0.0 | HARD_FILTERED |
| b2b_sales/repository_original_2 | PASS | 32.86 | FAIL | 0.0 | HARD_FILTERED |
| ai_automation/repository_original_3 | PASS | 11.73 | UNKNOWN | 13.01 | HARD_UNKNOWN |
| java_backend/repository_original_3 | PASS | 11.73 | FAIL | 0.0 | HARD_FILTERED |
| b2b_sales/repository_original_3 | PASS | 11.73 | UNKNOWN | 0.0 | HARD_UNKNOWN |
| ai_automation/repository_original_4 | PASS | 1.11 | UNKNOWN | 0.0 | HARD_UNKNOWN |
| java_backend/repository_original_4 | PASS | 1.11 | FAIL | 0.0 | HARD_FILTERED |
| b2b_sales/repository_original_4 | PASS | 1.11 | FAIL | 5.52 | HARD_FILTERED |
| ai_automation/repository_original_5 | FAIL | 13.16 | UNKNOWN | 29.46 | HARD_UNKNOWN |
| java_backend/repository_original_5 | FAIL | 13.16 | UNKNOWN | 0.0 | HARD_UNKNOWN |
| b2b_sales/repository_original_5 | FAIL | 13.16 | UNKNOWN | 0.0 | HARD_UNKNOWN |

## 差异解释与证据

每条结果的旧检查/命中、动态维度、精确原文片段和偏移、要求图路径、差异解释均保存在benchmark_results.json。
固定AI预算/学历与岗位独立配置、来源强度和语境、新旧分数公式、默认薪资周期差异是主要来源，不能把岗位更换造成的分数差异当模型质量指标。

重复画像采用prescreen-fixed-v2：只复制complete画像已有职责和skills，独立检查源字段等价与词集不变。初始v1曾额外加入此前没有的技能标签，不能用该无效控制样本声称重复计分回归；原始数据和结果保留在initial_repetition_fixture_failure.json。

## 自动断言

执行288项固定集断言，失败0项。

## 本机测量

下列耗时为本次临时SQLite与本地规则执行的观测值，不是生产SLA。冷编译仅3次，缓存命中编译300次；端到端包含Repository读取与证据校验。

```json
{
  "scope": "Local file SQLite and pure Tier0/1; not a production SLA",
  "model_calls": 0,
  "repository_bound_end_to_end": {
    "samples": 42,
    "median_ms": 20.79775,
    "p95_observed_ms": 25.685,
    "min_ms": 19.2098,
    "max_ms": 34.7504
  },
  "interpreter_cache": {
    "hits": 39,
    "misses": 3,
    "size": 3
  },
  "cold_compile": {
    "samples": 3,
    "median_ms": 1.2694,
    "p95_observed_ms": 1.3658,
    "min_ms": 0.8995,
    "max_ms": 1.3658
  },
  "warm_compile": {
    "samples": 300,
    "median_ms": 0.21875,
    "p95_observed_ms": 0.2986,
    "min_ms": 0.1823,
    "max_ms": 0.4301
  }
}
```
