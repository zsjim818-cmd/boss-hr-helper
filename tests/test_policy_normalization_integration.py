"""Independent normalization review: pure pass and full offline compiler/Repo.

Raw fake responses remain auditable; normalization cannot invent source text,
collapse distinct tools into aliases or resolve ambiguous choices by guessing.
"""
from copy import deepcopy
import hashlib
import json

from config.policy_compiler import JobPolicyCompiler
from config.policy_normalization import normalize_policy
from config.policy_service import PolicyCompilerService
from config.policy_validation import diagnose_policy
from schemas.job_policy import JobScoringPolicy
from storage.repositories import NotFound
from tests.database_support import DatabaseTestCase
from tests.policy_fixtures import offline_policy, policy_input
from tests.test_policy_compiler import RecordedCompilerClient


def tool_alias_policy():
    policy = offline_policy("java_backend")
    quality = next(group for group in policy["keyword_groups"] if group["group_id"] == "quality")
    quality["keywords"].append({"canonical": "JUnit", "aliases": ["TestNG"], "weight": 4.0})
    deployment = next(group for group in policy["keyword_groups"] if group["group_id"] == "extra")
    next(word for word in deployment["keywords"] if word["canonical"] == "Kubernetes")["aliases"] = ["Docker"]
    return policy


class PolicyNormalizationIntegrationTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.repo.create_job(self.ctx, "Normalization integration", job_id="job1")

    def test_distinct_tool_aliases_are_removed_with_audit_warning_and_raw_unchanged(self):
        raw = tool_alias_policy()
        snapshot = deepcopy(raw)
        normalized = normalize_policy(raw)
        self.assertEqual(raw, snapshot)
        self.assertEqual(normalized.sources.model_dump(), raw["sources"])
        self.assertEqual(normalized.provenance.model_dump(), raw["provenance"])
        words = {word.canonical: word.aliases for group in normalized.keyword_groups for word in group.keywords}
        self.assertEqual(words["JUnit"], [])
        self.assertEqual(words["Kubernetes"], [])
        self.assertTrue(any(item.code == "ALIASES_REMOVED_UNGROUNDED" and item.severity == "WARNING"
                            for item in normalized.diagnostics))
        self.assertFalse(any(item.severity == "ERROR" for item in diagnose_policy(normalized, policy_input("java_backend"))))

    def test_abbreviation_requires_an_explicit_source_equivalence(self):
        raw = offline_policy("java_backend")
        extra = next(group for group in raw["keyword_groups"] if group["group_id"] == "extra")
        next(word for word in extra["keywords"] if word["canonical"] == "Kubernetes")["aliases"] = ["K8s"]
        without_source = normalize_policy(raw)
        words = {word.canonical: word.aliases for group in without_source.keyword_groups for word in group.keywords}
        self.assertEqual(words["Kubernetes"], [])
        raw["sources"]["jd_text"] += "\n术语说明：Kubernetes（K8s）是同一工具的全称与简称。"
        with_source = normalize_policy(raw)
        words = {word.canonical: word.aliases for group in with_source.keyword_groups for word in group.keywords}
        self.assertEqual(words["Kubernetes"], ["K8s"])
        self.assertEqual(with_source.sources.jd_text, raw["sources"]["jd_text"])

    def test_two_tools_mentioned_in_the_same_source_are_still_not_synonyms(self):
        raw = tool_alias_policy()
        raw["sources"]["jd_text"] += "\n可使用Docker构建镜像，使用Kubernetes编排部署；JUnit和TestNG是不同的测试工具。"
        normalized = normalize_policy(raw)
        words = {word.canonical: word.aliases for group in normalized.keyword_groups for word in group.keywords}
        self.assertEqual(words["JUnit"], [])
        self.assertEqual(words["Kubernetes"], [])

    def test_explicit_atomic_or_is_lowered_to_any_and_all_projection_refs_follow(self):
        raw = offline_policy("ai_automation")
        target = next(item for item in raw["requirements"] if item["requirement_id"] == "application_route")
        target["label"] = "LangGraph或LangChain"
        normalized = normalize_policy(raw)
        requirements = {item.requirement_id: item for item in normalized.requirements}
        groups = {item.group_id: item for item in normalized.requirement_groups}
        self.assertNotIn("application_route", requirements)
        self.assertEqual(groups["application_route"].operator, "ANY")
        leaves = groups["application_route"].members
        self.assertEqual(len(leaves), 2)
        self.assertEqual({requirements[key].label for key in leaves}, {"LangGraph", "LangChain"})
        self.assertIn("application_route", groups["alternative_routes"].members)
        self.assertEqual(groups["application_route"].source_text, target["source_text"])
        self.assertTrue(all(requirements[key].source_text == requirements[key].label
                            and requirements[key].source_text in target["source_text"] for key in leaves))
        for collection in (normalized.keyword_groups, normalized.semantic_anchors, normalized.scoring_dimensions):
            for item in collection:
                self.assertNotIn("application_route", item.requirement_ids)
                self.assertTrue(set(item.requirement_ids) <= set(requirements))
        self.assertFalse(any(item.severity == "ERROR" for item in diagnose_policy(normalized, policy_input("ai_automation"))))

    def test_broad_source_with_or_does_not_split_an_unambiguous_evidence_requirement(self):
        raw = offline_policy("ai_automation")
        normalized = normalize_policy(raw)
        self.assertEqual([item.requirement_id for item in normalized.requirements],
                         [item["requirement_id"] for item in raw["requirements"]])
        self.assertEqual(normalized.sources.model_dump(), raw["sources"])
        self.assertFalse(any(item.group_id == "application_route" for item in normalized.requirement_groups))

    def test_normalization_is_idempotent_and_does_not_silently_repair_weights(self):
        raw = tool_alias_policy()
        raw["scoring_dimensions"][0]["weight"] = 0.9
        first = normalize_policy(raw)
        second = normalize_policy(first)
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual(first.scoring_dimensions[0].weight, 0.9)
        self.assertIn("INVALID_WEIGHT_SUM", [item.code for item in diagnose_policy(first, policy_input("java_backend"))])

    def test_ambiguous_multiple_choice_remains_blocked_instead_of_guessed(self):
        raw = offline_policy("ai_automation")
        target = next(item for item in raw["requirements"] if item["requirement_id"] == "application_route")
        target["label"] = "LangGraph或LangChain或LlamaIndex"
        normalized = normalize_policy(raw)
        self.assertTrue(any(item.requirement_id == "application_route" for item in normalized.requirements))
        self.assertFalse(any(item.group_id == "application_route" for item in normalized.requirement_groups))
        self.assertTrue(any(item.severity in ("ERROR", "CONFIRM")
                            for item in diagnose_policy(normalized, policy_input("ai_automation"))))

    def test_english_role_phrase_cannot_duplicate_qualifiers_during_lowering(self):
        raw = offline_policy("ai_automation")
        phrase = "Python developer or Java developer"
        raw["sources"]["jd_text"] += "\n" + phrase
        target = next(item for item in raw["requirements"] if item["requirement_id"] == "application_route")
        target["label"], target["source_text"] = phrase, phrase
        normalized = normalize_policy(raw)
        group = next((item for item in normalized.requirement_groups if item.group_id == "application_route"), None)
        if group is None:
            self.assertTrue(any(item.severity in ("ERROR", "CONFIRM") for item in diagnose_policy(normalized)))
        else:
            leaves = {item.requirement_id: item for item in normalized.requirements}
            self.assertEqual({leaves[key].label for key in group.members}, {"Python developer", "Java developer"})
        self.assertEqual(normalized.sources.model_dump(), raw["sources"])

    def test_full_compiler_retains_raw_response_while_repository_stores_normalized_policy(self):
        raw = tool_alias_policy()
        raw_content = json.dumps(raw, ensure_ascii=False)
        client = RecordedCompilerClient(content=raw_content)
        compiler = JobPolicyCompiler(client=client, model_name="offline-normalization-test-model")
        service = PolicyCompilerService(self.repo, compiler)
        result = service.compile_draft(self.ctx, "job1", policy_input("java_backend"))
        self.assertTrue(result["publishable"])
        self.assertEqual(compiler.last_response_content, raw_content)
        self.assertEqual(json.loads(compiler.last_response_content), raw)
        self.assertEqual(result["metrics"]["raw_response_sha256"], hashlib.sha256(raw_content.encode("utf-8")).hexdigest())
        self.assertTrue(result["metrics"]["policy_normalized"])
        self.assertTrue(result["metrics"]["normalization_actions"])
        saved = json.loads(result["draft"]["policy_json"])
        parsed = JobScoringPolicy.model_validate(saved)
        words = {word.canonical: word.aliases for group in parsed.keyword_groups for word in group.keywords}
        self.assertEqual(words["JUnit"], [])
        self.assertEqual(words["Kubernetes"], [])
        self.assertEqual(parsed.sources.model_dump(), raw["sources"])
        self.assertEqual(parsed.provenance.model_version, "offline-qwen-response")
        self.assertEqual(json.loads(result["compilation_run"]["policy_json"]), saved)
        self.assertEqual(json.loads(result["compilation_run"]["source_json"]), policy_input("java_backend"))
        persisted_metrics = json.loads(result["compilation_run"]["metrics_json"])
        self.assertEqual(persisted_metrics["raw_response_sha256"], result["metrics"]["raw_response_sha256"])
        encoded = json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(persisted_metrics["compiled_policy_sha256"], hashlib.sha256(encoded.encode("utf-8")).hexdigest())
        self.assertIn("ALIASES_REMOVED_UNGROUNDED", [item["code"] for item in result["diagnostics"]])
        self.assertEqual(result["compilation_run"]["status"], "SUCCEEDED")
        with self.assertRaises(NotFound):
            self.repo.get_current_policy(self.ctx, "job1")
        draft, run = result["draft"], result["compilation_run"]
        confirmation = service.confirm_draft(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                             confirmation_hash=draft["draft_hash"])
        profile = self.repo.create_execution_profile(self.ctx, {})
        sealed = service.create_version(self.ctx, "job1", draft["draft_id"], run["run_id"],
                                        confirmation["confirmation_id"], execution_profile_id=profile["profile_id"])
        service.publish_version(self.ctx, "job1", sealed["version"]["policy_id"], confirmation_hash=draft["draft_hash"],
                                enforce_quality_gate=False,
                                quality_override_reason="normalization mechanics; quality covered by gate tests")
        self.assertEqual(json.loads(self.repo.get_current_policy(self.ctx, "job1")["policy_json"]), saved)

    def test_raw_unknown_alias_cannot_bypass_validation_via_repository_success_flag(self):
        raw = tool_alias_policy()
        raw["scoring_dimensions"][0]["weight"] = 0.99  # ERROR: INVALID_WEIGHT_SUM
        draft = self.repo.create_policy_draft(self.ctx, "job1", raw,
                                              source_json=policy_input("java_backend"), diagnostics=[])
        with self.assertRaises(ValueError):
            self.repo.record_policy_compilation(self.ctx, "job1", policy_input("java_backend"),
                status="SUCCEEDED", provenance=raw["provenance"], policy_json=raw,
                draft_id=draft["draft_id"], draft_revision=draft["revision"])
        self.assertEqual(self.repo.list_policy_compilations(self.ctx, "job1"), [])
