"""Deterministic offline test doubles; these do not approximate BGE or Qwen quality.

The fixed semantic features only make algorithmic invariants testable. Scores from
these doubles must always be labelled Mock, never reported as live model results.
"""

import json
from types import SimpleNamespace

import numpy as np

from engine.rule_matcher import DEFAULT_EVIDENCE_QUALITY


class DeterministicEmbedder:
    version = "mock-feature-v1"
    # Each feature is binary so repeating a word does not change its embedding.
    FEATURES = (
        ("agent", "langgraph", "langchain", "智能体", "coze", "dify", "n8n", "工作流"),
        ("rag", "检索", "知识库", "embedding", "向量", "bge", "hnsw", "pgvector"),
        ("大模型", "模型调用", "工具调用", "litellm", "ollama", "deepseek"),
        ("playwright", "selenium", "影刀", "rpa", "网页自动化", "自动填报"),
        ("抓取", "采集", "清洗", "入库", "爬虫", "自动上架"),
        ("python",),
        ("fastapi", "java", "redis", "mysql", "接口", "后端", "rbac", "spring"),
        ("docker", "linux", "bash", "部署", "git"),
        ("cursor", "辅助编写", "研发效率", "重构", "自动化脚本", "编写"),
        ("文案", "新媒体", "小红书", "公众号", "短视频", "种草", "内容运营"),
        ("midjourney", "海报", "配图", "宣传图片", "stable diffusion"),
        ("ppt", "纪要", "周报", "公文", "办公软件"),
    )

    def __init__(self, *args, **kwargs):
        self.calls = []

    def encode(self, sentences, normalize_embeddings=True, **kwargs):
        single = isinstance(sentences, str)
        texts = [sentences] if single else list(sentences)
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            lowered = str(text).casefold()
            vector = np.array(
                [0.25] + [float(any(term in lowered for term in terms))
                          for terms in self.FEATURES],
                dtype=float,
            )
            if normalize_embeddings:
                vector /= np.linalg.norm(vector)
            vectors.append(vector)
        result = np.array(vectors, dtype=float).reshape((-1, len(self.FEATURES) + 1))
        return result[0] if single else result


class DeterministicLLMClient:
    """OpenAI-shaped double with configurable JSON, raw output, or exception.

    response may be a dictionary, string, or a callable receiving request kwargs.
    It is deliberately not a resumé reviewer: default scores are fixed fixtures.
    """

    version = "mock-llm-v1"

    def __init__(self, *args, response=None, error=None, **kwargs):
        self.response = response if response is not None else {
            "ai_automation_raw_score": 90,
            "engineering_raw_score": 85,
            "efficiency_raw_score": 80,
            "stability_raw_score": 90,
            "highlights": ["Mock fixed response; no candidate quality claim"],
            "risk_point": "Mock only; verify original resumé manually",
        }
        self.error = error
        self.calls = []
        completions = SimpleNamespace(create=self.create, parse=self.parse)
        self.chat = SimpleNamespace(completions=completions)
        self.beta = SimpleNamespace(chat=self.chat)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        response = self.response(kwargs) if callable(self.response) else self.response
        content = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        message = SimpleNamespace(content=content, refusal=None, parsed=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def parse(self, **kwargs):
        result = self.create(**kwargs)
        schema = kwargs.get("response_format")
        if hasattr(schema, "model_validate_json"):
            result.choices[0].message.parsed = schema.model_validate_json(
                result.choices[0].message.content
            )
        return result


def valid_review(kwargs):
    """Fixed scores with genuine source quotations: tests plumbing, not quality."""
    data = json.loads(kwargs["messages"][1]["content"])["untrusted_resume_data"]
    paths = [p for p in data if any(field in p for field in (".responsibility", ".performance", ".description", ".achievement"))]
    practice = next((p for p in paths if len(data[p]) >= 16 and any(term in data[p] for term in DEFAULT_EVIDENCE_QUALITY["implementation_terms"])), paths[0] if paths else "skills")
    return {
        dim: {"score": score, "evidence": [{"source_path": practice, "quote": data[practice]}],
              "missing_evidence": ["Mock评分不代表质量验证"], "confidence": .9}
        for dim, score in [("ai_automation", 90), ("engineering", 85), ("efficiency", 80), ("job_fit", 90)]
    } | {"highlights": [data[practice]], "risk_point": "Mock only"}


def make_engine(response=valid_review, error=None):
    from engine.hybrid_engine import HybridScoringEngine
    return HybridScoringEngine(embedder=DeterministicEmbedder(),
                               llm_client=DeterministicLLMClient(response=response, error=error))
