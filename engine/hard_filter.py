"""Tier 0: explicit facts only; unknown data never becomes a fabricated pass."""
from engine.resume_text import get_salary_text
import re
from dataclasses import dataclass
from typing import Optional

DEGREE_RANK = {"中专": 1, "高中": 2, "大专": 3, "本科": 4, "硕士": 5, "博士": 6}
DEGREE_ALIASES = {"专科": "大专", "大学本科": "本科", "博士研究生": "博士", "硕士研究生": "硕士"}


@dataclass(frozen=True)
class SalaryParse:
    status: str
    lower_k: Optional[float] = None
    period: Optional[str] = None
    payments: Optional[int] = None
    reason: str = ""


def parse_salary(text: str) -> SalaryParse:
    """Normalize to K in the stated period. Never silently convert day/year to month."""
    if not text or not text.strip():
        return SalaryParse("UNKNOWN", reason="薪资缺失")
    s = re.sub(r"\s+", "", text).replace("～", "-").replace("~", "-").replace("—", "-").replace("至", "-")
    if "面议" in s or "待定" in s:
        return SalaryParse("UNKNOWN", reason="薪资面议/待定")
    payments = re.search(r"[·•x×*]?(\d+)薪", s)
    count = int(payments.group(1)) if payments else 12
    if payments:
        s = s[:payments.start()] + s[payments.end():]
    period = "day" if re.search(r"/天|/日|元每天|日薪", s) else "year" if re.search(r"/年|年薪|万年", s) else "month"
    s = s.removeprefix("月薪").removeprefix("日薪").removeprefix("年薪")
    s = s.replace("千元", "千").replace("万元", "万")
    s = re.sub(r"/月|每月|/天|/日|/年|每年|元每天", "", s)
    # Units may appear on either bound, but mixed units are not comparable here.
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kK千万元]?)(?:-(\d+(?:\.\d+)?)([kK千万元]?))?(?:人民币)?", s)
    if not m:
        return SalaryParse("UNKNOWN", reason="无法解析薪资格式或周期")
    lo, u1, hi, u2 = m.groups()
    factors = {"k": 1.0, "千": 1.0, "万": 10.0, "元": .001}
    if u1 and u2 and factors[u1.lower()] != factors[u2.lower()]:
        return SalaryParse("UNKNOWN", reason="薪资区间单位不一致")
    unit = u2 or u1
    if not unit:
        return SalaryParse("UNKNOWN", reason="薪资单位缺失")
    low = float(lo) * factors[unit.lower()]
    if low <= 0 or (hi is not None and float(hi) < float(lo)) or count <= 0:
        return SalaryParse("UNKNOWN", reason="薪资区间无效")
    return SalaryParse("PASS", low, period, count, "按原周期解析；额外薪数仅记录，不擅自折算")


def degree_check(resume, required: str):
    if required == "不限":
        return "PASS", "学历不限"
    req = DEGREE_RANK.get(DEGREE_ALIASES.get(required, required))
    values = [resume.degree] + [e.degree for e in resume.education]
    normalized = [DEGREE_ALIASES.get(v.strip(), v.strip()) for v in values if v]
    ranks = [DEGREE_RANK[v] for v in normalized if v in DEGREE_RANK]
    if req is None or not ranks:
        return "UNKNOWN", "最低学历配置或候选人学历未知"
    # Highest explicitly recorded qualification; a higher education entry is not a conflict.
    highest = max(ranks)
    return ("PASS", "最高明确学历符合要求") if highest >= req else ("FAIL", f"最高明确学历低于{required}")


def years_check(value, allowed):
    if value in allowed:
        return "PASS", "年限匹配配置"
    if any(k in value for k in ("应届", "在校")):
        return ("PASS", "在校/应届类型匹配") if any(any(k in a for k in ("应届", "在校")) for a in allowed) else ("UNKNOWN", "在校/应届年限需核验")
    def bounds(text):
        if "不限" in text:
            return 0, float("inf")
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if not nums or "年" not in text:
            return None
        values = [float(v) for v in nums]
        if len(values) == 2 and values[0] <= values[1]:
            return tuple(values)
        if len(values) == 1:
            return (0, values[0]) if "以内" in text else (values[0], float("inf")) if "以上" in text else (values[0], values[0])
        return None
    candidate = bounds(value)
    intervals = [bounds(a) for a in allowed]
    if not candidate:
        return "UNKNOWN", "工作年限缺失或无法解析"
    if any(b and b[0] <= candidate[0] and b[1] >= candidate[1] for b in intervals):
        return "PASS", "工作年限在允许区间"
    if any(b is None for b in intervals) or any(b and b[0] <= candidate[1] and b[1] >= candidate[0] for b in intervals):
        return "UNKNOWN", "年限区间或配置不能明确判定"
    return "FAIL", "工作年限明确在允许区间之外"


def check_hard_filters(resume, config):
    hf = config.get("hard_filters", {})
    checks = {}
    checks["degree"] = degree_check(resume, hf.get("min_degree", "不限"))
    salary = parse_salary(get_salary_text(resume))
    budget = hf.get("max_expected_salary_k")
    if budget is None:
        checks["salary"] = ("PASS", "未配置薪资上限")
    elif salary.status == "UNKNOWN" or salary.period != hf.get("salary_budget_period", "month"):
        checks["salary"] = ("UNKNOWN", salary.reason if salary.status == "UNKNOWN" else "薪资与预算周期不同，需人工核验")
    else:
        detail = f"原周期={salary.period}，下限={salary.lower_k}K，{salary.payments}薪；额外薪数不擅自换算"
        checks["salary"] = ("FAIL", "薪资下限明确超预算；" + detail) if salary.lower_k > float(budget) else ("PASS", "薪资区间存在预算内空间；" + detail)
    if resume.job_status:
        checks["job_status"] = ("FAIL", "命中排除求职状态") if resume.job_status in hf.get("exclude_job_status", []) else ("PASS", "求职状态符合")
    elif hf.get("exclude_job_status"):
        checks["job_status"] = ("UNKNOWN", "求职状态缺失")
    if hf.get("active_status_allowed"):
        checks["active_status"] = ("UNKNOWN", "活跃状态缺失") if not resume.active_status else ("PASS", "活跃状态符合") if resume.active_status in hf["active_status_allowed"] else ("FAIL", "活跃状态不在允许列表")
    if hf.get("work_years_allowed"):
        checks["work_years"] = years_check(resume.work_years, hf["work_years_allowed"])
    # Historic title is a review clue, never sufficient evidence of inability.
    hits = [w.position for w in resume.work_experience if any(k in w.position for k in hf.get("exclude_position_keywords", []))]
    if hits:
        checks["historic_positions"] = ("PASS", f"历史职位仅作核验线索：{hits}；不据此硬拒绝")
    state = "FAIL" if any(v[0] == "FAIL" for v in checks.values()) else "UNKNOWN" if any(v[0] == "UNKNOWN" for v in checks.values()) else "PASS"
    return state, checks
