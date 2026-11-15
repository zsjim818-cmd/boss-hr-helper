"""Collect recommendation cards through a browser adapter into the repository."""
from __future__ import annotations

from browser.adapter import BrowserAdapter
from browser.contracts import (BrowserError, CollectionIssue, CollectionReport)
from browser.identity import (CandidateDetailParser, CandidateIdentityResolver)


class CandidateListCollector:
    """Read pages, validate details, and import via the existing repository.

    - Dedupe: repeated collection of the same stable identity reuses the
      repository candidate (``acquire_candidate_for_job``); no duplicates.
    - Binding: job/batch policy version is fixed by the repository batch.
    - Failures keep a structured reason; the run continues for other cards.
    """

    def __init__(self, repository, adapter: BrowserAdapter, *,
                 parser: CandidateDetailParser | None = None,
                 resolver: CandidateIdentityResolver | None = None):
        self.repository = repository
        self.adapter = adapter
        self.parser = parser or CandidateDetailParser()
        self.resolver = resolver or CandidateIdentityResolver()

    def collect(self, context, job_id: str, batch_id: str, *,
                pages: int = 3, page_size: int = 20) -> CollectionReport:
        report = CollectionReport(job_id=job_id, batch_id=batch_id)
        seen: set[str] = set()
        for page in range(pages):
            try:
                cards = self.adapter.list_cards(page, page_size)
            except BrowserError as exc:
                report.issues.append(CollectionIssue(
                    code=exc.code, message=str(exc), recoverable=exc.recoverable))
                if exc.code in ("PAGE_UNAVAILABLE", "TIMEOUT", "LOGIN_REQUIRED"):
                    break
                continue
            if not cards:
                break
            for card in cards:
                try:
                    identity = self.resolver.resolve_card(card)
                except BrowserError as exc:
                    report.failed += 1
                    report.issues.append(CollectionIssue(
                        code=exc.code, message=str(exc), recoverable=exc.recoverable))
                    continue
                if identity in seen:
                    continue
                seen.add(identity)
                report.collected += 1
                try:
                    detail = self.adapter.open_detail(identity)
                    self.resolver.assert_match(identity, detail.identity)
                    resume = self.parser.parse(detail, job_id=job_id)
                    acquired = self.repository.acquire_candidate_for_job(
                        context, job_id, batch_id, resume.meta.candidate_key,
                        resume=resume.model_dump(by_alias=True, mode="json"))
                except BrowserError as exc:
                    report.failed += 1
                    report.issues.append(CollectionIssue(
                        code=exc.code, message=str(exc), recoverable=exc.recoverable,
                        identity=identity))
                    continue
                except (ValueError, LookupError) as exc:
                    report.failed += 1
                    report.issues.append(CollectionIssue(
                        code="IMPORT_FAILED", message=str(exc)[:2000], recoverable=True,
                        identity=identity))
                    continue
                if acquired["is_new"]:
                    report.imported += 1
                else:
                    report.reused += 1
                report.imported_candidates.append({
                    "identity": identity,
                    "candidate_key": resume.meta.candidate_key,
                    "job_candidate_id": acquired["job_candidate"]["job_candidate_id"],
                    "policy_id": acquired["job_candidate"]["policy_id"],
                    "policy_version": acquired["job_candidate"]["policy_version"],
                })
        return report
