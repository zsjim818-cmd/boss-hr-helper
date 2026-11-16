"""Per-HR browser session state and precheck (zero schema changes).

State is kept in ``app.state`` keyed by ``(org_id, actor_user_id)``.  The browser
itself is owned by ``BrowserSessionRegistry``; this module only exposes a safe
state machine and read-only page facts.
"""
from __future__ import annotations

from datetime import datetime, timezone

from browser.cdp_adapter import CdpMockBossAdapter
from browser.contracts import BrowserError
from browser.mock_adapter import MockBossAdapter
from browser.mock_data import mock_cards


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _key(ctx):
    return (ctx.org_id, ctx.user_id)


def _store(request):
    if not hasattr(request.app.state, "browser_sessions"):
        request.app.state.browser_sessions = {}
    return request.app.state.browser_sessions


def _job_kind(title: str) -> str:
    return ("ai" if ("AI" in title or "自动化" in title)
            else "sales" if "销售" in title else "java")


def _actions(state: str) -> list[str]:
    return {
        "DISCONNECTED": ["connect", "navigate", "recheck"],
        "LOGIN_REQUIRED": ["recheck", "navigate"],
        "CAPTCHA_BLOCKED": ["recheck", "navigate"],
        "READY": ["navigate", "precheck", "scan"],
        "REAL_CONTRACT_UNVERIFIED": ["use_mock", "diagnostics"],
        "DISABLED": ["diagnostics"],
    }.get(state, ["diagnostics"])


def describe_state(request, ctx) -> dict:
    source = request.app.state.browser_source
    stored = _store(request).get(_key(ctx), {})
    base = {
        "source": source.name,
        "source_label": {"mock": "MOCK", "cdp_mock": "CDP_MOCK",
                         "boss_readonly": "BOSS_READONLY",
                         "disabled": "DISABLED"}.get(source.name, source.name.upper()),
        "real_contract_verified": False,
        "real_pii_storage_ready": False,
        "profile_id": None,
        "connected": False,
        "page_title": stored.get("page_title", ""),
        "page_url": stored.get("page_url", ""),
        "iframe_detected": bool(stored.get("iframe_detected", False)),
        "platform_job_id": stored.get("platform_job_id", ""),
        "page_job_id": stored.get("job_id", ""),
        "page_job_title": stored.get("job_title", ""),
        "filter_summary": stored.get("filter_summary", ""),
        "visible_cards": stored.get("visible_cards", 0),
        "checked_at": stored.get("checked_at"),
        "error_code": stored.get("error_code"),
        "error_message": stored.get("error_message"),
    }
    registry = getattr(request.app.state, "browser_session_registry", None)
    if source.name == "disabled":
        base.update(state="DISABLED", actions=_actions("DISABLED"))
        return base
    if source.name == "boss_readonly":
        base.update(state="REAL_CONTRACT_UNVERIFIED",
                    actions=_actions("REAL_CONTRACT_UNVERIFIED"))
        return base
    if source.name == "mock":
        base.update(state="READY" if stored.get("connected") else "DISCONNECTED",
                    connected=bool(stored.get("connected")),
                    browser="mock-in-memory",
                    actions=_actions("READY" if stored.get("connected") else "DISCONNECTED"))
        return base
    # cdp_mock: only health is read here; no browser is launched by GET.
    if registry is not None:
        try:
            binding = registry.binding_for(ctx)
            base["profile_id"] = binding.profile_id
        except BrowserError as exc:
            base.update(state="DISCONNECTED", error_code=exc.code,
                        error_message=str(exc))
            return base
        runtime = registry._runtimes.get(_key(ctx))
        if runtime is not None:
            health = runtime.manager.health()
            base.update(connected=health.connected, browser=health.browser,
                        page_url=base["page_url"] or health.page_url)
        error = stored.get("error_code")
        if error == "AUTH_REQUIRED":
            state = "LOGIN_REQUIRED"
        elif error in ("CAPTCHA_BLOCKED", "AUTH_OR_ANTI_BOT_BLOCKED"):
            state = "CAPTCHA_BLOCKED"
        else:
            state = "READY" if base["connected"] else "DISCONNECTED"
    else:
        state = "DISCONNECTED"
    base.update(state=state, actions=_actions(state))
    return base


def _record_session_error(request, ctx, exc: BrowserError) -> None:
    stored = _store(request).setdefault(_key(ctx), {})
    stored.update(adb_connected=False, error_code=exc.code,
                  error_message=str(exc)[:2000], checked_at=_now())


def connect_session(request, ctx) -> dict:
    source = request.app.state.browser_source
    stored = _store(request).setdefault(_key(ctx), {})
    if source.name == "disabled":
        raise BrowserError("UNSUPPORTED", "browser source is disabled", recoverable=False)
    if source.name == "boss_readonly":
        stored.update(state="REAL_CONTRACT_UNVERIFIED")
        return describe_state(request, ctx)
    if source.name == "mock":
        stored.update(connected=True, checked_at=_now())
        return describe_state(request, ctx)
    registry = request.app.state.browser_session_registry
    runtime = registry.runtime_for(ctx)
    try:
        health = runtime.manager.connect()
    except BrowserError as exc:
        _record_session_error(request, ctx, exc)
        raise
    if not health.connected:
        raise BrowserError("BROWSER_UNAVAILABLE",
                           health.message or "browser did not connect",
                           recoverable=True)
    stored.update(connected=True, checked_at=_now(),
                  error_code=None, error_message=None)
    return describe_state(request, ctx)


def navigate_session(request, ctx, *, job_id: str, repository) -> dict:
    source = request.app.state.browser_source
    stored = _store(request).setdefault(_key(ctx), {})
    job = repository.get_job(ctx, job_id)
    if source.name == "boss_readonly":
        raise BrowserError(
            "REAL_CONTRACT_UNVERIFIED",
            "real BOSS navigation/collection is disabled until an authorised "
            "account and verified DOM/API contract exist",
            recoverable=False)
    if source.name == "disabled":
        raise BrowserError("UNSUPPORTED", "browser source is disabled", recoverable=False)
    kind = _job_kind(job["title"])
    cards = mock_cards(job_id, kind, 3)
    platform_job_id = f"boss-job-{job_id}"
    if source.name == "mock":
        adapter = MockBossAdapter(cards, page_size=5)
        adapter.connect()
        visible = len(adapter.list_cards(0, 100))
        page_url = f"mock://zhipin/recommend/{job_id}"
        page_title = f"{job['title']} · 推荐牛人（Mock）"
    else:
        registry = request.app.state.browser_session_registry
        runtime = registry.runtime_for(ctx)
        adapter = CdpMockBossAdapter(
            runtime.manager,
            cards)
        try:
            adapter.connect()
        except BrowserError as exc:
            _record_session_error(request, ctx, exc)
            raise
        visible = len(adapter.list_cards(0, 100))
        page_url = adapter.page_url
        page_title = f"{job['title']} · 推荐牛人（CDP Mock）"
    stored.update({
        "adapter": adapter,
        "job_id": job_id,
        "job_title": job["title"],
        "page_url": page_url,
        "page_title": page_title,
        "iframe_detected": True,
        "platform_job_id": platform_job_id,
        "filter_summary": "Mock 推荐页：全部可见候选人（无额外筛选）",
        "visible_cards": visible,
        "checked_at": _now(),
        "connected": True,
        "error_code": None,
        "error_message": None,
    })
    return describe_state(request, ctx)


def precheck(request, ctx, *, job_id: str, repository) -> dict:
    source = request.app.state.browser_source
    stored = _store(request).get(_key(ctx), {})
    if source.name in ("disabled", "boss_readonly"):
        raise BrowserError(
            "REAL_CONTRACT_UNVERIFIED" if source.name == "boss_readonly" else "UNSUPPORTED",
            "page precheck is unavailable for this browser source",
            recoverable=False)
    if not stored.get("page_url"):
        raise BrowserError("PAGE_MISMATCH",
                           "no page has been opened for this session; call navigate first",
                           recoverable=True)
    job = repository.get_job(ctx, job_id)
    page_job_id = stored.get("job_id", "")
    page_title = stored.get("job_title", "")
    mismatch = bool(page_job_id and page_job_id != job_id)
    warnings = []
    if mismatch:
        warnings.append(
            f"页面当前岗位为 {page_title!r}，与系统选中岗位 {job['title']!r} 不一致")
    if not stored.get("iframe_detected"):
        warnings.append("未检测到推荐页 iframe；页面可能尚未加载完成")
    return {
        "job_id": job_id,
        "job_title": job["title"],
        "page_job_id": page_job_id,
        "page_job_title": page_title,
        "platform_job_id": stored.get("platform_job_id", ""),
        "filter_summary": stored.get("filter_summary", ""),
        "visible_cards": stored.get("visible_cards", 0),
        "iframe_detected": bool(stored.get("iframe_detected")),
        "job_mismatch": mismatch,
        "warnings": warnings,
        "checked_at": _now(),
        "source": source.name,
    }
