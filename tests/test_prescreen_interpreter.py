"""Actual candidate bindings, immutable rules/cache and scope checks."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from unittest.mock import patch

from engine.policy_interpreter import PolicyInterpreter, content_hash
from schemas.prescreen import PrescreenExecutionProfile
from storage.models import AccessContext
from storage.repositories import PermissionDenied
from tests.policy_fixtures import offline_policy
from tests.prescreen_support import PrescreenDatabaseCase


class PolicyInterpreterTests(PrescreenDatabaseCase):
    def compile(self, interpreter, policy=None, **identity):
        return interpreter.compile_policy(policy or offline_policy("java_backend"),
            **{"org_id": "org", "job_id": "job", "policy_id": "p1", "policy_version": 1, **identity})

    def test_bound_v1_is_loaded_after_v2_without_current_policy_reads(self):
        first = self.publish(policy_id="p1")
        candidate = self.acquire()
        second = self.publish(policy_id="p2")
        engine = PolicyInterpreter(self.repo)
        with patch.object(self.repo, "get_current_policy", side_effect=AssertionError("must not read current")):
            bound = engine.load_for_candidate(self.ctx, "job", candidate["job_candidate"]["job_candidate_id"])
        self.assertEqual((bound.plan.policy_id, bound.plan.policy_version), (first["policy_id"], 1))
        self.assertEqual(second["policy_version"], 2)
        later = self.acquire(key="new-candidate", resume=self.resume("new-candidate"))
        self.assertEqual(engine.load_for_candidate(self.ctx, "job", later["job_candidate"]["job_candidate_id"]).plan.policy_id, "p2")

    def test_same_version_reuses_cache_and_returned_models_cannot_poison_it(self):
        engine = PolicyInterpreter()
        first = self.compile(engine)
        first.policy.job.title = "mutated outside cache"
        self.assertNotEqual(first.policy.job.title, "mutated outside cache")
        with self.assertRaises(FrozenInstanceError):
            first.policy_id = "changed"
        self.assertIs(first, self.compile(engine))
        self.assertEqual((engine.cache_hits, engine.cache_misses, engine.cache_size), (1, 1, 1))

    def test_identical_ids_in_different_jobs_organizations_or_profiles_do_not_collide(self):
        engine = PolicyInterpreter()
        a = self.compile(engine)
        b = self.compile(engine, job_id="other-job")
        c = self.compile(engine, org_id="other-org")
        d = self.compile(PolicyInterpreter(profile=PrescreenExecutionProfile(salary_implicit_period="month")))
        self.assertIsNot(a, b)
        self.assertIsNot(a, c)
        self.assertNotEqual(a.profile_hash, d.profile_hash)

    def test_same_identity_with_different_content_or_wrong_hash_is_rejected(self):
        engine = PolicyInterpreter()
        self.compile(engine)
        modified = offline_policy("java_backend")
        modified["thresholds"]["coarse_pass"] += 1
        with self.assertRaises(ValueError):
            self.compile(engine, modified)
        with self.assertRaises(ValueError):
            self.compile(PolicyInterpreter(), policy_hash="not-the-snapshot-hash")

    def test_stored_json_hash_does_not_change_when_float_fields_were_integer_json(self):
        value = offline_policy("java_backend")
        value["thresholds"]["coarse_pass"] = 60
        raw_hash = content_hash(value)
        plan = self.compile(PolicyInterpreter(), value, policy_hash=raw_hash)
        self.assertEqual(plan.policy_hash, raw_hash)
        self.assertEqual(plan.policy.thresholds.coarse_pass, 60.0)

    def test_lru_cache_is_bounded(self):
        engine = PolicyInterpreter(max_cache_entries=2)
        for version in (1, 2, 3):
            self.compile(engine, policy_version=version)
        self.assertEqual(engine.cache_size, 2)
        misses = engine.cache_misses
        self.compile(engine, policy_version=1)
        self.assertEqual(engine.cache_misses, misses + 1)

    def test_unknown_refs_cycles_and_duplicate_dimensions_cannot_compile(self):
        for mutate in (lambda p: p["requirement_groups"][0]["members"].append("missing"),
                       lambda p: p["requirement_groups"][0]["members"].append(p["requirement_groups"][0]["group_id"]),
                       lambda p: p["scoring_dimensions"].append(deepcopy(p["scoring_dimensions"][0]))):
            value = offline_policy("java_backend")
            mutate(value)
            with self.assertRaises(ValueError):
                self.compile(PolicyInterpreter(), value)

    def test_group_scoped_words_never_prove_all_atomic_requirements(self):
        value = offline_policy("java_backend")
        group = value["keyword_groups"][0]
        group["keywords"].append({"canonical": "Derived capability", "aliases": [], "weight": 8.0})
        plan = self.compile(PolicyInterpreter(), value)
        keyword = next(item for item in plan.keywords if item.canonical == "Derived capability")
        self.assertEqual(keyword.precise_requirement_ids, ())
        self.assertGreater(len(keyword.declared_requirement_ids), 1)
        self.assertTrue(any("GROUP_SCOPED_KEYWORD" in item for item in plan.diagnostics))

    def test_duplicate_canonicals_and_alias_equivalent_rules_use_one_max_weight(self):
        value = offline_policy("java_backend")
        group = value["keyword_groups"][0]
        existing_weight = next(word["weight"] for word in group["keywords"] if word["canonical"] == "Spring Boot")
        group["keywords"].extend([{"canonical": "SpringBoot", "aliases": ["Spring Boot"], "weight": 9.0},
                                  {"canonical": "Spring Boot", "aliases": [], "weight": 7.0}])
        plan = self.compile(PolicyInterpreter(), value)
        names = [item for item in plan.keywords if item.canonical in ("Spring Boot", "SpringBoot")]
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0].weight, max(existing_weight, 9.0, 7.0))

    def test_authorization_is_checked_even_after_cache_hit(self):
        self.publish()
        row = self.acquire()["job_candidate"]
        self.repo.create_user(self.system, "prescreen:hr", user_id="hr")
        self.repo.add_organization_member(self.ctx, "hr")
        self.repo.grant_job_access(self.ctx, "job", "hr", permissions=("view",))
        hr = AccessContext("org", "hr")
        engine = PolicyInterpreter(self.repo)
        engine.load_for_candidate(hr, "job", row["job_candidate_id"])
        self.repo.revoke_job_access(self.ctx, "job", "hr")
        with self.assertRaises(PermissionDenied):
            engine.load_for_candidate(hr, "job", row["job_candidate_id"])

    def test_frozen_source_mismatch_on_cached_plan_is_rejected(self):
        engine = PolicyInterpreter()
        value = offline_policy("java_backend")
        self.compile(engine, value)
        with self.assertRaises(ValueError):
            self.compile(engine, value, source_input={"job_title": "wrong", **value["sources"]})

    def test_profile_objects_are_revalidated_before_rules_can_be_cached(self):
        invalid = PrescreenExecutionProfile().model_copy(update={"profile_version": ""})
        with self.assertRaises(ValueError):
            PolicyInterpreter(profile=invalid)
