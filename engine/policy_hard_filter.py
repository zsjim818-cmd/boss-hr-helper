"""Pure, bounded objective predicates and conservative three-state Policy DAG.

There is no model call, code evaluation, SQL or implicit current-policy lookup.
Capability-only nodes belong to Tier 1. Inside a mixed ANY they remain UNKNOWN
instead of being removed, so an unassessed alternative cannot cause rejection.
"""
from dataclasses import dataclass, field
import math
import re
import unicodedata

from schemas import ExpectationInfo, ResumeData
from schemas.prescreen import (
    GroupCheck, ObjectiveFacts, PrescreenEvidence, RequirementCheck, Tier0Result,
)


_DEGREES = {"中专": 1, "高中": 2, "大专": 3, "本科": 4, "硕士": 5, "博士": 6}
_DEGREE_ALIASES = {"专科": "大专", "大学本科": "本科", "学士": "本科", "硕士研究生": "硕士",
                   "博士研究生": "博士", "associate": "大专", "bachelor": "本科",
                   "master": "硕士", "phd": "博士", "ph.d": "博士", "high school": "高中"}
_MISSING = {"", "未知", "不详", "未填写", "暂无", "保密", "未提供", "unknown", "n/a", "none"}
_FOREIGN = re.compile(r"美元|美金|欧元|英镑|港币|港元|日元|USD|EUR|GBP|HKD|JPY|[$€£]", re.I)


def _text(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _missing(value):
    return _text(value).casefold() in _MISSING


def _all(states):
    return "FAIL" if "FAIL" in states else "UNKNOWN" if "UNKNOWN" in states else "PASS"


def _any(states):
    return "PASS" if "PASS" in states else "UNKNOWN" if "UNKNOWN" in states else "FAIL"


@dataclass
class _Fact:
    known: bool = False
    low: float | None = None
    high: float | None = None
    values: tuple[str, ...] = ()
    period: str | None = None
    wildcard: bool = False
    reason: str = "信息缺失或无法可靠解析"
    evidence: list = field(default_factory=list)


def _evidence(path, value, requirement_id):
    return PrescreenEvidence(source_path=path, quote=value, start=0, end=len(value),
        source_kind="objective_fact", context="FACT", requirement_ids=[requirement_id])


def _degree_value(value):
    value = _text(value).casefold()
    name = _DEGREE_ALIASES.get(value, value)
    return _DEGREES.get(name)


def parse_year_interval(value):
    """Closed bounds; open-ended statements remain conservative at boundaries."""
    text = re.sub(r"\s+", "", _text(value))
    if _missing(text):
        return None
    if text in ("无工作经验", "没有工作经验", "无经验", "0年", "零年"):
        return 0.0, 0.0
    numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    text = re.sub(r"([一二两三四五六七八九十])(?=年)", lambda m: str(numbers[m.group(1)]), text)
    text = text.replace("～", "-").replace("~", "-").replace("—", "-").replace("至", "-").replace("到", "-")
    combined = re.fullmatch(r"(\d+(?:\.\d+)?)年(\d+)个?月", text)
    if combined:
        months = int(combined.group(2))
        if months > 11:
            return None
        value = float(combined.group(1)) + months / 12
        return (value, value) if math.isfinite(value) else None
    interval = re.fullmatch(r"(\d+(?:\.\d+)?)(?:年)?-(\d+(?:\.\d+)?)年", text)
    if interval:
        lo, hi = map(float, interval.groups())
        return (lo, hi) if math.isfinite(lo) and math.isfinite(hi) and 0 <= lo <= hi else None
    point = re.fullmatch(r"(至少|不少于|不低于)?(\d+(?:\.\d+)?)年(及以上|以上|及以下|以下|以内|以下经验)?", text)
    if not point:
        return None
    prefix, number, suffix = point.groups()
    number = float(number)
    if not math.isfinite(number):
        return None
    if prefix and suffix in ("及以下", "以下", "以内", "以下经验"):
        return None
    return (number, math.inf) if prefix or suffix in ("及以上", "以上") else (0.0, number) if suffix else (number, number)


def parse_salary_interval(value, implicit_period=None):
    """CNY K plus its original period, full interval and separately noted pay count."""
    text = re.sub(r"\s+", "", _text(value))
    if _missing(text) or any(word in text for word in ("面议", "待定", "可议")):
        return None, "薪资缺失或面议，不能当作0K"
    if _FOREIGN.search(text):
        return None, "不是明确人民币薪资，禁止跨币种比较"
    payments = re.search(r"[·•x×*]?(\d+)薪", text, re.I)
    pay_count = int(payments.group(1)) if payments else None
    if pay_count is not None and pay_count <= 0:
        return None, "额外薪数无效"
    if payments:
        text = text[:payments.start()] + text[payments.end():]
    periods = {"month": r"月薪|/月|每月|月度", "year": r"年薪|/年|每年|年度",
               "day": r"日薪|/天|/日|每天|每日", "hour": r"时薪|/小时|/时|每小时"}
    found = [period for period, pattern in periods.items() if re.search(pattern, text)]
    if len(found) > 1:
        return None, "薪资存在多个周期，需人工核验"
    period = found[0] if found else implicit_period
    if period not in periods:
        return None, "薪资周期缺失，未配置隐含周期约定"
    for pattern in periods.values():
        text = re.sub(pattern, "", text)
    text = text.replace("人民币", "").replace("CNY", "").replace("RMB", "")
    text = text.replace("～", "-").replace("~", "-").replace("—", "-").replace("至", "-").replace("到", "-")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(万元?|千元?|[kK]|元)?(?:-(\d+(?:\.\d+)?)(万元?|千元?|[kK]|元)?)?(以上|及以上|以内|以下|及以下)?", text)
    if not match:
        return None, "薪资格式无法可靠解析"
    lo, unit1, hi, unit2, suffix = match.groups()
    factors = {"万": 10.0, "万元": 10.0, "千": 1.0, "千元": 1.0, "k": 1.0, "元": .001}
    unit1, unit2 = (unit1 or "").lower(), (unit2 or "").lower()
    if not (unit1 or unit2):
        return None, "薪资金额单位缺失"
    if unit1 and unit2 and factors[unit1] != factors[unit2]:
        return None, "薪资区间两端单位不一致，需人工核验"
    factor = factors[unit2 or unit1]
    low, high = float(lo) * factor, float(hi or lo) * factor
    if not math.isfinite(low) or not math.isfinite(high) or low <= 0 or high < low or (hi and suffix):
        return None, "薪资区间无效或范围表述有歧义"
    if suffix in ("以上", "及以上"):
        high = math.inf
    elif suffix:
        low = 0.0
    details = f"按原周期{period}的人民币K区间比较"
    if not found:
        details += "；周期来自显式执行配置约定"
    if pay_count:
        details += f"；记录{pay_count}薪，不跨周期折算"
    return (low, high, period), details


def _interval_status(low, high, operator, target=None, choices=()):
    if operator == "EXISTS":
        return "PASS"
    if operator == "IN":
        overlap = any(low <= value <= high for value in choices)
        return "PASS" if low == high and low in choices else "UNKNOWN" if overlap else "FAIL"
    if operator == "GTE":
        return "PASS" if low >= target else "FAIL" if high < target else "UNKNOWN"
    if operator == "LTE":
        return "PASS" if high <= target else "FAIL" if low > target else "UNKNOWN"
    if operator == "EQ":
        return "PASS" if low == high == target else "FAIL" if target < low or target > high else "UNKNOWN"
    if operator == "NE":
        return "FAIL" if low == high == target else "PASS" if target < low or target > high else "UNKNOWN"
    return "UNKNOWN"


_STATUS = {
    # Employment state alone is not proof of an arrival-time commitment.
    "离职随时到岗": "available",
    "在职考虑机会": "employed_open", "在职看机会": "employed_open", "在职看看机会": "employed_open",
    "在职暂不考虑": "not_seeking", "在职不考虑": "not_seeking", "暂不考虑": "not_seeking",
}
_AUTH = {"有": "authorized", "是": "authorized", "有工作许可": "authorized", "有合法工作许可": "authorized",
         "已授权": "authorized", "允许工作": "authorized", "authorized": "authorized", "yes": "authorized",
         "无": "unauthorized", "否": "unauthorized", "无工作许可": "unauthorized", "没有工作许可": "unauthorized",
         "未授权": "unauthorized", "unauthorized": "unauthorized", "notauthorized": "unauthorized", "no": "unauthorized"}
_ANY_CITY = {"不限", "全国", "任意城市", "anywhere", "any city"}
_QUALIFIED_CITY = re.compile(r"意向|期望|希望|计划|均可|可异地|可接受|接受|周边|附近|[()\[\]{}:：]")


def _string_key(value, field_name):
    text = re.sub(r"[\s_\-—]+", "", _text(value).casefold())
    if field_name == "city":
        # A free-form location statement is not a bare comparable city value.
        # Do not guess a city from qualifiers or reinterpret them as an explicit
        # different location. Exact, separately supported wildcards stay above.
        if _QUALIFIED_CITY.search(text):
            return None
        return text[:-1] if text.endswith("市") and len(text) > 1 else text
    return (_AUTH if field_name == "work_authorization" else _STATUS).get(text)


def _string_status(fact, condition):
    if condition.operator == "EXISTS":
        return "PASS"
    if condition.operator in ("GTE", "LTE"):
        return "UNKNOWN"
    requested = condition.value_list if condition.operator == "IN" else [condition.value_text]
    if not requested or any(value is None or _missing(value) for value in requested):
        return "UNKNOWN"
    if condition.field == "city" and (fact.wildcard or any(_text(value).casefold() in _ANY_CITY for value in requested)):
        return "PASS" if condition.operator in ("EQ", "IN") else "UNKNOWN"
    actual = fact.values
    canonical_actual = [_string_key(value, condition.field) for value in actual]
    canonical_requested = [_string_key(value, condition.field) for value in requested]
    # Valid city keys already cover literal spelling equality. The raw-string
    # fallback must not turn two identical ambiguous location statements into a
    # proven objective location.
    exact = condition.field != "city" and any(_text(a).casefold() == _text(b).casefold() for a in actual for b in requested)
    intersects = exact or any(a is not None and a == b for a in canonical_actual for b in canonical_requested)
    comparable = all(item is not None for item in [*canonical_actual, *canonical_requested])
    if condition.operator in ("EQ", "IN"):
        return "PASS" if intersects else "FAIL" if comparable else "UNKNOWN"
    if condition.operator == "NE":
        return "UNKNOWN" if intersects and len(actual) > 1 else "FAIL" if intersects else "PASS" if comparable else "UNKNOWN"
    return "UNKNOWN"


def _objective_fact(resume, facts, name, requirement_id):
    supplied = getattr(facts, name)
    if supplied is None:
        return _Fact(reason=f"缺少明确的{name}事实，不能推断")
    raw = resume.model_dump(mode="json", by_alias=True)
    # Only the two explicitly supported snapshot fields may supply these facts.
    value = raw.get(name)
    if (supplied.source_path != name or not isinstance(value, str) or supplied.quote != value
            or supplied.value != value or _missing(value)):
        return _Fact(reason=f"{name}补充事实无法定位到同份原文，禁止使用")
    if name == "current_city" and _text(value).casefold() in _ANY_CITY:
        return _Fact(reason="不限/全国不构成明确现居城市")
    return _Fact(known=True, values=(value,), reason=f"明确原始字段{name}",
                 evidence=[_evidence(name, value, requirement_id)])


def _read_fact(resume, condition, profile, facts):
    requirement_id = condition.requirement_id
    if condition.field == "education_level":
        values = [("degree", resume.degree)] + [(f"education[{i}].degree", item.degree) for i, item in enumerate(resume.education)]
        recorded = [(path, value, _degree_value(value)) for path, value in values if not _missing(value)]
        known = [(path, value, degree) for path, value, degree in recorded if degree is not None]
        if not known:
            return _Fact(reason="最高明确学历未知，不默认本科")
        highest = max(known, key=lambda item: item[2])
        evidence = [_evidence(path, value, requirement_id) for path, value, _ in recorded]
        if any(degree is None for _, _, degree in recorded):
            return _Fact(reason="学历记录含无法统一的学历值，需核验", evidence=evidence)
        return _Fact(known=True, low=float(highest[2]), high=float(highest[2]), reason="最高明确记录学历", evidence=evidence)
    if condition.field == "work_years":
        interval = parse_year_interval(resume.work_years)
        evidence = [_evidence("work_years", resume.work_years, requirement_id)] if resume.work_years else []
        return _Fact(known=interval is not None, low=interval[0] if interval else None, high=interval[1] if interval else None,
                     reason="按完整工作年限区间比较" if interval else "工作年限缺失或无法确定，不按0年处理", evidence=evidence)
    if condition.field == "expected_salary":
        value = resume.expectation.salary or "" if isinstance(resume.expectation, ExpectationInfo) else str(resume.expectation or "")
        path = "expectation.salary" if isinstance(resume.expectation, ExpectationInfo) else "expectation"
        interval, reason = parse_salary_interval(
            value, profile.salary_implicit_period or condition.unit)
        evidence = [_evidence(path, value, requirement_id)] if value else []
        return _Fact(known=interval is not None, low=interval[0] if interval else None, high=interval[1] if interval else None,
                     period=interval[2] if interval else None, reason=reason, evidence=evidence)
    if condition.field == "city":
        if profile.city_basis == "current":
            return _objective_fact(resume, facts, "current_city", requirement_id)
        value = resume.expectation.city or "" if isinstance(resume.expectation, ExpectationInfo) else ""
        if _missing(value) or value in ("暂无意向", "待沟通", "可协商", "待定"):
            return _Fact(reason="意向城市缺失，不能用现居地或其他文本替代")
        cities = tuple(item.strip() for item in re.split(r"[,，、/|;；]", value) if item.strip())
        return _Fact(known=bool(cities), values=cities, wildcard=_text(value).casefold() in _ANY_CITY,
                     reason="仅按明确意向工作城市比较，不证明现居地",
                     evidence=[_evidence("expectation.city", value, requirement_id)])
    if condition.field == "work_authorization":
        return _objective_fact(resume, facts, "work_authorization", requirement_id)
    if condition.field == "job_status":
        value = resume.job_status or ""
        return _Fact(known=not _missing(value), values=(value,), reason="仅按明确求职状态比较",
                     evidence=[_evidence("job_status", value, requirement_id)] if value else [])
    return _Fact(reason="未支持的客观字段")


def _predicate(resume, condition, profile, facts):
    fact = _read_fact(resume, condition, profile, facts)
    if not resume.ok or resume.data_completeness == "COLLECTION_FAILED":
        return "UNKNOWN", "采集失败，不能依据可能残留的字段作硬拒绝", fact.evidence
    if not fact.known:
        return "UNKNOWN", fact.reason, fact.evidence
    if condition.field == "expected_salary" and condition.operator != "EXISTS" and fact.period != condition.unit:
        return "UNKNOWN", "候选薪资与Policy周期不一致，禁止自动折算", fact.evidence
    if condition.field == "work_years" and condition.unit != "year":
        return "UNKNOWN", "工作年限比较单位不是year", fact.evidence
    if condition.field == "education_level" and condition.unit not in (None, "level"):
        return "UNKNOWN", "学历比较单位不是level", fact.evidence
    if condition.field in ("education_level", "work_years", "expected_salary"):
        if condition.field == "education_level":
            target = _degree_value(condition.value_text) if condition.value_text is not None else None
            choices = [_degree_value(value) for value in condition.value_list]
        else:
            target = condition.value_number
            try:
                choices = [float(value) for value in condition.value_list]
            except (TypeError, ValueError):
                choices = [None]
        if condition.operator != "EXISTS" and ((condition.operator == "IN" and (not choices or any(value is None or not math.isfinite(value) for value in choices)))
                or (condition.operator != "IN" and (target is None or not math.isfinite(target)))):
            return "UNKNOWN", "Policy比较值无法可靠归一", fact.evidence
        state = _interval_status(fact.low, fact.high, condition.operator, target, choices)
    else:
        state = _string_status(fact, condition)
    suffix = ("；区间跨比较边界，需核验" if condition.field in ("work_years", "expected_salary", "education_level")
              else "；语义或取值无法可靠比较，需核验") if state == "UNKNOWN" else "；明确满足" if state == "PASS" else "；明确不满足"
    return state, fact.reason + suffix, fact.evidence


def evaluate_hard_filters(plan, resume, *, facts=None):
    """Execute the bound immutable Policy's objective projection only."""
    resume = ResumeData.model_validate(resume)
    facts = ObjectiveFacts.model_validate(facts or {})
    policy, profile = plan.policy, plan.profile
    requirements = {item.requirement_id: item for item in policy.requirements}
    groups = {item.group_id: item for item in policy.requirement_groups}
    filters = {key: [item for item in policy.hard_filters if item.requirement_id == key] for key in requirements}
    atomic = {}
    review = []
    for key, requirement in requirements.items():
        conditions = filters[key]
        eligible = requirement.priority == "REQUIRED" and requirement.source_type != "INFERENCE" and not requirement.needs_confirmation
        evidence, reasons, states = [], [], []
        if conditions:
            for condition in conditions:
                state, reason, items = _predicate(resume, condition, profile, facts)
                comparison = condition.value_list if condition.operator == "IN" else condition.value_text if condition.value_text is not None else condition.value_number
                prefix = f"{condition.filter_id} ({condition.field} {condition.operator} {comparison})"
                states.append(state); reasons.append(prefix + ": " + reason); evidence.extend(items)
        if not conditions:
            state, reason = "UNKNOWN", "该能力要求无客观硬谓词，交Tier1/后续人工核验"
        elif not eligible:
            state, reason = "UNKNOWN", "偏好、可选、推断或待确认要求不能成为硬拒绝"
        else:
            state, reason = _all(states), "；".join(dict.fromkeys(reasons))
        atomic[key] = RequirementCheck(requirement_id=key, status=state, reason=reason,
            filter_ids=[item.filter_id for item in conditions], evidence=evidence, hard_eligible=eligible and bool(conditions))

    # Structural relevance is separate from the truth value of an untyped leaf.
    relevance = {}
    def has_hard(key, visiting=()):
        if key in visiting:
            raise ValueError("Cyclic requirement graph")
        if key in relevance:
            return relevance[key]
        if key in atomic:
            result = bool(filters[key]) and requirements[key].priority == "REQUIRED"
        elif key in groups:
            children = [has_hard(child, (*visiting, key)) for child in groups[key].members]
            result = groups[key].priority == "REQUIRED" and any(children)
        else:
            raise ValueError("Unknown requirement reference")
        relevance[key] = result
        return result
    for key in [*requirements, *groups]:
        has_hard(key)
    group_values, skipped, cache, active_atoms = {}, set(), {}, set()
    def evaluate(key, under_any=False, stack=()):
        if key in stack:
            raise ValueError("Cyclic requirement graph")
        if (key, under_any) in cache:
            return cache[key, under_any]
        item = requirements.get(key) or groups.get(key)
        if item is None:
            raise ValueError("Unknown requirement reference")
        if item.priority != "REQUIRED":
            skipped.add(key)
            return "UNKNOWN" if under_any else None
        if not relevance[key] and not under_any:
            skipped.add(key)
            return None
        if key in atomic:
            active_atoms.add(key)
            result = atomic[key].status
        elif item.source_type == "INFERENCE" or item.needs_confirmation:
            result = "UNKNOWN"
            review.append(f"{key}: 推断或待确认的关系不能作硬拒绝")
        else:
            child_states = [evaluate(child, under_any or item.operator == "ANY", (*stack, key)) for child in item.members]
            states = [state for state in child_states if state is not None]
            result = (_all(states) if item.operator == "ALL" else _any(states)) if states else "UNKNOWN"
        if key in groups:
            prior = group_values.get(key)
            shared_context = prior is not None and prior.status != result
            group_values[key] = GroupCheck(group_id=key, operator=item.operator, status="UNKNOWN" if shared_context else result,
                members=item.members, reason="共享组在不同ANY上下文中的硬投影状态不同，保守保留UNKNOWN" if shared_context else
                "三态递归；未执行的能力分支在ANY中保留UNKNOWN" if result == "UNKNOWN" else "按Policy三态关系求值")
        cache[key, under_any] = result
        return result
    roots = [evaluate(key) for key in plan.root_ids]
    overall = _all([state for state in roots if state is not None])
    # Preserve diagnostics for every group, including capability-only groups.
    for key, group in groups.items():
        if key not in group_values:
            group_values[key] = GroupCheck(group_id=key, operator=group.operator, status="UNKNOWN", members=group.members,
                reason="该组不构成可执行客观硬约束，未用其作硬拒绝")
    for key, item in atomic.items():
        item.hard_eligible = item.hard_eligible and key in active_atoms
    skipped.update(set(requirements) - active_atoms)
    relevant_atomic = [item for key, item in atomic.items() if key in active_atoms and filters[key] and requirements[key].priority == "REQUIRED"]
    for item in relevant_atomic:
        if item.status == "UNKNOWN":
            review.append(f"{item.requirement_id}: {item.reason}")
    if overall == "UNKNOWN":
        review.extend(f"{key}: {item.reason}" for key, item in group_values.items() if item.status == "UNKNOWN" and relevance[key])
    return Tier0Result(hard_filter_status=overall,
        matched_requirements=[item.requirement_id for item in relevant_atomic if item.status == "PASS"],
        failed_requirements=[item.requirement_id for item in relevant_atomic if item.status == "FAIL"],
        unknown_requirements=[item.requirement_id for item in relevant_atomic if item.status == "UNKNOWN"],
        atomic_results=list(atomic.values()), group_results=list(group_values.values()),
        evidence=[evidence for item in atomic.values() for evidence in item.evidence],
        review_reasons=list(dict.fromkeys(review)), skipped_requirements=sorted(skipped))
