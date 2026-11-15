# Tier 3 Qwen 真实调用超时诊断与修复

## 结论及验收

已完成两份原始候选人简历的**真实 Qwen Tier 3**评分，超时仍为45秒、SDK自动重试仍为0。没有使用Mock结果作为真实评分，没有关闭strict Schema或放宽Pydantic/原文引用校验。

| 原始样本 | API总流程耗时 | AI实战 | 工程基础 | 自动化提效 | 客观适配 | 正式深度分 | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 样本1 Agent实战 | 24.0289s | 75 | 80 | 65 | 90 | **76.0** | DEEP_SCORED |
| 样本2 工作流/RPA | 14.8428s | 65 | 45 | 60 | 90 | **60.5** | DEEP_SCORED |

以上仅验证Tier3。本轮新入口跳过Tier1/Tier2、不加载BGE，输出明确标记 `scope=tier3_only_diagnostic`、`skipped_stages=[tier1,tier2]`、`passed_tier2_coarse=false`，路由固定 `PENDING_REVIEW`。不能将其称为整个混合引擎所有阶段已通过，更不会触发发送。

实际有两个独立问题：

1. 原请求在等待非流式响应时触发底层ReadTimeout，未发生SDK重试。只关闭默认思考便能在13.9–15.9秒收到完整HTTP200响应，说明请求级思考控制是有效的最小延迟修复。
2. 端点虽然接受 `strict=true`，实际仍返回了顶层数组。Pydantic正确拒绝了它。补充Prompt中的顶层对象要求后，通过同一Schema与原文证据校验。

**推测边界：** 默认高强度思考是主要延迟来源的证据较强，但原超时没有返回Token用量，无法实测其推理Token数量，也无法排除服务端排队等因素。不能声称已测得某个推理Token降幅。两次成功不代表任何长度简历都能在45秒内完成。

## 原始完整请求与SDK

实际环境：`openai==3.50.0`、`httpx2==2.16.0`；该版本SDK依赖httpx2，此环境没有传统httpx。

| 参数 | 原请求 | 修复后 |
| --- | --- | --- |
| model | qwen3.8-27b | 相同 |
| temperature | 0 | 相同 |
| timeout | 45秒 | 相同 |
| SDK max_retries | 0 | 相同 |
| max_tokens | 未传，供应商默认值未确认 | 仍未传；可显式诊断覆盖 |
| max_completion_tokens | 未传 | 未传，不假定该27b端点支持它 |
| stream | 非流式 | 非流式 |
| response_format | json_schema | 相同 |
| strict | true | true |
| enable_thinking | 未传，使用供应商默认行为 | 显式false，经extra_body发送 |
| thinking_budget | 未传 | 未传；诊断工具支持单独测试 |

原样本1system Prompt为1332字符，user简历为1360字符，实际HTTP请求体6798个UTF-8字节。Schema为1430字符的常规JSON序列化、1317字符的紧凑序列化，只有两个 `$defs`，包含根对象、四维对象和原文证据数组，并非无限增长的Schema。

修复后system为1500字符，user未变化。追加的文字只明确首尾花括号、禁止数组包裹及六个顶层键；没有删除或修改评分Rubric、维度定义、缺失项、置信度、分数范围或引用校验。

Schema SHA256在所有真实实验中一致：

`9078cf914295822191cae20d86114d65076ec965365f8e297cb3f9b58a94c298`

Rubric文本SHA256由回归测试固定检查：

`73b2989d6141a4c56d1ad28a8c90fa5b86ca104bbd650043a14e488193cef818`

原Prompt/简历组合SHA为 `0393735295d2d7e5558159f715f4b0b7aaa5853dd399b07a8ea67f8dd9f3ece2`；仅关思考的两次实验仍相同。补充对象要求后的样本1为 `62213fe1a55599438dc5238557bcaa1835848ccab1f40e68b46b35b691a6e5a8`。Prompt版本已更新为 `rubric-v2.1-object-v1`，因此评分缓存不会沿用旧Prompt结果。

源码验证SDK同步重试循环为 `range(max_retries+1)`。无网络构建及真实HTTP请求钩子均确认 `timeout=45` 最终形成：

```json
{"connect":45,"read":45,"write":45,"pool":45}
```

这是各传输阶段的超时，不是完整调用的总墙钟上限。原45–49秒本身不能说明发生了重试；本次原请求实际HTTP尝试数为1，异常链是 `APITimeoutError → ReadTimeout → ReadTimeout`，未收到响应头，发生在API等待阶段，尚未开始JSON/Pydantic/证据校验。

本地来源：`.venv/Lib/site-packages/openai/_base_client.py`的同步request重试/timeout异常处理；`httpx2/_client.py`的request/response hooks。生产客户端在 `engine/hybrid_engine.py` 已明确 `max_retries=0`，本次没有通过增加重试掩盖问题。

## 真实实验记录

每轮只发送一份原始简历，均使用 `.env` 的真实模型与凭证。首次普通沙箱网络访问在0.0756秒返回ConnectError，属于本地网络权限环境，保存在 `llm_live/sandbox_network_blocked.json`，不作为Qwen延迟或故障证据；获得工具网络授权后才执行以下实验。

| 实验 | 唯一/新增变化 | 耗时s | HTTP | 输入/输出/总Token | reasoning_tokens | finish_reason | 结构/证据结果 |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| baseline_original | 原完整请求 | 45.3011 | 未收到响应头 | 未返回 | 未返回 | 未返回 | ReadTimeout，未开始验证 |
| thinking_off_only | 仅enable_thinking=false | 15.8630 | 200 | 1726/1107/2833 | null | stop | Pydantic根对象类型失败，未保存这轮原始回答 |
| thinking_off_inspect | 相同请求，保留回答供诊断 | 13.9292 | 200 | 1726/937/2663 | null | stop | 实际根类型list，被拒绝；未自动拆包 |
| thinking_off_object_prompt | 加强原有顶层对象要求 | 24.0289 | 200 | 1793/1020/2813 | null | stop | dict；Pydantic及原文引用均通过 |
| final_config_candidate2 | 使用已修复配置，原始样本2；无CLI思考覆盖 | 14.8428 | 200 | 1477/809/2286 | null | stop | dict；Pydantic及原文引用均通过 |

所有真实实验HTTP请求尝试数均为1。每次请求的参数、字符/字节数、Prompt/Schema哈希、有效传输timeout、响应头时间、SDK耗时、验证耗时、request_id、供应商model、usage、finish_reason及失败阶段都保存在对应JSON。

注意：这些响应的 `completion_tokens_details.reasoning_tokens` 没有可用数值，必须保留null，不能写成0。关闭思考后的响应 `reasoning_content` 长度为0，但字符长度不能替代Token计数。未保存或打印模型思考原文。Token usage来自真实服务响应，不是估算。

`finish_reason=stop` 且输出937–1107 Token时依然可能不符合根Schema，说明这次结构失败不是Token截断。增加Token上限或无限延长timeout无法修复该根类型问题。新逻辑还显式拒绝 `finish_reason=length`，即使偶然能解析部分JSON也不能记为成功。

样本1真实有效引用示例：`work_experience[0].responsibility` 中“独立设计并实现登录认证及RBAC 角色权限控制体系，结合Redis 实现认证状态管理。”；其他维度的原文、证据缺失和置信度均保留在成功JSON的 `evaluation.debug_trace.tier3_dimension_evidence`。两个成功结果全部经过现有引用连续原文检查、实战来源/动作/长度要求、分数范围和缺乏证据约束，没有宽松替代。

## 修复内容

| 文件 | 修改 |
| --- | --- |
| `engine/llm_reviewer.py` | 提取共用请求构建器；增加可选thinking/Token参数；只强化对象输出格式说明；Rubric、Schema、证据校验与加权不变 |
| `engine/llm_observability.py` | 记录安全请求摘要、阶段计时、usage及reasoning_tokens、finish_reason和cause链；区分结构/证据错误；拒绝截断输出 |
| `engine/llm_diagnostics.py` | 无BGE的单样本真实入口；共用生产请求和校验；捕获HTTP尝试/响应头时序；保存有效或失败记录；只允许人工核验 |
| `main.py` | 新增llm-diagnose命令 |
| `config.json` | 唯一配置值变更为llm_review.enable_thinking=false，timeout仍45；业务权重、阈值均不改 |
| `config/validation.py` | 校验可选bool开关、正整数预算及禁用思考与thinking_budget的矛盾配置 |
| `config/job_compiler.py` | 当前Qwen评分配置生成时同步关闭默认思考，避免重新编译后恢复超时 |
| `tests/test_llm_transport.py` | 新增10项离线传输/指标/失败安全回归 |
| `README.md` | 新入口、参数和运行边界 |

默认不增加输出Token上限：本次没有 `finish_reason=length`，实际输出均能在原45秒内完成，没有证据证明需要调大输出容量。可选 `--max-tokens` 保留供后续明确实验使用。Schema复杂度未调整，严格结构和本地Pydantic均保留。

## 当前运行方式

```powershell
python main.py llm-diagnose --live --candidate 1 --label candidate1
python main.py llm-diagnose --live --candidate 2 --label candidate2
python main.py llm-diagnose --live --resume path/to/resume.json --label custom
```

均不加载BGE。默认读取最终配置，结果写入 `reports/llm_live/<label>.json`，失败退出1，成功退出0。同一label会覆盖同名诊断，请使用不同label保留对比。

保留思考的后续有限预算实验可显式使用 `--thinking on --thinking-budget 1024`；本次未执行此实验，不标记为通过。不要同时设置thinking_budget与reasoning_effort；也不要假定max_tokens能约束Qwen思维链。

原完整混合引擎入口 `python main.py diagnose --live --mode deep_mode` 会使用同一修复后的请求构建器，仍可运行。本轮为避免反复加载BGE，验收使用上述单Tier3入口，没有把未重跑的完整五份真实混合评分标为通过。

## 回归及验证限制

已运行全部原有 **80项回归测试，加新增10项，共90/90通过，0失败、0错误、0跳过**，记录见 [llm_regression_tests.log](llm_regression_tests.log)。回归是离线Mock，只验证代码契约及异常路由；本报告表中的真实评分来自另外的真实API调用。

新增测试覆盖：Rubric哈希/Schema不变、可选请求参数、reasoning usage缺失与正常值、finish_reason=length拒绝、JSON/Pydantic与引用错误分类、APITimeoutError cause链、无BGE的成功深评和无AUTO资格、部分数据禁止调用及参数校验。

尚未验证：真实端点多轮稳定性、更长简历/高并发延迟、保留思考模式的质量与预算权衡。根数组说明供应商本次strict行为并不可靠；后续若再发生结构漂移，继续安全失败并凭request_id排查网关，不能自动拆包或跳过校验。未调整40/30/20/10权重、52/60/82业务阈值；未进入浏览器采集或发送阶段。

## 官方依据与推断边界

- 官方列出qwen3.8-27b为默认开启思考的混合模型，支持通过extra_body设置enable_thinking：[深度思考](https://help.aliyun.com/zh/model-studio/deep-thinking)。
- 官方参数页列出默认reasoning_effort为xhigh，以及未设置时的thinking_budget映射；它也区分max_tokens与max_completion_tokens。该文档描述能力与默认规则，不能代替本次端点的未返回Token统计：[Chat Completions参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)。
- 官方支持JSON Schema及本地$defs/$ref，但支持清单不能证明本次自建部署每次都严格遵守；实际list响应就是需要本地校验的反例：[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。
