"""Conservative, job-independent lowering of model proposals into policy IR.

No job vocabulary is embedded here. Unknown synonyms are not trusted, and only
one explicit ASCII-name alternative with matching original evidence is lowered.
The input model/dict is never mutated; warnings preserve what the pass changed.
"""
from collections import Counter
import hashlib
import json
import re
import unicodedata

from schemas.job_policy import JobScoringPolicy, PolicyDiagnostic


NORMALIZER_VERSION = "policy-normalizer-v1"
_ASCII_NAME = r"[A-Za-z][A-Za-z0-9.+#_-]*(?:[ \t]+(?-i:[A-Z])[A-Za-z0-9.+#_-]*)?"
_ALTERNATIVE = re.compile(r"(?P<left>" + _ASCII_NAME + r")\s*(?:或|\bor\b)\s*(?P<right>" + _ASCII_NAME + r")", re.I)


def alias_key(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def _literal(text):
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    escaped = r"\s+".join(re.escape(part) for part in text.split())
    before = r"(?<![a-z0-9_])" if text and text[0].isascii() and text[0].isalnum() else ""
    after = r"(?![a-z0-9_])" if text and text[-1].isascii() and text[-1].isalnum() else ""
    return before + escaped + after


def alias_is_grounded(canonical, alias, sources):
    """Same normalized spelling, or explicit naming evidence in original input."""
    if not alias_key(alias):
        return False
    if alias_key(canonical) == alias_key(alias):
        return True
    source = unicodedata.normalize("NFKC", sources.jd_text + "\n" + sources.hr_requirements).casefold()
    first, second = _literal(canonical), _literal(alias)
    naming = r"\s*[,，:：]?\s*(?:又称|亦称|也称|简称(?:为)?|缩写(?:为)?|又名|aka|also known as|abbreviated as)\s*[,，:：]?\s*"
    return any(re.search(pattern, source) for pattern in (
        first + r"\s*\(\s*" + second + r"\s*\)",
        second + r"\s*\(\s*" + first + r"\s*\)",
        first + naming + second,
        second + naming + first,
    ))


def _alternative_matches(text):
    # The regex's optional second title token must not swallow an English verb
    # before the actual name. This finite grammatical prefix is not a job list.
    matches = []
    for match in _ALTERNATIVE.finditer(text):
        left, right = match.group("left"), match.group("right")
        start, end = match.start(), match.end()
        prefix = re.match(r"^(?:use|using|know|learn|with)\s+", left, re.I)
        if prefix:
            start += prefix.end()
            left = left[prefix.end():]
        # Avoid greedily swallowing an English lower-case phrase after a name.
        # Technical multi-word names have title-case subsequent words.
        right_parts = right.split()
        if len(right_parts) == 2 and not right_parts[1][0].isupper():
            end -= len(right) - len(right_parts[0])
            right = right_parts[0]
        matches.append({"left": left, "right": right, "start": start, "end": end})
    return matches


def alternative_analysis(requirement):
    """Finite recognized ASCII X-or-Y syntax; ambiguity is never guessed."""
    if requirement.requirement_type not in ("SKILL", "EXPERIENCE"):
        return {"status": "none"}
    matches = _alternative_matches(requirement.label)
    if not matches:
        return {"status": "none"}
    if len(matches) != 1 or len(re.findall(r"或|\bor\b", requirement.label, re.I)) != 1:
        return {"status": "ambiguous", "reason": "More than one alternative is present; split it explicitly"}
    match = matches[0]
    if alias_key(match["left"]) == alias_key(match["right"]):
        return {"status": "ambiguous", "reason": "Both alternatives normalize to the same name"}
    before, after = requirement.label[:match["start"]], requirement.label[match["end"]:]
    left_context = re.search(r"([A-Za-z][A-Za-z0-9.+#_-]*)\s+$", before)
    right_context = re.match(r"\s+([A-Za-z][A-Za-z0-9.+#_-]*)", after)
    if (re.match(r"^(?:and|or)\s+", match["left"], re.I)
            or (left_context and left_context.group(1).casefold() not in
                {"use", "using", "know", "learn", "with", "require", "requires", "either"})
            or right_context):
        return {"status": "ambiguous", "reason": "English multi-word alternatives cannot be safely segmented into two names"}
    if (re.search(r"[A-Za-z0-9.+#_-]+\s*(?:和|与|及|\band\b|/)\s*$", before, re.I)
            or re.match(r"\s*(?:和|与|及|\band\b|/)\s*[A-Za-z]", after, re.I)):
        return {"status": "ambiguous", "reason": "Mixed adjacent AND/slash alternatives have ambiguous precedence"}
    target = {alias_key(match["left"]), alias_key(match["right"])}
    evidence = [item for item in _alternative_matches(requirement.source_text)
                if {alias_key(item["left"]), alias_key(item["right"])} == target]
    if requirement.source_type == "INFERENCE" or len(evidence) != 1:
        return {"status": "ambiguous", "reason": "Original source does not establish exactly the same two-name alternative"}
    names = {alias_key(evidence[0]["left"]): evidence[0]["left"], alias_key(evidence[0]["right"]): evidence[0]["right"]}
    return {"status": "lowerable", **match,
            "source_left": names[alias_key(match["left"])], "source_right": names[alias_key(match["right"])]}


def normalization_diagnostics(policy):
    """Direct repository writes must pass the same rules as compiled proposals."""
    policy = JobScoringPolicy.model_validate(policy)
    diagnostics = []
    for group in policy.keyword_groups:
        for keyword in group.keywords:
            unknown = [alias for alias in keyword.aliases if not alias_is_grounded(keyword.canonical, alias, policy.sources)]
            if unknown:
                message = f"Unproven aliases for {keyword.canonical!r}: " + json.dumps(unknown, ensure_ascii=False)
                diagnostics.append(PolicyDiagnostic(code="UNGROUNDED_KEYWORD_ALIAS", severity="CONFIRM",
                    message=message[:2000], requirement_ids=group.requirement_ids))
    hard_refs = {item.requirement_id for item in policy.hard_filters}
    for requirement in policy.requirements:
        analysis = alternative_analysis(requirement)
        if analysis["status"] == "lowerable":
            diagnostics.append(PolicyDiagnostic(code="UNLOWERED_EXPLICIT_ALTERNATIVE", severity="ERROR",
                message="An explicit two-name alternative must be lowered to an ANY group before publishing",
                requirement_ids=[requirement.requirement_id]))
            if requirement.requirement_id in hard_refs:
                diagnostics.append(PolicyDiagnostic(code="ALTERNATIVE_HARD_FILTER_REFERENCE", severity="ERROR",
                    message="A composite alternative cannot be used as an atomic hard-filter target",
                    requirement_ids=[requirement.requirement_id]))
        elif analysis["status"] == "ambiguous":
            diagnostics.append(PolicyDiagnostic(code="AMBIGUOUS_EXPLICIT_ALTERNATIVE", severity="CONFIRM",
                message=analysis["reason"], requirement_ids=[requirement.requirement_id]))
    return diagnostics


def _salary_period(text):
    """Finite salary-period vocabulary; returns month by explicit default."""
    value = unicodedata.normalize("NFKC", text or "").casefold()
    for period, terms in (
            ("year", ("年薪", "/年", "每年", "year", "annum")),
            ("month", ("月薪", "/月", "每月", "month")),
            ("day", ("日薪", "/天", "/日", "每天", "day")),
            ("hour", ("时薪", "/时", "每小时", "hour"))):
        if any(term in value for term in terms):
            return period
    return None


def _normalize_salary_periods(result, diagnostics):
    """Align salary predicate units with the explicit quote (default: month).

    A compensation quote such as ``12K`` has no period marker.  v1 salary
    predicates are monthly CNY K, so the safe ordinary interpretation is MONTH;
    the requirement is marked reviewed instead of asking HR for a second round.
    """
    requirements = {item["requirement_id"]: item for item in result["requirements"]}
    for condition in result["hard_filters"]:
        if condition.get("field") != "expected_salary":
            continue
        requirement = requirements.get(condition.get("requirement_id"))
        if requirement is None:
            continue
        detected = (_salary_period(requirement.get("source_text"))
                    or _salary_period(requirement.get("label")))
        target = detected or "month"
        changed = condition.get("unit") != target
        if changed:
            condition["unit"] = target
        if not detected and requirement.get("needs_confirmation"):
            requirement["needs_confirmation"] = False
            changed = True
        if changed:
            diagnostics.append({
                "code": "NORMALIZED_SALARY_PERIOD", "severity": "WARNING",
                "message": (f"Salary predicate unit normalized to {target.upper()}"
                            + ("" if detected else " (explicit safe default)")),
                "requirement_ids": [condition["requirement_id"]],
            })



def _unique_diagnostics(items):
    result = {}
    for item in items:
        key = (item["code"], item["severity"], item["message"], tuple(item["requirement_ids"]))
        result[key] = item
    return list(result.values())


def normalize_policy(policy):
    policy = JobScoringPolicy.model_validate(policy)
    result = policy.model_dump(mode="json")
    diagnostics = result["diagnostics"]
    for group in result["keyword_groups"]:
        for keyword in group["keywords"]:
            kept, seen, unknown, duplicate = [], set(), [], []
            for alias in keyword["aliases"]:
                key = alias_key(alias)
                if not alias_is_grounded(keyword["canonical"], alias, policy.sources):
                    unknown.append(alias)
                elif key in seen:
                    duplicate.append(alias)
                else:
                    kept.append(alias)
                    seen.add(key)
            keyword["aliases"] = kept
            for code, removed in (("ALIASES_REMOVED_UNGROUNDED", unknown), ("ALIASES_DEDUPLICATED", duplicate)):
                if removed:
                    message = f"Keyword {keyword['canonical']!r}: removed aliases " + json.dumps(removed, ensure_ascii=False)
                    diagnostics.append({"code": code, "severity": "WARNING", "message": message[:2000], "requirement_ids": list(group["requirement_ids"])})
    counts = Counter([item.requirement_id for item in policy.requirements] + [item.group_id for item in policy.requirement_groups])
    used_ids = set(counts)
    hard_refs = {item.requirement_id for item in policy.hard_filters}
    replacements, requirements = {}, []
    def fresh_id(old_id, name):
        salt = 0
        while True:
            value = "alt_" + hashlib.sha256(f"{old_id}|{alias_key(name)}|{salt}".encode("utf-8")).hexdigest()[:24]
            if value not in used_ids:
                used_ids.add(value)
                return value
            salt += 1
    for requirement in policy.requirements:
        analysis = alternative_analysis(requirement)
        if analysis["status"] != "lowerable" or requirement.requirement_id in hard_refs:
            requirements.append(requirement.model_dump(mode="json"))
            continue
        if counts[requirement.requirement_id] != 1 or len(result["requirement_groups"]) >= 30 or len(policy.requirements) + len(replacements) >= 60:
            requirements.append(requirement.model_dump(mode="json"))
            diagnostics.append({"code": "ALTERNATIVE_LOWERING_LIMIT", "severity": "CONFIRM",
                "message": "Cannot lower this alternative without exceeding policy bounds or colliding with existing IDs", "requirement_ids": [requirement.requirement_id]})
            continue
        leaves = []
        for side in ("left", "right"):
            original_token = analysis["source_" + side]
            leaf = requirement.model_dump(mode="json")
            leaf["requirement_id"] = fresh_id(requirement.requirement_id, original_token)
            leaf["label"] = requirement.label[:analysis["start"]] + original_token + requirement.label[analysis["end"]:]
            leaf["source_text"] = original_token
            leaves.append(leaf)
        requirements.extend(leaves)
        replacements[requirement.requirement_id] = [leaf["requirement_id"] for leaf in leaves]
        result["requirement_groups"].append({"group_id": requirement.requirement_id, "label": requirement.label,
            "operator": "ANY", "priority": requirement.priority, "members": replacements[requirement.requirement_id],
            "source_type": requirement.source_type, "source_text": requirement.source_text, "needs_confirmation": requirement.needs_confirmation})
        diagnostics.append({"code": "LOWERED_EXPLICIT_ALTERNATIVE", "severity": "WARNING",
            "message": f"Lowered {requirement.label!r} to ANY with original source tokens {analysis['source_left']!r} and {analysis['source_right']!r}",
            "requirement_ids": replacements[requirement.requirement_id]})
    result["requirements"] = requirements
    for projection in [*result["keyword_groups"], *result["semantic_anchors"], *result["scoring_dimensions"]]:
        references = [new for old in projection["requirement_ids"] for new in replacements.get(old, [old])]
        projection["requirement_ids"] = list(dict.fromkeys(references))
    # Safe fallback: every semantic (SKILL/EXPERIENCE) requirement projected into
    # a scoring dimension must own a single-target POSITIVE anchor.  Real models
    # occasionally omit one; without it Stage 5.5 reports a blocking missing
    # anchor even though the requirement itself is valid.
    projected = {rid for dimension in result["scoring_dimensions"]
                 for rid in dimension["requirement_ids"]}
    direct_positive = {tuple(anchor["requirement_ids"])
                       for anchor in result["semantic_anchors"]
                       if anchor["polarity"] == "POSITIVE"
                       and len(anchor["requirement_ids"]) == 1}
    used_anchor_ids = {anchor["anchor_id"] for anchor in result["semantic_anchors"]}
    for requirement in result["requirements"]:
        if requirement["requirement_type"] not in ("SKILL", "EXPERIENCE"):
            continue
        requirement_id = requirement["requirement_id"]
        if requirement_id not in projected or (requirement_id,) in direct_positive:
            continue
        if len(result["semantic_anchors"]) >= 30:
            diagnostics.append({"code": "POSITIVE_ANCHOR_AUTOFILL_LIMIT", "severity": "CONFIRM",
                "message": "Cannot add a dedicated positive anchor without exceeding the anchor limit",
                "requirement_ids": [requirement_id]})
            break
        salt = 0
        while True:
            anchor_id = "anchor_norm_" + hashlib.sha256(
                f"{requirement_id}|{salt}".encode("utf-8")).hexdigest()[:20]
            if anchor_id not in used_anchor_ids:
                break
            salt += 1
        used_anchor_ids.add(anchor_id)
        text = (f"具备「{requirement['label']}」相关经验或能力，"
                "并能提供可核验的实践证据")[:1000]
        result["semantic_anchors"].append({
            "anchor_id": anchor_id, "requirement_ids": [requirement_id],
            "polarity": "POSITIVE", "text": text})
        direct_positive.add((requirement_id,))
        diagnostics.append({"code": "NORMALIZED_POSITIVE_ANCHOR", "severity": "WARNING",
            "message": f"Added a dedicated POSITIVE anchor for {requirement_id!r}",
            "requirement_ids": [requirement_id]})
    # Salary predicates default to MONTH when the quote carries no period marker.
    _normalize_salary_periods(result, diagnostics)
    # Retained ambiguous/unsafe alternatives are explicit blockers, never guessed.
    interim = JobScoringPolicy.model_validate(result)
    diagnostics.extend(item.model_dump(mode="json") for item in normalization_diagnostics(interim))
    result["diagnostics"] = _unique_diagnostics(diagnostics)
    return JobScoringPolicy.model_validate(result)
