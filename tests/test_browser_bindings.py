"""Persisted HR browser account bindings (migration 011) and restart reuse."""
import tempfile
import unittest
from pathlib import Path

from browser.contracts import BrowserHealth
from browser.sessions import BrowserSessionRegistry
from browser.source import BrowserSourceConfig
from storage.database import Database
from storage.models import AccessContext, ProvisioningContext
from storage.repositories import RecruitmentRepository


class FakeRuntime:
    def __init__(self, binding):
        self.binding = binding
        self.manager = self
        self.closed = False

    def health(self):
        return BrowserHealth(connected=False, browser="fake",
                             profile=self.binding.user_data_dir, profile_verified=True)

    def close(self):
        self.closed = True


class BrowserBindingPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="bindings_")
        self.addCleanup(self.directory.cleanup)
        self.database = Database(Path(self.directory.name) / "db.sqlite3")
        self.assertEqual(self.database.migrate()["schema_version"], 12)
        self.repo = RecruitmentRepository(self.database)
        system = ProvisioningContext("binding persistence")
        self.repo.create_user(system, "b:owner", user_id="owner")
        self.repo.create_organization(system, "Binding org", "owner", org_id="org")
        self.repo.create_user(system, "b:hr2", user_id="hr2")
        self.repo.add_organization_member(AccessContext("org", "owner"), "hr2",
                                          org_role="ORG_ADMIN")
        self.owner = AccessContext("org", "owner")
        self.hr2 = AccessContext("org", "hr2")
        self.source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=9339,
            user_data_dir=str(Path(self.directory.name) / "base"))

    def registry(self):
        return BrowserSessionRegistry(
            self.source, profile_root=str(Path(self.directory.name) / "sessions"),
            runtime_factory=lambda binding: FakeRuntime(binding),
            store=self.repo)

    def test_binding_is_persisted_and_reused_after_restart(self):
        first = self.registry()
        self.addCleanup(first.close)
        binding = first.binding_for(self.owner)
        row = self.repo.get_browser_binding(self.owner)
        self.assertIsNotNone(row)
        self.assertEqual(row["browser_account_id"], binding.browser_account_id)
        self.assertEqual(row["cdp_port"], binding.cdp_port)
        self.assertEqual(row["profile_id"], binding.profile_id)
        self.assertEqual(row["status"], "ACTIVE")
        # No credential-like column exists in the persisted binding.
        for forbidden in ("cookie", "token", "authorization", "password"):
            self.assertFalse(any(forbidden in key.lower() for key in row))

        restarted = self.registry()
        self.addCleanup(restarted.close)
        reused = restarted.binding_for(self.owner)
        self.assertEqual(reused.browser_account_id, binding.browser_account_id)
        self.assertEqual(reused.cdp_port, binding.cdp_port)
        self.assertEqual(reused.profile_id, binding.profile_id)
        self.assertEqual(reused.mapping_source, "persisted")

    def test_each_hr_persists_an_independent_binding(self):
        registry = self.registry()
        self.addCleanup(registry.close)
        owner = registry.binding_for(self.owner)
        hr2 = registry.binding_for(self.hr2)
        self.assertNotEqual(owner.cdp_port, hr2.cdp_port)
        self.assertNotEqual(owner.browser_account_id, hr2.browser_account_id)
        self.assertEqual(self.repo.get_browser_binding(self.owner)["profile_id"],
                         owner.profile_id)
        self.assertEqual(self.repo.get_browser_binding(self.hr2)["profile_id"],
                         hr2.profile_id)


if __name__ == "__main__":
    unittest.main()
