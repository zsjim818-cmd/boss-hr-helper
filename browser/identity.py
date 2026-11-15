"""Stable candidate identity and detail parsing for the browser layer."""
from __future__ import annotations

from pydantic import ValidationError

from browser.contracts import BrowserError, CandidateCard, CandidateDetail
from schemas import ResumeData


class CandidateIdentityResolver:
    """encrypt_geek_id is the only stable identity; names are never trusted."""

    @staticmethod
    def resolve_card(card: CandidateCard) -> str:
        identity = card.identity.strip()
        if not identity:
            raise BrowserError("IDENTITY_MISMATCH", "card has no stable encrypt_geek_id",
                               recoverable=False)
        return identity

    @staticmethod
    def candidate_key(job_id: str, identity: str) -> str:
        return f"{job_id}:{identity}"

    @staticmethod
    def from_candidate_key(candidate_key: str) -> str | None:
        if ":" not in candidate_key:
            return None
        suffix = candidate_key.split(":", 1)[1].strip()
        return suffix or None

    @staticmethod
    def assert_match(expected: str, actual: str) -> None:
        if expected != actual:
            raise BrowserError(
                "IDENTITY_MISMATCH",
                f"card/detail identity mismatch: expected {expected!r}, got {actual!r}",
                recoverable=False)


class CandidateDetailParser:
    """Convert the platform detail payload into the strict ResumeData contract."""

    @staticmethod
    def parse(detail: CandidateDetail, *, job_id: str) -> ResumeData:
        raw = dict(detail.raw)
        raw["_meta"] = dict(raw.get("_meta") or {})
        meta = raw["_meta"]
        meta.setdefault("encrypt_geek_id", detail.identity)
        meta.setdefault("encrypt_job_id", detail.job_id or job_id)
        meta.setdefault("candidate_key",
                        CandidateIdentityResolver.candidate_key(job_id, detail.identity))
        try:
            resume = ResumeData.model_validate(raw)
        except ValidationError as exc:
            raise BrowserError(
                "UNEXPECTED_STATE",
                "detail payload does not satisfy ResumeData: " + str(exc.errors()[0]["msg"]),
                recoverable=True) from exc
        if resume.meta.encrypt_geek_id != detail.identity:
            raise BrowserError("IDENTITY_MISMATCH", "parsed meta identity differs from detail",
                               recoverable=False)
        return resume
