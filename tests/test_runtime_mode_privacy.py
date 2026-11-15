"""Least-privilege privacy checks for /api/runtime-mode."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from browser.contracts import BrowserHealth
from browser.sessions import BrowserSessionRegistry
from browser.source import BrowserSourceConfig
from server.app import create_app
from storage.models import AccessContext
from tests.pipeline_support import PipelineCase


class FakeRuntime:
    def __init__(self, binding):
        self.binding = binding
        self.manager = self

    def health(self):
        return BrowserHealth(connected=False, browser="fake",
                             profile=self.binding.user_data_dir,
                             profile_verified=True)

    def close(self):
        pass


class RuntimeModePrivacyTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.publish_case(job_id="job")
        self.directory = tempfile.TemporaryDirectory(prefix="runtime_mode_")
        self.addCleanup(self.directory.cleanup)
        source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=9333,
            user_data_dir=str(Path(self.directory.name) / "base"), headless=True)
        self.registry = BrowserSessionRegistry(
            source, profile_root=str(Path(self.directory.name) / "sessions"),
            runtime_factory=lambda binding: FakeRuntime(binding))
        self.addCleanup(self.registry.close)
        self.owner = AccessContext("org", "owner")
        self.hr2 = AccessContext("org", "hr2")
        self.owner_binding = self.registry.binding_for(self.owner)
        self.hr2_binding = self.registry.binding_for(self.hr2)
        self.app = create_app(self.repo, browser_source="mock",
                              browser_session_registry=self.registry)
        self.addCleanup(lambda: self.app.state.executor.shutdown(wait=True))
        self.client = TestClient(self.app)

    def test_anonymous_call_exposes_no_session_or_paths(self):
        response = self.client.get("/api/runtime-mode")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        source = body["browser_source"]
        self.assertEqual(source["source"], "mock")
        self.assertIsNone(source["session"])
        self.assertNotIn("sessions", source)
        self.assertNotIn("user_data_dir", response.text)
        self.assertNotIn(self.directory.name, response.text)
        self.assertNotIn("actor_user_id", response.text)

    def test_authenticated_hr_sees_only_their_own_session(self):
        owner = self.client.get("/api/runtime-mode",
                                headers={"X-Org-Id": "org", "X-User-Id": "owner"})
        self.assertEqual(owner.status_code, 200)
        owner_session = owner.json()["browser_source"]["session"]
        self.assertEqual(owner_session["actor_user_id"], "owner")
        self.assertEqual(owner_session["org_id"], "org")
        self.assertEqual(owner_session["browser_account_id"],
                         self.owner_binding.browser_account_id)
        self.assertNotIn("profile", owner_session)
        self.assertNotIn(self.directory.name, owner_session["profile_id"])
        self.assertNotIn("\\", owner_session["profile_id"])
        self.assertNotIn("/", owner_session["profile_id"])
        self.assertNotIn("user_data_dir", owner.text)
        self.assertNotIn(self.directory.name, owner.text)
        self.assertNotIn(self.hr2_binding.browser_account_id, owner.text)

        hr2 = self.client.get("/api/runtime-mode",
                              headers={"X-Org-Id": "org", "X-User-Id": "hr2"})
        hr2_session = hr2.json()["browser_source"]["session"]
        self.assertEqual(hr2_session["actor_user_id"], "hr2")
        self.assertNotIn(self.owner_binding.browser_account_id, hr2.text)
        self.assertNotIn(self.directory.name, hr2.text)

    def test_diagnostics_do_not_expose_local_profile_path(self):
        from fastapi.testclient import TestClient
        directory = tempfile.TemporaryDirectory(prefix="privacy_path_")
        self.addCleanup(directory.cleanup)
        profile = str(Path(directory.name) / "profile")
        source = BrowserSourceConfig(
            name="cdp_mock", enabled=True, port=9339, user_data_dir=profile,
            diagnostics=(f"CDP endpoint 127.0.0.1:9339; profile {profile}",))
        app = create_app(self.repo, browser_source=source)
        try:
            with TestClient(app) as client:
                response = client.get("/api/runtime-mode")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(directory.name, response.text)
                self.assertNotIn(profile, response.text)
                self.assertIn("<dedicated-profile>", response.text)
        finally:
            app.state.executor.shutdown(wait=True)

    def test_unknown_identity_gets_no_session_details(self):
        response = self.client.get("/api/runtime-mode",
                                   headers={"X-Org-Id": "org", "X-User-Id": "ghost"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["browser_source"]["session"])
        self.assertNotIn(self.directory.name, response.text)


if __name__ == "__main__":
    unittest.main()
