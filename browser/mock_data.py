"""Mock recruitment data (same ResumeData contract as the API import endpoint)."""
from __future__ import annotations

from copy import deepcopy


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


def mock_cards(job_id: str, kind: str = "java", count: int = 3,
               prefix: str = "geek") -> list[dict]:
    platform_job_id = f"boss-job-{job_id}"
    return [{"identity": f"{prefix}-{kind}-{index}",
             "name": f"Mock {kind} candidate {index}",
             "position": "Engineer",
             "job_id": job_id,
             "platform_job_id": platform_job_id,
             "degree": "本科",
             "work_years": "5年",
             "resume": resume_template(kind, f"{prefix}-{kind}-{index}", job_id)}
            for index in range(1, count + 1)]
