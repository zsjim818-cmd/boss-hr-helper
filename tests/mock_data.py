"""Preserved original benchmark and single-resume fixtures; no model imports."""

BENCHMARK_CANDIDATES = [
    # ==================== 样本 1：截图真实简历（AI Agent + RAG + FastAPI 实战派） ====================
    {
        "ok": True,
        "name": "钟盛 (样本1: 截图真实简历-Agent实战)",
        "age": "23岁",
        "degree": "硕士",
        "work_years": "28年应届生",
        "job_status": "在校-月内到岗",
        "active_status": "刚刚活跃",
        "expectation": {"position": "后端开发", "industry": "计算机软件", "salary": "4-8K"},
        "advantage": (
            "1、Agent开发实习经验，Java后端项目经验，软件工程硕士在读，熟悉 Python、LangChain、LangGraph 与 ReAct，参与 Agent 图工作流重构。\n"
            "2、熟悉 RAG 知识库构建流程，曾使用 BGE、Redis Stack（HNSW）实现专利知识问答系统。\n"
            "3、具备 FastAPI 异步接口与流式通信经验，使用 LiteLLM 管理多模型调用入口，通过 LangSmith 调试链路。"
        ),
        "work_experience": [
            {
                "company": "睿能智慧数字科技（深圳）有限公司",
                "position": "全栈工程师",
                "job_type": "实习",
                "start": "2026.06",
                "end": "2026.08",
                "responsibility": (
                    "• 参与 Agent 服务后端工程化，将原有While-Loop 执行流程重构为LangGraph 图工作流，抽象AgentConfig、AgentState 与Reducer 状态更新机制。\n"
                    "• 独立设计并实现登录认证及RBAC 角色权限控制体系，结合Redis 实现认证状态管理。\n"
                    "• 通过FastAPI 提供实时流式接口，使用LiteLLM与Docker Compose 部署多模型统一调用网关，编写Bash 脚本一键启动服务。"
                ),
                "keywords": ["智能体编排", "Python", "全栈开发"]
            }
        ],
        "project_experience": [
            {
                "name": "无人机专利Agent 系统",
                "role": "负责人",
                "start": "2026.04",
                "end": "2026.05",
                "description": "面向垂直领域的无人机私有专利智能Agent 问答与知识库检索平台（RAG）。技术栈：Python、LangChain、ReAct Agent、Redis Stack（HNSW）、BGE-Large-zh、DeepSeek-R1。",
                "achievement": "实现脱离外网环境下的毫秒级检索-智能推理-长文本总结服务闭环"
            },
            {
                "name": "同城到店通",
                "role": "负责人",
                "start": "2026.03",
                "end": "2026.04",
                "description": "O2O 聚合交易平台。技术栈：SpringBoot 3、MySQL、Redis、Redisson、Lua。",
                "achievement": "解决高并发秒杀超卖与缓存雪崩问题"
            }
        ],
        "education": [
            {"school": "江西师范大学", "major": "软件工程", "degree": "硕士", "start": "2025", "end": "2028", "school_tags": ["省部共建"]},
            {
                "school": "东华理工大学", "major": "软件工程", "degree": "本科", "start": "2021", "end": "2025", "school_tags": ["省部共建"],
                "thesis_title": "基于springboot的考研伴侣系统",
                "thesis_description": "加入了Ollama本地部署的Deepseek-R1:8b模型实现智能对话陪伴。"
            }
        ],
        "skills": "熟悉Linux、Git、Docker、Bash脚本；熟悉Python、FastAPI、LangChain、LangGraph、RAG、PgVector、LiteLLM；熟悉Java、Spring Boot、Redis。",
        "_meta": {"candidate_key": "job_1:geek_zhongsheng", "encrypt_geek_id": "geek_001"}
    },

    # ==================== 样本 2：Coze/Dify 工作流 + Playwright/影刀 RPA 专员 ====================
    {
        "ok": True,
        "name": "陈同学 (样本2: Coze工作流+RPA专员)",
        "age": "24岁",
        "degree": "本科",
        "work_years": "1年",
        "job_status": "离职-随时到岗",
        "expectation": {"position": "AI自动化工程师", "salary": "7-9K"},
        "advantage": "1年Python自动化与AI工作流搭建经验，熟练使用Coze、Dify搭建业务智能体，精通影刀RPA与Playwright网页自动化脚本。",
        "work_experience": [
            {
                "company": "某跨境电商科技公司",
                "position": "RPA与AI自动化专员",
                "start": "2025.07",
                "end": "2026.10",
                "responsibility": (
                    "• 使用 影刀RPA 与 Python Playwright 编写跨平台数据采集与自动上架脚本，每日自动处理 2000+ 商品表单。\n"
                    "• 基于 Dify 和 Coze 搭建客服邮件自动分类与回复草稿生成工作流，接入 DeepSeek API，人工审核耗时降低 60%。"
                ),
                "keywords": ["Playwright", "影刀", "Coze"]
            }
        ],
        "project_experience": [],
        "education": [{"school": "广东工业大学", "major": "信息管理与信息系统", "degree": "本科"}],
        "skills": "Python, Playwright, 影刀RPA, Coze, Dify, Cursor",
        "_meta": {"candidate_key": "job_1:geek_chen", "encrypt_geek_id": "geek_002"}
    },

    # ==================== 样本 3：传统 Python 爬虫/后端 + 日常使用 Cursor (中分待核验) ====================
    {
        "ok": True,
        "name": "刘同学 (样本3: 传统Python开发+AI提效)",
        "age": "25岁",
        "degree": "本科",
        "work_years": "2年",
        "job_status": "在职-考虑机会",
        "expectation": {"position": "Python开发", "salary": "8-11K"},
        "advantage": "2年Python后端与网络爬虫经验，熟悉FastAPI、Redis、Docker，日常深度使用Cursor与Claude辅助编写代码提升效率。",
        "work_experience": [
            {
                "company": "某数据服务有限公司",
                "position": "Python爬虫工程师",
                "start": "2024.07",
                "end": "至今",
                "responsibility": "负责行业公开招投标数据的定时抓取、清洗与入库，使用 Python + FastAPI 封装内部数据查询接口，使用 Cursor 辅助重构老旧采集脚本。",
                "keywords": ["Python", "FastAPI", "爬虫"]
            }
        ],
        "project_experience": [],
        "education": [{"school": "南昌航空大学", "major": "计算机科学与技术", "degree": "本科"}],
        "skills": "Python, FastAPI, MySQL, Redis, Docker, Cursor, Claude",
        "_meta": {"candidate_key": "job_1:geek_liu", "encrypt_geek_id": "geek_003"}
    },

    # ==================== 样本 4：伪 AI 经验（纯用 ChatGPT 写文案/画图，测试 Tier 2 拦截） ====================
    {
        "ok": True,
        "name": "王同学 (样本4: 伪AI文案运营-应被拦截)",
        "age": "23岁",
        "degree": "本科",
        "work_years": "1年",
        "job_status": "离职-随时到岗",
        "expectation": {"position": "AI内容运营", "salary": "6-8K"},
        "advantage": "深度拥抱AI，精通使用ChatGPT、Claude、文心一言、豆包进行小红书爆款文案创作与公文润色，熟练使用Midjourney生成宣传海报。",
        "work_experience": [
            {
                "company": "某文化传媒公司",
                "position": "新媒体AI运营",
                "start": "2025.06",
                "end": "2026.09",
                "responsibility": "负责公司公众号与小红书矩阵账号日更，利用 ChatGPT 批量生成种草文案与短视频口播稿，使用 Midjourney 制作活动配图，使用 AI 工具生成周报 PPT。",
                "keywords": ["新媒体运营", "文案策划"]
            }
        ],
        "project_experience": [],
        "education": [{"school": "某财经大学", "major": "新闻学", "degree": "本科"}],
        "skills": "ChatGPT, Midjourney, 剪映, 秀米, 办公软件",
        "_meta": {"candidate_key": "job_1:geek_wang", "encrypt_geek_id": "geek_004"}
    },

    # ==================== 样本 5：硬性条件不符（期望薪资 18-25K 超出 12K 上限，测试 Tier 0 拦截） ====================
    {
        "ok": True,
        "name": "赵同学 (样本5: 薪资超上限-应被Tier0秒拒)",
        "age": "28岁",
        "degree": "硕士",
        "work_years": "4年",
        "job_status": "在职-月内到岗",
        "expectation": {"position": "AI算法工程师", "salary": "18-25K"},
        "advantage": "4年大模型微调与Agent开发经验，精通Python、LangGraph、RAG。",
        "work_experience": [],
        "project_experience": [],
        "education": [{"school": "某985高校", "major": "人工智能", "degree": "硕士"}],
        "skills": "Python, LangGraph, RAG",
        "_meta": {"candidate_key": "job_1:geek_zhao", "encrypt_geek_id": "geek_005"}
    }
]


SAMPLE_RESUME_JSON = {
    "ok": True,
    "name": "钟盛",
    "age": "23岁",
    "degree": "硕士",
    "work_years": "28年应届生",
    "job_status": "在校-月内到岗",
    "active_status": "刚刚活跃",
    "expectation": {
        "position": "后端开发",
        "industry": "半导体/芯片、计算机软件、互联网",
        "salary": "4-8K"
    },
    "advantage": (
        "1、Agent开发 实习经验，Java后端 项目经验，软件工程硕士在读，熟悉 Python、LangChain、LangGraph 与 ReAct，参与 Agent 图工作流重构，具备状态管理、节点编排及工具调用实践。\n"
        "2、熟悉 RAG 知识库构建流程，具备文本向量化、语义检索及问答上下文组织经验，曾使用 BGE、Redis Stack（HNSW）实现专利知识问答系统。\n"
        "3、具备 FastAPI 异步接口与流式通信经验，完成模型输出、工具调用等事件封装及 Vue 3 前后端接口联调，具备登录认证、RBAC 权限控制与 Redis 状态管理实践。\n"
        "4、具备多模型 API 接入与统一网关部署经验，使用 LiteLLM 管理不同厂商模型的调用入口，并通过 LangSmith 调试 Agent 调用链路。\n"
        "5、具备 Java、Spring Boot、MySQL、Redis 后端开发经验，有缓存、异步下单与库存一致性相关项目实践。\n"
        "6、熟悉 Linux、Git、Docker / Docker Compose，参与服务器环境搭建与应用部署，能够编写 Bash 启动脚本；通过 CET-6。"
    ),
    "work_experience": [
        {
            "company": "睿能智慧数字科技（深圳）有限公司",
            "position": "全栈工程师",
            "job_type": "实习",
            "start": "2026.06",
            "end": "2026.08",
            "duration": "3个月",
            "responsibility": (
                "• 参与 Agent 服务后端工程化，将原有While-Loop 执行流程重构为LangGraph 图工作流，抽象AgentConfig、AgentState 与Reducer 状态更新机制，拆分推理、工具调用及业务模块。\n"
                "• 独立设计并实现登录认证及RBAC 角色权限控制体系，结合Redis 实现认证状态及相关缓存管理。\n"
                "• 统一封装模型生成、工具调用和节点事件，通过FastAPI 提供实时流式接口，完成前后端异步事件链路。\n"
                "• 使用LiteLLM与Docker Compose 部署多模型统一调用网关。\n"
                "• 参与Linux 服务器环境与多服务部署、Git 协作规范制定，编写Bash 脚本一键启动Agent、后端及相关依赖服务。"
            ),
            "performance": "",
            "keywords": ["智能体编排", "Python", "全栈开发"]
        }
    ],
    "project_experience": [
        {
            "name": "无人机专利Agent 系统",
            "role": "负责人",
            "start": "2026.04",
            "end": "2026.05",
            "duration": "2个月",
            "description": (
                "项目简介：面向垂直领域的无人机私有专利智能Agent 问答与知识库检索平台。核心业务涵盖海量专利文档的向量化检索（RAG）、多工具智能路由调度以及流式对话交互。\n"
                "技术栈：Python、LangChain、ReAct Agent、Redis Stack（HNSW）、BGE-Large-zh、DeepSeek-R1。\n"
                "• 基于ReAct 范式构建智能体核心框架，注册并维护无人机专属工具集合；\n"
                "• 引入简单工厂模式统一管理大模型与Embedding 模型的实例化；\n"
                "• 借鉴后端AOP思想设计独立middleware 模块，实现工具调用监控与可视化日志输出。"
            ),
            "achievement": "实现了脱离外网环境下的“毫秒级检索-智能推理-长文本总结”服务闭环"
        },
        {
            "name": "同城到店通",
            "role": "负责人",
            "start": "2026.03",
            "end": "2026.04",
            "duration": "2个月",
            "description": (
                "面向海量用户的O2O 聚合交易与社交分享平台。技术栈：SpringBoot 3、MySQL、Redis、Redisson、Lua、MyBatis-Plus、Caffeine。"
            ),
            "achievement": "解决高并发抢购场景下超卖、宕机及缓存雪崩问题"
        }
    ],
    "education": [
        {
            "school": "江西师范大学",
            "major": "软件工程",
            "degree": "硕士",
            "start": "2025",
            "end": "2028",
            "school_tags": ["省部共建"]
        },
        {
            "school": "东华理工大学",
            "major": "软件工程",
            "degree": "本科",
            "start": "2021",
            "end": "2025",
            "school_tags": ["省部共建"],
            "honors_and_courses": "国家励志奖学金（两次）、校二等奖学金、蓝桥杯C/C++大学生B组省二",
            "thesis_title": "基于springboot的考研伴侣系统",
            "thesis_description": "一个为考研学子提供各种信息服务的系统，并加入了Ollama本地部署的Deepseek-R1:8b模型实现智能对话陪伴，提前写好了一些提示词对应不同角色供用户选择。"
        }
    ],
    "certifications": ["大学英语六级", "CET-6", "驾驶证C1"],
    "skills": "熟悉Linux、Git、Docker / Docker Compose，具备服务器部署、Bash 脚本编写经验；熟悉Python、FastAPI，具备LLM/Agent 应用开发经验，熟悉LangChain、LangGraph、RAG、PgVector、LiteLLM等技术；熟悉Java SE、Spring Boot、Redis。",
    "_meta": {
        "candidate_key": "job_ai_dev:geek_zhongsheng",
        "encrypt_job_id": "job_ai_dev",
        "encrypt_geek_id": "geek_zhongsheng",
        "dom_data_lid": "lid_882910",
        "dom_card_index": 1,
        "downloaded_at": "2026-11-13 17:55:00",
        "first_run_id": "run_20261113_01"
    }
}
