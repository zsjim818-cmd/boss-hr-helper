"""Validate complete V2 configs and identify immutable batch snapshots."""
from copy import deepcopy
import math

from engine.semantic_ranker import semantic_parameters


def validate_config(config):
    config = deepcopy(config)
    # JSON NaN is accepted by Python's decoder; never accept it for routing.
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Configuration contains non-finite numbers")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)
    finite(config)
    if not config.get("config_version"):
        raise ValueError("V2 config_version is required; migrate legacy config explicitly")
    for section in ("job_target", "quotas_and_switches", "hard_filters", "scoring_config", "model_versions"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Missing config section: {section}")
    scoring = config["scoring_config"]
    for field in ("keyword_rules", "keyword_aggregation", "evidence_source_weights", "evidence_quality",
                  "semantic_anchors", "semantic_scoring", "llm_review", "dimension_weights", "thresholds"):
        if not isinstance(scoring.get(field), dict):
            raise ValueError(f"Missing scoring_config.{field}")
    weights = scoring["dimension_weights"]
    names = ("ai_automation_practice", "engineering_foundation", "automation_efficiency", "objective_job_fit")
    if set(weights) != set(names) or any(not isinstance(weights[n], (float, int)) or weights[n] < 0 for n in names) or sum(weights.values()) <= 0:
        raise ValueError("Invalid V2 dimension weights")
    thresholds = scoring["thresholds"]
    if any(not 0 <= thresholds[k] <= 100 for k in ("coarse_pass_score", "review_threshold", "auto_greet_threshold")):
        raise ValueError("Score thresholds must be in [0,100]")
    if thresholds["auto_greet_threshold"] < thresholds["review_threshold"]:
        raise ValueError("auto_greet_threshold must be >= review_threshold")
    semantic_parameters(config)
    groups = scoring["semantic_anchors"].get("positive_anchor_groups", {})
    if set(groups) != {"agent", "rpa"} or any(not isinstance(v, list) or not v or any(not isinstance(s, str) or not s.strip() for s in v) for v in groups.values()):
        raise ValueError("Both agent and rpa anchor groups are required")
    categories = scoring["keyword_aggregation"]["categories"]
    if not categories or sum(v["cap"] for v in categories.values()) > 45 or any(v["scale"] <= 0 or v["cap"] < 0 for v in categories.values()):
        raise ValueError("Invalid Tier 1 category budgets")
    for tier in ("high_value_tags", "medium_value_tags"):
        for item in scoring["keyword_rules"].get(tier, []):
            if not item.get("word") or item["score"] < 0:
                raise ValueError("Invalid keyword rule")
    if any(w < 0 for w in scoring["evidence_source_weights"].values()):
        raise ValueError("Invalid evidence source weight")
    review = scoring["llm_review"]
    if review["temperature"] != 0 or review["timeout_seconds"] <= 0:
        raise ValueError("V2 review requires temperature=0 and a positive timeout")
    for key in ("max_unsupported_confidence", "auto_greet_min_confidence"):
        if not 0 <= review[key] <= 1:
            raise ValueError(f"Invalid {key}")
    for key in ("max_unsupported_score", "practice_required_above"):
        if not 0 <= review[key] <= 100:
            raise ValueError(f"Invalid {key}")
    if not isinstance(review["min_practice_quote_chars"], int) or review["min_practice_quote_chars"] < 1:
        raise ValueError("Invalid min_practice_quote_chars")
    if "enable_thinking" in review and not isinstance(review["enable_thinking"], bool):
        raise ValueError("enable_thinking must be a boolean")
    for key in ("max_tokens", "thinking_budget"):
        if key in review and (type(review[key]) is not int or review[key] <= 0):
            raise ValueError(f"{key} must be a positive integer")
    if review.get("enable_thinking") is False and "thinking_budget" in review:
        raise ValueError("thinking_budget requires thinking to be enabled")
    if config["quotas_and_switches"].get("scoring_mode") not in ("deep_mode", "fast_mode"):
        raise ValueError("Invalid scoring_mode")
    if not isinstance(config["quotas_and_switches"].get("enable_auto_greet"), bool):
        raise ValueError("enable_auto_greet must be a boolean")
    hf = config["hard_filters"]
    if hf.get("salary_budget_period", "month") not in ("month", "day", "year") or hf.get("max_expected_salary_k", 1) <= 0:
        raise ValueError("Invalid salary budget")
    for key in ("embedding", "llm"):
        if not isinstance(config["model_versions"].get(key), str) or not config["model_versions"][key]:
            raise ValueError(f"Explicit model_versions.{key} is required")
    return config
