"""Browser-layer data contracts (shared by Mock and future real adapters)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BrowserModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


BrowserErrorCode = Literal[
    "PAGE_UNAVAILABLE", "TIMEOUT", "LOGIN_REQUIRED", "NOT_FOUND",
    "IDENTITY_MISMATCH", "PAGE_CHANGED", "UNEXPECTED_STATE", "UNSUPPORTED",
    "BROWSER_UNAVAILABLE", "BROWSER_CLOSED", "CDP_CONNECT_FAILED",
    "TARGET_MISSING",
]
GreetingState = Literal[
    "DRY_RUN_READY", "SENT", "NOT_APPROVED", "ALREADY_SENT", "NOT_FOUND",
    "IDENTITY_MISMATCH", "PAGE_UNCERTAIN", "UNSUPPORTED",
]


class BrowserHealth(BrowserModel):
    connected: bool
    browser: str = ""
    message: str = ""
    pages: int = 0
    source: str = ""
    page_url: str = ""
    profile: str = ""
    profile_verified: bool = False


class CandidateCard(BrowserModel):
    """One recommendation-list card; identity is the stable encrypt_geek_id."""

    identity: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    position: str = Field(default="", max_length=300)
    job_id: str = Field(default="", max_length=200)
    dom: dict[str, Any] = Field(default_factory=dict)


class CandidateDetail(BrowserModel):
    """Raw detail payload shaped like ``schemas.candidate.ResumeData``."""

    identity: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    job_id: str = Field(default="", max_length=200)
    raw: dict[str, Any]


class CollectionIssue(BrowserModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    recoverable: bool = True
    identity: str | None = None


class CollectionReport(BrowserModel):
    job_id: str
    batch_id: str
    collected: int = 0
    imported: int = 0
    reused: int = 0
    failed: int = 0
    issues: list[CollectionIssue] = Field(default_factory=list)
    imported_candidates: list[dict[str, Any]] = Field(default_factory=list)


class GreetingOutcome(BrowserModel):
    state: GreetingState
    identity: str = ""
    message: str = ""
    dry_run: bool = True
    recorded: bool = False


class BrowserError(RuntimeError):
    """Structured collection/send failure; never silently swallowed."""

    def __init__(self, code: BrowserErrorCode, message: str, *, recoverable: bool = True):
        self.code = code
        self.recoverable = recoverable
        super().__init__(f"{code}: {message}")
