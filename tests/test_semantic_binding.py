"""P0-1: live semantic preparation must honor the candidate's locked Policy version."""
import unittest

import numpy as np

from server.semantic_runtime import LiveSemanticRuntime
from tests.pipeline_support import PipelineCase


class FakeEmbedder:
    """Deterministic 1024-dim unit vectors; exercises the real asset pipeline."""

    def encode(self, texts, normalize_embeddings=True, **kwargs):
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        vectors = np.zeros((len(items), 1024), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors[0] if single else vectors


class SemanticPolicyBindingTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.v1 = self.publish_case(job_id="job")
        acquired = self.import_resume(key="batch-a-candidate")
        self.jcid = acquired["job_candidate"]["job_candidate_id"]
        # Publish v2 after batch A already bound its candidate to v1.
        self.v2 = self.publish("java_backend", job_id="job")
        self.assertEqual(self.v2["policy_version"], 2)
        self.runtime = LiveSemanticRuntime(self.repo)
        self.runtime._model = FakeEmbedder()  # no BGE download in tests

    def test_candidate_keeps_its_locked_policy_for_semantics(self):
        candidate = self.repo.get_job_candidate(self.ctx, "job", self.jcid)
        self.assertEqual(candidate["policy_id"], self.v1["policy_id"])
        self.assertEqual(candidate["policy_version"], 1)

        prepared = self.runtime.prepare(self.ctx, "job", self.jcid)
        self.assertEqual(prepared["status"], "PREPARED")
        self.assertEqual(prepared["policy"]["policy_id"], self.v1["policy_id"])
        self.assertEqual(prepared["policy"]["policy_version"], 1)
        self.assertEqual(prepared["policy"]["policy_hash"], self.v1["policy_hash"])

        evaluation = self.runtime.semantic_service(self.repo).evaluate(
            self.ctx, "job", self.jcid)
        self.assertEqual(evaluation.policy_id, self.v1["policy_id"])
        self.assertEqual(evaluation.policy_version, 1)
        self.assertEqual(evaluation.policy_hash, self.v1["policy_hash"])
        self.assertEqual(evaluation.resume_id,
                         self.repo.get_sealed_input(self.ctx, "job", self.jcid)["resume_id"])

    def test_policy_only_preparation_uses_current_publication(self):
        prepared = self.runtime.prepare(self.ctx, "job")
        self.assertEqual(prepared["policy"]["policy_id"], self.v2["policy_id"])
        self.assertEqual(prepared["policy"]["policy_version"], 2)
        self.assertEqual(prepared["policy"]["policy_hash"], self.v2["policy_hash"])

    def test_existing_candidate_binding_is_never_upgraded(self):
        # Re-importing the same candidate (same key) keeps the original binding.
        again = self.import_resume(key="batch-a-candidate")
        self.assertEqual(again["job_candidate"]["policy_id"], self.v1["policy_id"])
        self.assertEqual(again["job_candidate"]["policy_version"], 1)
        self.assertFalse(again["is_new"])


if __name__ == "__main__":
    unittest.main()
