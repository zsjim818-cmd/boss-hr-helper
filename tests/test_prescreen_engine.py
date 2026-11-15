"""Read-only production service against actual policies, bindings and snapshots."""
from copy import deepcopy
from dataclasses import replace
import json
import os
import subprocess
import sys
from unittest.mock import patch

from pydantic import ValidationError

from config.settings import PROJECT_ROOT
from engine.prescreen_engine import PreliminaryScreeningEngine, resolve_source
from schemas.prescreen import PreliminaryEvaluation, PrescreenExecutionProfile, SourceWeights
from storage.models import AccessContext
from storage.repositories import PermissionDenied, NotFound
from tests.policy_fixtures import offline_policy
from tests.prescreen_support import PrescreenDatabaseCase


class PreliminaryEngineTests(PrescreenDatabaseCase):
    def full_resume(self, case_id="java_backend", **changes):
        policy = offline_policy(case_id)
        words = list(dict.fromkeys(word["canonical"] for group in policy["keyword_groups"] for word in group["keywords"]))
        return self.resume(work_experience=[{"company": "Synthetic", "responsibility":
            "。".join("负责并独立完成" + word + "的具体工作并交付结果" for word in words), "keywords": []}], **changes)

    def screen(self, engine, row):
        return engine.evaluate(self.ctx, row["job_id"], row["job_candidate_id"])

    def test_three_jobs_have_their_own_hard_filters_dimensions_and_weights(self):
        engine = PreliminaryScreeningEngine(self.repo)
        layouts = []
        for case in ("ai_automation", "java_backend", "b2b_sales"):
            version = self.publish(case, case)
            acquired = self.acquire(case, case + ":person", resume=self.full_resume(case))
            output = self.screen(engine, acquired["job_candidate"])
            self.assertEqual(output.hard_filter_status, "PASS")
            self.assertGreaterEqual(output.preliminary_score, output.thresholds.coarse_pass)
            self.assertEqual(output.policy_id, version["policy_id"])
            expected = offline_policy(case)["scoring_dimensions"]
            self.assertEqual([(d.dimension_id, d.dimension_weight) for d in output.dimension_scores],
                             [(d["dimension_id"], d["weight"]) for d in expected])
            self.assertEqual(output.scoring_status, "READY_FOR_DEEP")
            self.assertFalse(output.deep_thresholds_applied)
            self.assertIsNone(output.deep_score)
            layouts.append(tuple(d.dimension_id for d in output.dimension_scores))
        self.assertEqual(len(set(layouts)), 3)

    def test_high_keyword_score_cannot_override_hard_failure(self):
        self.publish()
        acquired = self.acquire(resume=self.full_resume(degree="高中", education=[{"degree": "高中"}]))
        output = self.screen(PreliminaryScreeningEngine(self.repo), acquired["job_candidate"])
        self.assertGreaterEqual(output.preliminary_score, 60)
        self.assertEqual((output.hard_filter_status, output.scoring_status), ("FAIL", "HARD_FILTERED"))

    def test_unknown_hard_field_has_review_reason_even_when_keywords_are_strong(self):
        self.publish()
        acquired = self.acquire(resume=self.full_resume(work_years=""))
        output = self.screen(PreliminaryScreeningEngine(self.repo), acquired["job_candidate"])
        self.assertEqual(output.scoring_status, "HARD_UNKNOWN")
        self.assertEqual(output.hard_filter_status, "UNKNOWN")
        self.assertTrue(output.unknown_requirements)
        self.assertTrue(any("工作年限" in reason for reason in output.review_reasons))

    def test_unsealed_partial_preview_requires_explicit_acquired_resume_id(self):
        self.publish()
        partial = self.resume()
        partial.pop("skills")
        acquired = self.acquire(resume=partial)
        self.assertIsNone(acquired["sealed_input"])
        engine = PreliminaryScreeningEngine(self.repo)
        with self.assertRaises(ValueError):
            self.screen(engine, acquired["job_candidate"])
        output = engine.evaluate(self.ctx, "job", acquired["job_candidate"]["job_candidate_id"],
                                 resume_id=acquired["resume_snapshot"]["resume_id"])
        self.assertEqual((output.input_kind, output.scoring_status), ("PARTIAL_PREVIEW", "DATA_INCOMPLETE"))
        self.assertIsNotNone(output.preliminary_score)

    def test_collection_or_mapping_failure_has_no_fake_score(self):
        self.publish()
        for key, changes in (("failed", {"ok": False}), ("mapping", {"work_years": {"wrong": "object"}})):
            with self.subTest(key=key):
                acquired = self.acquire(key=key, resume=self.resume(key, **changes))
                output = PreliminaryScreeningEngine(self.repo).evaluate(self.ctx, "job",
                    acquired["job_candidate"]["job_candidate_id"], resume_id=acquired["resume_snapshot"]["resume_id"])
                self.assertEqual(output.scoring_status, "SCORING_FAILED")
                self.assertIsNone(output.preliminary_score)
                self.assertEqual(output.dimension_scores, [])

    def test_sealed_input_stays_fixed_after_reacquisition(self):
        self.publish()
        acquired = self.acquire(resume=self.full_resume())
        engine = PreliminaryScreeningEngine(self.repo)
        original = self.screen(engine, acquired["job_candidate"])
        repeated = self.acquire(resume=self.resume(work_experience=[], skills=""))
        self.assertNotEqual(repeated["resume_snapshot"]["resume_id"], original.resume_id)
        self.assertEqual(self.screen(engine, acquired["job_candidate"]), original)
        with self.assertRaises(ValueError):
            engine.evaluate(self.ctx, "job", acquired["job_candidate"]["job_candidate_id"],
                            resume_id=repeated["resume_snapshot"]["resume_id"])

    def test_v1_candidate_and_new_v2_candidate_do_not_share_rules_or_thresholds(self):
        self.publish(policy_id="p1")
        a = self.acquire(key="A", resume=self.full_resume())
        changed = offline_policy("java_backend")
        changed["thresholds"]["coarse_pass"] = 99.0
        self.publish(policy=changed, policy_id="p2")
        b = self.acquire(key="B", resume=self.full_resume())
        engine = PreliminaryScreeningEngine(self.repo)
        with patch.object(self.repo, "get_current_policy", side_effect=AssertionError("must not use current")):
            old = self.screen(engine, a["job_candidate"])
            new = self.screen(engine, b["job_candidate"])
        self.assertEqual((old.policy_id, old.policy_version, old.thresholds.coarse_pass), ("p1", 1, 60.0))
        self.assertEqual((new.policy_id, new.policy_version, new.thresholds.coarse_pass), ("p2", 2, 99.0))

    def test_all_returned_evidence_is_an_exact_raw_snapshot_span(self):
        self.publish()
        value = self.full_resume(skills=["熟悉Java", "Spring Boot"], certifications=["SQL培训证书"])
        acquired = self.acquire(resume=value)
        output = self.screen(PreliminaryScreeningEngine(self.repo), acquired["job_candidate"])
        for evidence in [*output.evidence, *(e for dimension in output.dimension_scores for e in dimension.evidence)]:
            text = resolve_source(value, evidence.source_path)
            self.assertEqual(text[evidence.start:evidence.end], evidence.quote)
        self.assertEqual(PreliminaryEvaluation.model_validate_json(output.model_dump_json()), output)

    def test_read_only_scoring_does_not_save_formal_results_or_change_policy(self):
        version = self.publish()
        acquired = self.acquire()
        row = acquired["job_candidate"]
        before = self.repo.get_resume_snapshot(self.ctx, "job", row["job_candidate_id"], acquired["sealed_input"]["resume_id"])
        self.screen(PreliminaryScreeningEngine(self.repo), row)
        self.assertEqual(self.repo.get_candidate_evaluations(self.ctx, "job", row["job_candidate_id"]), [])
        self.assertEqual(self.repo.get_policy_version(self.ctx, "job", version["policy_id"]), version)
        self.assertEqual(self.repo.get_resume_snapshot(self.ctx, "job", row["job_candidate_id"], before["resume_id"]), before)

    def test_view_without_score_permission_is_rejected(self):
        self.publish()
        row = self.acquire()["job_candidate"]
        self.repo.create_user(self.system, "view-only", user_id="viewer")
        self.repo.add_organization_member(self.ctx, "viewer")
        self.repo.grant_job_access(self.ctx, "job", "viewer", permissions=("view",))
        with self.assertRaises(PermissionDenied):
            PreliminaryScreeningEngine(self.repo).evaluate(AccessContext("org", "viewer"), "job", row["job_candidate_id"])

    def test_work_authorization_comes_only_from_the_same_raw_snapshot(self):
        policy = offline_policy("java_backend")
        quote = "必须具备合法工作许可"
        policy["sources"]["hr_requirements"] += "\n" + quote
        policy["requirements"].append({"requirement_id": "auth", "label": "合法工作许可", "source_type": "HR",
            "source_text": quote, "priority": "REQUIRED", "requirement_type": "OTHER", "needs_confirmation": False})
        policy["hard_filters"].append({"filter_id": "auth_filter", "requirement_id": "auth", "field": "work_authorization",
            "operator": "EQ", "value_text": "有工作许可", "value_number": None, "value_list": [], "unit": None, "on_unknown": "REVIEW"})
        self.publish(policy=policy)
        acquired = self.acquire(resume=self.full_resume(work_authorization="有工作许可"))
        output = PreliminaryScreeningEngine(self.repo).evaluate(self.ctx, "job", acquired["job_candidate"]["job_candidate_id"],
            resume_id=acquired["resume_snapshot"]["resume_id"])
        self.assertEqual(output.hard_filter_status, "PASS")
        self.assertEqual(output.scoring_status, "DATA_INCOMPLETE")  # Old mapper's explicit unknown-field issue remains visible.
        self.assertTrue(any(e.source_path == "work_authorization" for e in output.evidence))

    def test_strict_contract_rejects_overriding_hard_failure_or_fake_deep_score(self):
        self.publish()
        row = self.acquire(resume=self.full_resume(degree="高中", education=[{"degree": "高中"}]))["job_candidate"]
        result = self.screen(PreliminaryScreeningEngine(self.repo), row).model_dump(mode="json")
        for change in ({"scoring_status": "READY_FOR_DEEP"}, {"deep_score": 99.0}, {"unexpected_field": True}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                PreliminaryEvaluation.model_validate({**result, **change})

    def test_runtime_profile_rejects_auxiliary_weights_stronger_than_practice(self):
        with self.assertRaises(ValidationError):
            PrescreenExecutionProfile(source_weights=SourceWeights(skills=5.0))

    def test_strict_contract_cannot_desynchronize_stages_or_forge_ready_status(self):
        self.publish()
        row = self.acquire(resume=self.full_resume())["job_candidate"]
        original = self.screen(PreliminaryScreeningEngine(self.repo), row).model_dump(mode="json")
        self.assertEqual(original["scoring_status"], "READY_FOR_DEEP")
        changes = [{"input_kind": "PARTIAL_PREVIEW"}, {"input_has_issues": True},
                   {"scoring_status": "HARD_UNKNOWN"}, {"scoring_status": "BELOW_COARSE"}]
        forged_tier1 = deepcopy(original["tier1"])
        forged_tier1["preliminary_score"] = 0.0
        changes.append({"tier1": forged_tier1})
        for change in changes:
            with self.subTest(change=list(change)), self.assertRaises(ValidationError):
                PreliminaryEvaluation.model_validate({**original, **change})

    def test_corrupt_snapshot_hash_never_produces_a_preliminary_score(self):
        self.publish()
        acquired = self.acquire(resume=self.full_resume())
        row = acquired["job_candidate"]
        snapshot = self.repo.get_resume_snapshot(self.ctx, "job", row["job_candidate_id"], acquired["sealed_input"]["resume_id"])
        for field in ("raw_hash", "content_hash"):
            damaged = {**snapshot, field: "not-the-real-content-hash"}
            with self.subTest(field=field), patch.object(self.repo, "get_resume_snapshot", return_value=damaged):
                result = self.screen(PreliminaryScreeningEngine(self.repo), row)
                self.assertEqual(result.scoring_status, "SCORING_FAILED")
                self.assertIsNone(result.preliminary_score)

    def test_injected_rule_plan_cannot_change_the_bound_candidate_scope(self):
        self.publish()
        row = self.acquire(resume=self.full_resume())["job_candidate"]
        engine = PreliminaryScreeningEngine(self.repo)
        actual = engine.interpreter.load_for_candidate(self.ctx, "job", row["job_candidate_id"])
        for change in ({"org_id": "foreign"}, {"job_id": "foreign"}, {"policy_version": 99}):
            wrong = replace(actual, plan=replace(actual.plan, **change))
            with self.subTest(change=change), patch.object(engine.interpreter, "load_for_candidate", return_value=wrong):
                with self.assertRaises(ValueError):
                    self.screen(engine, row)

    def test_injected_interpreter_cannot_use_another_repository(self):
        from engine.policy_interpreter import PolicyInterpreter
        with self.assertRaises(ValueError):
            PreliminaryScreeningEngine(self.repo, interpreter=PolicyInterpreter())

    def test_cli_from_another_working_directory_and_no_model_client_imports(self):
        self.publish()
        acquired = self.acquire()
        result = subprocess.run([sys.executable, "-X", "utf8", str(PROJECT_ROOT / "main.py"), "prescreen",
            "--database", str(self.path), "--org-id", "org", "--user-id", "owner", "--job-id", "job",
            "--job-candidate-id", acquired["job_candidate"]["job_candidate_id"]], cwd=self.directory.name,
            capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["assessment_scope"], "TIER0_TIER1_ONLY")
        script = "import sys; import engine.prescreen_engine; assert 'openai' not in sys.modules; assert 'torch' not in sys.modules; assert 'sentence_transformers' not in sys.modules"
        loaded = subprocess.run([sys.executable, "-X", "utf8", "-c", script], cwd=PROJECT_ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
