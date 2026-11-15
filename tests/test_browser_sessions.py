"""Per-HR browser session registry: isolated port + profile per actor."""
import socket
import tempfile
import unittest
from pathlib import Path

from browser.contracts import BrowserError, BrowserHealth
from browser.sessions import BrowserSessionRegistry
from browser.source import BrowserSourceConfig
from storage.models import AccessContext


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeRuntime:
    def __init__(self, binding):
        self.binding = binding
        self.closed = False
        self.manager = self

    def health(self):
        return BrowserHealth(connected=False, browser="fake",
                             profile=self.binding.user_data_dir,
                             profile_verified=True)

    def close(self):
        self.closed = True


class BrowserSessionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="sessions_")
        self.addCleanup(self.directory.cleanup)
        self.source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=free_port(),
            user_data_dir=str(Path(self.directory.name) / "base"))
        self.owner = AccessContext("org", "owner")
        self.hr2 = AccessContext("org", "hr2")

    def registry(self, **kwargs):
        registry = BrowserSessionRegistry(
            self.source, profile_root=str(Path(self.directory.name) / "sessions"),
            runtime_factory=lambda binding: FakeRuntime(binding), **kwargs)
        self.addCleanup(registry.close)
        return registry

    def test_each_hr_gets_an_isolated_port_and_profile(self):
        registry = self.registry()
        owner_runtime = registry.runtime_for(self.owner)
        hr_runtime = registry.runtime_for(self.hr2)
        self.assertIsNot(owner_runtime, hr_runtime)
        owner_binding = registry.binding_for(self.owner)
        hr_binding = registry.binding_for(self.hr2)
        self.assertNotEqual(owner_binding.cdp_port, hr_binding.cdp_port)
        self.assertNotEqual(owner_binding.user_data_dir, hr_binding.user_data_dir)
        self.assertNotEqual(owner_binding.browser_account_id,
                            hr_binding.browser_account_id)
        self.assertEqual(owner_binding.mapping_source, "simulated")
        self.assertTrue(owner_binding.browser_account_id.startswith("mock-account-"))
        self.assertIn("owner", owner_binding.user_data_dir)
        self.assertIn("hr2", hr_binding.user_data_dir)

    def test_same_actor_reuses_one_runtime(self):
        registry = self.registry()
        self.assertIs(registry.runtime_for(self.owner), registry.runtime_for(self.owner))

    def test_configured_mapping_overrides_account_and_port(self):
        configured_port = free_port()
        registry = self.registry(bindings={
            ("org", "hr2"): {
                "browser_account_id": "boss-account-002",
                "cdp_port": configured_port,
                "user_data_dir": str(Path(self.directory.name) / "hr2-profile"),
            }})
        binding = registry.binding_for(self.hr2)
        self.assertEqual(binding.browser_account_id, "boss-account-002")
        self.assertEqual(binding.cdp_port, configured_port)
        self.assertEqual(binding.mapping_source, "configured")
        self.assertTrue(binding.user_data_dir.endswith("hr2-profile"))

    def test_boss_readonly_requires_an_explicit_account_mapping(self):
        source = BrowserSourceConfig(
            name="boss_readonly", enabled=True, real_boss_allowed=True,
            port=self.source.port, user_data_dir=self.source.user_data_dir)
        registry = BrowserSessionRegistry(
            source, profile_root=str(Path(self.directory.name) / "sessions"),
            runtime_factory=lambda binding: FakeRuntime(binding))
        self.addCleanup(registry.close)
        with self.assertRaises(BrowserError) as captured:
            registry.runtime_for(self.owner)
        self.assertEqual(captured.exception.code, "BROWSER_ACCOUNT_UNMAPPED")

    def test_describe_and_close_cover_every_session(self):
        registry = self.registry()
        owner_runtime = registry.runtime_for(self.owner)
        hr_runtime = registry.runtime_for(self.hr2)
        described = registry.describe()
        self.assertEqual(len(described), 2)
        self.assertEqual({item["actor_user_id"] for item in described},
                         {"owner", "hr2"})
        for item in described:
            self.assertIn("cdp_port", item)
            self.assertIn("browser_account_id", item)
            self.assertIn("profile", item)
        registry.close()
        self.assertTrue(owner_runtime.closed)
        self.assertTrue(hr_runtime.closed)

    def test_port_allocation_skips_an_occupied_port(self):
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        self.addCleanup(occupied.close)
        port = occupied.getsockname()[1]
        source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=port,
            user_data_dir=str(Path(self.directory.name) / "base2"))
        registry = BrowserSessionRegistry(
            source, profile_root=str(Path(self.directory.name) / "sessions2"),
            runtime_factory=lambda binding: FakeRuntime(binding))
        self.addCleanup(registry.close)
        binding = registry.binding_for(self.owner)
        self.assertNotEqual(binding.cdp_port, port)
        self.assertGreater(binding.cdp_port, port)


if __name__ == "__main__":
    unittest.main()
