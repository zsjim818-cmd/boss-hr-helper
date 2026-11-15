"""Full resume input contracts and collection-state validation."""
from typing import List, Literal, Optional, Union
from pydantic import Field, model_validator

from .common import DataIssue, InputDataModel, collect_unknown_fields, deduplicate_issues


class ExpectationInfo(InputDataModel):
    position: Optional[str] = None
    industry: Optional[str] = None
    salary: Optional[str] = None
    city: Optional[str] = None


class WorkExperienceItem(InputDataModel):
    company: str = ""
    position: str = ""
    department: Optional[str] = None
    job_type: Optional[str] = None
    start: str = ""
    end: str = ""
    duration: Optional[str] = None
    responsibility: str = ""
    performance: Optional[str] = ""
    keywords: List[str] = Field(default_factory=list)


class ProjectExperienceItem(InputDataModel):
    name: str = ""
    role: Optional[str] = ""
    start: str = ""
    end: str = ""
    duration: Optional[str] = None
    description: str = ""
    achievement: Optional[str] = ""


class EducationItem(InputDataModel):
    school: str = ""
    major: str = ""
    degree: str = ""
    start: str = ""
    end: str = ""
    school_tags: List[str] = Field(default_factory=list)
    honors_and_courses: Optional[str] = ""
    thesis_title: Optional[str] = ""
    thesis_description: Optional[str] = ""


class CandidateMeta(InputDataModel):
    candidate_key: str = Field(min_length=1, pattern=r"\S")
    encrypt_job_id: str = ""
    encrypt_geek_id: str = Field(min_length=1, pattern=r"\S")
    security_id: Optional[str] = None
    dom_data_lid: Optional[str] = None
    dom_card_index: Optional[int] = None
    downloaded_at: str = ""
    first_run_id: str = ""


class ResumeData(InputDataModel):
    ok: bool = True
    data_completeness: Literal["COMPLETE", "PARTIAL", "COLLECTION_FAILED"] = "PARTIAL"
    collection_errors: List[str] = Field(default_factory=list)
    data_issues: List[DataIssue] = Field(default_factory=list)
    name: str
    age: Optional[str] = None
    degree: str = ""
    work_years: str = ""
    job_status: Optional[str] = None
    active_status: Optional[str] = None
    expectation: Optional[Union[ExpectationInfo, str]] = None

    advantage: Optional[str] = ""
    work_experience: List[WorkExperienceItem] = Field(default_factory=list)
    project_experience: List[ProjectExperienceItem] = Field(default_factory=list)
    education: List[EducationItem] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    skills: Optional[Union[str, List[str]]] = ""
    meta: CandidateMeta = Field(..., alias="_meta")

    @model_validator(mode="before")
    @classmethod
    def infer_collection_state(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        sections = ["work_experience", "project_experience", "education", "skills", "expectation"]
        missing = [name for name in sections if name not in data]
        state = data.get("data_completeness")
        if data.get("ok") is False:
            data["data_completeness"] = "COLLECTION_FAILED"
        elif state not in (None, "COMPLETE", "PARTIAL", "COLLECTION_FAILED"):
            return data  # Preserve invalid values for Pydantic to reject explicitly.
        elif state != "COLLECTION_FAILED":
            data["data_completeness"] = "PARTIAL" if missing or state == "PARTIAL" or data.get("collection_errors") else "COMPLETE"
        # Invalid caller-supplied issue payloads must remain validation errors.
        existing = data.get("data_issues", [])
        if isinstance(existing, list):
            data["data_issues"] = list(existing) + [
                DataIssue(path=name, kind="MISSING_SECTION", message="原始采集数据未包含该区块，不能推定为完整简历")
                for name in missing
            ]
        return data

    @model_validator(mode="after")
    def record_mapping_issues(self):
        self.data_issues = deduplicate_issues(self.data_issues + collect_unknown_fields(self))
        return self
