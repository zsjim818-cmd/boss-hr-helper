"""Per-HR browser session registry (CDP runtime pooling).

Business rule: two different HR accounts may contact the same candidate, and
each HR sends from their own BOSS account.  Therefore browser sessions are
**not** shared by default: every ``(org_id, actor_user_id)`` maps to a browser
identity and gets its own CDP port plus its own dedicated profile directory.

The BOSS ``browser_account_id`` is deliberately modelled separately from the
system ``actor_user_id``.  In the local/Mock phase the mapping is simulated by
server-side configuration; real account bindings would need an approved 011+
migration (not implemented here).
"""
from __future__ import annotations

import hashlib
import re
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

from browser.contracts import BrowserError
from browser.runtime import BrowserRuntime
from browser.source import BrowserSourceConfig


def _safe(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:48]
    return cleaned or "user"


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@dataclass(frozen=True)
class BrowserSessionBinding:
    org_id: str
    actor_user_id: str
    browser_account_id: str
    cdp_port: int
    user_data_dir: str
    mapping_source: str  # "configured" | "simulated" | "persisted"
    profile_id: str = ""

    def describe(self) -> dict:
        return {
            "org_id": self.org_id,
            "actor_user_id": self.actor_user_id,
            "browser_account_id": self.browser_account_id,
            "cdp_port": self.cdp_port,
            "mapping_source": self.mapping_source,
            "profile_id": self.profile_id,
        }


class BrowserSessionRegistry:
    """Lazily creates one isolated CDP runtime per HR account.

    Isolation guarantees:
    - distinct ``--remote-debugging-port`` per session;
    - distinct dedicated ``--user-data-dir`` per session;
    - the same actor always reuses their own runtime; different actors never do.
    """

    def __init__(self, source: BrowserSourceConfig, *, bindings: dict | None = None,
                 port_base: int | None = None, profile_root: str | None = None,
                 max_port_offset: int = 50, runtime_factory=None, store=None):
        self.source = source
        self._store = store
        self._bindings: dict[tuple[str, str], dict] = {}
        for key, value in (bindings or {}).items():
            if isinstance(key, tuple) and len(key) == 2:
                self._bindings[key] = dict(value)
            elif isinstance(key, str) and ":" in key:
                org, user = key.split(":", 1)
                self._bindings[(org, user)] = dict(value)
            else:
                raise ValueError("bindings keys must be (org_id, user_id) or 'org:user'")
        self.port_base = port_base or source.port
        root = profile_root or str(Path(source.user_data_dir).parent / "sessions"
                                   / f"port-{self.port_base}")
        self.profile_root = Path(root)
        self.max_port_offset = max_port_offset
        self._runtime_factory = runtime_factory or self._default_runtime
        self._lock = threading.RLock()
        self._runtimes: dict[tuple[str, str], BrowserRuntime] = {}
        self._bindings_active: dict[tuple[str, str], BrowserSessionBinding] = {}
        self._reserved_ports: dict[int, tuple[str, str]] = {}

    # -------------------------------------------------------------- bindings
    def _allocate_port(self, key) -> int:
        for offset in range(self.max_port_offset + 1):
            port = self.port_base + offset
            if port in self._reserved_ports:
                continue
            if _port_free(port):
                self._reserved_ports[port] = key
                return port
        raise BrowserError("BROWSER_UNAVAILABLE",
                           "no free CDP port available for a new HR session",
                           recoverable=True)

    def _binding_for(self, context) -> BrowserSessionBinding:
        key = (context.org_id, context.user_id)
        with self._lock:
            existing = self._bindings_active.get(key)
            if existing is not None:
                return existing
            configured = self._bindings.get(key) or {}
            persisted = None
            if self._store is not None:
                try:
                    persisted = self._store.get_browser_binding(context)
                except Exception:
                    persisted = None
            if persisted is not None and persisted.get("status") != "ACTIVE":
                raise BrowserError(
                    "BROWSER_ACCOUNT_DISABLED",
                    f"browser account binding for {key[0]}:{key[1]} is disabled",
                    recoverable=False)

            account = str(configured.get("browser_account_id") or "").strip()
            profile_id = str(configured.get("profile_id") or "").strip()
            port = configured.get("cdp_port")
            mapping_source = "configured" if (account or port or profile_id) else ""
            if persisted is not None:
                mapping_source = "configured" if mapping_source else "persisted"
                account = account or str(persisted["browser_account_id"])
                port = port if port is not None else persisted["cdp_port"]
                profile_id = profile_id or str(persisted["profile_id"])
            if not account:
                if self.source.name == "boss_readonly":
                    raise BrowserError(
                        "BROWSER_ACCOUNT_UNMAPPED",
                        f"no BOSS browser account is mapped for {key[0]}:{key[1]}; "
                        "bind it in browser_account_bindings before real collection",
                        recoverable=False)
                digest = hashlib.sha256(f"{key[0]}:{key[1]}".encode()).hexdigest()[:12]
                account = f"mock-account-{digest}"
                mapping_source = "simulated"
            if port is None:
                port = self._allocate_port(key)
            else:
                port = int(port)
                if port in self._reserved_ports and self._reserved_ports[port] != key:
                    raise BrowserError("BROWSER_UNAVAILABLE",
                                       f"configured CDP port {port} is already used "
                                       "by another HR session", recoverable=False)
                self._reserved_ports[port] = key
            profile_dir = configured.get("user_data_dir")
            if profile_dir:
                profile_id = profile_id or _safe(Path(profile_dir).name)
                profile = Path(profile_dir)
            else:
                profile_id = profile_id or (
                    f"{_safe(key[0])}-{_safe(key[1])}-{_safe(account)}")
                profile = self.profile_root / profile_id / f"profile-{port}"
            binding = BrowserSessionBinding(
                org_id=key[0], actor_user_id=key[1], browser_account_id=account,
                cdp_port=int(port), user_data_dir=str(Path(profile).resolve()),
                mapping_source=mapping_source or "simulated", profile_id=profile_id)
            if self._store is not None:
                self._store.upsert_browser_binding(
                    context, browser_account_id=binding.browser_account_id,
                    cdp_port=binding.cdp_port, profile_id=binding.profile_id)
            self._bindings_active[key] = binding
            return binding

    # --------------------------------------------------------------- runtimes
    def _default_runtime(self, binding: BrowserSessionBinding) -> BrowserRuntime:
        source = BrowserSourceConfig(
            name=self.source.name, enabled=True, real_boss_allowed=self.source.real_boss_allowed,
            host=self.source.host, port=binding.cdp_port,
            headless=self.source.headless, auto_launch=self.source.auto_launch,
            user_data_dir=binding.user_data_dir, channel=self.source.channel,
            diagnostics=self.source.diagnostics)
        return BrowserRuntime(source)

    def runtime_for(self, context) -> BrowserRuntime:
        key = (context.org_id, context.user_id)
        with self._lock:
            runtime = self._runtimes.get(key)
            if runtime is not None:
                return runtime
            binding = self._binding_for(context)
            runtime = self._runtime_factory(binding)
            self._runtimes[key] = runtime
            return runtime

    def binding_for(self, context) -> BrowserSessionBinding:
        return self._binding_for(context)

    def describe(self) -> list[dict]:
        """Internal diagnostics; never expose raw local paths via HTTP."""
        with self._lock:
            sessions = []
            for key, binding in sorted(self._bindings_active.items()):
                runtime = self._runtimes.get(key)
                body = binding.describe()
                body["profile_id"] = self._profile_id(binding)
                body["profile_isolated"] = True
                body["connected"] = False
                if runtime is not None:
                    try:
                        health = runtime.manager.health()
                        body["connected"] = health.connected
                        body["profile_verified"] = health.profile_verified
                    except Exception as exc:  # noqa: BLE001 - diagnostics never raise
                        body["health_error"] = str(exc)
                sessions.append(body)
            return sessions

    @staticmethod
    def _profile_id(binding: BrowserSessionBinding) -> str:
        return binding.profile_id

    def describe_for(self, context) -> dict | None:
        """Current caller's session only (least privilege)."""
        key = (context.org_id, context.user_id)
        with self._lock:
            binding = self._bindings_active.get(key)
            if binding is None:
                return None
            runtime = self._runtimes.get(key)
            body = binding.describe()
            body["profile_id"] = self._profile_id(binding)
            body["profile_isolated"] = True
            body["connected"] = False
            if runtime is not None:
                try:
                    health = runtime.manager.health()
                    body["connected"] = health.connected
                    body["profile_verified"] = health.profile_verified
                except Exception as exc:  # noqa: BLE001
                    body["health_error"] = str(exc)
            return body

    def close(self) -> None:
        with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            try:
                runtime.close()
            except Exception:
                pass
