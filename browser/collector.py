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
                pages: int = 3, page_size: int = 20,
                expected_platform_job_id: str | None = None,
                preview=None, max_details: int | None = None,
                cancel_check=None) -> CollectionReport:
        report = CollectionReport(job_id=job_id, batch_id=batch_id)
        seen: set[str] = set()
        expected_platform = (expected_platform_job_id or "").strip() or None
        for page in range(pages):
            if cancel_check is not None and cancel_check():
                report.stop_reason = "CANCELLED"
                break
            try:
                cards = self.adapter.list_cards(page, page_size)
            except BrowserError as exc:
                report.issues.append(CollectionIssue(
                    code=exc.code, message=str(exc), recoverable=exc.recoverable))
                if exc.code in ("PAGE_UNAVAILABLE", "TIMEOUT", "LOGIN_REQUIRED",
                                "BROWSER_CLOSED", "BROWSER_UNAVAILABLE",
                                "CDP_CONNECT_FAILED"):
                    break
                continue
            except Exception as exc:  # noqa: BLE001 - adapters must not leak raw errors
                report.issues.append(CollectionIssue(
                    code="UNEXPECTED_STATE",
                    message=f"browser page listing failed: {exc}", recoverable=True))
                break
            if not cards:
                if not report.stop_reason:
                    report.stop_reason = "PAGE_END"
                break
            report.listed += len(cards)
            stopped = False
            for card in cards:
                if cancel_check is not None and cancel_check():
                    report.stop_reason = "CANCELLED"
                    stopped = True
                    break
                try:
                    identity = self.resolver.resolve_card(card)
                except BrowserError as exc:
                    report.failed += 1
                    report.issues.append(CollectionIssue(
                        code=exc.code, message=str(exc), recoverable=exc.recoverable))
                    continue
                if identity in seen:
                    report.deduplicated += 1
                    continue
                seen.add(identity)
                report.collected += 1
                if (expected_platform and card.platform_job_id
                        and card.platform_job_id != expected_platform):
                    report.failed += 1
                    report.issues.append(CollectionIssue(
                        code="PAGE_MISMATCH",
                        message=(f"card platform job {card.platform_job_id!r} does not "
                                 f"match confirmed {expected_platform!r}"),
                        recoverable=False, identity=identity))
                    continue
                if preview is not None:
                    decision = preview(card)
                    if decision.decision == "SKIP_CONFIRMED":
                        report.preview_skipped += 1
                        continue
                    if decision.decision == "NEEDS_VERIFICATION":
                        report.review_pending += 1
                if max_details is not None and report.detail_attempted >= max_details:
                    report.stop_reason = "MAX_DETAILS"
                    stopped = True
                    break
                report.detail_attempted += 1
                try:
                    detail = self.adapter.open_detail(identity)
                    self.resolver.assert_match(identity, detail.identity)
                    if (expected_platform and detail.platform_job_id
                            and detail.platform_job_id != expected_platform):
                        raise BrowserError(
                            "CONTRACT_MISMATCH",
                            f"detail platform job {detail.platform_job_id!r} does not "
                            f"match confirmed {expected_platform!r}",
                            recoverable=False)
                    resume = self.parser.parse(detail, job_id=job_id)
                    report.detail_completed += 1
                    if resume.data_completeness != "COMPLETE":
                        report.detail_partial += 1
                        report.review_pending += 1
                    acquired = self.repository.acquire_candidate_for_job(
                        context, job_id, batch_id, resume.meta.candidate_key,
                        source="browser", external_id=identity,
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
                except Exception as exc:  # noqa: BLE001 - never leak raw 500s
                    report.failed += 1
                    report.issues.append(CollectionIssue(
                        code="UNEXPECTED_STATE", message=str(exc)[:2000],
                        recoverable=True, identity=identity))
                    continue
                if acquired.get("sealed_input"):
                    report.sealed += 1
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
                    "completeness": resume.data_completeness,
                })
            if stopped:
                break
        if not report.stop_reason:
            report.stop_reason = "MAX_PAGES"
        return report
