"""Offline helpers for the generic pipeline: stub Tier2, mock Tier3 fixtures."""
from copy import deepcopy

from engine.pipeline_cli import offline_response
from engine.policy_interpreter import PolicyInterpreter
from engine.policy_llm_reviewer import PolicyReviewService
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import DeterministicLLMClient
from tests.prescreen_support import PrescreenDatabaseCase


def resume_for(key="cand", **changes):
    value = deepcopy(BENCHMARK_CANDIDATES[0])
    value.update(name="Synthetic candidate", degree="本科", work_years="5年",
                 job_status="离职-随时到岗", active_status="今日活跃",
                 expectation={"city": "北京", "salary": "12K/月"},
                 skills="Java, SQL, Spring Boot, 接口自动化测试",
                 work_experience=[{"company": "Synthetic company", "position": "Engineer",
                                   "responsibility": "负责Java服务端开发与SQL事务设计，独立完成Spring Boot接口与接口自动化测试。",
                                   "keywords": []}],
                 project_experience=[], education=[{"school": "Synthetic school", "degree": "本科"}],
                 advantage="", certifications=[])
    value["_meta"]["candidate_key"] = key
    value.update(changes)
    return value


class RecordingSemantic:
    """Deterministic Tier2 stub that counts calls, so 'no BGE work' is provable."""

    def __init__(self, repository, *, status="SEMANTIC_SCORED", coverage=1.0,
                 dimension_status="SCORED", error=None):
        self.repository = repository
        self.status = status
        self.coverage = coverage
        self.dimension_status = dimension_status
        self.error = error
        self.calls = 0

    def evaluate(self, context, job_id, job_candidate_id, *, preliminary=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        candidate = self.repository.get_job_candidate(context, job_id, job_candidate_id)
        sealed = self.repository.get_sealed_input(context, job_id, job_candidate_id)
        snapshot = self.repository.get_resume_snapshot(context, job_id, job_candidate_id,
                                                       sealed["resume_id"])
        plan = PolicyInterpreter(self.repository).load_for_candidate(
            context, job_id, job_candidate_id).plan
        scored = self.status == "SEMANTIC_SCORED"
        return {
            "org_id": context.org_id, "job_id": job_id,
            "job_candidate_id": job_candidate_id, "candidate_id": candidate["candidate_id"],
            "policy_id": plan.policy_id, "policy_version": plan.policy_version,
            "policy_hash": plan.policy_hash, "resume_id": sealed["resume_id"],
            "resume_content_hash": snapshot["content_hash"],
            "scoring_status": self.status,
            "semantic_score": 80.0 if scored else None,
            "semantic_coverage": self.coverage,
            "dimension_scores": [
                {"dimension_id": dimension.dimension_id,
                 "scoring_status": self.dimension_status,
                 "semantic_score": 80.0 if self.dimension_status == "SCORED" else None}
                for dimension in plan.policy.scoring_dimensions],
        }


class RecordingReview:
    """Wraps the mock-LLM Tier3 service and counts calls / can raise."""

    def __init__(self, repository, *, error=None):
        self.service = PolicyReviewService(
            repository, client=DeterministicLLMClient(response=offline_response),
            model_name="offline-tier3-mock")
        self.error = error
        self.calls = 0

    def evaluate(self, context, job_id, job_candidate_id):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.service.evaluate(context, job_id, job_candidate_id)


def raise_if_called(message):
    def _explode(*args, **kwargs):
        raise AssertionError(message)
    return _explode


class PipelineCase(PrescreenDatabaseCase):
    def publish_case(self, case_id="java_backend", job_id="job"):
        return self.publish(case_id, job_id=job_id)

    def import_resume(self, job_id="job", key="cand", **changes):
        batch = self.repo.create_scan_batch(self.ctx, job_id)
        return self.repo.acquire_candidate_for_job(
            self.ctx, job_id, batch["batch_id"], key, resume=resume_for(key, **changes))

    def pipeline(self, *, semantic=None, review=None):
        return PolicyScoringPipeline(self.repo, semantic_service=semantic,
                                     review_service=review)
