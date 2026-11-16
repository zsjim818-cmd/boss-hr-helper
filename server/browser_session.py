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


def _runtime(request, ctx):
    registry = getattr(request.app.state, "browser_session_registry", None)
    if registry is not None:
        return registry.runtime_for(ctx)
    return getattr(request.app.state, "browser_runtime", None)


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
        if not source.real_boss_dom_allowed:
            base.update(state="REAL_CONTRACT_UNVERIFIED",
                        actions=_actions("REAL_CONTRACT_UNVERIFIED"))
            return base
        runtime = _runtime(request, ctx)
        if runtime is None:
            base.update(state="DISCONNECTED", actions=_actions("DISCONNECTED"))
            return base
        if registry is not None:
            try:
                base["profile_id"] = registry.binding_for(ctx).profile_id
            except BrowserError:
                pass
        health = runtime.manager.health()
        base.update(connected=health.connected, browser=health.browser)
        if health.connected:
            from browser.boss_readonly_adapter import BossReadonlyCdpAdapter
            try:
                state = BossReadonlyCdpAdapter(
                    runtime.manager, page_url=source.boss_url).probe_state()
                stored.update(error_code=None, error_message=None)
            except BrowserError as exc:
                if exc.code == "AUTH_REQUIRED":
                    state = "LOGIN_REQUIRED"
                elif exc.code in ("CAPTCHA_BLOCKED",
                                  "AUTH_OR_ANTI_BOT_BLOCKED"):
                    state = "CAPTCHA_BLOCKED"
                else:
                    state = "DISCONNECTED"
                _record_session_error(request, ctx, exc)
            base["error_code"] = stored.get("error_code")
            base["error_message"] = stored.get("error_message")
        else:
            state = "DISCONNECTED"
        base.update(state=state, actions=_actions(state))
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
    stored.update(connected=False, error_code=exc.code,
                  error_message=str(exc)[:2000], checked_at=_now())


def _open_boss_readonly(request, ctx, source, *, job_id=None, repository=None,
                        navigate=False) -> dict:
    """Open the configured real BOSS page read-only and persist safe page facts.

    Login/captcha is reported as a state, never bypassed.  No greet/send control
    is clicked here.
    """
    from browser.boss_readonly_adapter import BossReadonlyCdpAdapter
    runtime = _runtime(request, ctx)
    if runtime is None:
        raise BrowserError("BROWSER_UNAVAILABLE",
                           "no CDP runtime is configured for boss_readonly",
                           recoverable=True)
    health = runtime.manager.connect()
    if not health.connected:
        raise BrowserError("BROWSER_UNAVAILABLE",
                           health.message or "browser did not connect",
                           recoverable=True)
    job_title = ""
    if job_id and repository is not None:
        job_title = repository.get_job(ctx, job_id)["title"]
    stored = _store(request).setdefault(_key(ctx), {})
    adapter = BossReadonlyCdpAdapter(runtime.manager, page_url=source.boss_url)
    visible, page_url, page_title, iframe = 0, source.boss_url, "", False
    error = None
    if navigate:
        try:
            probe = adapter.probe_state()
        except BrowserError:
            probe = "DISCONNECTED"
        if (probe == "READY"
                and not (adapter._url or "").startswith(source.boss_url)):
            runtime.manager.run(lambda: adapter._page.goto(
                source.boss_url, wait_until="load", timeout=15_000))
    try:
        adapter.connect()
        visible = len(adapter.list_cards(0, 100))
        page_url = adapter._url or source.boss_url
        page_title = runtime.manager.run(lambda: adapter._page.title())
        iframe = True
    except BrowserError as exc:
        if exc.code not in ("AUTH_REQUIRED", "CAPTCHA_BLOCKED",
                            "AUTH_OR_ANTI_BOT_BLOCKED"):
            raise
        error = exc
    finally:
        adapter.close()
    stored.update({
        "connected": True,
        "job_id": job_id or stored.get("job_id", ""),
        "job_title": job_title or stored.get("job_title", ""),
        "page_url": page_url,
        "page_title": page_title,
        "iframe_detected": iframe,
        "platform_job_id": "",
        "filter_summary": "真实 BOSS 推荐页（只读；筛选标签需人工核对）",
        "visible_cards": visible,
        "checked_at": _now(),
        "error_code": error.code if error else None,
        "error_message": str(error)[:2000] if error else None,
    })
    return describe_state(request, ctx)


def connect_session(request, ctx, *, job_id=None, repository=None) -> dict:
    source = request.app.state.browser_source
    stored = _store(request).setdefault(_key(ctx), {})
    if source.name == "disabled":
        raise BrowserError("UNSUPPORTED", "browser source is disabled", recoverable=False)
    if source.name == "boss_readonly":
        if not source.real_boss_dom_allowed:
            stored.update(state="REAL_CONTRACT_UNVERIFIED")
            return describe_state(request, ctx)
        return _open_boss_readonly(request, ctx, source, job_id=job_id,
                                   repository=repository)
    if source.name == "mock":
        stored.update(connected=True, checked_at=_now())
        if job_id and repository is not None:
            return navigate_session(request, ctx, job_id=job_id,
                                    repository=repository)
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
    if job_id and repository is not None:
        # One-click summon: turn the launched about:blank window into the Mock
        # recommendation page instead of leaving a blank Edge window behind.
        try:
            return navigate_session(request, ctx, job_id=job_id,
                                    repository=repository)
        except BrowserError as exc:
            _record_session_error(request, ctx, exc)
            raise
    return describe_state(request, ctx)


def navigate_session(request, ctx, *, job_id: str, repository) -> dict:
    source = request.app.state.browser_source
    stored = _store(request).setdefault(_key(ctx), {})
    job = repository.get_job(ctx, job_id)
    if source.name == "boss_readonly":
        if not source.real_boss_dom_allowed:
            raise BrowserError(
                "REAL_CONTRACT_UNVERIFIED",
                "real BOSS navigation is locked: set BROWSER_ALLOW_REAL_BOSS=1 "
                "and BROWSER_ALLOW_REAL_BOSS_DOM=1 for read-only joint debugging",
                recoverable=False)
        return _open_boss_readonly(request, ctx, source, job_id=job_id,
                                   repository=repository, navigate=True)
    if source.name == "disabled":
        raise BrowserError("UNSUPPORTED", "browser source is disabled", recoverable=False)
    kind = _job_kind(job["title"])
    cards = mock_cards(job_id, kind, 3)
    platform_job_id = f"boss-job-{job_id}"
    if source.name == "mock":
        adapter = MockBossAdapter(cards, page_size=5)
        adapter.connect()
        visible = len(adapter.list_cards(0, 100))
        adapter.close()
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
            visible = len(adapter.list_cards(0, 100))
            page_url = adapter.page_url
        except BrowserError as exc:
            _record_session_error(request, ctx, exc)
            raise
        finally:
            adapter.close()
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


def precheck(request, ctx, *, job_id: str, repository, batch_id: str | None = None) -> dict:
    source = request.app.state.browser_source
    stored = _store(request).get(_key(ctx), {})
    if source.name == "disabled":
        raise BrowserError("UNSUPPORTED", "page precheck is unavailable for this browser source",
                           recoverable=False)
    if source.name == "boss_readonly":
        if not source.real_boss_dom_allowed:
            raise BrowserError(
                "REAL_CONTRACT_UNVERIFIED",
                "real BOSS precheck is locked until BROWSER_ALLOW_REAL_BOSS=1 "
                "and BROWSER_ALLOW_REAL_BOSS_DOM=1",
                recoverable=False)
        from browser.boss_readonly_adapter import BossReadonlyCdpAdapter
        runtime = _runtime(request, ctx)
        if runtime is None:
            raise BrowserError("BROWSER_UNAVAILABLE",
                               "no CDP runtime for boss_readonly", recoverable=True)
        probe = BossReadonlyCdpAdapter(
            runtime.manager, page_url=source.boss_url).probe_state()
        if probe == "LOGIN_REQUIRED":
            raise BrowserError("AUTH_REQUIRED",
                               "BOSS recruiter login is required; complete it manually",
                               recoverable=True)
        if probe == "CAPTCHA_BLOCKED":
            raise BrowserError("CAPTCHA_BLOCKED",
                               "BOSS security verification is blocking the page",
                               recoverable=False)
        if probe != "READY":
            raise BrowserError("PAGE_UNAVAILABLE",
                               "BOSS recommendation page is not ready",
                               recoverable=True)
    if not stored.get("page_url"):
        raise BrowserError("PAGE_MISMATCH",
                           "no page has been opened for this session; call navigate first",
                           recoverable=True)
    if source.name == "cdp_mock":
        # Precheck is read-only, but a manually closed tab must be healed here
        # instead of reporting stale facts for a page that no longer exists.
        registry = getattr(request.app.state, "browser_session_registry", None)
        if registry is not None:
            try:
                registry.runtime_for(ctx).manager.page_for(
                    stored["page_url"])
            except BrowserError as exc:
                _record_session_error(request, ctx, exc)
                raise
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
    if source.name == "boss_readonly":
        warnings.append("真实 BOSS 页面为只读联调模式；请人工核对当前页面岗位与筛选标签")
    confirmation = {}
    if batch_id:
        batch = repository.get_scan_batch(ctx, job_id, batch_id)
        confirmation = {
            "batch_id": batch["batch_id"],
            "confirmed": bool(batch["filters_confirmed_at"]),
            "confirmed_at": batch["filters_confirmed_at"],
            "confirmed_by": batch["confirmed_by"],
            "confirmed_platform_job_id": batch["platform_job_id"] or "",
            "confirmed_filter_summary_json": batch["filter_summary_json"] or "[]",
        }
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
        **confirmation,
    }


def confirm_precheck(request, ctx, *, job_id, batch_id, platform_job_id,
                     filter_summary, visible_cards, repository):
    """Persist a human confirmation, refusing a page/job mismatch."""
    source = request.app.state.browser_source
    if source.name in ("disabled", "boss_readonly"):
        raise BrowserError(
            "REAL_CONTRACT_UNVERIFIED" if source.name == "boss_readonly" else "UNSUPPORTED",
            "precheck confirmation is unavailable for this browser source",
            recoverable=False)
    stored = _store(request).get(_key(ctx), {})
    if not stored.get("page_url"):
        raise BrowserError("PAGE_MISMATCH",
                           "run the page precheck before confirming",
                           recoverable=True)
    if stored.get("job_id") and stored["job_id"] != job_id:
        raise BrowserError(
            "PAGE_MISMATCH",
            f"page is showing {stored.get('job_title')!r}, not the selected job",
            recoverable=True)
    if (stored.get("platform_job_id") and platform_job_id
            and stored["platform_job_id"] != platform_job_id):
        raise BrowserError(
            "PAGE_MISMATCH",
            "confirmed platform job id differs from the page read-back",
            recoverable=True)
    return repository.confirm_batch_precheck(
        ctx, job_id, batch_id, platform_job_id=platform_job_id,
        filter_summary=filter_summary, visible_cards=visible_cards)
