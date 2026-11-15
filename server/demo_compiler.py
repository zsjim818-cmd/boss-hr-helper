"""Deterministic demo compiler for the offline run mode.

It turns a JD into a small, valid ``JobScoringPolicy`` with no model call:
one requirement per JD clause (exact source quotes), one keyword group and one
positive anchor per requirement, one ALL group, and three static dimensions.
All ids/labels/rubrics are generic; the only job-specific data is the JD text
itself.  The output passes the deterministic publish diagnostics, so the whole
HTTP flow (compile -> confirm -> version -> publish) works without Qwen.
"""
import re

from config.policy_compiler import CompilationResult
from schemas.job_policy import (HardFilter, JobScoringPolicy, PolicyCompileInput,
                                PolicyDiagnostic, PolicyJob, PolicyProvenance,
                                PolicyRequirement, PolicySources, PolicyThresholds,
                                RequirementGroup, RubricBand, ScoringDimension,
                                SemanticAnchor, KeywordGroup, PolicyKeyword)

DEMO_COMPILER_VERSION = "demo-compiler-v1"
DEMO_PROMPT_VERSION = "demo-prompt-v1"
DEMO_MODEL_VERSION = "deterministic-demo"

_PREFERRED = re.compile(r"优先|加分|更佳|最好|preferred|nice\s+to\s+have|bonus", re.I)


class DemoJobPolicyCompiler:
    """Drop-in replacement for ``JobPolicyCompiler`` in deterministic mode."""

    def __init__(self, model_name=DEMO_MODEL_VERSION):
        self.model_name = model_name
        self.last_metrics = None
        self.last_response_content = None

    def provenance(self, response_model=None):
        return {"compiler_version": DEMO_COMPILER_VERSION,
                "prompt_version": DEMO_PROMPT_VERSION,
                "validator_version": "deterministic-demo",
                "model_version": response_model or self.model_name}

    def compile(self, upload) -> CompilationResult:
        upload = PolicyCompileInput.model_validate(upload)
        policy = build_demo_policy(upload)
        metrics = {"status": "SUCCEEDED", "stage": "completed", "model": self.model_name,
                   "deterministic": True, "model_calls": 0, "elapsed_seconds": 0.0}
        self.last_metrics = metrics
        self.last_response_content = None
        return CompilationResult(policy=policy, diagnostics=[], metrics=metrics)


def _clauses(jd_text: str) -> list[str]:
    raw = [part.strip() for part in re.split(r"[\n。；;]+", jd_text) if part.strip()]
    if not raw:
        raw = [jd_text.strip() or "岗位职责"]
    texts: list[str] = []
    for clause in raw:
        if len(clause) > 800:
            texts.extend(part.strip() for part in re.split(r"[，,]", clause) if part.strip())
        else:
            texts.append(clause)
    unique = []
    for text in texts:
        if text not in unique:
            unique.append(text)
    return [text[:1500] for text in unique[:6]] or [jd_text[:200]]


def build_demo_policy(upload: PolicyCompileInput) -> JobScoringPolicy:
    texts = _clauses(upload.jd_text)
    preferred = any(_PREFERRED.search(text) for text in texts)
    priority = "PREFERRED" if preferred else "REQUIRED"
    requirements = []
    anchors = []
    keyword_groups = []
    for index, text in enumerate(texts, start=1):
        requirement_id = f"demo_req_{index}"
        label = (text[:80] or f"要求{index}")
        requirements.append(PolicyRequirement(
            requirement_id=requirement_id, label=label, source_type="JD",
            source_text=text, priority=priority, requirement_type="SKILL",
            needs_confirmation=False))
        anchors.append(SemanticAnchor(
            anchor_id=f"demo_anchor_{index}", requirement_ids=[requirement_id],
            polarity="POSITIVE",
            text=f"候选人能够独立说明{label}的具体实现、取舍与可核验结果。"))
        keyword_groups.append(KeywordGroup(
            group_id=f"demo_kw_{index}", label=label[:120],
            requirement_ids=[requirement_id],
            keywords=[PolicyKeyword(canonical=label[:120], aliases=[], weight=10.0)]))
    order = [requirement.requirement_id for requirement in requirements]
    dimensions = []
    specs = (("demo_capability", "综合能力", 0.4,
              "交付结果、问题解决与真实实施过程"),
             ("demo_engineering", "工程基础", 0.35,
              "工程质量、工具链与可维护实现"),
             ("demo_communication", "协作沟通", 0.25,
              "需求理解、协作推进与表达清晰度"))
    for position, (dimension_id, label, weight, focus) in enumerate(specs):
        projected = order[position::len(specs)] or order
        dimensions.append(ScoringDimension(
            dimension_id=dimension_id, label=label,
            description=f"依据岗位要求评估{focus}，只接受可核验证据。",
            requirement_ids=projected, weight=weight,
            rubric=[RubricBand(min_score=0, max_score=39,
                               description="仅复述概念或标签，缺少可核验的个人行动。"),
                    RubricBand(min_score=40, max_score=69,
                               description="说明部分实际承担的工作，过程或结果证据不完整。"),
                    RubricBand(min_score=70, max_score=100,
                               description="完整说明具体过程、取舍与可核验结果。")]))
    first = texts[0]
    policy = JobScoringPolicy(
        schema_version="job_scoring_policy_v1",
        job=PolicyJob(title=upload.job_title, summary=(upload.jd_text.splitlines()[0][:500]
                                                       or upload.job_title)),
        sources=PolicySources(jd_text=upload.jd_text,
                              hr_requirements=upload.hr_requirements),
        requirements=requirements,
        requirement_groups=[RequirementGroup(
            group_id="demo_group_all", label="岗位要求组合", operator="ALL",
            priority=priority, members=order, source_type="JD", source_text=first,
            needs_confirmation=False)],
        hard_filters=[],
        keyword_groups=keyword_groups,
        semantic_anchors=anchors,
        scoring_dimensions=dimensions,
        thresholds=PolicyThresholds(coarse_pass=0.0, deep_review=0.0,
                                    deep_recommend=99.0),
        diagnostics=[],
        provenance=PolicyProvenance(
            compiler_version=DEMO_COMPILER_VERSION, prompt_version=DEMO_PROMPT_VERSION,
            validator_version="deterministic-demo", model_version=DEMO_MODEL_VERSION))
    return policy
