"""Playwright adapter against the local Mock recruitment page (Edge channel)."""
from __future__ import annotations

import json

from browser.adapter import BrowserAdapter
from browser.contracts import (BrowserError, BrowserHealth, CandidateCard,
                               CandidateDetail, GreetingOutcome)
from browser.mock_page import build_html


_SHARED = {"playwright": None, "refs": 0}


def _acquire_playwright():
    if _SHARED["playwright"] is None:
        from playwright.sync_api import sync_playwright
        _SHARED["playwright"] = sync_playwright().start()
    _SHARED["refs"] += 1
    return _SHARED["playwright"]


def _release_playwright():
    if _SHARED["refs"] > 0:
        _SHARED["refs"] -= 1
    if _SHARED["refs"] == 0 and _SHARED["playwright"] is not None:
        try:
            _SHARED["playwright"].stop()
        finally:
            _SHARED["playwright"] = None


class PlaywrightBossAdapter(BrowserAdapter):
    """Uses the locally installed Edge (channel) to avoid shipping Chromium.

    Selectors target ``browser/mock_page.py`` only; real BOSS selectors are
    unverified and deliberately not guessed in this batch.
    """

    def __init__(self, cards: list[dict], *, page_error: bool = False,
                 login_required: bool = False, greeting_confirm: bool = True,
                 channel: str = "msedge", headless: bool = True):
        self.cards = cards
        self.page_error = page_error
        self.login_required = login_required
        self.greeting_confirm = greeting_confirm
        self.channel = channel
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._page = None

    def connect(self) -> BrowserHealth:
        self._playwright = _acquire_playwright()
        try:
            self._browser = self._playwright.chromium.launch(
                channel=self.channel, headless=self.headless)
            self._page = self._browser.new_page()
            self._page.set_content(build_html(
                self.cards, page_error=self.page_error,
                login_required=self.login_required,
                greeting_confirm=self.greeting_confirm))
        except Exception as exc:
            self._stop()
            raise BrowserError("PAGE_UNAVAILABLE",
                               f"could not launch {self.channel}: {exc}",
                               recoverable=False) from exc
        return self.health()

    def _stop(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._browser = None
            self._page = None
            if self._playwright is not None:
                _release_playwright()
                self._playwright = None

    def close(self) -> None:
        self._stop()

    def health(self) -> BrowserHealth:
        connected = self._page is not None
        return BrowserHealth(connected=connected, browser=self.channel,
                             message="ready" if connected else "disconnected")

    def list_cards(self, page: int = 0, page_size: int = 20) -> list[CandidateCard]:
        if self._page is None:
            raise BrowserError("PAGE_UNAVAILABLE", "adapter is not connected")
        if self._page.locator("#login-required").is_visible():
            raise BrowserError("LOGIN_REQUIRED", "recruiter login is required",
                               recoverable=False)
        if self._page.locator("#page-error").is_visible():
            raise BrowserError("PAGE_UNAVAILABLE", "recommendation page failed to load")
        size = page_size or 20
        start, end = page * size, page * size + size
        cards = self._page.locator(".candidate-card").all()
        result = []
        for index, element in enumerate(cards):
            if not start <= index < end:
                continue
            result.append(CandidateCard(
                identity=element.get_attribute("data-geek-id") or "",
                name=element.locator(".name").inner_text(),
                position=element.locator(".position").inner_text(),
                job_id="",
                dom={"card_index": index}))
        return result

    def open_detail(self, identity: str) -> CandidateDetail:
        if self._page is None:
            raise BrowserError("PAGE_UNAVAILABLE", "adapter is not connected")
        card = self._page.locator(f'.candidate-card[data-geek-id="{identity}"]')
        if card.count() == 0:
            raise BrowserError("NOT_FOUND", "candidate is no longer visible")
        card.first.locator(".open-detail").click()
        self._page.locator("#detail-panel").wait_for(state="visible", timeout=5000)
        detail_identity = self._page.locator("#detail-panel").get_attribute("data-geek-id") or ""
        if detail_identity != identity:
            raise BrowserError("IDENTITY_MISMATCH", "detail identity differs from card",
                               recoverable=False)
        raw = json.loads(self._page.locator("#detail-resume").inner_text())
        return CandidateDetail(identity=identity,
                               name=self._page.locator("#detail-name").inner_text(),
                               job_id="", raw=raw)

    def send_greeting(self, identity: str, message: str, *,
                      dry_run: bool = True) -> GreetingOutcome:
        if self._page is None:
            raise BrowserError("PAGE_UNAVAILABLE", "adapter is not connected")
        page = self._page
        card = page.locator(f'.candidate-card[data-geek-id="{identity}"]')
        count = card.count()
        if count == 0:
            return GreetingOutcome(state="NOT_FOUND", identity=identity,
                                   message="candidate is no longer visible", dry_run=dry_run)
        if count != 1:
            return GreetingOutcome(state="PAGE_UNCERTAIN", identity=identity,
                                   message="multiple cards carry the same identity",
                                   dry_run=dry_run)
        if (card.first.get_attribute("data-geek-id") or "") != identity:
            return GreetingOutcome(state="IDENTITY_MISMATCH", identity=identity,
                                   message="card identity attribute does not match", dry_run=dry_run)
        if not dry_run:
            return GreetingOutcome(state="UNSUPPORTED", identity=identity,
                                   message="real sending is disabled in this batch",
                                   dry_run=False)
        panel = page.locator("#detail-panel")
        if panel.is_visible():
            active = panel.get_attribute("data-geek-id") or ""
            if active != identity:
                return GreetingOutcome(
                    state="IDENTITY_MISMATCH", identity=identity,
                    message=f"active detail panel belongs to {active or 'unknown'!s}",
                    dry_run=dry_run)
        card.first.locator(".open-detail").click()
        panel.wait_for(state="visible", timeout=5000)
        if (panel.get_attribute("data-geek-id") or "") != identity:
            return GreetingOutcome(state="IDENTITY_MISMATCH", identity=identity,
                                   message="detail panel identity does not match",
                                   dry_run=dry_run)
        controls = panel.locator("#greet-button")
        if controls.count() != 1:
            return GreetingOutcome(state="PAGE_UNCERTAIN", identity=identity,
                                   message="greet control is not uniquely present in the "
                                           "verified panel", dry_run=dry_run)
        controls.wait_for(state="visible", timeout=5000)
        return GreetingOutcome(state="DRY_RUN_READY", identity=identity,
                               message="greet control re-located inside the verified "
                                       "candidate panel; nothing was sent",
                               dry_run=True, recorded=False)
