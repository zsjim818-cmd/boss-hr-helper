# 分阶段验证记录

目录整理后统一入口为 `python main.py test/benchmark/diagnose/sample`。下方 P0/P1/P2 记录保留当时命令与结果；历史根脚本现在位于 `tests/`，当前运行方法见 README。

全部采用本地确定性 Mock，无真实 BGE/Qwen/API 调用。

## P0

执行 `.venv\Scripts\python.exe -X utf8 -m unittest tests.test_p0 -v`。
实际运行 8 个测试，通过 8 个，失败 0 个。
覆盖薪资格式、学历三态、日薪/缺失薪资、标签污染、历史客服转开发、超时与解析异常路由、采集失败/部分数据、低分人工判断。
P0 通过后开始 P1。

## P1

执行 `.venv\Scripts\python.exe -X utf8 -m unittest tests.test_p0 tests.test_llm_review tests.test_job_compiler tests.test_rankers -v`。
实际运行 32 个测试，通过 32 个，失败 0 个。P0 回归、规则与语义 16 项、LLM 7 项、配置编译 1 项。
开发中一轮测试的数学占位文本被标签规则降权，已隔离公式测试和标签规则测试后重新运行通过；早期未落盘测试模块的导入错误也已解决。
P1 通过后开始集成 P2。

## P2 及最终回归

首次集成运行 63 项全部通过。随后独立审查发现并修复了单技术名引用支撑高分、缓存缺失维度证据仍放行，以及“熟悉+技术列表”被当作实战的遗漏，增加对应断言。
最终运行 `.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v` 的同等 unittest suite，77 项全部通过，失败/错误/跳过均为0。完整逐项结果在 `tests_final.log`。

最终边界覆盖：小数薪资、中文单位、13薪、日/年周期、面议/缺失/无单位/单位冲突/倒置区间，学历未知与教育条目归一，纯标签与熟悉前缀、别名及多字段去重、历史客服加真实项目、Agent/RPA各自满足、重复文本和同经历拆分、块内负向惩罚，LLM超时/无效JSON/越界/伪引用/单技术名引用/缺证据/低置信度/个人字段隔离，fast永不AUTO，Schema映射与卡片隔离、缓存损坏/伪状态/缺失维度/配置与模型Prompt变化失效、批次快照、确定性Mock复算。

执行 `uv lock --cache-dir .uv-cache` 成功（补齐原本使用却未声明的 python-dotenv）；随后 `uv lock --check --offline --cache-dir .uv-cache` 通过。前两次普通权限/离线解析未成功，之后经工具授权联网解析完成；未安装或升级当前Python环境。

## 真实本地 BGE 运行检查

执行本地模型 `test_benchmark.py --live --mode fast_mode`，启用 HF/Transformers 离线模式，CPU执行，退出码0。模型快照 `79e7739b6ab944e86d6171e44d24c997fc1e0116`。
五类粗排82.06 / 86.07 / 37.25 / 17.84 / 0，正式深度分均为空，无AUTO_GREET。见 `local_bge_fast.json` 与 `local_bge_fast.log`。
最终再用最终配置补跑并由诊断CLI直接导出JSON，标准输出和标准错误合并保存为干净日志；参数快照与最终配置保持一致。
未调用Qwen API。真实BGE检查证明接入和计算可运行，不能证明匹配质量提升；第三类样本低于粗排线需HR标注校准。

## 目录整理回归

按 `开发文档/项目预设结构.md` 将现有文件迁到 `config/`、`schemas/`、`engine/`、`storage/`、`tests/`。Schema的文本投影移入 `engine/resume_text.py`，没有修改评分公式、阈值或配置。
五类样本内容哈希迁移前后均为 `668750b215e2a568f5a832b92bb1fee022611051763811341339d2a650d54d1e`；单例样本同样保持一致。
原77项测试全部通过，新增3项统一入口/跨工作目录回归后 **80/80通过**。完整记录更新于 `tests_final.log`。
新基准与迁移前报告的五类fast/deep完整输出、边界结果、稳定性、配置快照及样本内容哈希逐项相等。更新的报告仅调整源码位置及源码哈希。
独立检查还从TEMP目录验证了统一入口与各诊断help、Mock运行，无包导入循环。本轮未重跑真实BGE或调用Qwen，上一轮真实BGE证据保留。

## 后续：Qwen Tier3真实诊断

真实复现45.3011秒ReadTimeout后，保持45秒/0重试，显式关闭思考并补充顶层对象Prompt要求。两份原始简历Tier3分别24.0289秒/76分、14.8428秒/60.5分，严格Pydantic及原文证据均通过；诊断固定人工核验，未运行BGE。
全部原80项加新增10项，共90项回归通过，见 `llm_regression_tests.log`；完整实验、失败类型、Token和限制见 `llm_live_diagnosis.md`。
