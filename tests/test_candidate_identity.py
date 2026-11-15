"""Server-authoritative candidate identity: page metadata may never override it."""
import unittest

from browser.contracts import BrowserError, CandidateDetail
from browser.identity import CandidateDetailParser
from browser.mock_data import resume_template
from tests.pipeline_support import PipelineCase, resume_for


class ParserIdentityTests(unittest.TestCase):
    def test_page_meta_cannot_override_the_authoritative_identity(self):
        raw = resume_template("java", "geek-1", "job")
        raw["_meta"] = {
            "candidate_key": "other-job:attacker",
            "encrypt_geek_id": "attacker",
            "encrypt_job_id": "other-job",
        }
        resume = CandidateDetailParser.parse(
            CandidateDetail(identity="geek-1", name="n", job_id="job", raw=raw),
            job_id="job")
        self.assertEqual(resume.meta.candidate_key, "job:geek-1")
        self.assertEqual(resume.meta.encrypt_geek_id, "geek-1")
        self.assertEqual(resume.meta.encrypt_job_id, "")

    def test_parser_requires_stable_identity(self):
        raw = resume_template("java", "geek-1", "job")
        empty = CandidateDetail.model_construct(
            identity="", name="n", job_id="job", raw=raw)
        with self.assertRaises(BrowserError):
            CandidateDetailParser.parse(empty, job_id="job")


class RepositoryIdentityTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self.publish_case(job_id="job")
        self.batch = self.repo.create_scan_batch(self.ctx, "job")

    def acquire(self, key, *, source="browser", resume=None):
        return self.repo.acquire_candidate_for_job(
            self.ctx, "job", self.batch["batch_id"], key, source=source,
            resume=resume if resume is not None else resume_for(key))

    def test_browser_import_requires_the_canonical_key(self):
        resume = resume_for("job:geek-a")
        resume["_meta"]["encrypt_geek_id"] = "geek-a"
        resume["_meta"]["encrypt_job_id"] = "job"
        acquired = self.acquire("job:geek-a", resume=resume)
        self.assertEqual(acquired["job_candidate"]["candidate_key"], "job:geek-a")

        mismatch = resume_for("job:geek-b")
        mismatch["_meta"]["encrypt_geek_id"] = "geek-other"
        mismatch["_meta"]["encrypt_job_id"] = "job"
        with self.assertRaises(ValueError):
            self.acquire("job:geek-b", resume=mismatch)

    def test_cross_job_candidate_key_is_rejected(self):
        resume = resume_for("other-job:geek-a")
        resume["_meta"]["encrypt_geek_id"] = "geek-a"
        resume["_meta"]["encrypt_job_id"] = "other-job"
        with self.assertRaises(ValueError):
            self.acquire("other-job:geek-a", source="browser", resume=resume)

    def test_manual_import_ignores_page_meta_candidate_key(self):
        resume = resume_for("manual-1")
        resume["_meta"]["candidate_key"] = "job:geek-hacked"
        acquired = self.acquire("manual-1", source="manual", resume=resume)
        self.assertEqual(acquired["job_candidate"]["candidate_key"], "manual-1")
        self.assertEqual(acquired["job_candidate"]["candidate_id"],
                         acquired["job_candidate"]["candidate_id"])

    def test_browser_import_requires_a_stable_geek_id(self):
        resume = resume_for("job:geek-a")
        resume["_meta"].pop("encrypt_geek_id", None)
        with self.assertRaises(ValueError):
            self.acquire("job:geek-a", resume=resume)


if __name__ == "__main__":
    unittest.main()
