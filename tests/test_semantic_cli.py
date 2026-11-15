"""In-process CLI tests for embedding-check / semantic-prepare / semantic-diagnose.

The embedding model is replaced by a deterministic, non-BGE stand-in: these
tests exercise CLI wiring, permission and version isolation and output hygiene,
never real semantic quality.
"""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
import unittest
from unittest import mock

import numpy as np

from engine import semantic_cli
from tests.prescreen_support import PrescreenDatabaseCase


class _FakePoolingModule:
    def __init__(self, mode, dimension):
        self.pooling_mode = mode
        self.embedding_dimension = dimension


class FakeLocalModel:
    """Deterministic stand-in for a locally loaded sentence-transformers model."""

    def __init__(self, *, pooling="cls", dimension=1024, max_seq_length=512):
        self._pooling = _FakePoolingModule(pooling, dimension)
        self._dimension = dimension
        self.max_seq_length = max_seq_length
        self.calls = 0

    def __iter__(self):
        return iter([self._pooling])

    def get_embedding_dimension(self):
        return self._dimension

    def encode(self, texts, **kwargs):
        self.calls += 1
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
            rows.append(rng.standard_normal(self._dimension))
        return np.asarray(rows)


class SemanticCliTests(PrescreenDatabaseCase):
    def setUp(self):
        super().setUp()
        self.v1 = self.publish("java_backend", "job")
        acquired = self.acquire("job", "cli-candidate")
        self.jcid = acquired["job_candidate"]["job_candidate_id"]
        self.model = FakeLocalModel()
        patcher = mock.patch.object(semantic_cli, "_load_embedder", return_value=self.model)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, command, *arguments):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = semantic_cli.main(command, list(map(str, arguments)))
        return code, json.loads(buffer.getvalue())

    def scoped(self, *extra, user_id="owner"):
        return ["--database", str(self.path), "--org-id", "org", "--user-id", user_id,
                "--job-id", "job", *extra]

    def prepare(self):
        code, result = self.run_cli("semantic-prepare", *self.scoped("--job-candidate-id", self.jcid))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["policy"]["status"], "READY")
        self.assertEqual(result["candidate"]["status"], "READY")
        return result

    def test_embedding_check_confirms_runtime_matches_declared_profile(self):
        code, result = self.run_cli("embedding-check")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["runtime"]["pooling"]["actual"], "CLS")
        self.assertEqual(result["runtime"]["dimension"]["actual"], 1024)
        self.assertTrue(all(check["ok"] for check in result["runtime"].values()))

    def test_embedding_check_fails_when_runtime_pooling_drifts_from_profile(self):
        self.model = FakeLocalModel(pooling="mean")
        semantic_cli._load_embedder.return_value = self.model
        code, result = self.run_cli("embedding-check")
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["runtime"]["pooling"]["ok"])

    def test_prepare_then_diagnose_scores_without_calling_models_again(self):
        self.prepare()
        before = self.model.calls
        code, result = self.run_cli("semantic-diagnose", *self.scoped("--job-candidate-id", self.jcid))
        self.assertEqual(code, 0, result)
        semantic = result["semantic"]
        self.assertIn(semantic["scoring_status"], {"SEMANTIC_SCORED", "PARTIAL_SEMANTIC"})
        self.assertIsNotNone(semantic["semantic_score"])
        self.assertEqual(semantic["assessment_scope"], "TIER2_ONLY")
        self.assertFalse(semantic["routing_authority"])
        self.assertFalse(semantic["hard_filter_override"])
        self.assertEqual(self.model.calls, before)

    def test_diagnose_before_prepare_reports_asset_incomplete_without_score(self):
        code, result = self.run_cli("semantic-diagnose", *self.scoped("--job-candidate-id", self.jcid))
        self.assertEqual(code, 1)
        semantic = result["semantic"]
        self.assertEqual(semantic["scoring_status"], "ASSET_INCOMPLETE")
        self.assertIsNone(semantic["semantic_score"])

    def test_diagnose_keeps_candidate_bound_policy_after_newer_publication(self):
        self.prepare()
        self.publish("java_backend", "job")
        code, result = self.run_cli("semantic-diagnose", *self.scoped("--job-candidate-id", self.jcid))
        self.assertEqual(code, 0, result)
        semantic = result["semantic"]
        self.assertEqual(semantic["policy_id"], self.v1["policy_id"])
        self.assertEqual(semantic["policy_version"], self.v1["policy_version"])

    def test_prepare_rejects_an_ungranted_identity(self):
        self.repo.create_user(self.system, "cli:stranger", user_id="stranger")
        code, result = self.run_cli("semantic-prepare",
                                    *self.scoped("--job-candidate-id", self.jcid, user_id="stranger"))
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "PermissionDenied")

    def test_output_carries_no_api_key_or_raw_resume_payload(self):
        self.prepare()
        secret = "sk-cli-secret-should-never-appear"
        target = self.path.parent / "diagnose.json"
        with mock.patch.dict(os.environ, {"LLM_API_KEY": secret, "OPENAI_API_KEY": secret}):
            code, _ = self.run_cli("semantic-diagnose",
                                   *self.scoped("--job-candidate-id", self.jcid, "--output", target))
        self.assertEqual(code, 0)
        text = target.read_text(encoding="utf-8")
        self.assertNotIn(secret, text)
        self.assertNotIn("raw_resume_json", text)
        self.assertNotIn("normalized_resume_json", text)


if __name__ == "__main__":
    unittest.main()
