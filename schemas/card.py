"""Listing-card input contracts, kept independent of complete resumes."""
from typing import List, Optional
from pydantic import Field, model_validator

from .common import DataIssue, InputDataModel, collect_unknown_fields, deduplicate_issues


class CardActionStatus(InputDataModel):
    button_text: str
    is_contacted: bool


class CardBasicInfo(InputDataModel):
    name: str
    degree: str = ""
    work_years: str = ""
    expected_salary: str = ""
    active_text: Optional[str] = None
    age: Optional[str] = None
    job_status: Optional[str] = None
    expected_city: str = ""
    expected_position: str = ""


class CardTimelineItem(InputDataModel):
    time_span: str = ""
    entity_name: str = ""
    role_or_major: str = ""


class CardPreviewClues(InputDataModel):
    advantage_snippet: str = ""
    highlight_tags: List[str] = Field(default_factory=list)
    brief_timeline: List[CardTimelineItem] = Field(default_factory=list)


class CardDomAnchor(InputDataModel):
    dom_card_index: int = Field(ge=0)
    dom_data_lid: Optional[str] = None
    security_id: Optional[str] = None
    has_close_btn: bool = False


class CandidateCardData(InputDataModel):
    """Partial listing-card contract; intentionally independent of ResumeData."""
    action_status: CardActionStatus
    basic_info: CardBasicInfo
    preview_clues: CardPreviewClues = Field(default_factory=CardPreviewClues)
    dom_anchor: CardDomAnchor = Field(alias="_dom_anchor")
    data_issues: List[DataIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def record_mapping_issues(self):
        self.data_issues = deduplicate_issues(self.data_issues + collect_unknown_fields(self))
        return self
