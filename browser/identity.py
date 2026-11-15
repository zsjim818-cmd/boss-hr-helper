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
        # The page payload is untrusted for identity: the stable identity comes
        # from the adapter (data-geek-id) and the binding key is canonical.
        meta["encrypt_geek_id"] = detail.identity
        # encryptJobId is a PLATFORM identifier and must never be filled with
        # the internal jobs.job_id.  Absent platform evidence stays empty.
        meta["encrypt_job_id"] = detail.platform_job_id or ""
        meta["candidate_key"] = CandidateIdentityResolver.candidate_key(
            job_id, detail.identity)
        # Short-lived security context is never persisted.
        meta.pop("security_id", None)
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
        if resume.meta.candidate_key != meta["candidate_key"]:
            raise BrowserError("IDENTITY_MISMATCH",
                               "parsed candidate_key is not the canonical binding key",
                               recoverable=False)
        return resume
