"""Conservative list-preview decisions (does not replace formal scoring).

Only *explicit* list facts may skip a detail fetch.  Missing or ambiguous list
fields always fall through to FETCH_DETAIL; unknown is never treated as FAIL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from browser.contracts import CandidateCard

DEGREE_LEVELS = {
    "初中": 1, "高中": 2, "中专": 2, "大专": 3, "专科": 3,
    "本科": 4, "学士": 4, "硕士": 5, "研究生": 5, "博士": 6,
}
DECISIONS = ("SKIP_CONFIRMED", "FETCH_DETAIL", "NEEDS_VERIFICATION")


@dataclass(frozen=True)
class PreviewDecision:
    decision: str
    reason: str = ""

    def __post_init__(self):
        if self.decision not in DECISIONS:
            raise ValueError(f"Unsupported preview decision: {self.decision}")


def _degree_level(text: str) -> int | None:
    value = (text or "").strip()
    for name, level in DEGREE_LEVELS.items():
        if name in value:
            return level
    return None


def _work_years(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(match.group(1)) if match else None


def decide_preview(card: CandidateCard, *, required_degree: str | None = None,
                   minimum_work_years: float | None = None) -> PreviewDecision:
    if required_degree:
        required = _degree_level(required_degree)
        actual = _degree_level(card.degree)
        if required and actual and actual < required:
            return PreviewDecision(
                "SKIP_CONFIRMED",
                f"list degree {card.degree!r} is below required {required_degree!r}")
    if minimum_work_years is not None:
        actual = _work_years(card.work_years)
        if actual is not None and actual < minimum_work_years:
            return PreviewDecision(
                "SKIP_CONFIRMED",
                f"list work years {actual} is below required {minimum_work_years}")
    return PreviewDecision("FETCH_DETAIL", "list evidence is not sufficient to skip")


def policy_preview_requirements(policy_json) -> dict:
    """Extract only deterministic list-checkable filters from a stored policy."""
    policy = policy_json if isinstance(policy_json, dict) else {}
    requirements = {item.get("requirement_id"): item
                    for item in policy.get("requirements", [])}
    degree = None
    years = None
    for item in policy.get("hard_filters", []):
        requirement = requirements.get(item.get("requirement_id")) or {}
        if requirement.get("priority") != "REQUIRED":
            continue
        if item.get("field") == "education_level" and item.get("operator") in ("GTE", "EQ"):
            degree = item.get("value_text")
        if item.get("field") == "work_years" and item.get("operator") in ("GTE", "EQ"):
            years = item.get("value_number")
    return {"required_degree": degree, "minimum_work_years": years}
