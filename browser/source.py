"""Browser data-source configuration, independent of the model run mode.

``APP_RUN_MODE`` (deterministic/live) selects how policies are compiled and how
Tier 2/3 are scored.  ``BROWSER_SOURCE`` selects where candidate pages come from:

- ``disabled``     no browser routes are mounted;
- ``mock``         in-memory MockBossAdapter (no Playwright, no Edge);
- ``cdp_mock``     local Mock DOM rendered in a real Edge over CDP (9222);
- ``boss_readonly`` real BOSS pages, read-only; requires explicit authorisation.

The two settings are deliberately decoupled: deterministic scoring may use
``cdp_mock`` and live scoring may use ``mock``/``cdp_mock``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BROWSER_SOURCES = ("disabled", "mock", "cdp_mock", "boss_readonly")
DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222
# Explicit opt-in for real BOSS pages; without it boss_readonly is downgraded.
REAL_BOSS_AUTH_ENV = "BROWSER_ALLOW_REAL_BOSS"


def default_source_for(mode: str) -> str:
    """Deterministic demos keep working; live deployments default to disabled."""
    return "mock" if mode == "deterministic" else "disabled"


def _dedicated_profile_dir(port: int) -> str:
    """A private profile under LOCALAPPDATA (never the daily Edge profile)."""
    base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_DATA_HOME") or os.getenv("TEMP") or "."
    return str(Path(base) / "boss-hr-helper" / "edge-profile" / f"port-{port}")


@dataclass(frozen=True)
class BrowserSourceConfig:
    name: str
    enabled: bool
    real_boss_allowed: bool = False
    host: str = DEFAULT_CDP_HOST
    port: int = DEFAULT_CDP_PORT
    headless: bool = True
    auto_launch: bool = True
    user_data_dir: str = ""
    channel: str = "msedge"
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def describe(self) -> dict:
        return {
            "source": self.name,
            "enabled": self.enabled,
            "real_boss_allowed": self.real_boss_allowed,
            "cdp_endpoint": self.endpoint if self.name in ("cdp_mock", "boss_readonly") else None,
            "headless": self.headless,
            "auto_launch": self.auto_launch,
            "profile_isolated": bool(self.user_data_dir),
            "real_contract_verified": False,
            "real_pii_storage_ready": False,
            "channel": self.channel,
            "diagnostics": list(self.diagnostics),
        }


def _flag(env, name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def load_browser_source(mode: str, *, environ=None) -> BrowserSourceConfig:
    """Resolve ``BROWSER_SOURCE`` for a model mode and record startup diagnostics."""
    env = os.environ if environ is None else environ
    raw = (env.get("BROWSER_SOURCE") or "").strip().lower()
    diagnostics: list[str] = []
    if not raw:
        raw = default_source_for(mode)
        diagnostics.append(f"BROWSER_SOURCE unset; defaulted to {raw!r} for {mode} mode")
    if raw not in BROWSER_SOURCES:
        raise ValueError(
            f"BROWSER_SOURCE must be one of {BROWSER_SOURCES}, got {raw!r}")
    if raw == "disabled":
        diagnostics.append("browser routes are disabled; scanning returns 404")
        return BrowserSourceConfig(name="disabled", enabled=False,
                                   diagnostics=tuple(diagnostics))
    if raw == "mock":
        diagnostics.append("in-memory MockBossAdapter; no browser process is used")
        return BrowserSourceConfig(name="mock", enabled=True,
                                   diagnostics=tuple(diagnostics))

    host = (env.get("BROWSER_CDP_HOST") or DEFAULT_CDP_HOST).strip()
    try:
        port = int(env.get("BROWSER_CDP_PORT") or DEFAULT_CDP_PORT)
    except ValueError as exc:
        raise ValueError("BROWSER_CDP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("BROWSER_CDP_PORT must be in [1,65535]")
    profile = (env.get("BROWSER_USER_DATA_DIR") or "").strip() or _dedicated_profile_dir(port)
    headless = _flag(env, "BROWSER_HEADLESS", True)
    auto_launch = _flag(env, "BROWSER_CDP_AUTOLAUNCH", True)
    channel = (env.get("BROWSER_CHANNEL") or "msedge").strip() or "msedge"
    diagnostics.append(f"CDP endpoint {host}:{port}; profile {profile}")
    diagnostics.append("browser operations use one dedicated worker thread (Playwright sync affinity)")

    if raw == "boss_readonly":
        allowed = _flag(env, REAL_BOSS_AUTH_ENV, False)
        if not allowed:
            diagnostics.append(
                f"boss_readonly requires explicit authorisation ({REAL_BOSS_AUTH_ENV}=1); "
                "downgraded to 'disabled' so no real session is opened")
            return BrowserSourceConfig(name="disabled", enabled=False,
                                       real_boss_allowed=False, host=host, port=port,
                                       headless=headless, auto_launch=auto_launch,
                                       user_data_dir=profile, channel=channel,
                                       diagnostics=tuple(diagnostics))
        diagnostics.append(
            "read-only real BOSS source authorised: connect/page-list only; "
            "real DOM contracts are unverified so scanning stays UNSUPPORTED")
        return BrowserSourceConfig(name="boss_readonly", enabled=True,
                                   real_boss_allowed=True, host=host, port=port,
                                   headless=headless, auto_launch=auto_launch,
                                   user_data_dir=profile, channel=channel,
                                   diagnostics=tuple(diagnostics))

    diagnostics.append("local Mock DOM rendered in Edge via CDP; no real BOSS session")
    return BrowserSourceConfig(name="cdp_mock", enabled=True, host=host, port=port,
                               headless=headless, auto_launch=auto_launch,
                               user_data_dir=profile, channel=channel,
                               diagnostics=tuple(diagnostics))
