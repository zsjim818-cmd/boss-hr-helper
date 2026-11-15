"""Pure static checks for a generic policy; no model/network/database imports."""
from collections import Counter
import math
import re

from schemas.job_policy import (
    DynamicPolicyAssessment,
    JobScoringPolicy,
    PolicyCompileInput,
    PolicyDiagnostic,
)
from config.policy_normalization import normalization_diagnostics


VALIDATOR_VERSION = "policy-validator-v1.2"
MAX_HARD_CONFLICT_PATHS = 256

# v1 salary value_number is thousands of CNY (K); unit is the pay period.
_DEGREES = {
    "高中": ("高中", "high school"), "中专": ("中专",),
    "大专": ("大专", "专科", "associate"),
    "本科": ("本科", "学士", "bachelor"),
    "硕士": ("硕士", "研究生", "master"), "博士": ("博士", "phd", "ph.d", "doctorate"),
}
_FIELD_REQUIREMENT_TYPES = {
    "education_level": "EDUCATION", "work_years": "EXPERIENCE",
    "expected_salary": "COMPENSATION", "city": "LOCATION",
    "work_authorization": "OTHER", "job_status": "OTHER",
}
_FOREIGN_CURRENCY = re.compile(r"美元|美金|欧元|英镑|港币|港元|日元|USD|EUR|GBP|HKD|JPY|\$|€|£", re.I)


def _personal_attribute_dimension(dimension):
    """Catch explicit candidate attributes, not customer/service characteristics.

    This is a narrow safety lint, not a claim to understand all discriminatory
    semantics. Negated exclusion rules are allowed; clearly stated scoring or
    subjective retention predictions are rejected even when IDs cite valid work.
    """
    english_attribute = r"(?:age|gender|sex|marital status|pregnancy)"
    label = re.sub(r"[\s_/-]+", " ", dimension.label.casefold()).strip()
    if re.fullmatch(r"(?:(?:candidate|applicant|personal) )?" + english_attribute +
                    r"(?: (?:and |or )?" + english_attribute + r")*(?: (?:score|fit|assessment|evaluation))?", label):
        return True
    chinese_attribute = r"(?:年龄|性别|婚育|婚姻状况|生育计划)"
    if re.fullmatch(r"(?:候选人|个人)?" + chinese_attribute + r"(?:[与和及、/]*" + chinese_attribute +
                    r")*(?:适配|匹配|评分|评价|评估|条件)?", dimension.label.strip()):
        return True
    if re.fullmatch(r"(?:subjective stability|turnover risk|tenure prediction|retention prediction)", label):
        return True
    text_items = [dimension.label, dimension.description, *[band.description for band in dimension.rubric]]
    for text in text_items:
        for clause in re.split(r"[。！？；;,，\n]|但(?:是|仍)?|然而|不过|\b(?:but|however|yet)\b", text, flags=re.I):
            # These phrases explicitly exclude the personal attribute from scoring.
            excluded = re.search(
                r"不(?:得|应|能|要)?(?:使用|考虑|依据|根据|评分|评价|评估|预测|推测|推断|计入|纳入|因|以|将|把)|"
                r"(?:无需|不需要)(?:使用|考虑|依据|根据|评分|评价|评估|预测|推测|推断)|不作为|禁止(?:使用|考虑|依据|根据|评价|评估|预测|将)|无论|不论|"
                r"\b(?:do not|must not|should not|never)\s+(?:score|evaluate|assess|predict|consider|use)|"
                r"\b(?:exclude|ignore|regardless of|irrespective of|without using|without considering)\b", clause, re.I)
            if excluded:
                continue
            if re.search(r"(?:候选人|应聘者|个人).{0,10}(?:年龄|性别|婚育|婚姻状况|生育计划|稳定性|离职|留任)|主观稳定性|离职概率预测", clause):
                return True
            if re.search(r"\b(?:candidate|applicant|personal|employee)(?:['’]s)?\s+(?:age|gender|sex|marital status|pregnancy|stability|retention|turnover|loyalty|obedience)\b", clause, re.I):
                return True
            # A customer demographic profile is an objective task, distinct from
            # awarding a candidate points for being a particular age or gender.
            if not re.search(r"客户|用户|客群|customer|client|consumer", clause, re.I):
                if re.search(r"(?:年龄|性别|婚育).{0,10}(?:加分|扣分|高分|满分|低分|得分|\d{1,2}岁)", clause):
                    return True
    return False


def _numeric_source_values(text, salary=False):
    values = []
    for match in re.finditer(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(万|千|[kK]|元)?", text):
        value = float(match.group(1))
        if salary:
            marker = match.group(2)
            if marker == "万":
                value *= 10
            elif marker == "元":
                value /= 1000
            elif marker is None:
                # An explicit range can share its unit on the upper bound.
                following = text[match.end():]
                shared = re.match(r"\s*[-—~至到]\s*\d+(?:\.\d+)?\s*(万|千|[kK]|元)", following)
                if shared:
                    marker = shared.group(1)
                    value *= 10 if marker == "万" else .001 if marker == "元" else 1
                else:
                    continue  # No defensible salary unit: ask for clarification.
        values.append(value)
    if not salary:
        chinese_numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        values.extend(float(chinese_numbers[match.group(1)])
                      for match in re.finditer(r"([一二两三四五六七八九十])年", text))
    return values


class PolicyNotPublishable(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__("Policy has blocking diagnostics: " + ", ".join(item.code for item in diagnostics))


class _ConflictPathLimit(ValueError):
    pass


def _required_conjunctions(requirements, groups):
    """Expand only mandatory paths, preserving each ANY choice separately.

    This finite static check is not a general satisfiability solver. A bound
    produces a visible confirmation diagnostic instead of guessing a result.
    Invalid references/cycles are reported by the existing graph validator.
    """
    memo, visiting = {}, set()

    def unique(paths):
        result = set()
        for path in paths:
            result.add(path)
            if len(result) > MAX_HARD_CONFLICT_PATHS:
                raise _ConflictPathLimit
        return tuple(sorted(result, key=lambda item: tuple(sorted(item))))

    def conjunction(parts):
        result = (frozenset(),)
        for part in parts:
            result = unique(left | right for left in result for right in part)
        return result

    def walk(key):
        if key in visiting:
            raise ValueError("Invalid cyclic requirement graph")
        if key in memo:
            return memo[key]
        node = requirements.get(key) or groups.get(key)
        if node is None:
            raise ValueError("Unknown requirement reference")
        if node.priority != "REQUIRED":
            result = (frozenset(),)
        elif key in requirements:
            result = (frozenset((key,)),)
        else:
            visiting.add(key)
            children = [walk(member) for member in node.members]
            visiting.remove(key)
            result = (conjunction(children) if node.operator == "ALL" else
                      unique(path for child in children for path in child))
        memo[key] = result
        return result

    referenced = {member for group in groups.values() for member in group.members}
    roots = sorted((requirements.keys() | groups.keys()) - referenced)
    return conjunction(walk(root) for root in roots)


def _scalar_conflicts(conditions):
    """Known scalar contradictions within one simultaneously required path."""
    result = []
    for index, first in enumerate(conditions):
        for second in conditions[index + 1:]:
            if first.field != second.field or first.unit != second.unit:
                continue  # Different salary periods are not silently converted.
            pair = {first.operator, second.operator}
            ids = list(dict.fromkeys((first.requirement_id, second.requirement_id)))
            if pair == {"GTE", "LTE"}:
                low, high = (first, second) if first.operator == "GTE" else (second, first)
                if low.value_number is not None and high.value_number is not None and low.value_number > high.value_number:
                    result.append(("Lower bound exceeds upper bound in the same unit", ids))
            elif first.operator == second.operator == "EQ":
                def scalar(item):
                    if item.value_number is not None:
                        return ("number", item.value_number)
                    value = (item.value_text or "").strip().casefold()
                    if item.field == "education_level":
                        value = next((name for name, aliases in _DEGREES.items()
                                      if value in {alias.casefold() for alias in aliases}), value)
                    return ("text", value)
                if scalar(first) != scalar(second):
                    result.append(("The same scalar field has incompatible required equality values", ids))
            elif "EQ" in pair and pair.intersection(("GTE", "LTE")):
                equality, bound = (first, second) if first.operator == "EQ" else (second, first)
                if equality.value_number is not None and bound.value_number is not None:
                    outside = (equality.value_number < bound.value_number if bound.operator == "GTE" else
                               equality.value_number > bound.value_number)
                    if outside:
                        result.append(("Required equality is outside a simultaneous required bound", ids))
    return result


def diagnose_policy(policy, expected_input=None):
    """Return deterministic diagnostics, never trust a model's empty issues list.

    ERRORs require policy/source edits. CONFIRMs require explicit human review.
    Warnings can remain visible without blocking. Model-reported diagnostics are
    preserved, but cannot suppress independently detected problems.
    """
    policy = JobScoringPolicy.model_validate(policy)
    if expected_input is not None:
        expected_input = PolicyCompileInput.model_validate(expected_input)
    result = []
    def add(code, severity, message, ids=()):
        result.append(PolicyDiagnostic(code=code, severity=severity, message=message,
                                       requirement_ids=list(ids)))
    if expected_input is not None:
        if policy.sources.jd_text != expected_input.jd_text or policy.sources.hr_requirements != expected_input.hr_requirements:
            add("SOURCE_INPUT_MISMATCH", "ERROR", "Policy source text must equal the submitted JD and HR input exactly")
        if policy.job.title != expected_input.job_title:
            add("JOB_TITLE_MISMATCH", "ERROR", "Policy title must equal the submitted job title")
    requirements = {item.requirement_id: item for item in policy.requirements}
    groups = {item.group_id: item for item in policy.requirement_groups}
    all_ids = [item.requirement_id for item in policy.requirements] + [item.group_id for item in policy.requirement_groups]
    for key, count in Counter(all_ids).items():
        if count > 1:
            add("DUPLICATE_REQUIREMENT_ID", "ERROR", "Requirement and group IDs must be globally unique", [key])
    for label, items, attr in (("DIMENSION", policy.scoring_dimensions, "dimension_id"),
                               ("ANCHOR", policy.semantic_anchors, "anchor_id"),
                               ("KEYWORD_GROUP", policy.keyword_groups, "group_id"),
                               ("FILTER", policy.hard_filters, "filter_id")):
        for key, count in Counter(getattr(item, attr) for item in items).items():
            if count > 1:
                add("DUPLICATE_" + label + "_ID", "ERROR", f"{label} IDs must be unique", [key])
    sources = {"JD": policy.sources.jd_text, "HR": policy.sources.hr_requirements}
    preferred_words = re.compile(r"优先|加分|更佳|最好|preferred|nice\s+to\s+have|bonus", re.I)
    explicit_required = re.compile(r"必须|必需|必备|要求|至少|不少于|不得|不接受|仅限|需具备|需要|must|required|minimum", re.I)
    for item in [*policy.requirements, *policy.requirement_groups]:
        key = getattr(item, "requirement_id", None) or item.group_id
        if item.source_type == "INFERENCE":
            if item.source_text:
                add("INFERENCE_HAS_SOURCE_QUOTE", "ERROR", "Inferred recommendations cannot masquerade as source quotations", [key])
            if not item.needs_confirmation:
                add("INFERENCE_REQUIRES_CONFIRMATION", "CONFIRM", "An inferred recommendation needs human confirmation", [key])
            if item.priority == "REQUIRED":
                add("INFERRED_MANDATORY_REQUIREMENT", "CONFIRM", "An inferred requirement cannot silently become mandatory", [key])
        elif not item.source_text.strip() or item.source_text not in sources[item.source_type]:
            add("SOURCE_QUOTE_NOT_FOUND", "ERROR", "Requirement quotation must be an exact nonempty substring of its declared source", [key])
        if item.needs_confirmation:
            add("REQUIREMENT_NEEDS_CONFIRMATION", "CONFIRM", "Requirement has unresolved source, priority or interpretation", [key])
        if item.priority == "REQUIRED" and preferred_words.search(item.source_text):
            add("PREFERRED_PROMOTED_TO_REQUIRED", "ERROR", "A quoted preference cannot be promoted to a mandatory requirement; use a precise separate quote", [key])
        if getattr(item, "requirement_type", None) == "COMPENSATION" and _FOREIGN_CURRENCY.search(item.source_text):
            add("UNSUPPORTED_SALARY_CURRENCY", "CONFIRM", "v1 salary predicates use CNY K; a foreign-currency requirement needs explicit supported treatment", [key])
        if (getattr(item, "requirement_type", None) == "OTHER" and item.priority == "REQUIRED"
                and not any(condition.requirement_id == key and condition.field in ("work_authorization", "job_status")
                            for condition in policy.hard_filters)):
            add("UNSUPPORTED_REQUIRED_CONDITION", "CONFIRM", "This mandatory condition has no supported typed predicate; do not pretend it is an executable hard filter", [key])
    for group in policy.requirement_groups:
        explicit_alternative = re.search(r"至少(?:满足|具备|掌握|具有|选择|有)?(?:其中)?(?:一(?:条|种|项|个)|其一)", group.source_text)
        preference_context = preferred_words.search(group.source_text) or re.search(r"更好|非必要|不是.*必要|不强制|并非必需", group.source_text)
        if explicit_alternative and not preference_context:
            if group.priority != "REQUIRED":
                add("REQUIRED_ALTERNATIVE_DEMOTED", "ERROR", "An explicitly mandatory choice of at least one route cannot become a preference", [group.group_id])
            if group.operator != "ANY":
                add("ALTERNATIVE_RELATION_MISMATCH", "ERROR", "An explicit choice of at least one alternative must use ANY", [group.group_id])
        if len(set(group.members)) != len(group.members):
            add("DUPLICATE_GROUP_MEMBER", "ERROR", "A group cannot count the same member twice", [group.group_id])
        for member in group.members:
            if member not in requirements and member not in groups:
                add("UNKNOWN_REQUIREMENT_REFERENCE", "ERROR", "Group references an undefined requirement or group", [group.group_id, member])
        if group.priority == "REQUIRED":
            lower = [member for member in group.members if (requirements.get(member) or groups.get(member))
                     and (requirements.get(member) or groups.get(member)).priority != "REQUIRED"]
            if lower:
                add("GROUP_PROMOTES_PREFERENCE", "ERROR", "A REQUIRED group cannot turn preferred/optional members into mandatory alternatives", [group.group_id, *lower])
    visiting, visited = set(), set()
    def visit(key):
        if key in visiting:
            add("CYCLIC_REQUIREMENT_GRAPH", "ERROR", "Requirement groups must be an acyclic graph", [key])
            return
        if key in visited or key not in groups:
            return
        visiting.add(key)
        for member in groups[key].members:
            visit(member)
        visiting.remove(key)
        visited.add(key)
    for key in groups:
        visit(key)
    def refs(items, attr):
        for item in items:
            references = getattr(item, attr)
            if len(set(references)) != len(references):
                add("DUPLICATE_REQUIREMENT_REFERENCE", "ERROR", "Evidence projection references must be unique", references)
            for reference in references:
                if reference not in requirements:
                    add("UNKNOWN_REQUIREMENT_REFERENCE", "ERROR", "Evidence projection must reference an atomic requirement", [reference])
    refs(policy.keyword_groups, "requirement_ids")
    refs(policy.semantic_anchors, "requirement_ids")
    refs(policy.scoring_dimensions, "requirement_ids")
    for condition in policy.hard_filters:
        req = requirements.get(condition.requirement_id)
        if req is None:
            add("UNKNOWN_REQUIREMENT_REFERENCE", "ERROR", "Hard filter must reference a defined atomic requirement", [condition.requirement_id])
            continue
        if req.priority != "REQUIRED" or preferred_words.search(req.source_text):
            add("PREFERENCE_AS_HARD_FILTER", "ERROR", "Preferred/optional requirements cannot be hard filters", [req.requirement_id])
        if req.source_type == "INFERENCE":
            add("INFERRED_HARD_FILTER", "ERROR", "Hard filters require explicit source evidence, not model inference", [req.requirement_id])
        elif not explicit_required.search(req.source_text):
            add("HARD_FILTER_NOT_EXPLICIT", "CONFIRM", "The quote does not explicitly establish a mandatory condition", [req.requirement_id])
        if req.requirement_type != _FIELD_REQUIREMENT_TYPES[condition.field]:
            add("PREDICATE_REQUIREMENT_TYPE_MISMATCH", "ERROR", "Hard-filter field must match its source requirement type", [req.requirement_id])
        numeric = condition.field in ("work_years", "expected_salary")
        if condition.operator in ("GTE", "LTE"):
            if not numeric and condition.field != "education_level":
                add("UNSUPPORTED_PREDICATE", "ERROR", "Ordered comparisons are unsupported for this field", [req.requirement_id])
            if numeric and (condition.value_number is None or condition.value_text is not None or condition.value_list):
                add("INVALID_PREDICATE_VALUE", "ERROR", "A numeric comparison requires only value_number", [req.requirement_id])
            if condition.field == "education_level" and (condition.value_text is None or condition.value_number is not None or condition.value_list):
                add("INVALID_PREDICATE_VALUE", "ERROR", "An education comparison requires only value_text", [req.requirement_id])
        elif condition.operator == "IN":
            if not condition.value_list or condition.value_number is not None or condition.value_text is not None:
                add("INVALID_PREDICATE_VALUE", "ERROR", "IN requires only a nonempty value_list", [req.requirement_id])
        elif condition.operator == "EXISTS":
            if condition.value_text is not None or condition.value_number is not None or condition.value_list:
                add("INVALID_PREDICATE_VALUE", "ERROR", "EXISTS takes no comparison value", [req.requirement_id])
        elif (condition.value_text is None) == (condition.value_number is None) or condition.value_list:
            add("INVALID_PREDICATE_VALUE", "ERROR", "Equality requires exactly one scalar comparison value", [req.requirement_id])
        if condition.field == "work_years" and condition.unit != "year":
            add("INVALID_PREDICATE_UNIT", "ERROR", "Work experience must use years", [req.requirement_id])
        if condition.field == "expected_salary" and condition.unit not in ("month", "year", "day", "hour"):
            add("INVALID_PREDICATE_UNIT", "ERROR", "Salary comparisons require an explicit period", [req.requirement_id])
        if condition.value_number is not None and condition.value_number < 0:
            add("INVALID_PREDICATE_VALUE", "ERROR", "Negative salary/experience constraints are unsupported", [req.requirement_id])
        # A heading such as "以下要求" is not an upper bound.
        bounded_value = r"(?:\d+(?:\.\d+)?\s*(?:年|[kK]|万元|万|元)?|[一二两三四五六七八九十]+年|高中|中专|大专|专科|本科|硕士|博士)"
        lower_bound = re.search(r"至少|不少于|不低于|" + bounded_value + r"\s*(?:及)?以上|minimum|at\s+least|no\s+less\s+than", req.source_text, re.I)
        upper_bound = re.search(r"至多|不多于|不高于|不超过|" + bounded_value + r"\s*(?:及以下|以下|以内)|maximum|at\s+most|no\s+more\s+than", req.source_text, re.I)
        if condition.operator in ("GTE", "LTE") and bool(lower_bound) != bool(upper_bound):
            expected_operator = "GTE" if lower_bound else "LTE"
            if condition.operator != expected_operator:
                add("PREDICATE_DIRECTION_MISMATCH", "ERROR", "A single unambiguous lower/upper bound cannot be compiled with the opposite comparison", [req.requirement_id])
        if numeric and condition.value_number is not None:
            values = _numeric_source_values(req.source_text, salary=condition.field == "expected_salary")
            if not any(math.isclose(condition.value_number, value, abs_tol=1e-6) for value in values):
                add("PREDICATE_VALUE_NOT_GROUNDED", "ERROR", "Numeric filter value must be explicitly grounded in its own source quote (salary in CNY K)", [req.requirement_id])
        if condition.field == "education_level" and condition.value_text is not None:
            normalized = next((name for name, aliases in _DEGREES.items()
                               if any(alias.casefold() in condition.value_text.casefold() for alias in aliases)), None)
            if normalized is None or not any(alias.casefold() in req.source_text.casefold() for alias in _DEGREES[normalized]):
                add("PREDICATE_VALUE_NOT_GROUNDED", "ERROR", "Education filter must use a recognized degree explicitly present in its source", [req.requirement_id])
        if condition.field == "expected_salary":
            if _FOREIGN_CURRENCY.search(req.source_text):
                add("UNSUPPORTED_CURRENCY_HARD_FILTER", "ERROR", "Foreign currency cannot be executed as a CNY salary filter", [req.requirement_id])
            period_terms = {"month": ("月", "month"), "year": ("年", "year", "annum"),
                            "day": ("日", "天", "day"), "hour": ("时", "hour")}
            if condition.unit in period_terms and not any(term in req.source_text.lower() for term in period_terms[condition.unit]):
                add("SALARY_PERIOD_NEEDS_CONFIRMATION", "CONFIRM", "The quoted salary period does not explicitly match the predicate period", [req.requirement_id])
    # JD/HR have equal authority, but only simultaneous REQUIRED paths can
    # conflict. Never globally AND mutually alternative ANY branches.
    mandatory_conditions = [condition for condition in policy.hard_filters
                            if (req := requirements.get(condition.requirement_id)) is not None
                            and req.priority == "REQUIRED" and req.source_type != "INFERENCE"]
    if _scalar_conflicts(mandatory_conditions):
        paths = ()
        try:
            paths = _required_conjunctions(requirements, groups)
        except _ConflictPathLimit:
            add("HARD_CONFLICT_ANALYSIS_LIMIT", "CONFIRM",
                f"Mandatory branch analysis exceeds {MAX_HARD_CONFLICT_PATHS} paths; explicitly review or simplify the requirement graph")
        except ValueError:
            paths = ()  # Existing graph diagnostics explain cycles/references.
        if paths:
            path_conflicts = [_scalar_conflicts([item for item in mandatory_conditions if item.requirement_id in path])
                              for path in paths]
            all_impossible = all(path_conflicts)
            for conflicts in path_conflicts:
                for message, ids in conflicts:
                    add("CONFLICTING_HARD_FILTERS" if all_impossible else "UNSATISFIABLE_REQUIRED_BRANCH",
                        "ERROR" if all_impossible else "WARNING",
                        message if all_impossible else message + "; another required alternative remains feasible",
                        ids)
    for group in policy.keyword_groups:
        names = [word.casefold().strip() for keyword in group.keywords for word in [keyword.canonical, *keyword.aliases]]
        if any(not name for name in names):
            add("EMPTY_KEYWORD_ALIAS", "ERROR", "Keyword aliases cannot be blank")
        # canonical name repeated as one of its own aliases adds no information.
        if len(names) != len(set(names)):
            add("DUPLICATE_KEYWORD_ALIAS", "WARNING", "Repeated aliases will require canonical deduplication")
    if not any(anchor.polarity == "POSITIVE" for anchor in policy.semantic_anchors):
        add("MISSING_POSITIVE_ANCHOR", "ERROR", "A policy needs a positive evidence anchor")
    if not math.isclose(sum(item.weight for item in policy.scoring_dimensions), 1.0, abs_tol=1e-6):
        add("INVALID_WEIGHT_SUM", "ERROR", "Dimension weights must sum to one")
    for dimension in policy.scoring_dimensions:
        if _personal_attribute_dimension(dimension):
            add("IRRELEVANT_PERSONAL_ATTRIBUTE_DIMENSION", "ERROR", "Candidate age, gender, marital/fertility traits and subjective stability predictions are not permitted scoring dimensions", dimension.requirement_ids)
        bands = sorted(dimension.rubric, key=lambda band: band.min_score)
        expected = 0
        for band in bands:
            if band.min_score != expected or band.max_score < band.min_score:
                add("INVALID_RUBRIC_COVERAGE", "ERROR", "Rubric bands must cover integer scores 0-100 without gaps or overlaps", dimension.requirement_ids)
                break
            expected = band.max_score + 1
        if expected != 101:
            add("INVALID_RUBRIC_COVERAGE", "ERROR", "Rubric bands must end at score 100", dimension.requirement_ids)
    if policy.thresholds.deep_review > policy.thresholds.deep_recommend:
        add("INVALID_THRESHOLD_ORDER", "ERROR", "Deep review threshold must not exceed deep recommendation threshold")
    result.extend(normalization_diagnostics(policy))
    result.extend(policy.diagnostics)
    unique = {}
    for item in result:
        key = (item.code, item.severity, item.message, tuple(item.requirement_ids))
        unique[key] = item
    return list(unique.values())


def assert_publishable(policy, expected_input=None, *, confirmed_requirement_ids=()):
    policy = JobScoringPolicy.model_validate(policy)
    acknowledged = set(confirmed_requirement_ids)
    diagnostics = diagnose_policy(policy, expected_input)
    blocking = [item for item in diagnostics if item.severity == "ERROR" or
                (item.severity == "CONFIRM" and (not item.requirement_ids or not set(item.requirement_ids) <= acknowledged))]
    if blocking:
        raise PolicyNotPublishable(blocking)
    return policy


def validate_publishable_policy(policy_json, source_json=None, *, confirmed_requirement_ids=()):
    """Repository gate; source_json is the frozen PolicyCompileInput-shaped input."""
    expected = None
    if source_json is not None:
        expected = PolicyCompileInput.model_validate(source_json)
    return assert_publishable(policy_json, expected, confirmed_requirement_ids=confirmed_requirement_ids)


def validate_dynamic_assessment(policy, assessment):
    policy = JobScoringPolicy.model_validate(policy)
    assessment = DynamicPolicyAssessment.model_validate(assessment)
    keys = [item.dimension_id for item in assessment.dimensions]
    expected = {item.dimension_id for item in policy.scoring_dimensions}
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("Assessment dimension IDs must equal the policy dimension IDs exactly")
    return assessment
