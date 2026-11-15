# Boss直聘 HR 智能简历筛选与自动沟通系统 — 需求规格说明书（PRD）

## 1. 业务全景与版本演进路线

系统面向 **“AI自动化开发助理/专员”** 等岗位，在 Boss 直聘网页端“推荐牛人”列表页实现 **“两级漏斗采集 $\rightarrow$ 三级混合打分 $\rightarrow$ 双阈值分流（高分直发 / 中分核验后批准发送）”** 的自动化闭环。

```
[推荐牛人列表加载]
       │
       ▼
[第一级漏斗：外层卡片秒筛] ──(状态为"继续沟通" / 命中本地去重库 / 硬条件不符)──► [跳过 (可选自动点"×不合适")]
       │ (通过硬筛选)
       ▼ (随机延时点开在线微简历弹窗)
[第二级漏斗：内层详情解析与三级混合打分]
       ├─► Tier 1: 关键词与规则底座打分
       ├─► Tier 2: 正负语义锚点 Embedding 粗排 ──(低于粗排及格线)──► [关闭弹窗跳过]
       └─► Tier 3: LLM 多维加权终审 (输出4维得分 + 3亮点 + 1风险)
                │
                ├──► [总分 ≥ 高分直发阈值 (默认85)] ──(开启直发时)──► [直接点击"立即沟通"发默认招呼语]
                ├──► [核验阈值 (默认60) ≤ 总分 < 85] ──────────────► [暂存至HR审核面板 → 勾选批准 → A+B寻址点击"立即沟通"]
                └──► [总分 < 核验阈值 (60)] ───────────────────────► [标记淘汰并跳过 (可选自动点"×不合适")]

```

### 版本迭代边界

| 版本阶段 | 核心交付范围 | 关键业务机制 |
| --- | --- | --- |
| **V1.0 核心闭环**<br>

<br>*(当前开发目标)* | 批量采集、三级混合打分、HR 审核看板、A+B 混合寻址批准发送、可视化热配置中心、配额与风控刹车 | 借鉴 `boss-hr-agent-toolkit` 模式：单次采集达到设定批次上限后暂停，HR 在看板集中审阅并勾选批准，脚本回溯页面精准触发“立即沟通” |
| **V1.1 进阶扩展**<br>

<br>*(二期迭代目标)* | 跨时段异步核验、自动收藏中转、批量解除收藏 | 采集到“待核验”牛人时自动点击卡片“收藏（感兴趣）”；支持跨天在审核面板批准后，自动进入“收藏夹”寻址发送，并对淘汰或已发人员执行“批量取消收藏” |

---

## 2. 核心功能模块与业务规则规格

### 2.1 两级漏斗采集与风控刹车模块

1. **配额与刹车控制（参数可动态修改）**
* **单批次采集上限（`batch_limit`）**：默认 **30 人**（指通过第一级漏斗并完成打分进入结果池的人数，或单次累计触发沟通动作上限），达到后自动暂停采集，等待 HR 在看板核验。
* **单日累计沟通上限（`daily_greet_limit`）**：默认 **100 人/天**，达到后锁死当日发送动作，防止账号触发商业化配额封禁。
* **风控验证码熔断**：实时监听 DOM 是否出现滑块验证弹窗（如 `.verify-slider` 或风控拦截页）。一旦捕获，立即挂起所有自动化协程，通过桌面通知/声音提醒人工介入，验证解除后支持断点续跑。


2. **第一级漏斗（外层卡片不点开秒筛）**
* **沟通状态过滤**：读取卡片主按钮文本，若为 `“继续沟通”`（代表已有历史聊天记录），直接跳过。
* **本地指纹去重**：提取卡片绑定 ID 或 `姓名+年龄+活跃状态+最近公司` 组合哈希，命中本地 SQLite 已评估记录库则直接跳过（优先级可调）。
* **硬性边界过滤**：按配置文件校验**最低学历、工作年限范围、期望薪资上限、活跃度要求（如排除“本月活跃/半年活跃”）、求职状态、排除岗位词（如纯销售/行政/客服）**。


3. **内层详情获取与“不合适”动作开关**
* 对通过第一级漏斗的卡片，按正态分布随机延时（如 `2.5s ~ 5.5s`）点击打开在线微简历详情弹窗，提取完整**个人优势、工作经历、项目经历**文本。
* **自动标记不合适开关（`auto_mark_unsuitable`）**：布尔型配置开关（默认 `false`）。开启时，对判定淘汰的候选人自动点击卡片右上角 `“×（不合适）”` 按钮以净化 Boss 推荐流；关闭时仅关闭弹窗并向下滚动，减少非必要点击。



---

### 2.2 可插拔三级混合打分引擎

支持在配置中心一键切换 **“极速模式（Rule + Embedding）”** 与 **“深度模式（Rule + Embedding + LLM）”**。各维度满分比例支持热修改，系统自动按百分比归一化计算总分（0~100 分）。

| 打分层级 | 作用阶段 | 核心计算逻辑 | 性能与成本控制 |
| --- | --- | --- | --- |
| **Tier 1：规则与关键词底座** | 详情文本秒级扫描 | 扫描预置的高/中权重技术词表（如 `Coze`, `Dify`, `LangGraph`, `n8n`, `Playwright`, `影刀`, `Python`, `RAG`, `Cursor`），区分“出现在项目经历”与“仅出现在个人总结”赋予不同系数，生成**命中技术标签集**与**规则基础分**。 | 毫秒级、0 算力消耗 |
| **Tier 2：正负锚点语义粗排** | 本地向量模型过滤 | 将候选人项目与工作经历切分为独立语义块，调用本地 Embedding 模型计算：<br>

<br>• **正锚点得分**：与 3~5 条理想画像语句的最大余弦相似度 $\max(\text{sim}_{pos})$<br>

<br>• **负锚点惩罚**：与“仅用AI写文案/画图等水词描述”的相似度 $\max(\text{sim}_{neg})$<br>

<br>• **粗排拦截**：若 Tier 1 + Tier 2 折算分低于 `coarse_pass_score`（默认 55 分），直接淘汰，不调用 LLM。 | 毫秒级本地推理，拦截 60%~70% 伪 AI 简历 |
| **Tier 3：LLM 多维结构化终审** | 过线候选人深度评估 | 注入岗位要求与可配置权重的四维评分标准（Rubric），强制输出 JSON 格式的各维度得分、**3 条核心匹配亮点**及 **1 条潜在风险/存疑点**。 | 仅对前两层过线者调用，单批次仅需请求十余次 |

#### 四维动态加权评分体系（默认权重比例可调）

* **维度 A：AI 应用与自动化实战经验（默认权重 `40%`）**：重点考察 Agent/工作流（Dify/Coze/LangGraph/n8n）、LLM API 工程化接入、RAG 或 RPA（Playwright/影刀）的实际项目交付，而非单纯概念了解。
* **维度 B：编程与技术工程底座（默认权重 `30%`）**：考察 Python/JS/后端接口开发、数据爬虫与清洗、自动化脚本编写等工程实现能力。
* **维度 C：AI 提效意识与工具链（默认权重 `20%`）**：考察日常使用 Cursor/Claude/Copilot 辅助开发或搭建内部自动化流并产生明确提效结果的经历。
* **维度 D：岗位契合度与稳定性（默认权重 `10%`）**：考察沟通表达、文档沉淀意识，以及工作经历是否存在高频闪辞风险。

---

### 2.3 分流决策与“A+B 混合寻址”发送模块

1. **双阈值分流策略（带全局直发开关 `enable_auto_greet`）**
* **高分直发区（`score >= auto_greet_threshold`，默认 `85` 分）**：
* 当 `enable_auto_greet = true` 时：在微简历弹窗评估完的瞬间，直接触发弹窗内的 `“立即沟通”` 按钮（由 Boss 直聘自动发出后台预设的默认招呼语：`“hello，是有在看机会吗？我们这边的AI自动化开发助理/专员岗位，不知道有没有兴趣了解一下~”`）。
* 当 `enable_auto_greet = false` 时（冷启动调优期）：并入待核验池，并在看板标注 `🔥 建议直发` 标签。


* **人工核验区（`review_threshold <= score < auto_greet_threshold`，默认 `60 ~ 84` 分）**：存入当前批次待核验队列，关闭微简历弹窗继续扫描下一位。
* **低分淘汰区（`score < review_threshold`，默认 `< 60` 分）**：记录状态为已淘汰，关闭弹窗跳过。


2. **A+B 混合寻址执行机制（保障“先批量保存、后批准发送” 100% 命中）**
* **采集期双重锚定**：扫描每张牛人卡片时，同时记录 **锚点 A（DOM 定位属性）**：卡片的 `data-lid`、列表序号索引及当前 DOM 节点引用；**锚点 B（底层业务标识）**：拦截网络响应或解析 DOM 绑定的 Vue/React 数据属性，提取候选人唯一 `encryptGeekId` 与 `securityId`。
* **批准发送期寻址回溯**：
1. **优先走方案 A（同页 DOM 回溯）**：由于采集 30 人期间页面保持驻留不刷新，脚本根据 `data-lid` 或 `encryptGeekId` 选择器定位目标卡片，调用 `scrollIntoView({ behavior: 'smooth', block: 'center' })` 滚动至可视区域，随机停顿 `1.2s ~ 2.5s` 后点击卡片上的 `“立即沟通”` 按钮。
2. **兜底走方案 B（加密 ID 弹窗/接口寻址）**：若页面因虚拟列表回收导致外层 DOM 节点卸载，通过注入脚本或携带 `encryptGeekId` + `securityId` 触发候选人详情组件重新挂载，再点击弹窗内的 `“立即沟通”` 按钮。





---

### 2.4 HR 核验看板与可视化配置中心

提供轻量化 Web 控制台（与浏览器自动化实例实时通信），包含两个核心视图：

1. **待核验候选人看板（Review Dashboard）**
* **顶部状态栏**：显示今日已打招呼 `X / 100`、本批次已扫描 `Y / 30`、当前运行状态（采集中 / 等待核验 / 触发滑块暂停 / 批量发送中）。
* **候选人审阅卡片**：按总分降序排列，每张卡片直观展示：
* 基础信息（姓名、年龄、学历、经验、期望薪资、在线活跃度）
* **总分徽章 + 四维得分迷你进度条**
* **命中的技术栈高亮 Tag**（如 `Python` `Coze` `Playwright`）
* **AI 提炼的 3 条核心亮点（绿色条目）+ 1 条潜在风险（橙色条目）**（点击可展开查看抓取到的原始简历长文本）


* **批量操作栏**：支持 `全选 ≥75分`、`勾选批准并批量发送`、`一键标记淘汰`。


2. **规则与权重热配置抽屉（Config Drawer）**
* 提供可视化滑块与表单，随时修改配额数字（30/100）、高低分阈值（60/85）、四维权重滑块、关键词词库及功能开关（是否开启高分直发、是否自动点“不合适”），保存后立即对下一批次生效。



---

## 3. 核心数据结构与配置 Schema 设计

### 3.1 全局热加载配置结构（`config.json` / `config.yaml`）

```json
{
  "job_target": {
    "job_title": "AI自动化开发助理/专员",
    "default_greeting_note": "使用Boss直聘后台预设默认招呼语，仅触发点击立即沟通"
  },
  "quotas_and_switches": {
    "batch_scan_limit": 30,
    "daily_greet_limit": 100,
    "enable_auto_greet": false,
    "auto_mark_unsuitable": false,
    "enable_local_dedup": true,
    "scoring_mode": "deep_mode",
    "action_delay_seconds": [2.5, 5.5]
  },
  "hard_filters": {
    "min_degree": "本科",
    "work_years_allowed": ["应届生", "1年以内", "1-3年"],
    "max_expected_salary_k": 12,
    "active_status_allowed": ["刚刚活跃", "今日活跃", "3日内活跃", "本周活跃"],
    "exclude_job_Status": ["在职-暂不考虑"],
    "exclude_position_keywords": ["销售", "客服", "行政", "财务", "纯设计", "地产"]
  },
  "scoring_config": {
    "thresholds": {
      "coarse_pass_score": 55,
      "review_threshold": 60,
      "auto_greet_threshold": 85
    },
    "dimension_weights": {
      "ai_automation_practice": 0.40,
      "engineering_foundation": 0.30,
      "ai_efficiency_mindset": 0.20,
      "job_fit_and_stability": 0.10
    },
    "keyword_rules": {
      "high_value_tags": [
        {"word": "Coze", "score": 12},
        {"word": "Dify", "score": 12},
        {"word": "n8n", "score": 12},
        {"word": "LangChain", "score": 15},
        {"word": "LangGraph", "score": 15},
        {"word": "Playwright", "score": 12},
        {"word": "影刀", "score": 10},
        {"word": "RAG", "score": 12},
        {"word": "Agent", "score": 10}
      ],
      "medium_value_tags": [
        {"word": "Python", "score": 8},
        {"word": "FastAPI", "score": 6},
        {"word": "爬虫", "score": 6},
        {"word": "Cursor", "score": 6},
        {"word": "Claude", "score": 5},
        {"word": "自动化脚本", "score": 8}
      ]
    },
    "semantic_anchors": {
      "positive_anchors": [
        "使用Python、Playwright或影刀RPA开发跨系统自动化脚本，实现业务流程自动抓取与执行",
        "基于Dify、Coze、n8n或LangChain/LangGraph搭建AI Agent工作流，接入大模型API解决实际业务问题",
        "构建知识库RAG检索增强系统，熟练使用Cursor、Copilot等AI编程工具大幅提升研发与交付效率"
      ],
      "negative_anchors": [
        "仅在日常办公中使用ChatGPT、文心一言撰写宣传文案、公众号推文、小红书笔记或周报总结",
        "使用Midjourney、Stable Diffusion生成海报图片，无任何编程开发、脚本编写或自动化工作流搭建经验"
      ]
    }
  }
}

```

### 3.2 候选人评估与寻址输出结构（`CandidateRecord` JSON Schema）

这是打分模块输出给前端核验看板及执行模块的统一数据契约：

```json
{
  "candidate_id": "fd89a7s6d8f7a6s5d",
  "addressing_anchor": {
    "dom_data_lid": "1029384756",
    "dom_card_index": 14,
    "encrypt_geek_id": "849302a8b9c0d1e2",
    "security_id": "v1_987654321abcdef"
  },
  "basic_info": {
    "name": "张同学",
    "age": 23,
    "degree": "本科",
    "work_years": "1年以内",
    "expected_salary": "7-10K",
    "active_text": "刚刚活跃",
    "recent_company_and_role": "某科技公司 · Python开发实习生"
  },
  "evaluation": {
    "total_score": 81.5,
    "routing_decision": "PENDING_REVIEW",
    "matched_tags": ["Python", "Coze", "Playwright", "Cursor"],
    "semantic_similarity": {
      "pos_max_sim": 0.82,
      "neg_max_sim": 0.21
    },
    "dimension_scores": {
      "ai_automation_practice": 33,
      "engineering_foundation": 25,
      "ai_efficiency_mindset": 16,
      "job_fit_and_stability": 7.5
    },
    "highlights": [
      "有基于 Coze 搭建企业内部客服自动分流工作流的实战落地经验，熟悉 Webhook 与 API 插件配置",
      "具备扎实的 Python 与 Playwright 网页自动化脚本开发能力，曾独立完成电商后台数据自动巡检工具",
      "日常深度使用 Cursor 辅助开发，在简历中有明确的研发提效量化产出"
    ],
    "risk_point": "工作经历主要为一段 4 个月的实习，缺乏复杂多节点 Agent 状态机调优经验，需面试核实代码独立完成度"
  },
  "status": "WAITING_HR_APPROVAL",
  "evaluated_at": "2026-11-13T14:40:00+08:00"
}

```

---

## 4. 技术选型与模块落地建议

为了让你最快跑通 V1.0 并复用成熟工程组件，建议采用以下轻量级架构组合：

* **浏览器控制与反检测采集层**：Python `Playwright`（通过 **CDP 协议 `connect_over_cdp` 直接接管你本地已登录的 Chrome 浏览器端口**，免去每次扫码登录，且天然保留真实浏览器指纹，极大降低触发 Boss 直聘滑块的概率）。
* **三级混合打分引擎层**：
* **Tier 1（规则）**：Python 正则与词典权重计算。
* **Tier 2（语义粗排）**：直接调用本地中文 Embedding 模型（如 `bge-large-zh-v1.5`）结合 `numpy` 矩阵点积计算正负锚点 Cosine Similarity，耗时 `< 20ms`。
* **Tier 3（LLM 终审）**：通过 `LiteLLM` 或 OpenAI 兼容接口支持本地大模型或高性价比云端 API（开启 `response_format={"type": "json_object"}` 强约束输出）。


* **后端服务与状态存储**：`FastAPI`（提供配置热更新接口、采集启停控制接口、批量批准发送接口）+ 本地 `SQLite`（存储每日配额计数与候选人历史评估记录）。
* **HR 审核与配置面板**：单页 `Vue 3`（或直接用 Streamlit/FastAPI 挂载静态面板）实现卡片列表审阅、分数过滤与一键批准发送。

这份需求与架构设计是否已经完全覆盖你的预期？如果确认无误，我们可以直接开始推进代码实现——你想先从 **“Playwright 接管已登录 Chrome 并解析 Boss 直聘列表/微简历 DOM 的采集模块”** 开始写，还是先写 **“三级混合打分引擎（规则 + BGE 正负锚点 + LLM 结构化输出）”**？