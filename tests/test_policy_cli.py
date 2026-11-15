"""Real CLI subprocess integration; compile fixtures remain explicitly offline.

Only the parent process uses a no-network fake compiler to seed a draft. Child
CLI processes use actual repositories and migrations without patched code.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

from config.policy_compiler import JobPolicyCompiler
from config.policy_service import PolicyCompilerService
from config.settings import PROJECT_ROOT
from tests.database_support import DatabaseTestCase
from tests.policy_fixtures import offline_policy, policy_input
from tests.test_policy_compiler import RecordedCompilerClient


class PolicyCliTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.repo.create_job(self.ctx, "CLI test job", job_id="job1")
        self.root = Path(self.directory.name)

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def run_cli(self, command, *arguments, scope=False, user_id="owner", parse=True):
        scoped = ["--database", str(self.path), "--org-id", "org1", "--user-id", user_id,
                  "--job-id", "job1"] if scope else []
        environment = os.environ.copy()
        # If an authorization regression reaches the SDK, it still cannot use
        # the developer's real model credentials or endpoint.
        environment.update(LLM_BASE_URL="http://127.0.0.1:1/v1", LLM_API_KEY="offline-cli-test-only",
                           LLM_MODEL_NAME="offline-cli-test-model")
        process = subprocess.run([sys.executable, "-X", "utf8", str(PROJECT_ROOT / "main.py"),
                                  command, *scoped, *map(str, arguments)], cwd=self.root,
                                 env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
        if not parse:
            return process, None
        self.assertTrue(process.stdout.strip(), process.stderr)
        return process, json.loads(process.stdout)

    def compiled_draft(self, case_id="java_backend"):
        client = RecordedCompilerClient(offline_policy(case_id))
        compiler = JobPolicyCompiler(client=client, model_name="offline-cli-parent-compiler")
        result = PolicyCompilerService(self.repo, compiler).compile_draft(self.ctx, "job1", policy_input(case_id))
        self.assertEqual(len(client.requests), 1)
        return result

    def test_schema_subprocess_writes_schema_and_only_output_path_metadata(self):
        target = self.root / "nested" / "policy.schema.json"
        process, metadata = self.run_cli("policy-schema", "--output", target)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(metadata, {"output": str(target)})
        schema = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "JobScoringPolicy")
        self.assertIs(schema["additionalProperties"], False)
        self.assertIn("scoring_dimensions", schema["properties"])

    def test_validate_subprocess_accepts_three_distinct_offline_policies(self):
        for case_id in ("ai_automation", "java_backend", "b2b_sales"):
            path = self.write_json(case_id + ".json", offline_policy(case_id))
            with self.subTest(case_id=case_id):
                process, result = self.run_cli("policy-validate", "--policy", path)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertTrue(result["pydantic_valid"])
                self.assertTrue(result["publishable"])
                self.assertEqual(result["diagnostics"], [])

    def test_invalid_json_and_illegal_weight_sum_return_failure(self):
        invalid = self.root / "invalid.json"
        invalid.write_text("{invalid-json}", encoding="utf-8")
        process, result = self.run_cli("policy-validate", "--policy", invalid)
        self.assertEqual(process.returncode, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "JSONDecodeError")
        wrong = deepcopy(offline_policy("java_backend"))
        wrong["scoring_dimensions"][0]["weight"] = 0.9
        process, result = self.run_cli("policy-validate", "--policy", self.write_json("invalid-weight.json", wrong))
        self.assertEqual(process.returncode, 1)
        self.assertTrue(result["pydantic_valid"])
        self.assertFalse(result["publishable"])
        self.assertIn("INVALID_WEIGHT_SUM", [item["code"] for item in result["diagnostics"]])

    def test_compile_requires_explicit_live_flag_before_any_model_request(self):
        path = self.write_json("input.json", policy_input("ai_automation"))
        process, _ = self.run_cli("policy-compile", "--input", path, scope=True, parse=False)
        self.assertEqual(process.returncode, 2)
        self.assertIn("--live", process.stderr)
        self.assertEqual(self.repo.list_policy_compilations(self.ctx, "job1"), [])

    def test_compile_rejects_unauthorized_identity_before_api_or_compilation_record(self):
        self.repo.create_user(self.system, "test:ungranted-cli", user_id="ungranted")
        self.repo.add_organization_member(self.ctx, "ungranted")
        path = self.write_json("input.json", policy_input("ai_automation"))
        process, result = self.run_cli("policy-compile", "--live", "--input", path,
                                      "--timeout", "1", "--max-tokens", "512", scope=True, user_id="ungranted")
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(result["error_type"], "PermissionDenied")
        self.assertNotIn("metrics", result)
        self.assertEqual(self.repo.list_policy_compilations(self.ctx, "job1"), [])

    def test_actual_cli_draft_runs_confirmation_version_publication_and_history(self):
        compiled = self.compiled_draft()
        draft, run = compiled["draft"], compiled["compilation_run"]
        process, fetched = self.run_cli("policy-draft", "--draft-id", draft["draft_id"], scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(fetched["draft_hash"], draft["draft_hash"])
        self.assertEqual(json.loads(fetched["source_json"]), policy_input("java_backend"))
        process, runs = self.run_cli("policy-runs", scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual([item["run_id"] for item in runs], [run["run_id"]])
        self.assertEqual(runs[0]["status"], "SUCCEEDED")
        process, error = self.run_cli("policy-confirm", "--draft-id", draft["draft_id"], "--run-id", run["run_id"],
                                      "--confirm-hash", "stale-or-wrong", scope=True)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(error["error_type"], "ValueError")
        process, confirmed = self.run_cli("policy-confirm", "--draft-id", draft["draft_id"], "--run-id", run["run_id"],
                                          "--confirm-hash", draft["draft_hash"], scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(confirmed["draft_hash"], draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {"policy_protocol": "job_scoring_policy_v1"})
        process, sealed = self.run_cli("policy-version", "--draft-id", draft["draft_id"], "--run-id", run["run_id"],
                                      "--confirmation-id", confirmed["confirmation_id"],
                                      "--execution-profile-id", profile["profile_id"], scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        version = sealed["version"]
        process, missing = self.run_cli("policy-current", scope=True)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(missing["error_type"], "NotFound")
        process, publication = self.run_cli("policy-publish", "--policy-id", version["policy_id"],
                                           "--confirm-hash", draft["draft_hash"], scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(publication["policy_id"], version["policy_id"])
        output = self.root / "artifacts" / "current.json"
        process, metadata = self.run_cli("policy-current", "--output", output, scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(metadata, {"output": str(output)})
        current = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(current["policy_id"], version["policy_id"])
        self.assertEqual(json.loads(current["policy_json"]), json.loads(draft["policy_json"]))
        process, historical = self.run_cli("policy-history", "--version", version["policy_version"], scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(historical, current)

    def test_cli_update_revision_invalidates_previous_hash_and_compilation(self):
        compiled = self.compiled_draft("ai_automation")
        draft, run = compiled["draft"], compiled["compilation_run"]
        changed = json.loads(draft["policy_json"])
        changed["scoring_dimensions"][0]["description"] += "；HR补充具体验收标准。"
        path = self.write_json("edited-policy.json", changed)
        process, updated = self.run_cli("policy-update", "--draft-id", draft["draft_id"],
                                        "--expected-revision", "1", "--parent-run-id", run["run_id"],
                                        "--policy", path, scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(updated["draft"]["revision"], 2)
        self.assertTrue(updated["publishable"])
        self.assertNotEqual(updated["draft"]["draft_hash"], draft["draft_hash"])
        process, rejected = self.run_cli("policy-confirm", "--draft-id", draft["draft_id"], "--run-id", run["run_id"],
                                        "--confirm-hash", draft["draft_hash"], scope=True)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(rejected["error_type"], "ValueError")
        process, fetched = self.run_cli("policy-draft", "--draft-id", draft["draft_id"], scope=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(fetched["revision"], 2)
        self.assertEqual(len(self.repo.list_policy_compilations(self.ctx, "job1")), 2)
