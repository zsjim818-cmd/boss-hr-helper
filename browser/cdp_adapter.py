"""Playwright adapter that drives a local Mock DOM page in a real Edge via CDP.

The Mock HTML is written to a private file and opened in the shared CDP browser;
all selectors read the rendered DOM of that real browser (no in-memory card
fallback).  Every Playwright call is executed on the runtime's single worker
thread, so repeated FastAPI requests can safely reuse the same 9222 browser.
"""
from __future__ import annotations

import json

from browser.adapter import BrowserAdapter
from browser.contracts import (BrowserError, BrowserHealth, CandidateCard,
                               CandidateDetail, GreetingOutcome)
from browser.mock_page import build_html
from browser.runtime import CDPConnectionManager


class CdpMockBossAdapter(BrowserAdapter):
    """Real Edge (CDP) + local Mock DOM; none of the selectors touch BOSS."""

    def __init__(self, manager: CDPConnectionManager, cards: list[dict], *,
                 page_error: bool = False, login_required: bool = False,
                 greeting_confirm: bool = True, source: str = "cdp_mock"):
        self.manager = manager
        self.cards = cards
        self.page_error = page_error
        self.login_required = login_required
        self.greeting_confirm = greeting_confirm
        self.source = source
        self.page_url = ""
        self._page = None

    # ------------------------------------------------------------------ connect
    def connect(self) -> BrowserHealth:
        health = self.manager.connect()
        if not health.connected:
            raise BrowserError("BROWSER_UNAVAILABLE",
                               health.message or "CDP browser is not connected",
                               recoverable=True)
        html = build_html(self.cards, page_error=self.page_error,
                          login_required=self.login_required,
                          greeting_confirm=self.greeting_confirm)
        self.page_url = self.manager.write_mock_page(html)
        try:
            self._page = self.manager.open_page(self.page_url)
        except BrowserError:
            self._page = None
            raise
        except Exception as exc:  # noqa: BLE001 - normalise DOM failures
            self._page = None
            raise BrowserError("PAGE_UNAVAILABLE",
                               f"could not open the Mock page: {exc}",
                               recoverable=True) from exc
        return self.health()

    def close(self) -> None:
        # The browser belongs to the runtime; only drop our page handle.
        self._page = None

    def health(self) -> BrowserHealth:
        health = self.manager.health()
        return health.model_copy(update={"source": self.source,
                                         "page_url": self.page_url})

    # -------------------------------------------------------------------- dom
    def _require_page(self):
        if self._page is None:
            raise BrowserError("BROWSER_CLOSED",
                               "Mock page is not open; call connect() first",
                               recoverable=True)
        return self._page

    def list_cards(self, page: int = 0, page_size: int = 20) -> list[CandidateCard]:
        return self.manager.run(lambda: self._list_cards_impl(page, page_size))

    def _list_cards_impl(self, page: int, page_size: int) -> list[CandidateCard]:
        target = self._require_page()
        try:
            if target.locator("#login-required").is_visible():
                raise BrowserError("LOGIN_REQUIRED", "recruiter login is required",
                                   recoverable=False)
            if target.locator("#page-error").is_visible():
                raise BrowserError("PAGE_UNAVAILABLE",
                                   "recommendation page failed to load")
            size = page_size or 20
            start, end = page * size, page * size + size
            cards = target.locator(".candidate-card").all()
            result = []
            for index, element in enumerate(cards):
                if not start <= index < end:
                    continue
                result.append(CandidateCard(
                    identity=element.get_attribute("data-geek-id") or "",
                    name=element.locator(".name").inner_text(),
                    position=element.locator(".position").inner_text(),
                    job_id="",
                    dom={"card_index": index, "page_url": self.page_url}))
            return result
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001 - closed tab / interrupted DOM
            raise BrowserError("PAGE_UNAVAILABLE",
                               f"Mock page is no longer usable: {exc}",
                               recoverable=True) from exc

    def open_detail(self, identity: str) -> CandidateDetail:
        return self.manager.run(lambda: self._open_detail_impl(identity))

    def _open_detail_impl(self, identity: str) -> CandidateDetail:
        target = self._require_page()
        try:
            card = target.locator(f'.candidate-card[data-geek-id="{identity}"]')
            if card.count() == 0:
                raise BrowserError("NOT_FOUND", "candidate is no longer visible")
            card.first.locator(".open-detail").click()
            target.locator("#detail-panel").wait_for(state="visible", timeout=5000)
            detail_identity = target.locator(
                "#detail-panel").get_attribute("data-geek-id") or ""
            if detail_identity != identity:
                raise BrowserError("IDENTITY_MISMATCH",
                                   "detail identity differs from the card",
                                   recoverable=False)
            raw = json.loads(target.locator("#detail-resume").inner_text())
            return CandidateDetail(identity=identity,
                                   name=target.locator("#detail-name").inner_text(),
                                   job_id="", raw=raw)
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BrowserError("PAGE_CHANGED",
                               f"could not read the detail panel: {exc}",
                               recoverable=True) from exc

    def send_greeting(self, identity: str, message: str, *,
                      dry_run: bool = True) -> GreetingOutcome:
        return self.manager.run(
            lambda: self._send_greeting_impl(identity, message, dry_run))

    def _send_greeting_impl(self, identity: str, message: str,
                            dry_run: bool) -> GreetingOutcome:
        target = self._require_page()
        try:
            card = target.locator(f'.candidate-card[data-geek-id="{identity}"]')
            card_count = card.count()
            if card_count == 0:
                return GreetingOutcome(state="NOT_FOUND", identity=identity,
                                       message="candidate is no longer visible",
                                       dry_run=dry_run)
            if card_count != 1:
                return GreetingOutcome(
                    state="PAGE_UNCERTAIN", identity=identity,
                    message="multiple cards carry the same stable identity",
                    dry_run=dry_run)
            if (card.first.get_attribute("data-geek-id") or "") != identity:
                return GreetingOutcome(
                    state="IDENTITY_MISMATCH", identity=identity,
                    message="card identity attribute does not match the request",
                    dry_run=dry_run)
            if not dry_run:
                # Real sending stays physically disabled in Sprint 4.
                return GreetingOutcome(
                    state="UNSUPPORTED", identity=identity,
                    message="real sending is disabled in this batch", dry_run=False)

            panel = target.locator("#detail-panel")
            if panel.is_visible():
                active_identity = panel.get_attribute("data-geek-id") or ""
                if active_identity != identity:
                    # Never let candidate A's open panel satisfy candidate B.
                    return GreetingOutcome(
                        state="IDENTITY_MISMATCH", identity=identity,
                        message=(f"active detail panel belongs to "
                                 f"{active_identity or 'an unknown candidate'!s}, "
                                 f"not {identity!r}"),
                        dry_run=dry_run)
            # Open the target's own detail panel through its card-scoped control.
            card.first.locator(".open-detail").click()
            panel.wait_for(state="visible", timeout=5000)
            panel_identity = panel.get_attribute("data-geek-id") or ""
            if panel_identity != identity:
                return GreetingOutcome(
                    state="IDENTITY_MISMATCH", identity=identity,
                    message=(f"detail panel identity {panel_identity!r} does not "
                             f"match the requested candidate {identity!r}"),
                    dry_run=dry_run)
            card_name = card.first.locator(".name").inner_text()
            panel_name = panel.locator("#detail-name").inner_text()
            if card_name and panel_name and card_name != panel_name:
                return GreetingOutcome(
                    state="IDENTITY_MISMATCH", identity=identity,
                    message="detail panel candidate name does not match the card",
                    dry_run=dry_run)
            controls = panel.locator("#greet-button")
            if controls.count() != 1:
                return GreetingOutcome(
                    state="PAGE_UNCERTAIN", identity=identity,
                    message="greet control is not uniquely present inside the "
                            "verified detail panel",
                    dry_run=dry_run)
            controls.wait_for(state="visible", timeout=5000)
            return GreetingOutcome(
                state="DRY_RUN_READY", identity=identity,
                message="greet control re-located inside the verified candidate "
                        "panel; nothing was sent",
                dry_run=True, recorded=False)
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001 - DOM/timeout/closed page
            raise BrowserError("PAGE_CHANGED",
                               f"greeting control could not be verified: {exc}",
                               recoverable=True) from exc
