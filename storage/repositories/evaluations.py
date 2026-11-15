"""Task-bound, append-only evaluation persistence; never invokes scoring models."""
import json

from engine.hard_filter import check_hard_filters
from engine.llm_reviewer import DIMENSIONS, LLMReviewerMixin
from schemas import EvaluationOutput, LLMStructuredEvaluation, ResumeData

from .base import BaseRepository, check_snapshot, digest, json_dump, uid


class _StoredReviewValidator(LLMReviewerMixin):
    def __init__(self, config):
        self.config = config


class EvaluationRepository(BaseRepository):
    def _task(self, conn, ctx, job_id, task_id):
        return self.one(conn, "SELECT * FROM evaluation_tasks WHERE org_id=? AND job_id=? AND task_id=?",
                        (ctx.org_id, job_id, task_id))

    def create_evaluation_task(self, ctx, job_id, job_candidate_id, scoring_mode, task_id=None):
        if scoring_mode not in ("fast_mode", "deep_mode"):
            raise ValueError("Unsupported scoring mode")
        with self.tx(ctx, job_id, "evaluate") as conn:
            existing = conn.execute("SELECT * FROM evaluation_tasks WHERE org_id=? AND job_id=? AND job_candidate_id=? AND scoring_mode=?",
                                    (ctx.org_id, job_id, job_candidate_id, scoring_mode)).fetchone()
            if existing:
                return dict(existing)
            source = self.one(conn, """SELECT i.*, p.execution_profile_id
                FROM job_candidate_inputs i JOIN job_policy_versions p
                  ON p.org_id=i.org_id AND p.job_id=i.job_id AND p.policy_id=i.policy_id
                WHERE i.org_id=? AND i.job_id=? AND i.job_candidate_id=?""",
                               (ctx.org_id, job_id, job_candidate_id))
            task_id = task_id or uid()
            identity = {k: source[k] for k in ("org_id", "job_id", "job_candidate_id", "candidate_id", "policy_id", "policy_version", "resume_id", "execution_profile_id")}
            identity["scoring_mode"] = scoring_mode
            conn.execute("""INSERT INTO evaluation_tasks
                (org_id,job_id,task_id,job_candidate_id,candidate_id,policy_id,policy_version,
                 resume_id,execution_profile_id,scoring_mode,idempotency_key,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (ctx.org_id, job_id, task_id, source["job_candidate_id"], source["candidate_id"],
                          source["policy_id"], source["policy_version"], source["resume_id"],
                          source["execution_profile_id"], scoring_mode, digest(identity), ctx.user_id))
            self.audit(conn, ctx, "CREATE_EVALUATION_TASK", task_id, identity)
            return self._task(conn, ctx, job_id, task_id)

    def _validate_result(self, conn, ctx, job_id, task, result):
        value = result.model_dump(mode="json") if isinstance(result, EvaluationOutput) else result
        output = EvaluationOutput.model_validate(value)
        check_snapshot(output.model_dump(mode="json"))
        if output.scoring_status not in ("COARSE_ONLY", "HARD_FILTERED", "DEEP_SCORED"):
            raise ValueError("Failed or incomplete requests belong in evaluation_attempts")
        if output.scoring_mode != task["scoring_mode"] or output.cache_hit:
            raise ValueError("Scoring mode mismatch or cache import is not a formal evaluation")
        if output.version_info.get("assessment_scope") not in (None, "full_pipeline"):
            raise ValueError("Tier-only diagnostics are not complete engine evaluations")
        source = self.one(conn, """SELECT r.normalized_resume_json,r.content_hash,p.policy_json,p.policy_hash,
                    e.profile_json,jc.candidate_key
                FROM resume_snapshots r JOIN job_policy_versions p
                  ON p.org_id=r.org_id AND p.job_id=? AND p.policy_id=?
                JOIN execution_profiles e ON e.org_id=p.org_id AND e.profile_id=p.execution_profile_id
                JOIN job_candidates jc ON jc.org_id=p.org_id AND jc.job_id=p.job_id AND jc.job_candidate_id=?
                WHERE r.org_id=? AND r.candidate_id=? AND r.resume_id=?""",
                               (job_id, task["policy_id"], task["job_candidate_id"], ctx.org_id,
                                task["candidate_id"], task["resume_id"]))
        if output.candidate_key != source["candidate_key"] or output.resume_content_hash != source["content_hash"]:
            raise ValueError("Evaluation identity or sealed resume hash mismatch")
        if output.version_info.get("job_config_version") != source["policy_hash"]:
            raise ValueError("Evaluation must use the candidate's bound policy snapshot")
        profile = json.loads(source["profile_json"])
        for key in ("engine_version", "prompt_version", "embedding_model_version", "llm_model_version"):
            if key in profile and output.version_info.get(key) != profile[key]:
                raise ValueError(f"Execution profile mismatch: {key}")
        resume = ResumeData.model_validate_json(source["normalized_resume_json"])
        if resume.data_completeness != "COMPLETE" or resume.collection_errors or resume.data_issues or output.data_issues:
            raise ValueError("Official evaluation requires a complete, sealed scoring input")
        config = json.loads(source["policy_json"])
        if not {"scoring_config", "hard_filters"}.issubset(config):
            raise ValueError("Current V2 persistence requires an executable legacy scoring snapshot")
        state, _ = check_hard_filters(resume, config)
        if output.debug_trace.hard_filter_status != state or output.passed_hard_filter != (state != "FAIL"):
            raise ValueError("Hard-filter provenance mismatch")
        if output.scoring_status == "HARD_FILTERED":
            if state != "FAIL":
                raise ValueError("HARD_FILTERED requires an explicit failing condition")
            return output
        tier1, tier2 = output.debug_trace.tier1_capped_score, output.debug_trace.tier2_semantic_score
        if not 0 <= tier1 <= 45 or not 0 <= tier2 <= 55 or output.coarse_score != round(tier1 + tier2, 2):
            raise ValueError("Coarse stage totals are inconsistent")
        coarse_pass = output.coarse_score >= config["scoring_config"]["thresholds"]["coarse_pass_score"]
        if output.passed_tier2_coarse != coarse_pass or state == "FAIL":
            raise ValueError("Coarse or hard-filter gate mismatch")
        if output.scoring_status == "COARSE_ONLY":
            if output.scoring_mode == "deep_mode" and coarse_pass:
                raise ValueError("A completed deep-mode task cannot stop after a passing coarse score")
            return output
        if not coarse_pass or output.data_completeness != "COMPLETE":
            raise ValueError("DEEP_SCORED requires complete input and a passing coarse stage")
        assessment = LLMStructuredEvaluation.model_validate({
            **{k: v.model_dump() for k, v in output.debug_trace.tier3_dimension_evidence.items()},
            "highlights": output.highlights, "risk_point": output.risk_point})
        validator = _StoredReviewValidator(config)
        validator._validate_llm_evaluation(assessment, resume)
        score, dimensions = validator._compute_weighted_dimensions(assessment)
        if output.total_score != score or output.dimension_scores != dimensions:
            raise ValueError("Formal score does not match bound-policy weighted dimensions")
        raw = {key: getattr(assessment, key).score for key in DIMENSIONS}
        if output.debug_trace.tier3_llm_raw_scores != raw:
            raise ValueError("Raw dimensions do not match validated evidence")
        if output.routing_decision == "AUTO_GREET":
            review, thresholds = config["scoring_config"]["llm_review"], config["scoring_config"]["thresholds"]
            if (state != "PASS" or not config["quotas_and_switches"].get("enable_auto_greet", False)
                    or score < thresholds["auto_greet_threshold"]
                    or any(not getattr(assessment, key).evidence or getattr(assessment, key).confidence < review["auto_greet_min_confidence"] for key in DIMENSIONS)):
                raise ValueError("AUTO_GREET does not meet the bound-policy conditions")
        return output

    def append_evaluation_attempt(self, ctx, job_id, task_id, status, error_type=None,
                                  response=None, attempt_id=None, metrics=None):
        if (status not in ("SUCCEEDED", "FAILED")
                or (status == "FAILED" and (not isinstance(error_type, str) or not error_type.strip()))
                or (status == "SUCCEEDED" and error_type is not None)):
            raise ValueError("Attempt status and error_type are inconsistent")
        check_snapshot(metrics if metrics is not None else {})
        with self.tx(ctx, job_id, "evaluate") as conn:
            task = self._task(conn, ctx, job_id, task_id)
            if task["technical_state"] == "SUCCEEDED":
                raise ValueError("An accepted successful task cannot receive another attempt")
            if status == "SUCCEEDED":
                response = self._validate_result(conn, ctx, job_id, task, response).model_dump(mode="json")
            elif isinstance(response, EvaluationOutput):
                response = response.model_dump(mode="json")
            check_snapshot(response if response is not None else {})
            attempt_id = attempt_id or uid()
            number = conn.execute("SELECT COALESCE(MAX(attempt_no),0)+1 FROM evaluation_attempts WHERE org_id=? AND task_id=?",
                                  (ctx.org_id, task_id)).fetchone()[0]
            conn.execute("UPDATE evaluation_tasks SET technical_state='RUNNING',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE org_id=? AND job_id=? AND task_id=?",
                         (ctx.org_id, job_id, task_id))
            conn.execute("""INSERT INTO evaluation_attempts
                (org_id,job_id,task_id,attempt_id,attempt_no,status,result_json,error_type,metrics_json)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                         (ctx.org_id, job_id, task_id, attempt_id, number, status,
                          json_dump(response if response is not None else {}), error_type, json_dump(metrics if metrics is not None else {})))
            conn.execute("UPDATE evaluation_tasks SET technical_state=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE org_id=? AND job_id=? AND task_id=?",
                         (status, ctx.org_id, job_id, task_id))
            self.audit(conn, ctx, "APPEND_EVALUATION_ATTEMPT", attempt_id, {"task_id": task_id, "status": status})
            return self.one(conn, "SELECT * FROM evaluation_attempts WHERE org_id=? AND job_id=? AND attempt_id=?", (ctx.org_id, job_id, attempt_id))

    def save_evaluation(self, ctx, job_id, task_id, result, attempt_id=None, evaluation_id=None):
        with self.tx(ctx, job_id, "evaluate") as conn:
            task = self._task(conn, ctx, job_id, task_id)
            output = self._validate_result(conn, ctx, job_id, task, result)
            if attempt_id is None:
                attempt = self.one(conn, "SELECT * FROM evaluation_attempts WHERE org_id=? AND job_id=? AND task_id=? AND status='SUCCEEDED' ORDER BY attempt_no DESC LIMIT 1",
                                   (ctx.org_id, job_id, task_id))
            else:
                attempt = self.one(conn, "SELECT * FROM evaluation_attempts WHERE org_id=? AND job_id=? AND task_id=? AND attempt_id=? AND status='SUCCEEDED'",
                                   (ctx.org_id, job_id, task_id, attempt_id))
            previous = self._validate_result(conn, ctx, job_id, task, json.loads(attempt["result_json"]))
            if previous.model_dump(mode="json") != output.model_dump(mode="json"):
                raise ValueError("Formal result must equal the accepted successful attempt")
            evaluation_id = evaluation_id or uid()
            trace = output.debug_trace
            conn.execute("""INSERT INTO evaluations
                (org_id,job_id,evaluation_id,task_id,attempt_id,job_candidate_id,candidate_id,
                 policy_id,policy_version,resume_id,execution_profile_id,scoring_mode,scoring_status,
                 final_score,tier0_json,tier1_score,tier2_score,dimensions_json,evidence_json,result_json,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (ctx.org_id, job_id, evaluation_id, task_id, attempt["attempt_id"], task["job_candidate_id"],
                          task["candidate_id"], task["policy_id"], task["policy_version"], task["resume_id"],
                          task["execution_profile_id"], output.scoring_mode, output.scoring_status, output.total_score,
                          json_dump(trace.hard_filter_checks), trace.tier1_capped_score, trace.tier2_semantic_score,
                          json_dump(output.dimension_scores), json_dump([e.model_dump(mode="json") for e in output.evidence_details]),
                          json_dump(output.model_dump(mode="json")), ctx.user_id))
            self.audit(conn, ctx, "SAVE_EVALUATION", evaluation_id, {"task_id": task_id, "policy_id": task["policy_id"]})
            return self.one(conn, "SELECT * FROM evaluations WHERE org_id=? AND job_id=? AND evaluation_id=?", (ctx.org_id, job_id, evaluation_id))

    def get_candidate_evaluations(self, ctx, job_id, job_candidate_id):
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM job_candidates WHERE org_id=? AND job_id=? AND job_candidate_id=?", (ctx.org_id, job_id, job_candidate_id))
            return [dict(row) for row in conn.execute("SELECT * FROM evaluations WHERE org_id=? AND job_id=? AND job_candidate_id=? ORDER BY created_at,evaluation_id",
                                                    (ctx.org_id, job_id, job_candidate_id))]

    def get_evaluation_task(self, ctx, job_id, task_id):
        with self.read(ctx, job_id, "view") as conn:
            return self._task(conn, ctx, job_id, task_id)

    def list_evaluation_attempts(self, ctx, job_id, task_id):
        with self.read(ctx, job_id, "view") as conn:
            self._task(conn, ctx, job_id, task_id)
            return [dict(row) for row in conn.execute("SELECT * FROM evaluation_attempts WHERE org_id=? AND job_id=? AND task_id=? ORDER BY attempt_no",
                                                    (ctx.org_id, job_id, task_id))]

    def append_hr_review(self, ctx, job_id, evaluation_id, action, reason=""):
        if action not in ("APPROVE", "REJECT", "REQUEST_REVIEW", "NOTE"):
            raise ValueError("Unsupported HR review action")
        with self.tx(ctx, job_id, "review") as conn:
            self.one(conn, "SELECT 1 FROM evaluations WHERE org_id=? AND job_id=? AND evaluation_id=?", (ctx.org_id, job_id, evaluation_id))
            review_id = uid()
            conn.execute("INSERT INTO hr_review_events(org_id,job_id,review_event_id,evaluation_id,actor_id,action,reason) VALUES(?,?,?,?,?,?,?)",
                         (ctx.org_id, job_id, review_id, evaluation_id, ctx.user_id, action, reason))
            self.audit(conn, ctx, "HR_REVIEW", review_id, {"evaluation_id": evaluation_id, "action": action})
            return self.one(conn, "SELECT * FROM hr_review_events WHERE org_id=? AND job_id=? AND review_event_id=?", (ctx.org_id, job_id, review_id))
