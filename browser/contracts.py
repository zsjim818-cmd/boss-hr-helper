"""Browser-layer data contracts (shared by Mock and future real adapters)."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BrowserModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


BrowserErrorCode = Literal[
    "PAGE_UNAVAILABLE", "TIMEOUT", "LOGIN_REQUIRED", "NOT_FOUND",
    "IDENTITY_MISMATCH", "PAGE_CHANGED", "UNEXPECTED_STATE", "UNSUPPORTED",
    "BROWSER_UNAVAILABLE", "BROWSER_CLOSED", "CDP_CONNECT_FAILED",
    "TARGET_MISSING", "AUTH_REQUIRED", "CAPTCHA_BLOCKED",
    "AUTH_OR_ANTI_BOT_BLOCKED", "PAGE_MISMATCH", "CONTRACT_MISMATCH",
    "EMPTY_UNVERIFIED", "EMPTY_CONFIRMED", "CANCELLED",
    "REAL_CONTRACT_UNVERIFIED", "REAL_PII_STORAGE_NOT_READY",
    "SCAN_ALREADY_RUNNING", "FILTERS_UNCONFIRMED",
    "BROWSER_ACCOUNT_UNMAPPED", "BROWSER_ACCOUNT_DISABLED",
    "CDP_PROFILE_MISMATCH", "CDP_PROFILE_UNVERIFIED",
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
    # Internal DB job id (never the platform encryptJobId).
    job_id: str = Field(default="", max_length=200)
    # Platform job identity if the list exposes it (validated separately).
    platform_job_id: str = Field(default="", max_length=200)
    degree: str = Field(default="", max_length=100)
    work_years: str = Field(default="", max_length=100)
    dom: dict[str, Any] = Field(default_factory=dict)


class CandidateDetail(BrowserModel):
    """Raw detail payload shaped like ``schemas.candidate.ResumeData``."""

    identity: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    job_id: str = Field(default="", max_length=200)
    platform_job_id: str = Field(default="", max_length=200)
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
    listed: int = 0
    deduplicated: int = 0
    preview_skipped: int = 0
    detail_attempted: int = 0
    detail_completed: int = 0
    detail_partial: int = 0
    sealed: int = 0
    scored: int = 0
    review_pending: int = 0
    imported: int = 0
    reused: int = 0
    failed: int = 0
    stop_reason: str = ""
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
