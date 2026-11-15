"""Resume text projections for scoring; data contracts contain no projections."""
from typing import List

from schemas import Evidence, ExpectationInfo


def get_evidence(resume) -> List[Evidence]:
    return [Evidence(stage="INPUT", source=source, source_path=path, snippet=text,
                     evaluation="原始采集文本，尚未核验真实性")
            for source, path, text in get_source_texts(resume)]


def get_core_practice_texts(resume) -> List[str]:
    chunks = []
    for w in resume.work_experience:
        chunks.extend([w.responsibility, w.performance or ""])
    for p in resume.project_experience:
        chunks.extend([p.description, p.achievement or ""])
    for edu in resume.education:
        chunks.append(edu.thesis_description or "")
    return [c.strip() for c in chunks if c.strip()]


def get_source_texts(resume):
    """Separate raw evidence from labels; no company/title/header contamination."""
    result = []
    for i, w in enumerate(resume.work_experience):
        result.extend([("work", f"work_experience[{i}].responsibility", w.responsibility),
                       ("work", f"work_experience[{i}].performance", w.performance or ""),
                       ("work_tags", f"work_experience[{i}].keywords", " | ".join(w.keywords))])
    for i, p in enumerate(resume.project_experience):
        result.extend([("project", f"project_experience[{i}].description", p.description),
                       ("project", f"project_experience[{i}].achievement", p.achievement or "")])
    for i, e in enumerate(resume.education):
        result.append(("thesis", f"education[{i}].thesis_description", e.thesis_description or ""))
    result.extend([("skills", "skills", " | ".join(resume.skills) if isinstance(resume.skills, list) else resume.skills or ""),
                   ("advantage", "advantage", resume.advantage or "")])
    return [(s, p, t) for s, p, t in result if t.strip()]


def get_summary_and_skills_text(resume) -> str:
    parts = []
    if resume.advantage:
        parts.append(resume.advantage)
    if isinstance(resume.skills, list):
        parts.append(" | ".join(resume.skills))
    elif resume.skills:
        parts.append(resume.skills)
    for w in resume.work_experience:
        if w.keywords:
            parts.append(" ".join(w.keywords))
    return "\n".join(parts)


def get_salary_text(resume) -> str:
    if isinstance(resume.expectation, ExpectationInfo):
        return resume.expectation.salary or ""
    return str(resume.expectation or "")
