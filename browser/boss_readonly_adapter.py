"""Read-only adapter for the real BOSS recommendation page (local fixtures only).

Activated only when ``BROWSER_SOURCE=boss_readonly`` and
``BROWSER_ALLOW_REAL_BOSS=1``.  It navigates the HR's dedicated Edge session,
preflights login/captcha, reads cards/details from the recommendation iframe and
re-locates the greet control inside the verified candidate panel for dry runs.

It never fills credentials, never solves captchas/sliders and never clicks the
real greet button (``dry_run=False`` stays ``UNSUPPORTED``).
"""
from __future__ import annotations

import json
import time

from browser.adapter import BrowserAdapter
from browser.contracts import (BrowserError, BrowserHealth, CandidateCard,
                               CandidateDetail, GreetingOutcome)
from browser.runtime import CDPConnectionManager

DEFAULT_BOSS_URL = "https://www.zhipin.com/web/chat/recommend"
FRAME_SELECTOR = "iframe#recommendFrame"
CARD_SELECTOR = ".job-card-box"
DETAIL_PANEL = "#detail-panel"
DETAIL_BUTTON = ".open-detail"
GREET_BUTTON = ".btn-greet"
LOGIN_MARKERS = (".login-guide", "#login-guide", ".boss-login-dialog")
CAPTCHA_MARKERS = (".captcha-dialog", "#captcha-dialog", ".geetest_panel")
SLIDER_MARKERS = (".slider-verify", "#slider-verify", ".nc-container")


class BossReadonlyCdpAdapter(BrowserAdapter):
    def __init__(self, manager: CDPConnectionManager, *, page_url: str | None = None,
                 fixture_html: str | None = None, delay_ms: int = 0,
                 max_detail_chars: int = 200_000):
        self.manager = manager
        self.page_url = page_url or DEFAULT_BOSS_URL
        self.fixture_html = fixture_html
        self.delay_ms = max(0, int(delay_ms))
        self.max_detail_chars = max_detail_chars
        self.source = "boss_readonly"
        self._page = None
        self._frame = None
        self._url = ""

    # --------------------------------------------------------------- connect
    def connect(self) -> BrowserHealth:
        health = self.manager.connect()
        if not health.connected:
            raise BrowserError("BROWSER_UNAVAILABLE",
                               health.message or "CDP browser is not connected",
                               recoverable=True)
        url = self.page_url
        if self.fixture_html is not None:
            url = self.manager.write_mock_page(self.fixture_html)
        try:
            self.manager.run(lambda: self._setup_page(url))
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001 - DOM/navigation failures
            raise BrowserError("PAGE_UNAVAILABLE",
                               f"BOSS recommendation page is unavailable: {exc}",
                               recoverable=True) from exc
        return self.health()

    def _setup_page(self, url: str) -> None:
        self._page = self.manager._open_page_impl(url, url, navigate=False)
        self._url = self._page.url
        self._frame = self._page.frame_locator(FRAME_SELECTOR)
        self._preflight()
        # Wait for the recommendation iframe to render its list.
        self._frame.locator(CARD_SELECTOR).first.wait_for(
            state="attached", timeout=15_000)

    def _preflight(self) -> None:
        scopes = [self._page]
        if self._frame is not None:
            scopes.append(self._frame)
        for scope in scopes:
            for selector in LOGIN_MARKERS:
                try:
                    if scope.locator(selector).count() and \
                            scope.locator(selector).first.is_visible():
                        raise BrowserError(
                            "AUTH_REQUIRED",
                            "BOSS recruiter login is required; complete the QR "
                            "login manually",
                            recoverable=True)
                except BrowserError:
                    raise
                except Exception:
                    continue
            for selector in CAPTCHA_MARKERS + SLIDER_MARKERS:
                try:
                    if scope.locator(selector).count() and \
                            scope.locator(selector).first.is_visible():
                        raise BrowserError(
                            "CAPTCHA_BLOCKED",
                            "BOSS security verification/slider is blocking the page; "
                            "resolve it manually and retry",
                            recoverable=False)
                except BrowserError:
                    raise
                except Exception:
                    continue

    def close(self) -> None:
        self._page = None
        self._frame = None

    def probe_state(self) -> str:
        """Read login/captcha state without clicking or waiting for cards."""
        return self.manager.run_with_recovery(self._probe_state_impl)

    def _probe_state_impl(self) -> str:
        self._page = self.manager._open_page_impl(
            self.page_url, self.page_url, navigate=False)
        self._url = self._page.url
        self._frame = self._page.frame_locator(FRAME_SELECTOR)
        try:
            self._preflight()
        except BrowserError as exc:
            if exc.code == "AUTH_REQUIRED":
                return "LOGIN_REQUIRED"
            if exc.code in ("CAPTCHA_BLOCKED", "AUTH_OR_ANTI_BOT_BLOCKED"):
                return "CAPTCHA_BLOCKED"
            raise
        return "READY"

    def health(self) -> BrowserHealth:
        health = self.manager.health()
        return health.model_copy(update={"source": self.source,
                                         "page_url": self._url})

    def _require_frame(self):
        if self._frame is None:
            raise BrowserError("BROWSER_CLOSED",
                               "BOSS recommendation page is not connected",
                               recoverable=True)
        return self._frame

    def _pause(self) -> None:
        if self.delay_ms:
            time.sleep(self.delay_ms / 1000.0)

    # -------------------------------------------------------------------- dom
    def list_cards(self, page: int = 0, page_size: int = 20) -> list[CandidateCard]:
        return self.manager.run_with_recovery(
            lambda: self._list_cards_impl(page, page_size))

    def _list_cards_impl(self, page: int, page_size: int) -> list[CandidateCard]:
        frame = self._require_frame()
        try:
            self._preflight()
            size = page_size or 20
            start, end = page * size, page * size + size
            cards = frame.locator(CARD_SELECTOR).all()
            result = []
            for index, element in enumerate(cards):
                if not start <= index < end:
                    continue
                identity = (element.get_attribute("data-geek-id") or "").strip()
                if not identity:
                    raise BrowserError("IDENTITY_MISMATCH",
                                       "candidate card has no stable data-geek-id",
                                       recoverable=False)
                result.append(CandidateCard(
                    identity=identity,
                    name=element.locator(".name").inner_text().strip(),
                    position=element.locator(".expect").inner_text().strip(),
                    job_id="",
                    platform_job_id=element.get_attribute("data-encrypt-job-id") or "",
                    degree=element.get_attribute("data-degree") or "",
                    work_years=element.get_attribute("data-work-years") or "",
                    dom={"card_index": index, "page_url": self._url}))
                self._pause()
            return result
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BrowserError("PAGE_UNAVAILABLE",
                               f"BOSS recommendation list could not be read: {exc}",
                               recoverable=True) from exc

    def open_detail(self, identity: str) -> CandidateDetail:
        return self.manager.run_with_recovery(
            lambda: self._open_detail_impl(identity))

    def _open_detail_impl(self, identity: str) -> CandidateDetail:
        frame = self._require_frame()
        try:
            self._preflight()
            card = frame.locator(f'{CARD_SELECTOR}[data-geek-id="{identity}"]')
            if card.count() != 1:
                raise BrowserError("NOT_FOUND",
                                   "candidate card is missing or ambiguous")
            card.first.locator(DETAIL_BUTTON).click()
            panel = frame.locator(DETAIL_PANEL)
            panel.wait_for(state="visible", timeout=10_000)
            panel_identity = (panel.get_attribute("data-geek-id") or "").strip()
            if panel_identity != identity:
                raise BrowserError(
                    "IDENTITY_MISMATCH",
                    f"detail panel belongs to {panel_identity!r}, not {identity!r}",
                    recoverable=False)
            name = panel.locator("#detail-name").inner_text().strip()
            raw_text = panel.locator("#detail-resume").inner_text()
            if len(raw_text) > self.max_detail_chars:
                raise BrowserError("UNEXPECTED_STATE",
                                   "detail payload exceeds the safety limit",
                                   recoverable=True)
            try:
                raw = json.loads(raw_text)
            except ValueError as exc:
                raise BrowserError("UNEXPECTED_STATE",
                                   f"detail payload is not valid JSON: {exc}",
                                   recoverable=True) from exc
            self._pause()
            return CandidateDetail(identity=identity, name=name, job_id="",
                                   platform_job_id=card.first.get_attribute(
                                       "data-encrypt-job-id") or "",
                                   raw=raw)
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BrowserError("PAGE_CHANGED",
                               f"BOSS detail panel could not be read: {exc}",
                               recoverable=True) from exc

    # --------------------------------------------------------------- greeting
    def send_greeting(self, identity: str, message: str, *,
                      dry_run: bool = True) -> GreetingOutcome:
        return self.manager.run_with_recovery(
            lambda: self._send_greeting_impl(identity, message, dry_run))

    def _send_greeting_impl(self, identity: str, message: str,
                            dry_run: bool) -> GreetingOutcome:
        frame = self._require_frame()
        try:
            card = frame.locator(f'{CARD_SELECTOR}[data-geek-id="{identity}"]')
            if card.count() != 1:
                return GreetingOutcome(state="NOT_FOUND", identity=identity,
                                       message="candidate card is missing or ambiguous",
                                       dry_run=dry_run)
            if not dry_run:
                return GreetingOutcome(
                    state="UNSUPPORTED", identity=identity,
                    message="real sending is disabled; this adapter is read-only",
                    dry_run=False)
            panel = frame.locator(DETAIL_PANEL)
            if panel.is_visible():
                panel_identity = (panel.get_attribute("data-geek-id") or "").strip()
                if panel_identity != identity:
                    return GreetingOutcome(
                        state="IDENTITY_MISMATCH", identity=identity,
                        message=(f"active detail panel belongs to {panel_identity!r}, "
                                 f"not {identity!r}"),
                        dry_run=dry_run)
            else:
                card.first.locator(DETAIL_BUTTON).click()
                panel.wait_for(state="visible", timeout=10_000)
                if (panel.get_attribute("data-geek-id") or "").strip() != identity:
                    return GreetingOutcome(
                        state="IDENTITY_MISMATCH", identity=identity,
                        message="detail panel identity does not match the card",
                        dry_run=dry_run)
            controls = panel.locator(GREET_BUTTON)
            if controls.count() != 1:
                return GreetingOutcome(
                    state="PAGE_UNCERTAIN", identity=identity,
                    message="greet control is not uniquely present in the verified panel",
                    dry_run=dry_run)
            control = controls.first
            if not control.is_visible() or control.is_disabled() \
                    or "上限" in control.inner_text():
                return GreetingOutcome(
                    state="PAGE_UNCERTAIN", identity=identity,
                    message="greet control is disabled or the daily limit is reached",
                    dry_run=dry_run)
            clicked = self._page.evaluate("() => window.__BOSS_GREET_CLICKED__ === true")
            if clicked:
                return GreetingOutcome(
                    state="PAGE_UNCERTAIN", identity=identity,
                    message="greet button was clicked elsewhere; refusing the rehearsal",
                    dry_run=dry_run)
            return GreetingOutcome(
                state="DRY_RUN_READY", identity=identity,
                message="BOSS greet control re-located inside the verified panel; "
                        "nothing was sent",
                dry_run=True, recorded=False)
        except BrowserError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BrowserError("PAGE_CHANGED",
                               f"BOSS greet control could not be verified: {exc}",
                               recoverable=True) from exc
