"""Deterministic in-memory BOSS-like site used for adapter/collector tests."""
from __future__ import annotations

from copy import deepcopy

from browser.adapter import BrowserAdapter
from browser.contracts import (BrowserError, BrowserHealth, CandidateCard,
                               CandidateDetail, GreetingOutcome)


class MockBossAdapter(BrowserAdapter):
    """In-memory recommendation page with injectable failure modes.

    ``cards`` items: {identity, name, position, job_id, resume(dict)}.
    """

    def __init__(self, cards: list[dict], *, browser="mock-in-memory", page_size=2):
        self._cards = {item["identity"]: deepcopy(item) for item in cards}
        self._order = [item["identity"] for item in cards]
        self._page_size = page_size
        self._removed: set[str] = set()
        self._sent: set[str] = set()
        self._connected = False
        self._browser = browser
        self.login_required = False
        self.fail_list = False
        self.timeout_list = False
        self.fail_detail = False
        self.page_changed_detail = False
        self.uncertain_send = False
        self.greeting_confirmed = True
        self.send_calls = 0

    # ---------------------------------------------------------------- adapter
    def connect(self) -> BrowserHealth:
        self._connected = True
        return self.health()

    def close(self) -> None:
        self._connected = False

    def health(self) -> BrowserHealth:
        return BrowserHealth(connected=self._connected, browser=self._browser,
                             message="ready" if self._connected else "disconnected")

    def list_cards(self, page: int = 0, page_size: int = 20) -> list[CandidateCard]:
        if not self._connected:
            raise BrowserError("PAGE_UNAVAILABLE", "adapter is not connected")
        if self.login_required:
            raise BrowserError("LOGIN_REQUIRED", "recruiter login is required", recoverable=False)
        if self.timeout_list:
            raise BrowserError("TIMEOUT", "recommendation page timed out")
        if self.fail_list:
            raise BrowserError("PAGE_UNAVAILABLE", "recommendation page failed to load")
        visible = [identity for identity in self._order if identity not in self._removed]
        size = page_size or self._page_size
        start = page * size
        slice_ids = visible[start:start + size]
        return [CandidateCard(identity=identity,
                              name=self._cards[identity]["name"],
                              position=self._cards[identity].get("position", ""),
                              job_id=self._cards[identity].get("job_id", ""),
                              platform_job_id=self._cards[identity].get(
                                  "platform_job_id", ""),
                              degree=self._cards[identity].get("degree", ""),
                              work_years=self._cards[identity].get("work_years", ""),
                              dom={"card_index": start + offset})
                for offset, identity in enumerate(slice_ids)]

    def open_detail(self, identity: str) -> CandidateDetail:
        if not self._connected:
            raise BrowserError("PAGE_UNAVAILABLE", "adapter is not connected")
        if self.page_changed_detail:
            raise BrowserError("PAGE_CHANGED", "detail container changed after refresh")
        if self.fail_detail:
            raise BrowserError("UNEXPECTED_STATE", "detail extraction failed")
        card = self._cards.get(identity)
        if card is None or identity in self._removed:
            raise BrowserError("NOT_FOUND", "candidate is no longer visible", recoverable=True)
        return CandidateDetail(identity=identity, name=card["name"],
                               job_id=card.get("job_id", ""),
                               platform_job_id=card.get("platform_job_id", ""),
                               raw=deepcopy(card["resume"]))

    def send_greeting(self, identity: str, message: str, *,
                      dry_run: bool = True) -> GreetingOutcome:
        if not self._connected:
            raise BrowserError("PAGE_UNAVAILABLE", "adapter is not connected")
        card = self._cards.get(identity)
        if card is None or identity in self._removed:
            return GreetingOutcome(state="NOT_FOUND", identity=identity,
                                   message="candidate is no longer visible", dry_run=dry_run)
        if not self.greeting_confirmed:
            return GreetingOutcome(state="PAGE_UNCERTAIN", identity=identity,
                                   message="greeting dialog is not confirmed", dry_run=dry_run)
        if dry_run:
            return GreetingOutcome(state="DRY_RUN_READY", identity=identity,
                                   message=f"dry-run ready for {card['name']}",
                                   dry_run=True, recorded=False)
        self.send_calls += 1
        if self.uncertain_send:
            return GreetingOutcome(state="PAGE_UNCERTAIN", identity=identity,
                                   message="page state after click is uncertain", dry_run=False)
        if identity in self._sent:
            return GreetingOutcome(state="ALREADY_SENT", identity=identity,
                                   message="greeting was already sent to this candidate",
                                   dry_run=False)
        self._sent.add(identity)
        return GreetingOutcome(state="SENT", identity=identity,
                               message=f"simulated greeting sent: {message[:40]}",
                               dry_run=False, recorded=True)

    # ------------------------------------------------------------- simulation
    def remove(self, identity: str) -> None:
        self._removed.add(identity)

    def reorder(self, identities: list[str]) -> None:
        self._order = list(identities) + [i for i in self._order if i not in identities]

    def sent_identities(self) -> set[str]:
        return set(self._sent)
