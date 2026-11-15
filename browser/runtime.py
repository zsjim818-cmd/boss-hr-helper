"""CDP browser runtime: dedicated-profile Edge + Playwright single-thread affinity.

Playwright's sync API is bound to the thread that started it, while FastAPI runs
requests in a worker pool.  All CDP calls therefore go through *one* dedicated
worker thread owned by :class:`CDPConnectionManager`; HTTP handlers submit work
to it and never touch Playwright objects from their own thread.

Safety rules (Sprint 4 Batch 2A):
- the CDP endpoint may be an already-running Edge (never launched/closed by us);
- a browser we launch uses a **dedicated** ``--user-data-dir`` and debugging port;
- cookies/tokens are never read or persisted, only page URLs/titles are listed;
- login, sliders and captchas are never automated.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from browser.contracts import BrowserError, BrowserHealth
from browser.source import BrowserSourceConfig, load_browser_source

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _default_edge_profile() -> Path | None:
    local = os.getenv("LOCALAPPDATA")
    if not local:
        return None
    return Path(local) / "Microsoft" / "Edge" / "User Data"


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_edge() -> str:
    override = os.getenv("BROWSER_EDGE_PATH")
    if override:
        return override
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("msedge")
    if found:
        return found
    raise BrowserError("BROWSER_UNAVAILABLE",
                       "Microsoft Edge was not found; set BROWSER_EDGE_PATH",
                       recoverable=True)


@dataclass(frozen=True)
class LaunchPlan:
    command: list[str]
    user_data_dir: str
    port: int

    def describe(self) -> dict:
        return {"port": self.port, "user_data_dir": self.user_data_dir,
                "headless": "--headless=new" in self.command}


class CDPConnectionManager:
    """Owns one CDP connection and serialises every Playwright call."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 9222,
                 user_data_dir: str | None = None, headless: bool = True,
                 auto_launch: bool = True, channel: str = "msedge",
                 edge_path: str | None = None, launch_timeout: float = 20.0):
        self.host = self._loopback_host(host)
        self.port = port
        self.user_data_dir = str(user_data_dir or self.default_profile_dir(port))
        self.headless = headless
        self.auto_launch = auto_launch
        self.channel = channel
        self.edge_path = edge_path
        self.launch_timeout = launch_timeout
        self._validate_profile()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-cdp")
        self._playwright = None
        self._browser = None
        self._process: subprocess.Popen | None = None
        self._owned = False
        self._profile_verified = False
        self._closed = False

    @staticmethod
    def _loopback_host(host: str) -> str:
        """CDP is a local control channel; remote hosts are refused."""
        normalised = (host or "").strip().lower()
        if normalised in ("localhost", "::1", "127.0.0.1"):
            return "127.0.0.1"
        raise ValueError(
            f"CDP host must be a loopback address (127.0.0.1/localhost), got {host!r}")

    # ------------------------------------------------------------- construction
    @staticmethod
    def default_profile_dir(port: int) -> str:
        base = (os.getenv("LOCALAPPDATA") or os.getenv("XDG_DATA_HOME")
                or os.getenv("TEMP") or tempfile.gettempdir())
        return str(Path(base) / "boss-hr-helper" / "edge-profile" / f"port-{port}")

    def _validate_profile(self) -> None:
        """Hard refusal to reuse the user's daily Edge profile."""
        resolved = Path(self.user_data_dir).expanduser().resolve()
        daily = _default_edge_profile()
        if daily is not None and resolved == daily.resolve():
            raise ValueError("Refusing to use the daily Edge profile; configure "
                             "BROWSER_USER_DATA_DIR with a dedicated directory")
        resolved.mkdir(parents=True, exist_ok=True)
        self.user_data_dir = str(resolved)

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def owned(self) -> bool:
        return self._owned

    def launch_plan(self) -> LaunchPlan:
        """Command line for the dedicated test instance (no default profile)."""
        edge = self.edge_path or find_edge()
        command = [
            edge,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--enable-automation",
            "about:blank",
        ]
        if self.headless:
            command.insert(1, "--headless=new")
            command.insert(2, "--disable-gpu")
        return LaunchPlan(command=command, user_data_dir=self.user_data_dir, port=self.port)

    # ------------------------------------------------------------- thread bridge
    def run(self, function):
        """Execute ``function`` on the single CDP worker thread."""
        if self._closed:
            raise BrowserError("BROWSER_CLOSED", "browser runtime has been closed",
                               recoverable=False)
        return self._executor.submit(function).result()

    # --------------------------------------------------------------------- cdp
    def connect(self) -> BrowserHealth:
        return self.run(self._connect_impl)

    def _connect_impl(self) -> BrowserHealth:
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    return self._health_impl()
            except Exception:
                self._browser = None
        started = time.monotonic()
        self._ensure_playwright()
        # Preserve ownership across CDP reconnects: a process we launched may
        # still be alive even though the CDP connection dropped.
        was_owned = (self._owned and self._process is not None
                     and self._process.poll() is None)
        launched = False
        verified = False
        if not _port_open(self.host, self.port):
            if not self.auto_launch:
                self._teardown_impl()
                raise BrowserError(
                    "BROWSER_UNAVAILABLE",
                    f"no CDP endpoint on {self.endpoint} and auto-launch is disabled",
                    recoverable=True)
            self._launch_edge()
            launched = True
            verified = True
        try:
            self._browser = self._connect_with_retry(started)
            if not (launched or was_owned):
                # Attaching to somebody else's browser: prove the Profile.
                verified = self._verify_external_profile()
        except BrowserError:
            self._teardown_impl()
            raise
        except Exception as exc:  # noqa: BLE001 - normalised below
            self._teardown_impl()
            raise BrowserError(
                "CDP_CONNECT_FAILED",
                f"could not attach to {self.endpoint}: {exc}",
                recoverable=True) from exc
        self._owned = launched or was_owned
        self._profile_verified = verified or self._owned
        return self._health_impl()

    # ------------------------------------------------------ profile provenance
    def _cdp_command_line(self) -> list[str] | None:
        """Actual Edge command line via a browser-level CDP session."""
        try:
            session = self._browser.new_browser_cdp_session()
        except Exception:
            return None
        try:
            result = session.send("Browser.getBrowserCommandLine")
            arguments = result.get("arguments") if isinstance(result, dict) else None
            return list(arguments) if isinstance(arguments, list) else None
        except Exception:
            return None

    @staticmethod
    def _profile_from_arguments(arguments: list[str]) -> str | None:
        for argument in arguments:
            if argument.startswith("--user-data-dir="):
                return argument.split("=", 1)[1].strip().strip('"') or None
        return None

    def _devtools_active_port_matches(self) -> bool:
        """Runtime handshake: the active profile's DevToolsActivePort must match.

        Edge writes ``<user-data-dir>/DevToolsActivePort`` with the actual port
        and browser websocket path.  A stale/planted file cannot match both the
        live endpoint's ``/json/version`` payload and our configured port.
        """
        path = Path(self.user_data_dir) / "DevToolsActivePort"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        if len(lines) < 2:
            return False
        try:
            port = int(lines[0].strip())
        except ValueError:
            return False
        if port != self.port:
            return False
        expected_path = lines[1].strip().strip("/")
        if not expected_path:
            return False
        try:
            with urllib.request.urlopen(
                    f"http://{self.host}:{self.port}/json/version", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except Exception:
            return False
        debugger_url = str(payload.get("webSocketDebuggerUrl") or "")
        if not debugger_url:
            return False
        actual_path = urllib.parse.urlparse(debugger_url).path.lstrip("/")
        return bool(actual_path) and actual_path == expected_path

    def _verify_external_profile(self) -> bool:
        """Prove the attached browser uses OUR dedicated profile.

        Trust requires runtime evidence:
        1. the browser's real ``--user-data-dir`` read over CDP; or
        2. the active profile's ``DevToolsActivePort`` handshake matching this
           endpoint's ``/json/version`` websocket path.

        A static file planted on disk is NOT evidence.  Without evidence the
        connection is refused unless ``BROWSER_ALLOW_UNVERIFIED_CDP=1`` is set
        explicitly (then ``profile_verified`` stays False).
        """
        expected = Path(self.user_data_dir).resolve()
        arguments = self._cdp_command_line()
        actual = self._profile_from_arguments(arguments or [])
        if actual:
            try:
                actual_path = Path(actual).expanduser().resolve()
            except OSError:
                actual_path = None
            if actual_path != expected:
                raise BrowserError(
                    "CDP_PROFILE_MISMATCH",
                    f"CDP endpoint {self.endpoint} runs with profile {actual!r}, "
                    f"not the configured dedicated profile {str(expected)!r}",
                    recoverable=False)
            return True
        if self._devtools_active_port_matches():
            return True
        if os.getenv("BROWSER_ALLOW_UNVERIFIED_CDP", "").strip() == "1":
            return False
        raise BrowserError(
            "CDP_PROFILE_UNVERIFIED",
            f"could not verify the profile of {self.endpoint}; refusing to attach. "
            "Provide a matching --user-data-dir or an active DevToolsActivePort "
            "handshake, or explicitly set BROWSER_ALLOW_UNVERIFIED_CDP=1",
            recoverable=False)

    def _connect_with_retry(self, started: float):
        last_error: Exception | None = None
        while True:
            remaining = max(0.5, self.launch_timeout - (time.monotonic() - started))
            try:
                return self._playwright.chromium.connect_over_cdp(
                    self.endpoint, timeout=int(min(10.0, remaining) * 1000))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            if time.monotonic() - started > self.launch_timeout:
                break
            time.sleep(0.25)
        raise BrowserError(
            "CDP_CONNECT_FAILED",
            f"CDP endpoint {self.endpoint} did not accept a connection: {last_error}",
            recoverable=True)

    def _ensure_playwright(self) -> None:
        if self._playwright is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - environment dependent
            raise BrowserError("BROWSER_UNAVAILABLE",
                               f"Playwright Python is not installed: {exc}",
                               recoverable=False) from exc
        self._playwright = sync_playwright().start()

    def _launch_edge(self) -> None:
        plan = self.launch_plan()
        try:
            self._process = subprocess.Popen(
                plan.command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise BrowserError("BROWSER_UNAVAILABLE",
                               f"could not launch Edge: {exc}", recoverable=False) from exc
        deadline = time.monotonic() + self.launch_timeout
        while time.monotonic() < deadline:
            if _port_open(self.host, self.port):
                return
            if self._process.poll() is not None:
                raise BrowserError(
                    "BROWSER_UNAVAILABLE",
                    f"Edge exited during startup (code {self._process.returncode})",
                    recoverable=True)
            time.sleep(0.25)
        raise BrowserError("TIMEOUT",
                           f"Edge did not open CDP port {self.port} in time",
                           recoverable=True)

    def health(self) -> BrowserHealth:
        if self._closed:
            return BrowserHealth(connected=False, browser="edge-cdp",
                                 message="browser runtime has been closed")
        return self.run(self._health_impl)

    def _health_impl(self) -> BrowserHealth:
        if self._browser is None:
            return BrowserHealth(connected=False, browser="edge-cdp",
                                 message=f"not connected to {self.endpoint}",
                                 profile=self.user_data_dir,
                                 profile_verified=False)
        try:
            pages = sum(len(context.pages) for context in self._browser.contexts)
            if not self._browser.is_connected():
                raise RuntimeError("CDP connection was closed by the browser")
        except Exception as exc:  # noqa: BLE001 - connection loss is an error state
            self._browser = None
            return BrowserHealth(connected=False, browser="edge-cdp",
                                 message=f"connection lost: {exc}",
                                 profile=self.user_data_dir,
                                 profile_verified=self._profile_verified)
        return BrowserHealth(connected=True, browser="edge-cdp", pages=pages,
                             message=f"connected to {self.endpoint}",
                             profile=self.user_data_dir,
                             profile_verified=self._profile_verified)

    def list_pages(self) -> list[dict]:
        return self.run(self._list_pages_impl)

    def _list_pages_impl(self) -> list[dict]:
        if self._browser is None:
            raise BrowserError("BROWSER_CLOSED", "not connected to a CDP browser",
                               recoverable=True)
        pages: list[dict] = []
        try:
            for context_index, context in enumerate(self._browser.contexts):
                for page in context.pages:
                    pages.append({"context": context_index, "url": page.url,
                                  "title": page.title()})
        except Exception as exc:  # noqa: BLE001
            raise BrowserError("BROWSER_CLOSED",
                               f"CDP connection lost while listing pages: {exc}",
                               recoverable=True) from exc
        return pages

    # ------------------------------------------------------------- mock pages
    def write_mock_page(self, html: str) -> str:
        """Persist Mock HTML in the runtime's private directory as a file:// URL."""
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
        directory = Path(self.user_data_dir).parent / "mock-pages"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"mock-{digest}.html"
        if not path.is_file():
            path.write_text(html, encoding="utf-8")
        return path.resolve().as_uri()

    def open_page(self, url: str, *, prefix: str | None = None):
        """Open (or reuse) a tab whose URL starts with ``prefix``."""
        return self.run(lambda: self._open_page_impl(url, prefix or url))

    def _open_page_impl(self, url: str, prefix: str, *, navigate: bool = True):
        if self._browser is None:
            raise BrowserError("BROWSER_CLOSED", "not connected to a CDP browser",
                               recoverable=True)
        contexts = list(self._browser.contexts)
        context = contexts[0] if contexts else self._browser.new_context()
        for page in context.pages:
            if page.url.startswith(prefix):
                # A real BOSS tab may carry manually selected filters; only
                # navigate when the caller explicitly asks for it.
                if navigate:
                    page.goto(url, wait_until="load", timeout=15_000)
                return page
        page = context.new_page()
        page.goto(url, wait_until="load", timeout=15_000)
        return page

    # ---------------------------------------------------------------- lifecycle
    def close(self) -> None:
        if self._closed:
            return
        try:
            self.run(self._teardown_impl)
        finally:
            self._closed = True
            self._executor.shutdown(wait=True)

    def _teardown_impl(self) -> None:
        browser, self._browser = self._browser, None
        owned, self._owned = self._owned, False
        process, self._process = self._process, None
        self._profile_verified = False
        if browser is not None:
            try:
                # For connect_over_cdp this disconnects; it does not close the
                # user's browser process.
                browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        if owned and process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - slow shutdown
                process.kill()


class BrowserRuntime:
    """High-level runtime: source config + CDP manager + adapter construction."""

    def __init__(self, source: BrowserSourceConfig,
                 manager: CDPConnectionManager | None = None):
        self.source = source
        self.manager = manager or CDPConnectionManager(
            host=source.host, port=source.port, user_data_dir=source.user_data_dir,
            headless=source.headless, auto_launch=source.auto_launch,
            channel=source.channel)

    @classmethod
    def from_environment(cls, mode: str) -> "BrowserRuntime":
        return cls(load_browser_source(mode))

    def adapter(self, cards: list[dict], **kwargs):
        from browser.cdp_adapter import CdpMockBossAdapter
        return CdpMockBossAdapter(self.manager, cards, **kwargs)

    def diagnose(self) -> dict:
        body = self.source.describe()
        body["connected"] = False
        try:
            health = self.manager.health()
            body["connected"] = health.connected
            body["health"] = health.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 - diagnostics never raise
            body["health"] = {"connected": False, "message": str(exc)}
        return body

    def close(self) -> None:
        self.manager.close()
