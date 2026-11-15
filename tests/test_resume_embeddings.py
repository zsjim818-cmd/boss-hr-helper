from contextlib import closing
import unittest

from engine.embedding_service import ResumeEmbeddingService
from engine.resume_chunker import chunk_resume, validate_chunk_sources
from engine.semantic_profiles import load_semantic_profiles
from storage.repositories import NotFound
from tests.test_policy_embeddings import RecordingEmbedder, profiles
from tests.prescreen_support import PrescreenDatabaseCase


class ResumeChunkerTests(unittest.TestCase):
    def test_chunks_keep_exact_paths_offsets_quality_and_experience_identity(self):
        raw = {
            "work_experience": [{
                "responsibility": "Python Java SQL 技术栈",
                "performance": "完成订单服务交付并记录故障处理。",
                "keywords": ["Java"],
            }],
            "project_experience": [{
                "description": "负责实现支付接口；优化事务边界。",
                "achievement": "交付可复核测试报告。",
            }],
            "education": [{"thesis_description": "研究检索排序并完成实验。"}],
            "skills": ["Java", "JavaScript"],
            "certifications": ["PMP"],
            "advantage": "熟悉客户沟通与项目协作。",
        }
        profile = load_semantic_profiles().chunker
        first = chunk_resume(raw, org_id="org", candidate_id="candidate", resume_id="resume",
                             resume_content_hash="a" * 64, profile=profile)
        second = chunk_resume(raw, org_id="org", candidate_id="candidate", resume_id="resume",
                              resume_content_hash="a" * 64, profile=profile)
        self.assertEqual(first, second)
        validate_chunk_sources(raw, first)
        by_path = {item.source_path: item for item in first}
        self.assertEqual(by_path["work_experience[0].responsibility"].evidence_quality_type,
                         "AUXILIARY")
        self.assertEqual(by_path["work_experience[0].performance"].evidence_quality_type,
                         "DELIVERABLE")
        self.assertEqual(by_path["work_experience[0].keywords[0]"].source_type, "WORK_TAGS")
        self.assertEqual(by_path["skills[0]"].text, "Java")
        self.assertEqual(by_path["skills[0]"].evidence_quality_type, "AUXILIARY")
        work_keys = {item.experience_key for item in first
                     if item.source_path.startswith("work_experience[0]")}
        self.assertEqual(work_keys, {"work_experience[0]"})

    def test_long_single_experience_is_split_but_keeps_one_experience_key(self):
        raw = {"work_experience": [{"responsibility": "负责" + "流程自动化" * 100,
                                      "performance": "", "keywords": []}],
               "project_experience": [], "education": [], "skills": [],
               "certifications": [], "advantage": ""}
        chunks = chunk_resume(raw, org_id="org", candidate_id="candidate", resume_id="resume",
                              resume_content_hash="b" * 64, profile=load_semantic_profiles().chunker)
        self.assertGreater(len(chunks), 1)
        self.assertEqual({item.experience_key for item in chunks}, {"work_experience[0]"})
        self.assertTrue(all(item.truncated for item in chunks))
        validate_chunk_sources(raw, chunks)


class ResumeEmbeddingTests(PrescreenDatabaseCase):
    def setUp(self):
        super().setUp()
        self.version = self.publish("java_backend", "job")
        self.acquired = self.acquire("job", "candidate", resume=self.resume("candidate"))
        self.embedder = RecordingEmbedder()
        self.service = ResumeEmbeddingService(self.repo, self.embedder, profiles=profiles())

    def test_sealed_resume_chunks_and_vectors_persist_and_hit_cache_after_restart(self):
        first = self.service.prepare_candidate(
            self.ctx, "job", self.acquired["job_candidate"]["job_candidate_id"])
        self.assertEqual(first["status"], "READY")
        self.assertGreater(first["chunk_count"], 0)
        self.assertEqual(first["encoded_count"], self.embedder.encoded_count)
        before = self.embedder.encoded_count
        restarted_service = ResumeEmbeddingService(
            type(self.repo)(type(self.db)(self.path)), self.embedder, profiles=profiles())
        second = restarted_service.prepare_candidate(
            self.ctx, "job", self.acquired["job_candidate"]["job_candidate_id"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["resume_id"], first["resume_id"])
        self.assertEqual(self.embedder.encoded_count, before)

    def test_later_collection_never_replaces_sealed_semantic_input(self):
        original = self.acquired["sealed_input"]["resume_id"]
        newer = self.resume("candidate", advantage="负责新增销售流程并交付另一份完整材料。")
        self.repo.append_resume_snapshot(
            self.ctx, "job", self.acquired["job_candidate"]["job_candidate_id"], newer,
            batch_id=self.acquired["job_candidate"]["first_batch_id"])
        result = self.service.prepare_candidate(
            self.ctx, "job", self.acquired["job_candidate"]["job_candidate_id"])
        self.assertEqual(result["resume_id"], original)

    def test_same_candidate_resume_across_jobs_reuses_vectors_not_score_results(self):
        # Build a second isolated fixture in the same DB with a stable source identity.
        other_version = self.publish("b2b_sales", "job2")
        first_batch = self.repo.create_scan_batch(self.ctx, "job")
        shared_resume = self.resume("shared")
        first = self.repo.acquire_candidate_for_job(
            self.ctx, "job", first_batch["batch_id"], "shared-job1", external_id="shared-ext",
            resume=shared_resume)
        first_result = self.service.prepare_candidate(
            self.ctx, "job", first["job_candidate"]["job_candidate_id"])
        before = self.embedder.encoded_count
        second_batch = self.repo.create_scan_batch(self.ctx, "job2")
        second = self.repo.acquire_candidate_for_job(
            self.ctx, "job2", second_batch["batch_id"], "shared-job2", external_id="shared-ext",
            resume=shared_resume)
        second_result = self.service.prepare_candidate(
            self.ctx, "job2", second["job_candidate"]["job_candidate_id"])
        self.assertNotEqual(first["job_candidate"]["job_candidate_id"],
                            second["job_candidate"]["job_candidate_id"])
        self.assertEqual(first_result["candidate_id"], second_result["candidate_id"])
        self.assertEqual(first_result["resume_id"], second_result["resume_id"])
        self.assertTrue(second_result["cache_hit"])
        self.assertEqual(self.embedder.encoded_count, before)
        self.assertNotEqual(self.version["policy_id"], other_version["policy_id"])

    def test_model_failure_records_attempt_without_vector_manifest(self):
        broken_profiles = profiles("resume-broken-profile", "broken-r1")
        broken = ResumeEmbeddingService(
            self.repo, RecordingEmbedder(bad_dimension=True), profiles=broken_profiles)
        jcid = self.acquired["job_candidate"]["job_candidate_id"]
        with self.assertRaisesRegex(ValueError, "batch shape|size 3"):
            broken.prepare_candidate(self.ctx, "job", jcid)
        with self.assertRaises(NotFound):
            self.repo.get_resume_embedding_manifest(
                self.ctx, "job", jcid, self.acquired["sealed_input"]["resume_id"],
                broken_profiles.chunker.chunker_version,
                broken_profiles.embedding.embedding_profile_id)
        with closing(self.db.connect()) as connection:
            rows = connection.execute("""SELECT status,error_type,chunker_version
                FROM embedding_build_attempts WHERE org_id=? AND target_kind='RESUME'
                  AND embedding_profile_id=?""",
                (self.ctx.org_id, broken_profiles.embedding.embedding_profile_id)).fetchall()
        self.assertEqual([(row["status"], row["error_type"], row["chunker_version"]) for row in rows],
                         [("FAILED", "ValueError", broken_profiles.chunker.chunker_version)])


if __name__ == "__main__":
    unittest.main()
