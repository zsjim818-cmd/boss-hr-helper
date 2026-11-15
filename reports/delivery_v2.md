# Phase 1.5 Scoring Engine V2 交付说明

> 本文保留最初交付阶段记录。后续已完成Qwen真实超时诊断，两份原始简历Tier3评分成功；最新请求、证据与90项回归结果见 `llm_live_diagnosis.md`。下文原“Qwen未验证”属于当时状态。

已按 P0 → P1 → P2 顺序修改，每阶段通过测试后继续。保留四级架构、Pydantic 契约与五类原始样本；本次不包含 Phase 2 浏览器或 Phase 3 发送实现。

## 1. 文件清单

本清单按目录整理后的当前路径展示。原五类和单例数据集中于 `tests/mock_data.py`，数据内容不变；Schema 文本投影已移至 `engine/resume_text.py`。根目录 `main.py` 统一提供 test、benchmark、diagnose、sample 命令，`config/settings.py` 统一定位项目根目录和环境变量。

| 文件 | 修改内容 |
| --- | --- |
| `engine/hybrid_engine.py` | 保留原构造参数和统一入口；串联四级；安全路由、正式深度分与粗排分分离；异常状态；配置重载 |
| `engine/hard_filter.py`（新增） | 学历三态与统一学历来源；小数/中文单位/周期/13薪解析；缺失、无法解析、周期不同比较为UNKNOWN；历史岗位不再一票否决；按配置检查活跃度/年限 |
| `engine/rule_matcher.py`（新增） | 别名归一、全局去重、最强来源；能力类别预算和平滑饱和；标签格式检测 |
| `engine/semantic_ranker.py`（新增） | Agent/RPA OR；固定窗口、重复去除、同经历一槽Top-K；同块惩罚、辅助来源独立降权；完整诊断 |
| `engine/llm_reviewer.py`（新增） | 四维Rubric、隐私字段裁剪、不可信数据边界；JSON Schema输出；Pydantic与原文引用校验；禁止标签或短技术名支撑高分；缺失项与置信度 |
| `schemas/` | 新增Evidence/DataIssue/Card；输入严格类型并保留未知字段；完整/部分/采集失败；输出状态、版本、缓存与安全不变量 |
| `engine/scoring_session.py`（新增） | 输入错误落诊断；配置批次锁；版本/内容哈希；缓存资格、命中重校验；结构化证据输出 |
| `storage/scoring_cache.py`（新增） | 标准库稳定哈希、内存/可选磁盘缓存、原子写、深拷贝、损坏安全miss与诊断 |
| `config/validation.py`（新增） | 完整V2配置显式验证；拒绝缺少分组、非法权重/阈值、NaN及旧维度配置 |
| `config.json` | V2分组锚点、来源权重、类别预算、Top-K、惩罚、引用门槛、模型版本与缓存参数；保留旧关键词基础分及52/60/82分阈值 |
| `config/job_compiler.py` | 同步生成V2分组与客观岗位适配维度；小数薪资上限；全部V2默认项 |
| `tests/test_benchmark.py` | 五类原字典保留；更新模式/状态/证据/惩罚/路由诊断；默认Mock，真实模式显式选择；支持JSON输出 |
| `tests/test_engine.py` | 原单简历字典保留；示例脚本默认Mock，显式live才接真实模型；去除硬编码模型连接参数 |
| `tests/benchmark_runner.py`（新增） | 可复现离线五类新旧对比、边界输出、版本与源码SHA、配置快照、稳定性检查 |
| `tests/mock_models.py`、`tests/__init__.py`（新增） | 确定性离线模型与可配置异常/响应，无外部API依赖 |
| `tests/test_p0.py`（新增） | P0安全与硬过滤8项 |
| `tests/test_rankers.py`（新增） | 规则与语义16项 |
| `tests/test_llm_review.py`（新增） | LLM证据、输出、隐私与失败7项 |
| `tests/test_job_compiler.py`（新增） | 编译配置可被V2引擎加载1项 |
| `tests/test_cache_unit.py`（新增） | 缓存与哈希11项 |
| `tests/test_schema_v2.py`（新增） | 数据契约、完整性、未知字段与安全不变量11项 |
| `tests/test_session.py`（新增） | 端到端缓存、版本、配置快照、映射及损坏缓存10项 |
| `tests/test_additional_boundaries.py`（新增） | 额外薪资/配置条件/伪引用/标签边界6项 |
| `tests/test_benchmark_v2.py`（新增） | 五类基准、稳定性、异常及报告7项 |
| `pyproject.toml`、`uv.lock` | 补齐原代码已使用的python-dotenv依赖及锁定，不增加算法/测试第三方库 |
| `.gitignore` | 忽略.env、评分缓存与本地uv缓存 |
| `README.md` | 运行命令、公式、配置、契约迁移与验证限制 |
| `reports/` | 旧Mock基线、新Mock全诊断、真实BGEfast结果与日志、分阶段/最终测试记录及本说明 |

## 2. 新旧算法差异

| 项目 | V1 | V2 |
| --- | --- | --- |
| 学历 | 未识别默认本科，仅顶层 | 合并顶层/教育条目最高明确声明学历；未知为UNKNOWN |
| 薪资 | 整数K正则；日薪/缺失当0 | 可解析小数/中文单位/13薪；原周期比较，无法比较为UNKNOWN；仅明确超预算FAIL |
| 历史岗位 | 全部命中排除词直接拒绝 | 历史职位是核验线索，不否定其他技术项目 |
| 实战 | 工作标签拼入实战文本 | 职责/项目/毕设与标签/技能/优势分离；标签不等于开发经历 |
| Tier1 | 单词累加后min(45,raw)，高分挤平 | 同义词、重复规则与跨字段去重；类别预算28/12/5；每类`cap*(1-exp(-raw/scale))` |
| Tier2 | 全局单点正最大值；跨经历负最大值扣分 | 每块、每路线正负对比；有界惩罚；按经历Top-K聚合，再Agent/RPA取较高路线；满分55 |
| Tier3 | 四个裸分，含主观稳定性；宽松提取JSON | Rubric、原文引用、缺失与置信度；严格结构输出；客观适配替换稳定性；保留40/30/20/10 |
| 路由 | fast或LLM失败可用粗排AUTO；低分REJECT | 只有成功深评及全部资格通过才AUTO；失败保留独立状态；低分NOT_RECOMMENDED交HR判断 |
| 分数 | total混杂粗排/LLM | coarse固定表示Tier1+2；total仅成功深评存在，不相加 |
| 追溯 | 无固定配置/模型缓存键 | 内容/配置/模型/Prompt/模式/引擎版本标识；批次快照；命中重校验 |

## 3. 配置结构

完整值在根目录 `config.json`，每次报告也保存完整快照。

```text
config_version
model_versions: embedding / llm
cache: enabled / directory
job_target: job_title / raw_jd_text / hr_custom_requirements
quotas_and_switches: scoring_mode / enable_auto_greet / 原有配额开关
hard_filters: min_degree / max_expected_salary_k / salary_budget_period
              exclude_job_status / exclude_position_keywords
              [可选 work_years_allowed / active_status_allowed]
scoring_config:
  thresholds: coarse_pass_score / review_threshold / auto_greet_threshold
  dimension_weights: ai_automation_practice / engineering_foundation
                     automation_efficiency / objective_job_fit
  keyword_rules: high_value_tags / medium_value_tags
  keyword_aggregation: aliases / categories.{cap,scale,keywords} / default_category
  evidence_source_weights: work / project / thesis / work_tags / skills / advantage
  evidence_quality: detect_label_only_practice / label_prefixes / implementation_terms / ...
  semantic_anchors: positive_anchor_groups.agent / .rpa / negative_anchors
  semantic_scoring: max_score / similarity_min,max / top_k / top_k_weights
                    chunk_chars / min_chunk_chars / penalty_* / source_weights
  llm_review: temperature / timeout_seconds / max_unsupported_score,confidence
              practice_required_above / min_practice_quote_chars / auto_greet_min_confidence
```

旧平铺锚点和稳定性维度配置需要显式迁移，不能静默回退。整个配置哈希也加入版本，人工标签忘记改仍会失效。

## 4. 实际测试结果

P0：8/8通过。P1：32/32通过（含P0回归）。P2首轮：63/63通过。审查修复并扩展后最终：**77/77通过，0失败、0错误、0跳过**。

逐项证据：`tests_final.log`；阶段记录：`phase_validation.md`。`uv lock --check --offline --cache-dir .uv-cache`通过。

关键验收11条均有自动断言：小数薪资、未知学历、日薪/缺失、标签污染、跨字段重复、历史客服转开发、两路线OR、重复/长度控制、超时/JSON失败无AUTO、fast无AUTO、相同版本确定性Mock稳定。其他边界包含伪造引用、技术名引用、置信度、未知字段、映射失败、缓存缺证据和版本失效。

无未通过自动测试。开发中的失败测试及修复记录保留于阶段说明；没有将未执行的模型检查标为通过。

## 5. 五类原始样本对比

同一确定性Mock下，以下比较能验证公式和路由变化，不能验证真实招聘效果。固定LLM四维90/85/80/90，前三样本正式深度分均86.5；这是管线fixture，不是能力评价。

| 样本 | 旧Mock粗排 | 新Mock粗排 | 新Tier1（45） | 新Tier2（55） | 新Mock深度分 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Agent实战 | 100 | 88.82 | 35.51 | 53.31 | 86.5 |
| 工作流/RPA | 100 | 87.86 | 32.86 | 55 | 86.5 |
| 传统Python | 82.07 | 60.05 | 11.73 | 48.32 | 86.5 |
| 文案运营 | 3.75 | 1.11 | 1.11 | 0 | 空 |
| 超预算 | 0 | 0 | 0 | 0 | 空 |

完整旧新路由/原始规则分/边界见 `benchmark_comparison.md`、`baseline_v1.json`、`benchmark_v2_mock.json`。

另实际使用本地BGE快照 `79e7739b6ab944e86d6171e44d24c997fc1e0116` 在CPU执行fast模式，启用离线加载；进程退出0，五类结果均无正式深度分、无AUTO。这不是上表Mock结果，不能混比。

| 样本 | 真实BGE V2粗排 | Tier1 | Tier2 | 语义惩罚 | 路由 |
| --- | ---: | ---: | ---: | ---: | --- |
| Agent实战 | 82.06 | 35.51 | 46.55 | 0 | PENDING_REVIEW |
| 工作流/RPA | 86.07 | 32.86 | 53.21 | 0 | PENDING_REVIEW |
| 传统Python | 37.25 | 11.73 | 25.52 | 7.24 | NOT_RECOMMENDED |
| 文案运营 | 17.84 | 1.11 | 16.73 | 12 | NOT_RECOMMENDED |
| 超预算 | 0 | 0 | 0 | 0 | NOT_RECOMMENDED |

原文、版本、参数及每块明细见 `local_bge_fast.json`，执行日志见 `local_bge_fast.log`。没有本次真实BGE旧版基线，不能用开发文档历史分数证明提升。

## 6. 边界结果

LLM超时/无效JSON/越界/伪造引用/只引用技术名：SCORING_FAILED、正式分为空、PENDING_REVIEW。fast：仅粗排，永无AUTO。采集失败与部分简历分别为SCORING_FAILED和DATA_INCOMPLETE。学历/薪资UNKNOWN仍可人工评估，但不得AUTO。技术标签不获实战权重；技能重复与别名只计一次。历史客服不硬拒绝已有Agent项目。缓存缺失证据或置信度不一致会miss重算，失败/不完整结果不缓存。

## 7. 尚未验证与后续审查

1. 真实Qwen API未调用。需验证服务对严格JSON Schema的支持、引用质量和多次评分波动；不兼容会安全失败，不降级为AUTO。
2. 真实BGE样本3粗排37.25低于52线，负向惩罚7.24，需HR标注后校准映射区间、Top-K和惩罚参数，不能先声称匹配质量提升。五类样本不足以估计召回率、误拒率或公平性。
3. 标签格式、具体动作词与引用长度是保守启发式；无法证明经历真实，也不能保证任意简历长度或提示注入均无偏差。安全门控和人工核验仍是边界。
4. 旧数据的完整性按区块存在性推导，无法知道DOM是否截断；后续采集必须显式上报完整性及错误。日/年薪不擅自转月薪，13薪只记录；学历最高声明不等于核验毕业。
5. 模型版本配置目前含unpinned标签；本地快照路径可追溯，远端模型别名可能漂移。相同缓存输入稳定不等于供应商跨版本可重复；正式使用应填不可变模型修订标识。
6. 当前AUTO_GREET是评分侧资格，无任何浏览器或发送动作。本次在Phase1.5结束，等待人工审查后才讨论Phase2。

## 8. 目录整理补充

现有实现已按预设职责迁到 `config/`、`schemas/`、`engine/`、`storage/`、`tests/`，根目录仅保留 `main.py`、业务配置、依赖和README等必要文件。数据格式示例及参考截图归入开发文档。未来的浏览器、发送和看板目录尚未创建。
原五类及单例字典统一放入 `tests/mock_data.py`，迁移前后内容哈希一致。完整评分输出、异常边界和配置快照与整理前完全相等。
本轮全部 **80项测试通过**（原77项加3项入口/跨目录回归），详情在 `tests_final.log`。评分参数和公式未调整；原真实BGE快照继续保留，本轮验证使用Mock。
