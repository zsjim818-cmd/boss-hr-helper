"""Deterministic runtime doubles for the demo/test run mode.

This module is part of production code (``engine/``); it never imports
``tests/``.  It provides an OpenAI-shaped client and a fixed Tier 3 response
builder so the whole pipeline can run without any network.  Outputs are
explicitly labelled as deterministic demo data, never model quality claims.
"""
import json
import re
from types import SimpleNamespace


MOCK_MODEL = "offline-demo-model"
MOCK_SCORES = (88.0, 82.0, 76.0, 70.0, 64.0, 58.0, 52.0, 46.0)


class DeterministicLLMClient:
    """OpenAI-compatible deterministic double (no sockets, no credentials)."""

    version = "demo-llm-v1"
    max_retries = 0

    def __init__(self, response=None, *, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        response = self.response(request) if callable(self.response) else self.response
        content = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        message = SimpleNamespace(content=content, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def demo_tier3_response(kwargs):
    """Deterministic Tier 3 answer derived from the request's dynamic dimensions."""
    system = kwargs["messages"][0]["content"]
    match = re.search(r"维度\(含权重与Rubric\):(\[.*?\])\n", system, re.S)
    if not match:
        raise ValueError("Deterministic demo could not locate the dynamic dimension block")
    dimensions = json.loads(match.group(1))
    data = json.loads(kwargs["messages"][1]["content"])["untrusted_resume_data"]
    quote_path = max(data, key=lambda path: len(data[path]), default=None)
    quote = (data[quote_path].strip()[:60]
             if quote_path and len(data[quote_path].strip()) >= 4 else "")
    result = []
    for index, dimension in enumerate(dimensions):
        score = MOCK_SCORES[index % len(MOCK_SCORES)]
        if quote:
            result.append({"dimension_id": dimension["dimension_id"], "score": score,
                           "evidence_quotes": [quote], "missing_evidence": [],
                           "confidence": 0.8})
        else:
            result.append({"dimension_id": dimension["dimension_id"],
                           "score": min(score, 60.0), "evidence_quotes": [],
                           "missing_evidence": ["封存简历无可用证据片段"],
                           "confidence": 0.4})
    return {"dimensions": result}
