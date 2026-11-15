import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field
from openai import OpenAI
from config.settings import DEFAULT_CONFIG_PATH
from engine.rule_matcher import DEFAULT_KEYWORD_CONFIG, DEFAULT_SOURCE_WEIGHTS, DEFAULT_EVIDENCE_QUALITY
from engine.semantic_ranker import DEFAULT_SEMANTIC_CONFIG

logger = logging.getLogger("JobCompiler")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ==================== 1. HR 上传的原始岗位与筛选要求输入 ====================
class HRJobUploadInput(BaseModel):
    job_title: str = Field(..., description="招聘岗位名称")
    raw_jd_text: str = Field(..., description="直接复制粘贴的岗位 JD 原文（含岗位职责与任职要求）")
    hr_custom_requirements: str = Field(
        ...,
        description="HR平时筛选简历的具体要求、加分项与一票否决雷区（自然语言）"
    )
    batch_scan_limit: int = Field(30, description="单批次扫描上限")
    daily_greet_limit: int = Field(100, description="单日打招呼上限")


# ==================== 2. LLM 自动编译生成的打分规则结构 ====================
class KeywordRuleItem(BaseModel):
    word: str = Field(..., description="技术栈、工具或核心能力关键词，如 Coze, LangGraph, Python, Playwright")
    score: int = Field(..., ge=3, le=18, description="单次命中基础分(核心词10-15分，辅助词5-8分)")


class PositiveAnchorGroups(BaseModel):
    agent: List[str] = Field(min_length=1, max_length=5)
    rpa: List[str] = Field(min_length=1, max_length=5)


class CompiledScoringProfile(BaseModel):
    min_degree: str = Field(..., description="最低学历要求，如：大专、本科、硕士、不限")
    max_expected_salary_k: float = Field(..., gt=0, description="期望月薪上限(K)，如未提及默认填 15")
    exclude_position_keywords: List[str] = Field(
        ..., description="仅作人工核验线索的历史岗位词，不单凭职位名称硬拒绝"
    )
    high_value_tags: List[KeywordRuleItem] = Field(
        ..., description="从JD和HR要求中提取的核心必杀技关键词列表（6-12个）"
    )
    medium_value_tags: List[KeywordRuleItem] = Field(
        ..., description="从JD和HR要求中提取的次要加分技术词或工具词列表（6-12个）"
    )
    jd_core_requirement_chunks: List[str] = Field(
        ..., min_length=2, max_length=4,
        description="提炼后的JD核心职责与技术要求条目（剔除公司介绍和福利噪音，供BGE向量直接对比）"
    )
    positive_anchor_groups: PositiveAnchorGroups = Field(
        description="两条OR技术路线分别生成具体开发经历锚点，agent和rpa均保留，候选人仅需满足其中一路"
    )
    negative_anchors: List[str] = Field(
        ..., min_length=2, max_length=4,
        description="将HR排斥的伪经验转化为2-4段【低分/伪AI候选人简历中常出现的水词经历描述】，供BGE计算负向惩罚"
    )


class JobProfileCompiler:
    def __init__(
        self,
        llm_base_url: str = "http://localhost:4000/v1",
        llm_api_key: str = "sk-local-key",
        llm_model_name: str = "gpt-4o-mini"
    ):
        self.client = OpenAI(base_url=llm_base_url, api_key=llm_api_key)
        self.model_name = llm_model_name

    def compile_and_save(
        self, upload_input: HRJobUploadInput, output_config_path: str = str(DEFAULT_CONFIG_PATH)
    ) -> dict:
        """
        接收 HR 上传的【岗位JD】+【HR自然语言要求】，自动编译为三级打分引擎所需的 config.json
        """
        logger.info(f"正在解析岗位【{upload_input.job_title}】的 JD 与 HR 筛选要求...")

        system_prompt = """你是一位资深技术招聘架构师。你的任务是将 HR 上传的【岗位 JD 原文】和【HR 平时筛简历的口头要求】，编译为自动化简历筛选系统的三级打分配置：
1. 提取硬性过滤条件（学历、薪资上限、不相关的排除岗位词）。
2. 提取 Tier 1 规则引擎所需的关键词表（high_value_tags 与 medium_value_tags），注意关键词必须是简历中常出现的标准技术名词（如 Python, LangChain, LangGraph, Coze, Dify, n8n, Playwright, 影刀, RAG, FastAPI, Cursor 等）。
3. 生成 Tier 2 BGE 向量引擎所需的对比锚点：
   - jd_core_requirement_chunks：剔除公司介绍、五险一金等废话，保留纯粹的技术职责与要求。
   - positive_anchor_groups：agent与rpa各生成1~5段具体开发经历锚点，两路线OR，不能要求候选人同时满足。
   - negative_anchors：模仿不符合要求或“蹭概念/伪AI经验”候选人的简历语气，写出 2~4 段典型水词描述（用于 BGE 负向扣分拦截）。"""

        user_content = (
            f"【岗位名称】\n{upload_input.job_title}\n\n"
            f"【岗位 JD 原文】\n{upload_input.raw_jd_text}\n\n"
            f"【HR 重点筛选要求与偏好】\n{upload_input.hr_custom_requirements}"
        )

        response = self.client.beta.chat.completions.parse(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            response_format=CompiledScoringProfile,
            temperature=0.1
        )
        compiled: CompiledScoringProfile = response.choices[0].message.parsed

        # 组装完整配置文件（保留 JD 原文与 HR 要求原文，供 Tier 3 LLM 终审直接阅读）
        full_config = {
            "job_target": {
                "job_title": upload_input.job_title,
                "raw_jd_text": upload_input.raw_jd_text,
                "hr_custom_requirements": upload_input.hr_custom_requirements,
                "default_greeting_note": "hello，是有在看机会吗？我们这边的AI自动化开发助理/专员岗位，不知道有没有兴趣了解一下~"
            },
            "quotas_and_switches": {
                "batch_scan_limit": upload_input.batch_scan_limit,
                "daily_greet_limit": upload_input.daily_greet_limit,
                "enable_auto_greet": False,
                "auto_mark_unsuitable": False,
                "enable_local_dedup": True,
                "scoring_mode": "deep_mode",
                "action_delay_seconds": [2.5, 5.5]
            },
            "hard_filters": {
                "min_degree": compiled.min_degree,
                "work_years_allowed": ["在校/应届", "26年应届生", "27年应届生", "28年应届生", "1年以内", "1-3年"],
                "max_expected_salary_k": compiled.max_expected_salary_k,
                "active_status_allowed": ["刚刚活跃", "今日活跃", "3日内活跃", "本周活跃"],
                "exclude_job_status": ["在职-暂不考虑"],
                "exclude_position_keywords": compiled.exclude_position_keywords
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
                    "automation_efficiency": 0.20,
                    "objective_job_fit": 0.10
                },
                "keyword_rules": {
                    "high_value_tags": [item.model_dump() for item in compiled.high_value_tags],
                    "medium_value_tags": [item.model_dump() for item in compiled.medium_value_tags]
                },
                "semantic_anchors": {
                    "jd_core_requirement_chunks": compiled.jd_core_requirement_chunks,
                    "positive_anchor_groups": compiled.positive_anchor_groups.model_dump(),
                    "negative_anchors": compiled.negative_anchors
                }
            }
        }

        full_config["config_version"] = "scoring-v2.1"
        full_config["model_versions"] = {"embedding": "bge-large-zh-v1.5-local-unpinned", "llm": "provider-alias-unpinned"}
        full_config["cache"] = {"enabled": True, "directory": ".scoring_cache"}
        full_config["hard_filters"]["salary_budget_period"] = "month"
        scoring = full_config["scoring_config"]
        scoring["keyword_aggregation"] = deepcopy(DEFAULT_KEYWORD_CONFIG)
        scoring["evidence_source_weights"] = deepcopy(DEFAULT_SOURCE_WEIGHTS)
        scoring["evidence_quality"] = deepcopy(DEFAULT_EVIDENCE_QUALITY)
        scoring["semantic_scoring"] = deepcopy(DEFAULT_SEMANTIC_CONFIG)
        scoring["llm_review"] = {
            "temperature": 0, "timeout_seconds": 45, "max_unsupported_score": 20,
            "max_unsupported_confidence": .35, "practice_required_above": 50,
            "auto_greet_min_confidence": .7,
            "min_practice_quote_chars": 16,
            "enable_thinking": False,
        }

        Path(output_config_path).write_text(
            json.dumps(full_config, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(f"岗位配置编译完成，已写入 {output_config_path}")
        return full_config
