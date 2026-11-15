"""HR-approved greeting pre-checks and dry-run execution (no real sending)."""
from __future__ import annotations

from browser.adapter import BrowserAdapter
from browser.contracts import BrowserError, GreetingOutcome
from browser.identity import CandidateIdentityResolver


class GreetingExecutor:
    """Only HR-approved evaluations may be rehearsed/sent, and never twice.

    Review status and contact status are separate persisted facts:
    ``pipeline_hr_reviews`` (APPROVE/REJECT/REQUEST_REVIEW/NOTE) and
    ``pipeline_contact_events`` (CONTACTED/DECLINED/PENDING).  Dry-runs persist
    nothing; real sending is disabled in Sprint 4 Batch 1.
    """

    def __init__(self, repository, adapter: BrowserAdapter, *,
                 resolver: CandidateIdentityResolver | None = None):
        self.repository = repository
        self.adapter = adapter
        self.resolver = resolver or CandidateIdentityResolver()

    def review_state(self, context, job_id: str, evaluation_id: str) -> str | None:
        reviews = self.repository.list_pipeline_reviews(context, job_id, evaluation_id)
        state = None
        for event in reviews:
            if event["action"] != "NOTE":
                state = event["action"]
        return state

    def execute(self, context, job_id: str, evaluation_id: str, *,
                message: str, identity: str | None = None,
                dry_run: bool = True) -> GreetingOutcome:
        evaluation = self.repository.get_pipeline_result(context, job_id, evaluation_id)
        candidate = self.repository.get_job_candidate(
            context, job_id, evaluation["job_candidate_id"])
        state = self.review_state(context, job_id, evaluation_id)
        if state != "APPROVE":
            return GreetingOutcome(
                state="NOT_APPROVED", identity=identity or "",
                message=f"HR review status is {state or 'MISSING'}; approval is required",
                dry_run=dry_run)
        # Cross-evaluation dedupe for THIS HR account: a CONTACTED event on ANY
        # evaluation of the candidate (fast/deep, any Policy version) blocks a
        # re-send.  Different HR accounts may legitimately contact the same
        # candidate from their own BOSS account.
        if self.repository.has_pipeline_contact_event(
                context, job_id, candidate["job_candidate_id"], action="CONTACTED",
                actor_id=context.user_id):
            return GreetingOutcome(
                state="ALREADY_SENT", identity=identity or "",
                message="this HR account already contacted the candidate "
                        "(any scoring mode or Policy version)",
                dry_run=dry_run)
        # The database-bound identity is authoritative; an explicit identity may
        # only CONFIRM it, never override or redirect the action.
        db_identity = self.resolver.from_candidate_key(candidate["candidate_key"])
        if not db_identity:
            return GreetingOutcome(state="UNSUPPORTED", identity="",
                                   message="candidate has no stable browser identity",
                                   dry_run=dry_run)
        if identity is not None and identity != db_identity:
            return GreetingOutcome(
                state="IDENTITY_MISMATCH", identity=identity,
                message=f"requested identity {identity!r} does not match the approved "
                        f"candidate identity {db_identity!r}",
                dry_run=dry_run)
        identity = db_identity
        try:
            detail = self.adapter.open_detail(identity)
        except BrowserError as exc:
            if exc.code == "NOT_FOUND":
                return GreetingOutcome(state="NOT_FOUND", identity=identity,
                                       message=str(exc), dry_run=dry_run)
            if exc.code == "IDENTITY_MISMATCH":
                return GreetingOutcome(state="IDENTITY_MISMATCH", identity=identity,
                                       message=str(exc), dry_run=dry_run)
            return GreetingOutcome(state="PAGE_UNCERTAIN", identity=identity,
                                   message=str(exc), dry_run=dry_run)
        if detail.identity != identity:
            return GreetingOutcome(state="IDENTITY_MISMATCH", identity=identity,
                                   message="detail identity does not match the stored candidate",
                                   dry_run=dry_run)
        if not dry_run:
            return GreetingOutcome(
                state="UNSUPPORTED", identity=identity,
                message="real greeting sending is disabled in Sprint 4 Batch 1",
                dry_run=False)
        if not message.strip():
            return GreetingOutcome(state="PAGE_UNCERTAIN", identity=identity,
                                   message="greeting message is empty", dry_run=True)
        return self.adapter.send_greeting(identity, message, dry_run=True)
