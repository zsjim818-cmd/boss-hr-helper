"""Explicit Mock transport + actual SQLite compiler-service integration.

The fake client records requests and returns human-authored offline fixtures;
these tests never invoke Qwen or claim live model compilation quality.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx2
from openai import APITimeoutError

from config.policy_compiler import CompilationError, JobPolicyCompiler
from config.policy_service import PolicyCompilerService, PolicyQualityBlocked
from engine.policy_validator import POLICY_VALIDATOR_VERSION
from storage.models import AccessContext
from storage.repositories import NotFound, PermissionDenied
from tests.database_support import DatabaseTestCase
from tests.policy_fixtures import check_policy_matches_case, compiler_cases, offline_policy, policy_input


class RecordedCompilerClient:
    """No sockets: a request-recording fake with SDK retry setting exposed."""
    max_retries = 0

    def __init__(self, payload=None, *, content=None, error=None, finish_reason="stop", refusal=None):
        self.requests = []
        self.payload = deepcopy(payload)
        self.content = content
        self.error = error
        self.finish_reason = finish_reason
        self.refusal = refusal
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        content = self.content if self.content is not None else json.dumps(self.payload, ensure_ascii=False)
        return SimpleNamespace(
            model="offline-qwen-response", _request_id="offline-compiler-request",
            choices=[SimpleNamespace(finish_reason=self.finish_reason,
                                     message=SimpleNamespace(content=content, refusal=self.refusal))],
            usage=SimpleNamespace(prompt_tokens=400, completion_tokens=800, total_tokens=1200,
                                  completion_tokens_details={"reasoning_tokens": 0}))


class PolicyCompilerServiceTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.repo.create_job(self.ctx, "Compiler integration job", job_id="job1")

    def service(self, case_id="ai_automation", **options):
        client = RecordedCompilerClient(offline_policy(case_id), **options)
        compiler = JobPolicyCompiler(client=client, model_name="explicit-offline-test-model",
                                     timeout_seconds=45, max_tokens=8192)
        return PolicyCompilerService(self.repo, compiler), client

    def assert_no_version_or_publication(self):
        with self.assertRaises(NotFound):
            self.repo.get_policy_version(self.ctx, "job1", policy_version=1)
        with self.assertRaises(NotFound):
            self.repo.get_current_policy(self.ctx, "job1")

    def test_three_mock_compilations_create_drafts_and_runs_without_publishing(self):
        for case in compiler_cases():
            key = case["case_id"]
            service, client = self.service(key)
            with self.subTest(case=key):
                result = service.compile_draft(self.ctx, "job1", policy_input(key))
                self.assertTrue(result["publishable"])
                self.assertEqual(result["draft"]["revision"], 1)
                self.assertEqual(result["compilation_run"]["status"], "SUCCEEDED")
                self.assertEqual(result["compilation_run"]["draft_id"], result["draft"]["draft_id"])
                self.assertEqual(result["compilation_run"]["draft_hash"], result["draft"]["draft_hash"])
                self.assertEqual(len(client.requests), 1)
                saved = self.repo.get_policy_draft(self.ctx, "job1", result["draft"]["draft_id"])
                check_policy_matches_case(json.loads(saved["policy_json"]), key)
                self.assertEqual(json.loads(saved["policy_json"])["provenance"]["model_version"], "offline-qwen-response")
                self.assert_no_version_or_publication()
        self.assertEqual(len(self.repo.list_policy_compilations(self.ctx, "job1")), 3)

    def test_request_retains_strict_json_schema_and_bounded_single_attempt(self):
        service, client = self.service()
        result = service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        request = client.requests[0]
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertIs(request["response_format"]["json_schema"]["strict"], True)
        schema = request["response_format"]["json_schema"]["schema"]
        for definition in [schema, *schema.get("$defs", {}).values()]:
            if definition.get("type") == "object":
                self.assertIs(definition["additionalProperties"], False)
                self.assertEqual(set(definition["required"]), set(definition["properties"]))
        self.assertEqual(request["temperature"], 0)
        self.assertEqual(request["timeout"], 45)
        self.assertEqual(request["max_tokens"], 8192)
        self.assertEqual(request["extra_body"], {"enable_thinking": False})
        self.assertFalse(request["stream"])
        user = json.loads(request["messages"][1]["content"])
        self.assertEqual(user["untrusted_job_input"], policy_input("ai_automation"))
        self.assertNotIn("expected_features", user)
        self.assertEqual(result["metrics"]["sdk_max_retries"], 0)
        self.assertEqual(result["metrics"]["usage"]["total_tokens"], 1200)
        self.assertEqual(result["metrics"]["reasoning_tokens"], 0)
        self.assertEqual(result["metrics"]["finish_reason"], "stop")
        self.assertTrue(result["metrics"]["pydantic_valid"])
        self.assertTrue(result["metrics"]["business_valid"])
        self.assertGreaterEqual(result["metrics"]["elapsed_seconds"], 0)

    def test_timeout_is_recorded_as_failure_without_retry_draft_or_version(self):
        error = APITimeoutError(request=httpx2.Request("POST", "https://offline.invalid/chat/completions"))
        service, client = self.service(error=error)
        with self.assertRaises(CompilationError) as captured:
            service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(captured.exception.metrics["failure_type"], "APITimeoutError")
        self.assertEqual(captured.exception.metrics["stage"], "api_call")
        run = self.repo.list_policy_compilations(self.ctx, "job1")[0]
        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(run["error_type"], "APITimeoutError")
        self.assertIsNone(run["draft_id"])
        self.assert_no_version_or_publication()

    def test_invalid_json_is_recorded_without_pydantic_bypass(self):
        service, client = self.service(content="{not valid JSON}")
        with self.assertRaises(CompilationError) as captured:
            service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        self.assertEqual(captured.exception.metrics["stage"], "pydantic_validation")
        self.assertFalse(captured.exception.metrics["pydantic_valid"])
        self.assertEqual(len(client.requests), 1)
        run = self.repo.list_policy_compilations(self.ctx, "job1")[0]
        self.assertEqual(run["status"], "FAILED")
        self.assertIsNone(run["draft_id"])
        self.assert_no_version_or_publication()

    def test_truncated_response_cannot_be_saved_as_compiled_policy(self):
        service, client = self.service(finish_reason="length")
        with self.assertRaises(CompilationError) as captured:
            service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        self.assertEqual(captured.exception.metrics["finish_reason"], "length")
        self.assertEqual(captured.exception.metrics["stage"], "response_validation")
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(self.repo.list_policy_compilations(self.ctx, "job1")[0]["status"], "FAILED")
        self.assert_no_version_or_publication()

    def test_invalid_output_field_is_rejected_by_strict_pydantic(self):
        service, client = self.service()
        client.payload["agent_only_field"] = True
        with self.assertRaises(CompilationError) as captured:
            service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        self.assertEqual(captured.exception.metrics["failure_type"], "ValidationError")
        self.assertTrue(any(item["type"] == "extra_forbidden" for item in captured.exception.metrics["validation_errors"]))
        self.assertEqual(self.repo.list_policy_compilations(self.ctx, "job1")[0]["status"], "FAILED")
        self.assert_no_version_or_publication()

    def test_changed_model_source_creates_blocked_draft_not_publishable_version(self):
        service, client = self.service()
        client.payload["sources"]["jd_text"] = "The model substituted a different source"
        result = service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        self.assertFalse(result["publishable"])
        self.assertIn("SOURCE_INPUT_MISMATCH", [item["code"] for item in result["diagnostics"]])
        self.assertEqual(result["compilation_run"]["status"], "FAILED")
        self.assertTrue(json.loads(result["draft"]["diagnostics_json"]))
        with self.assertRaises(ValueError):
            service.confirm_draft(self.ctx, "job1", result["draft"]["draft_id"], result["compilation_run"]["run_id"],
                                  confirmation_hash=result["draft"]["draft_hash"])
        self.assert_no_version_or_publication()

    def test_invalid_weight_sum_cannot_be_confirmed_or_published(self):
        service, client = self.service()
        client.payload["scoring_dimensions"][0]["weight"] = 0.4
        result = service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        self.assertFalse(result["publishable"])
        self.assertIn("INVALID_WEIGHT_SUM", [item["code"] for item in result["diagnostics"]])
        with self.assertRaises(ValueError):
            service.confirm_draft(self.ctx, "job1", result["draft"]["draft_id"], result["compilation_run"]["run_id"],
                                  confirmation_hash=result["draft"]["draft_hash"])
        self.assert_no_version_or_publication()

    def test_hr_edit_creates_new_revision_and_invalidates_old_confirmation(self):
        service, _ = self.service()
        initial = service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        old_draft, old_run = initial["draft"], initial["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", old_draft["draft_id"], old_run["run_id"],
                                             confirmation_hash=old_draft["draft_hash"])
        changed = json.loads(old_draft["policy_json"])
        changed["scoring_dimensions"][0]["description"] += "；由HR明确修订验收说明。"
        updated = service.update_draft(self.ctx, "job1", old_draft["draft_id"], changed,
                                       expected_revision=1, parent_run_id=old_run["run_id"])
        self.assertEqual(updated["draft"]["revision"], 2)
        self.assertNotEqual(updated["draft"]["draft_hash"], old_draft["draft_hash"])
        self.assertEqual(updated["compilation_run"]["run_kind"], "VALIDATION")
        for run_id in (old_run["run_id"], updated["compilation_run"]["run_id"]):
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                service.confirm_draft(self.ctx, "job1", old_draft["draft_id"], run_id,
                                      confirmation_hash=old_draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {})
        with self.assertRaises(ValueError):
            service.create_version(self.ctx, "job1", old_draft["draft_id"], old_run["run_id"],
                                   confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        self.assert_no_version_or_publication()

    def test_explicit_confirmation_can_create_immutable_version_and_publish(self):
        service, _ = self.service("java_backend")
        compiled = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        draft, run = compiled["draft"], compiled["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                             confirmation_hash=draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"policy_protocol": "job_scoring_policy_v1"})
        sealed = service.create_version(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                        confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        version = sealed["version"]
        self.assertEqual(version["policy_schema_version"], "job_scoring_policy_v1")
        self.assertEqual(json.loads(version["source_snapshot_json"]), policy_input("java_backend"))
        self.assertEqual(version["source_draft_hash"], draft["draft_hash"])
        with self.assertRaises(NotFound):
            self.repo.get_current_policy(self.ctx, "job1")
        service.publish_version(self.ctx, "job1", version["policy_id"], confirmation_hash=draft["draft_hash"],
                                enforce_quality_gate=False,
                                quality_override_reason="offline fixture is quality-BLOCKED by design; publication mechanics coverage")  # explicit audited bypass
        current = self.repo.get_current_policy(self.ctx, "job1")
        self.assertEqual(current["policy_id"], version["policy_id"])
        self.assertEqual(json.loads(current["policy_json"]), json.loads(draft["policy_json"]))
        check_policy_matches_case(json.loads(current["policy_json"]), "java_backend")
        original = deepcopy(current)
        current["policy_json"] = "{}"
        self.assertEqual(self.repo.get_current_policy(self.ctx, "job1"), original)

    def test_unauthorized_hr_is_rejected_before_the_model_request(self):
        self.repo.create_user(self.system, "test:ungranted", user_id="ungranted")
        self.repo.add_organization_member(self.ctx, "ungranted")
        service, client = self.service()
        with self.assertRaises(PermissionDenied):
            service.compile_draft(AccessContext("org1", "ungranted"), "job1", policy_input("ai_automation"))
        self.assertEqual(client.requests, [])
        self.assertEqual(self.repo.list_policy_compilations(self.ctx, "job1"), [])

    def test_invalid_upload_is_rejected_before_request_or_persistence(self):
        service, client = self.service()
        with self.assertRaises(ValueError):
            service.compile_draft(self.ctx, "job1", {**policy_input("ai_automation"), "job_title": ""})
        self.assertEqual(client.requests, [])
        self.assertEqual(self.repo.list_policy_compilations(self.ctx, "job1"), [])

    def test_compile_draft_and_edit_report_compile_time_quality_review(self):
        # Stage 5.5 Batch 2: the structural quality review runs where the
        # compiler actually produces a Policy, report-only (no publish gate).
        service, _ = self.service("java_backend")
        compiled = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        quality = compiled["quality"]
        self.assertEqual(quality["validator_version"], POLICY_VALIDATOR_VERSION)
        self.assertEqual(quality["semantic_requirement_count"], 6)
        self.assertEqual(quality["missing_positive_count"], 3)
        self.assertEqual(quality["orphan_group_count"], 0)
        self.assertIn("issues", quality)
        self.assertEqual(compiled["publishable"], True)  # quality never gates here
        draft, run = compiled["draft"], compiled["compilation_run"]
        changed = json.loads(draft["policy_json"])
        changed["scoring_dimensions"][0]["description"] += "；HR补充具体验收标准。"
        updated = service.update_draft(self.ctx, "job1", draft["draft_id"], changed,
                                       expected_revision=1, parent_run_id=run["run_id"])
        self.assertEqual(updated["quality"]["validator_version"], POLICY_VALIDATOR_VERSION)
        self.assertEqual(updated["quality"]["semantic_requirement_count"], 6)

    def test_publish_quality_gate_blocks_blocked_policy_by_default(self):
        # Stage 5.5 close-out: BLOCKED quality refuses publication with the
        # explicit diagnostics; nothing is written to policy_publications.
        service, _ = self.service("java_backend")
        compiled = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        draft, run = compiled["draft"], compiled["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                             confirmation_hash=draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"policy_protocol": "job_scoring_policy_v1"})
        sealed = service.create_version(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                        confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        with self.assertRaises(PolicyQualityBlocked) as captured:
            service.publish_version(self.ctx, "job1", sealed["version"]["policy_id"],
                                    confirmation_hash=draft["draft_hash"])
        self.assertIn("MISSING_POSITIVE_ANCHOR",
                      {item.code for item in captured.exception.issues})
        # The sealed version exists, but nothing was published.
        with self.assertRaises(NotFound):
            self.repo.get_current_policy(self.ctx, "job1")

    def test_publish_quality_gate_passes_a_clean_policy(self):
        from tests.policy_fixtures import offline_policy
        from tests.test_policy_validator import _add_positive
        clean = offline_policy("java_backend")
        for requirement_id in ("testing", "duty", "spring_route", "quarkus_route"):
            _add_positive(clean, "fix_" + requirement_id, requirement_id)
        client = RecordedCompilerClient(clean)
        compiler = JobPolicyCompiler(client=client, model_name="explicit-offline-test-model",
                                     timeout_seconds=45, max_tokens=8192)
        service = PolicyCompilerService(self.repo, compiler)
        compiled = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        self.assertEqual(compiled["quality"]["status"], "PASS")
        draft, run = compiled["draft"], compiled["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                             confirmation_hash=draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"policy_protocol": "job_scoring_policy_v1"})
        sealed = service.create_version(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                        confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        publication = service.publish_version(self.ctx, "job1", sealed["version"]["policy_id"],
                                              confirmation_hash=draft["draft_hash"])
        self.assertEqual(publication["policy_id"], sealed["version"]["policy_id"])

    def test_publish_quality_gate_bypass_requires_explicit_reason(self):
        service, _ = self.service("java_backend")
        compiled = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        draft, run = compiled["draft"], compiled["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                             confirmation_hash=draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"policy_protocol": "job_scoring_policy_v1"})
        sealed = service.create_version(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                        confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        with self.assertRaisesRegex(ValueError, "explicit reason"):
            service.publish_version(self.ctx, "job1", sealed["version"]["policy_id"],
                                    confirmation_hash=draft["draft_hash"],
                                    enforce_quality_gate=False)
        with self.assertRaises(NotFound):
            self.repo.get_current_policy(self.ctx, "job1")

    def test_publish_quality_gate_bypass_requires_org_admin(self):
        self.repo.create_user(self.system, "test:gate-hr", user_id="hr1")
        self.repo.add_organization_member(self.ctx, "hr1", org_role="HR")
        service, _ = self.service("java_backend")
        compiled = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        draft, run = compiled["draft"], compiled["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                             confirmation_hash=draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"policy_protocol": "job_scoring_policy_v1"})
        sealed = service.create_version(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                        confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        with self.assertRaises(PermissionDenied):
            service.publish_version(AccessContext("org1", "hr1"), "job1",
                                    sealed["version"]["policy_id"],
                                    confirmation_hash=draft["draft_hash"],
                                    quality_override_reason="HR wants to skip")

    def test_publish_quality_gate_bypass_is_audited(self):
        service, _ = self.service("java_backend")
        compiled = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        draft, run = compiled["draft"], compiled["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                             confirmation_hash=draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"policy_protocol": "job_scoring_policy_v1"})
        sealed = service.create_version(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                        confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        service.publish_version(self.ctx, "job1", sealed["version"]["policy_id"],
                                confirmation_hash=draft["draft_hash"],
                                enforce_quality_gate=False,
                                quality_override_reason="audited acceptance bypass")
        events = [event for event in self.repo.list_audit_events(self.ctx, "job1")
                  if event["action"] == "POLICY_QUALITY_GATE_OVERRIDDEN"]
        self.assertEqual(len(events), 1)
        details = json.loads(events[0]["details_json"])
        self.assertEqual(details["reason"], "audited acceptance bypass")
        self.assertEqual(details["policy_version"], 1)
        self.assertEqual(details["policy_hash"], sealed["version"]["policy_hash"])
        self.assertIn("MISSING_POSITIVE_ANCHOR", details["quality_codes"])
        self.assertEqual(events[0]["actor_id"], "owner")

    def test_publish_quality_gate_bypass_is_explicit_and_auditable(self):
        service, _ = self.service("java_backend")
        compiled = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        draft, run = compiled["draft"], compiled["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                             confirmation_hash=draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"policy_protocol": "job_scoring_policy_v1"})
        sealed = service.create_version(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                        confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        publication = service.publish_version(self.ctx, "job1", sealed["version"]["policy_id"],
                                              confirmation_hash=draft["draft_hash"],
                                              enforce_quality_gate=False,
                                              quality_override_reason="explicit bypass audit test")
        self.assertEqual(publication["policy_id"], sealed["version"]["policy_id"])

    def test_human_edit_cannot_claim_a_different_model_or_prompt_call(self):
        service, client = self.service()
        initial = service.compile_draft(self.ctx, "job1", policy_input("ai_automation"))
        draft, run = initial["draft"], initial["compilation_run"]
        changed = json.loads(draft["policy_json"])
        changed["provenance"].update(model_version="a-model-never-called", prompt_version="a-prompt-never-used")
        edited = service.update_draft(self.ctx, "job1", draft["draft_id"], changed,
                                      expected_revision=1, parent_run_id=run["run_id"])
        saved = json.loads(edited["draft"]["policy_json"])
        self.assertEqual(saved["provenance"]["model_version"], run["model_version"])
        self.assertEqual(saved["provenance"]["prompt_version"], run["prompt_version"])
        self.assertEqual(edited["compilation_run"]["run_kind"], "VALIDATION")
        self.assertEqual(len(client.requests), 1)
