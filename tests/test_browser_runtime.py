"""Browser runtime/config unit tests: source decoupling, profile isolation, errors."""
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from browser.contracts import BrowserError
from browser.runtime import (CDPConnectionManager, _default_edge_profile, find_edge)
from browser.source import load_browser_source


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BrowserSourceTests(unittest.TestCase):
    def test_defaults_are_decoupled_from_model_mode(self):
        deterministic = load_browser_source("deterministic", environ={})
        self.assertEqual(deterministic.name, "mock")
        self.assertTrue(deterministic.enabled)
        live = load_browser_source("live", environ={})
        self.assertEqual(live.name, "disabled")
        self.assertFalse(live.enabled)
        self.assertTrue(any("BROWSER_SOURCE unset" in note
                            for note in live.diagnostics))

    def test_cdp_mock_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            source = load_browser_source("deterministic", environ={
                "BROWSER_SOURCE": "cdp_mock", "BROWSER_CDP_PORT": "9333",
                "BROWSER_USER_DATA_DIR": temp, "BROWSER_HEADLESS": "0"})
        self.assertEqual(source.name, "cdp_mock")
        self.assertTrue(source.enabled)
        self.assertEqual(source.endpoint, "http://127.0.0.1:9333")
        self.assertFalse(source.headless)
        self.assertTrue(source.auto_launch)
        self.assertEqual(source.user_data_dir, str(Path(temp).resolve()))
        body = source.describe()
        self.assertEqual(body["source"], "cdp_mock")
        self.assertEqual(body["cdp_endpoint"], "http://127.0.0.1:9333")
        self.assertTrue(body["diagnostics"])

    def test_boss_readonly_requires_explicit_authorisation(self):
        denied = load_browser_source("live", environ={
            "BROWSER_SOURCE": "boss_readonly", "BROWSER_USER_DATA_DIR": "C:/tmp/x"})
        self.assertEqual(denied.name, "disabled")
        self.assertFalse(denied.enabled)
        self.assertFalse(denied.real_boss_allowed)
        self.assertTrue(any("BROWSER_ALLOW_REAL_BOSS" in note
                            for note in denied.diagnostics))

        allowed = load_browser_source("live", environ={
            "BROWSER_SOURCE": "boss_readonly", "BROWSER_ALLOW_REAL_BOSS": "1",
            "BROWSER_USER_DATA_DIR": "C:/tmp/x"})
        self.assertEqual(allowed.name, "boss_readonly")
        self.assertTrue(allowed.enabled)
        self.assertTrue(allowed.real_boss_allowed)
        self.assertTrue(any("UNSUPPORTED" in note for note in allowed.diagnostics))

    def test_invalid_source_and_port_are_rejected(self):
        with self.assertRaises(ValueError):
            load_browser_source("deterministic", environ={"BROWSER_SOURCE": "boss"})
        with self.assertRaises(ValueError):
            load_browser_source("deterministic", environ={
                "BROWSER_SOURCE": "cdp_mock", "BROWSER_CDP_PORT": "not-a-port"})
        with self.assertRaises(ValueError):
            load_browser_source("deterministic", environ={
                "BROWSER_SOURCE": "cdp_mock", "BROWSER_CDP_PORT": "70000"})


class CDPManagerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cdp_runtime_")
        self.addCleanup(self.directory.cleanup)
        self.profile = str(Path(self.directory.name) / "profile")

    def _manager(self, **kwargs):
        options = {"port": free_port(), "user_data_dir": self.profile,
                   "auto_launch": False, "edge_path": r"C:\fake\msedge.exe"}
        options.update(kwargs)
        manager = CDPConnectionManager(**options)
        self.addCleanup(manager.close)
        return manager

    def test_launch_plan_uses_debug_port_and_dedicated_profile(self):
        manager = self._manager(headless=True)
        plan = manager.launch_plan()
        command = " ".join(plan.command)
        self.assertIn(f"--remote-debugging-port={manager.port}", command)
        self.assertIn(f"--user-data-dir={Path(self.profile).resolve()}", command)
        self.assertIn("--headless=new", command)
        self.assertNotIn("Microsoft\\Edge\\User Data", command)

    def test_default_profile_is_never_the_daily_edge_profile(self):
        daily = _default_edge_profile()
        if daily is None:  # pragma: no cover - non-Windows CI
            self.skipTest("no daily Edge profile on this platform")
        with self.assertRaises(ValueError):
            CDPConnectionManager(port=free_port(), user_data_dir=str(daily))
        default = CDPConnectionManager.default_profile_dir(9222)
        self.assertIn("boss-hr-helper", default)
        self.assertNotEqual(Path(default).resolve(), daily.resolve())
        manager = CDPConnectionManager(port=free_port(), user_data_dir=self.profile)
        self.addCleanup(manager.close)
        self.assertNotEqual(Path(manager.user_data_dir).resolve(), daily.resolve())

    def test_unavailable_endpoint_returns_structured_error(self):
        manager = self._manager()
        with self.assertRaises(BrowserError) as captured:
            manager.connect()
        self.assertEqual(captured.exception.code, "BROWSER_UNAVAILABLE")
        self.assertTrue(captured.exception.recoverable)
        health = manager.health()
        self.assertFalse(health.connected)

    def test_disconnected_manager_is_explicit(self):
        manager = self._manager()
        with self.assertRaises(BrowserError) as captured:
            manager.list_pages()
        self.assertEqual(captured.exception.code, "BROWSER_CLOSED")
        manager.close()
        with self.assertRaises(BrowserError):
            manager.run(lambda: None)
        self.assertFalse(manager.health().connected)

    def test_all_calls_use_one_dedicated_worker_thread(self):
        manager = self._manager()
        caller = threading.get_ident()
        identities = {manager.run(threading.get_ident) for _ in range(5)}
        self.assertEqual(len(identities), 1)
        self.assertNotIn(caller, identities)

    def test_port_occupied_by_a_non_cdp_service_is_detected(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        manager = CDPConnectionManager(
            port=port, user_data_dir=self.profile, auto_launch=False,
            edge_path=r"C:\fake\msedge.exe", launch_timeout=1.0)
        self.addCleanup(manager.close)
        self.addCleanup(server.close)
        with self.assertRaises(BrowserError) as captured:
            manager.connect()
        self.assertEqual(captured.exception.code, "CDP_CONNECT_FAILED")

    def test_external_browser_is_disconnected_but_not_killed(self):
        class FakeBrowser:
            def __init__(self):
                self.closed = False
                self.contexts = []

            def is_connected(self):
                return not self.closed

            def close(self):
                self.closed = True

        class FakeProcess:
            def __init__(self):
                self.terminated = False

            def poll(self):
                return 0

            def terminate(self):  # pragma: no cover - only for owned browsers
                self.terminated = True

            def wait(self, timeout=None):  # pragma: no cover
                return 0

            def kill(self):  # pragma: no cover
                self.terminated = True

        manager = self._manager()
        external = FakeBrowser()
        process = FakeProcess()
        manager._browser = external
        manager._process = process
        manager._owned = False
        manager.close()
        self.assertTrue(external.closed)  # CDP disconnect
        self.assertFalse(process.terminated)  # never kills a non-owned process

    def test_owned_browser_process_is_terminated_on_close(self):
        class FakeBrowser:
            def __init__(self):
                self.closed = False
                self.contexts = []

            def is_connected(self):
                return not self.closed

            def close(self):
                self.closed = True

        class FakeProcess:
            def __init__(self):
                self.terminated = False
                self.returncode = None

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):  # pragma: no cover
                self.terminated = True

        manager = self._manager()
        process = FakeProcess()
        manager._browser = FakeBrowser()
        manager._process = process
        manager._owned = True
        manager.close()
        self.assertTrue(process.terminated)

    def test_edge_discovery_override(self):
        with mock.patch.dict("os.environ", {"BROWSER_EDGE_PATH": r"C:\edge\msedge.exe"}):
            self.assertEqual(find_edge(), r"C:\edge\msedge.exe")

    def test_non_loopback_cdp_host_is_rejected(self):
        for host in ("10.0.0.5", "example.com", "0.0.0.0"):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    CDPConnectionManager(host=host, port=free_port(),
                                         user_data_dir=self.profile)
        manager = CDPConnectionManager(host="localhost", port=free_port(),
                                       user_data_dir=self.profile)
        self.addCleanup(manager.close)
        self.assertEqual(manager.host, "127.0.0.1")

    def test_external_profile_without_evidence_is_refused(self):
        manager = self._manager()
        with mock.patch.object(manager, "_cdp_command_line", return_value=None):
            with self.assertRaises(BrowserError) as captured:
                manager._verify_external_profile()
            self.assertEqual(captured.exception.code, "CDP_PROFILE_UNVERIFIED")
            with mock.patch.dict("os.environ", {"BROWSER_ALLOW_UNVERIFIED_CDP": "1"}):
                self.assertFalse(manager._verify_external_profile())

    def test_external_profile_mismatch_is_rejected(self):
        manager = self._manager()
        arguments = [r"C:\Edge\msedge.exe",
                     r"--user-data-dir=C:\Users\someone\AppData\Local\Microsoft\Edge\User Data"]
        with mock.patch.object(manager, "_cdp_command_line", return_value=arguments):
            with self.assertRaises(BrowserError) as captured:
                manager._verify_external_profile()
        self.assertEqual(captured.exception.code, "CDP_PROFILE_MISMATCH")

    def test_matching_external_profile_is_accepted(self):
        manager = self._manager()
        arguments = [r"C:\Edge\msedge.exe",
                     f"--user-data-dir={Path(self.profile).resolve()}"]
        with mock.patch.object(manager, "_cdp_command_line", return_value=arguments):
            self.assertTrue(manager._verify_external_profile())

    def test_sentinel_marks_the_dedicated_profile(self):
        manager = self._manager()
        self.assertFalse(manager._sentinel_valid())
        manager._write_sentinel()
        self.assertTrue(manager._sentinel_valid())
        with mock.patch.object(manager, "_cdp_command_line", return_value=None):
            self.assertTrue(manager._verify_external_profile())

    def test_reconnect_preserves_owned_process(self):
        class FakeBrowser:
            def __init__(self):
                self.contexts = []

            def is_connected(self):
                return True

            def close(self):
                pass

        class AliveProcess:
            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):  # pragma: no cover
                pass

        manager = self._manager()
        manager._owned = True
        manager._process = AliveProcess()
        manager._browser = None
        with mock.patch("browser.runtime._port_open", return_value=True), \
                mock.patch.object(manager, "_ensure_playwright"), \
                mock.patch.object(manager, "_connect_with_retry",
                                  return_value=FakeBrowser()), \
                mock.patch.object(manager, "_verify_external_profile",
                                  side_effect=AssertionError("must not re-verify")):
            health = manager.connect()
        self.assertTrue(health.connected)
        self.assertTrue(manager.owned)
        self.assertTrue(manager._profile_verified)

    def test_reconnect_to_a_foreign_browser_keeps_ownership_false(self):
        class FakeBrowser:
            def __init__(self):
                self.contexts = []

            def is_connected(self):
                return True

            def close(self):
                pass

        manager = self._manager()
        manager._owned = False
        manager._process = None
        manager._browser = None
        with mock.patch("browser.runtime._port_open", return_value=True), \
                mock.patch.object(manager, "_ensure_playwright"), \
                mock.patch.object(manager, "_connect_with_retry",
                                  return_value=FakeBrowser()), \
                mock.patch.object(manager, "_verify_external_profile",
                                  return_value=True):
            manager.connect()
        self.assertFalse(manager.owned)
        self.assertTrue(manager._profile_verified)


if __name__ == "__main__":
    unittest.main()
