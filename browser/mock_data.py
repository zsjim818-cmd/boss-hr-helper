"""Mock recruitment data (same ResumeData contract as the API import endpoint)."""
from __future__ import annotations

from copy import deepcopy
import json
from functools import lru_cache
from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "candidates"


@lru_cache(maxsize=1)
def _ai_fixture_resumes() -> tuple[dict, ...]:
    """Load the eight realistic AI/embedded joint-debug resumes once."""
    result = []
    for path in sorted(FIXTURE_DIR.glob("candidate_0*.json"))[:8]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("name"):
            result.append(value)
    return tuple(result)



def resume_template(kind: str, identity: str, job_id: str) -> dict:
    kind = kind if kind in ("java", "sales", "ai") else "java"
    base = {
        "name": f"Mock {kind} candidate",
        "degree": "本科",
        "work_years": "5年",
        "job_status": "离职-随时到岗",
        "active_status": "今日活跃",
        "expectation": {"city": "北京", "salary": "12K/月", "position": "Engineer"},
        "certifications": [],
        "advantage": "",
    }
    if kind == "java":
        base.update({
            "skills": "Java, SQL, Spring Boot, 接口自动化测试",
            "work_experience": [{"company": "Mock payments", "position": "Java engineer",
                                 "responsibility": "负责订单支付后台Java服务开发，设计SQL事务与幂等约束，"
                                                   "使用Spring Boot提供接口并编写接口自动化测试。",
                                 "keywords": []}],
            "project_experience": [],
            "education": [{"school": "Mock university", "degree": "本科"}],
        })
    elif kind == "sales":
        base.update({
            "skills": "企业客户销售, CRM, 合同谈判, 销售漏斗",
            "work_experience": [{"company": "Mock software", "position": "Enterprise sales",
                                 "responsibility": "负责企业客户需求访谈与方案沟通，独立推进合同谈判与签约，"
                                                   "维护销售漏斗并跟进回款。",
                                 "keywords": []}],
            "project_experience": [],
            "education": [{"school": "Mock university", "degree": "本科"}],
        })
    else:
        base.update({
            "skills": "Python, LangGraph, RAG, Playwright",
            "work_experience": [{"company": "Mock AI", "position": "AI engineer",
                                 "responsibility": "使用LangGraph搭建智能体工作流，构建RAG知识库问答，"
                                                   "编写Playwright自动化脚本。",
                                 "keywords": []}],
            "project_experience": [],
            "education": [{"school": "Mock university", "degree": "硕士"}],
        })
    value = deepcopy(base)
    value["_meta"] = {
        "candidate_key": f"{job_id}:{identity}",
        "encrypt_job_id": f"boss-job-{job_id}",
        "encrypt_geek_id": identity,
    }
    return value


def _high_match_ai_resume() -> dict:
    """First Mock AI candidate: realistic high-match evidence for Qwen policies."""
    return {
        "name": "Mock AI automation candidate 1",
        "degree": "本科",
        "work_years": "5年",
        "job_status": "离职-随时到岗",
        "active_status": "今日活跃",
        "expectation": {"city": "北京", "salary": "10K/月",
                        "position": "AI自动化应用开发工程师"},
        "skills": ("Dify, Coze, n8n, LangChain, LangGraph, Python, Playwright, "
                   "影刀RPA, RAG, 企业知识库, 大模型API, 提示词工程, 检索优化, "
                   "跨系统数据抓取, 数据清洗, 自动填报"),
        "work_experience": [{
            "company": "Mock AI Automation",
            "position": "AI应用开发工程师",
            "responsibility": (
                "使用Dify、Coze、n8n、LangChain、LangGraph搭建企业智能体工作流，"
                "编写Python自动化脚本，使用Playwright和影刀RPA完成跨系统数据抓取、"
                "清洗与自动填报；参与企业知识库RAG构建和大模型API接口联调，"
                "持续优化提示词与检索准确率，交付可核验的AI提效项目。"),
            "keywords": ["Dify", "Coze", "n8n", "LangGraph", "Playwright", "RPA"],
        }],
        "project_experience": [
            {"name": "企业知识库RAG问答", "role": "核心开发",
             "description": ("使用LangChain/LangGraph构建RAG检索链路，接入大模型API，"
                             "优化提示词与检索准确率，服务内部知识问答。")},
            {"name": "跨系统RPA数据自动化", "role": "开发负责人",
             "description": ("使用Playwright与影刀RPA实现跨系统数据抓取、清洗和自动填报，"
                             "以Python脚本和n8n编排智能体工作流。")},
        ],
        "education": [{"school": "Mock university", "degree": "本科"}],
        "certifications": ["AI应用开发实践"],
        "advantage": ("具备真实AI智能体、RAG知识库与大模型API联调项目经验；"
                      "能独立用Dify/Coze/n8n/LangChain/LangGraph搭建工作流，"
                      "并用Python、Playwright、影刀RPA落地跨系统自动化与数据闭环。"),
    }


def mock_cards(job_id: str, kind: str = "java", count: int = 3,
               prefix: str = "geek") -> list[dict]:
    platform_job_id = f"boss-job-{job_id}"
    fixtures = _ai_fixture_resumes() if kind == "ai" else ()
    if fixtures:
        count = max(count, len(fixtures))
    cards = []
    for index in range(1, count + 1):
        if fixtures and index <= len(fixtures):
            resume = deepcopy(fixtures[index - 1])
            resume["_meta"] = {
                "candidate_key": f"{job_id}:{prefix}-{kind}-{index}",
                "encrypt_job_id": f"boss-job-{job_id}",
                "encrypt_geek_id": f"{prefix}-{kind}-{index}",
            }
        else:
            resume = resume_template(kind, f"{prefix}-{kind}-{index}", job_id)
        cards.append({
            "identity": f"{prefix}-{kind}-{index}",
            "name": resume.get("name") or f"Mock {kind} candidate {index}",
            "position": (resume.get("expectation") or {}).get("position", "Engineer")
                        if isinstance(resume.get("expectation"), dict) else "Engineer",
            "job_id": job_id,
            "platform_job_id": platform_job_id,
            "degree": resume.get("degree", "本科"),
            "work_years": resume.get("work_years", "5年"),
            "resume": resume,
        })
    return cards
