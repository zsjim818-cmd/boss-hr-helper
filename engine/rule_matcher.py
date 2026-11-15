"""Tier 1: canonical skills, strongest evidence, and capability budgets."""
from engine.resume_text import get_source_texts
import math
import re


DEFAULT_KEYWORD_CONFIG = {
    "aliases": {
        "Agent": ["智能体", "Agents"], "LangChain": ["Lang Chain"],
        "LangGraph": ["Lang Graph"], "影刀": ["影刀RPA"],
        "PgVector": ["pg_vector", "pg-vector"],
    },
    "categories": {
        "development_practice": {
            "cap": 28, "scale": 35,
            "keywords": ["LangGraph", "LangChain", "Coze", "Dify", "n8n", "RAG", "Agent", "Playwright", "Selenium", "影刀", "RPA", "LiteLLM"],
        },
        "engineering": {
            "cap": 12, "scale": 18,
            "keywords": ["Python", "FastAPI", "BGE", "PgVector", "Redis", "Docker", "Bash", "Ollama", "DeepSeek"],
        },
        "efficiency": {"cap": 5, "scale": 8, "keywords": ["Cursor", "Claude"]},
    },
    "default_category": "engineering",
}

DEFAULT_SOURCE_WEIGHTS = {"work": 1.2, "project": 1.2, "thesis": 1.0, "work_tags": .35, "skills": .5, "advantage": .4}
DEFAULT_EVIDENCE_QUALITY = {
    "detect_label_only_practice": True,
    "label_only_max_token_chars": 32,
    "label_prefixes": ["技术栈", "技术标签", "技能", "关键词", "tech stack", "skills", "熟练掌握", "熟练使用", "熟悉", "精通", "掌握", "了解"],
    "proficiency_prefixes": ["熟练掌握", "熟练使用", "熟悉", "精通", "掌握", "了解"],
    "implementation_terms": ["负责", "实现", "开发", "设计", "构建", "搭建", "编写", "重构", "接入", "部署", "封装", "维护", "优化", "上线", "集成", "built", "implemented", "developed", "designed", "deployed", "integrated", "maintained"],
}


def evidence_sources(resume, scoring):
    """A labels-only list stays auxiliary even when mapped into a practice field.

    This catches a conservative formatting case, not the truth of a candidate's
    claim. Descriptive text still requires a human to verify implementation.
    """
    quality = {**DEFAULT_EVIDENCE_QUALITY, **scoring.get("evidence_quality", {})}
    known = {str(item["word"]).casefold() for level in ("high_value_tags", "medium_value_tags")
             for item in scoring.get("keyword_rules", {}).get(level, [])}
    aliases = scoring.get("keyword_aggregation", DEFAULT_KEYWORD_CONFIG).get("aliases", DEFAULT_KEYWORD_CONFIG["aliases"])
    for word, variants in aliases.items():
        known.update(str(v).casefold() for v in [word] + list(variants))
    sources = []
    for source, path, text in get_source_texts(resume):
        if source in {"work", "project", "thesis"} and quality["detect_label_only_practice"]:
            value = text.strip()
            proficiency_only = any(value.startswith(prefix) for prefix in quality["proficiency_prefixes"])
            for prefix in quality["label_prefixes"]:
                if value.casefold().startswith(prefix.casefold()):
                    value = value[len(prefix):].lstrip(":： ")
                    break
            parts = [p.strip() for p in re.split(r"[,，、;；|\n]+", value) if p.strip()]
            action = any(match_keyword(term, value) for term in quality["implementation_terms"])
            concrete_action = any(match_keyword(term, value) for term in quality["implementation_terms"] if term != "开发")
            labels = bool(parts) and all(len(p) <= int(quality["label_only_max_token_chars"]) and
                                         (p.casefold() in known or re.fullmatch(r"[a-zA-Z0-9.+#_ /-]+", p))
                                         for p in parts)
            if (labels and not action) or (proficiency_only and not concrete_action):
                source = "work_tags"
        sources.append((source, path, text))
    return sources


def match_keyword(word, text):
    if re.fullmatch(r"[a-zA-Z0-9\-.+ ]+", word):
        return bool(re.search(rf"(?<![a-zA-Z0-9]){re.escape(word)}(?![a-zA-Z0-9])", text, re.IGNORECASE))
    return word.casefold() in text.casefold()


class RuleMatcherMixin:
    @staticmethod
    def _match_keyword(word, text):
        return match_keyword(word, text)

    def _run_tier1_rules(self, resume):
        scoring = self.config["scoring_config"]
        aggregation = scoring.get("keyword_aggregation", DEFAULT_KEYWORD_CONFIG)
        aliases = aggregation.get("aliases", DEFAULT_KEYWORD_CONFIG["aliases"])
        canonical = {}
        for name, variants in aliases.items():
            for variant in [name] + list(variants):
                canonical[variant.casefold()] = name
        for info in aggregation.get("categories", DEFAULT_KEYWORD_CONFIG["categories"]).values():
            for word in info.get("keywords", []):
                canonical.setdefault(word.casefold(), word)

        rules = {}
        kw_config = scoring["keyword_rules"]
        for item in kw_config.get("high_value_tags", []) + kw_config.get("medium_value_tags", []):
            word = canonical.get(item["word"].casefold(), item["word"])
            key = word.casefold()
            if key not in rules or float(item["score"]) > float(rules[key]["score"]):
                rules[key] = dict(item, word=word)

        categories = aggregation.get("categories", DEFAULT_KEYWORD_CONFIG["categories"])
        if not categories or sum(float(v["cap"]) for v in categories.values()) > 45.000001:
            raise ValueError("Tier 1 category budgets must total at most 45")
        if any(float(v["cap"]) < 0 or float(v["scale"]) <= 0 for v in categories.values()):
            raise ValueError("Tier 1 category caps/scales must be nonnegative/positive")
        default_category = aggregation.get("default_category", "engineering")
        if default_category not in categories:
            raise ValueError("Tier 1 default category is missing")
        category_for = {}
        for category, info in categories.items():
            for word in info.get("keywords", []):
                category_for[canonical.get(word.casefold(), word).casefold()] = category

        weights = {**DEFAULT_SOURCE_WEIGHTS, **scoring.get("evidence_source_weights", {})}
        sources = evidence_sources(resume, scoring)
        category_raw = {key: 0.0 for key in categories}
        raw, tags, details = 0.0, [], []
        for key, item in sorted(rules.items()):
            word = item["word"]
            variants = list(dict.fromkeys([word] + aliases.get(word, [])))
            matches = [(float(weights[source]), source, path)
                       for source, path, text in sources
                       if any(match_keyword(variant, text) for variant in variants)]
            if not matches:
                continue
            weight, source, path = max(matches)
            points = round(float(item["score"]) * weight, 4)
            category = item.get("category", category_for.get(key, default_category))
            if category not in categories:
                raise ValueError(f"Unknown keyword category: {category}")
            category_raw[category] += points
            raw += points
            tags.append(word)
            details.append(f"{word} [{category}] {source}:{path} x{weight:g} +{points:g}")

        score = 0.0
        for category, value in category_raw.items():
            info = categories[category]
            contribution = float(info["cap"]) * (1 - math.exp(-value / float(info["scale"])))
            score += contribution
            if value:
                details.append(f"category:{category} raw={value:.2f}, contribution={contribution:.2f}/{info['cap']}")
        return round(raw, 2), round(min(45.0, score), 2), tags, details
