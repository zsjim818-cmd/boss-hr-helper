"""Generic pipeline persistence: async tasks, formal results, attempts, HR review.

All writes are authorized through the same repository boundary and permissions
as the rest of the system (evaluate / review / view).  Formal results and
attempts are append-only (migration 009 triggers); re-running the same binding
returns the existing formal result instead of creating a second one.
"""
import json

from schemas.job_policy import JobScoringPolicy
from schemas.pipeline import PolicyPipelineResult

from .base import BaseRepository, check_snapshot, json_dump, uid, utcnow


FORMAL_STATUSES = ("HARD_FILTERED", "DATA_INCOMPLETE", "NEEDS_REVIEW",
                   "COARSE_ONLY", "DEEP_SCORED")
TASK_STATUSES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED")
REVIEW_ACTIONS = ("APPROVE", "REJECT", "REQUEST_REVIEW", "NOTE")
CONTACT_ACTIONS = ("CONTACTED", "DECLINED", "PENDING")


class PipelineConflict(ValueError):
    """Idempotency or task-identity conflict; the API maps this to HTTP 409."""


class PipelineRepository(BaseRepository):
    # ------------------------------------------------------------------ tasks
    def create_pipeline_task(self, ctx, job_id, job_candidate_id, scoring_mode,
                             idempotency_key=None, task_id=None):
        if scoring_mode not in ("fast_mode", "deep_mode"):
            raise ValueError("Unsupported scoring mode")
        if idempotency_key is not None and (not isinstance(idempotency_key, str)
                                            or not idempotency_key.strip()):
            raise ValueError("idempotency_key must be a non-empty string when supplied")
        with self.tx(ctx, job_id, "evaluate") as conn:
            self.one(conn, "SELECT 1 FROM job_candidates WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                     (ctx.org_id, job_id, job_candidate_id))
            if idempotency_key is not None:
                existing = conn.execute("""SELECT * FROM pipeline_tasks
                    WHERE org_id=? AND job_id=? AND idempotency_key=?""",
                                        (ctx.org_id, job_id, idempotency_key)).fetchone()
                if existing is not None:
                    return self._reuse_task(existing, job_candidate_id, scoring_mode)
            identifier = task_id or uid()
            try:
                conn.execute("""INSERT INTO pipeline_tasks
                    (org_id,job_id,pipeline_task_id,job_candidate_id,scoring_mode,idempotency_key,created_by)
                    VALUES(?,?,?,?,?,?,?)""",
                             (ctx.org_id, job_id, identifier, job_candidate_id, scoring_mode,
                              idempotency_key, ctx.user_id))
            except Exception:
                # Concurrent same-key creation: the unique index is the arbiter.
                if idempotency_key is not None:
                    existing = conn.execute("""SELECT * FROM pipeline_tasks
                        WHERE org_id=? AND job_id=? AND idempotency_key=?""",
                                            (ctx.org_id, job_id, idempotency_key)).fetchone()
                    if existing is not None:
                        return self._reuse_task(existing, job_candidate_id, scoring_mode)
                raise
            self.audit(conn, ctx, "PIPELINE_TASK_CREATED", identifier,
                       {"job_id": job_id, "job_candidate_id": job_candidate_id,
                        "scoring_mode": scoring_mode})
            return self.one(conn, "SELECT * FROM pipeline_tasks WHERE org_id=? AND job_id=? AND pipeline_task_id=?",
                            (ctx.org_id, job_id, identifier))

    @staticmethod
    def _reuse_task(existing, job_candidate_id, scoring_mode):
        """An idempotency key may only be reused for the same candidate + mode."""
        row = dict(existing)
        if (row["job_candidate_id"], row["scoring_mode"]) != (job_candidate_id, scoring_mode):
            raise PipelineConflict(
                "Idempotency key was already used for a different candidate or scoring mode "
                f"(task candidate={row['job_candidate_id']}, mode={row['scoring_mode']})")
        return row

    def update_pipeline_task(self, ctx, job_id, task_id, *, status,
                             pipeline_evaluation_id=None, error_type=None,
                             error_message=None):
        if status not in TASK_STATUSES:
            raise ValueError("Unsupported pipeline task status")
        if status == "FAILED" and (not isinstance(error_type, str) or not error_type.strip()):
            raise ValueError("FAILED requires an error_type")
        with self.tx(ctx, job_id, "evaluate") as conn:
            self.one(conn, "SELECT 1 FROM pipeline_tasks WHERE org_id=? AND job_id=? AND pipeline_task_id=?",
                     (ctx.org_id, job_id, task_id))
            conn.execute("""UPDATE pipeline_tasks SET status=?,pipeline_evaluation_id=COALESCE(?,pipeline_evaluation_id),
                error_type=?,error_message=?,updated_at=? WHERE org_id=? AND job_id=? AND pipeline_task_id=?""",
                         (status, pipeline_evaluation_id, error_type, error_message, utcnow(),
                          ctx.org_id, job_id, task_id))
            self.audit(conn, ctx, "PIPELINE_TASK_STATUS", task_id, {"status": status})
            return self.one(conn, "SELECT * FROM pipeline_tasks WHERE org_id=? AND job_id=? AND pipeline_task_id=?",
                            (ctx.org_id, job_id, task_id))

    def get_pipeline_task(self, ctx, job_id, task_id):
        with self.read(ctx, job_id, "view") as conn:
            return self.one(conn, "SELECT * FROM pipeline_tasks WHERE org_id=? AND job_id=? AND pipeline_task_id=?",
                            (ctx.org_id, job_id, task_id))

    # --------------------------------------------------------- formal results
    def _binding_truth(self, conn, ctx, job_id, job_candidate_id, policy_id=None):
        """Resolve DB truth for a candidate binding (policy + sealed resume)."""
        clause, params = "", [ctx.org_id, job_id, job_candidate_id]
        if policy_id is not None:
            clause = " AND jc.policy_id=?"
            params.append(policy_id)
        return self.one(conn, """SELECT jc.*, r.content_hash AS resume_content_hash,
                    r.resume_id, p.policy_hash, p.policy_json, p.policy_version
                FROM job_candidates jc
                JOIN job_candidate_inputs i ON i.org_id=jc.org_id AND i.job_id=jc.job_id
                  AND i.job_candidate_id=jc.job_candidate_id
                JOIN resume_snapshots r ON r.org_id=jc.org_id AND r.candidate_id=jc.candidate_id
                  AND r.resume_id=i.resume_id
                JOIN job_policy_versions p ON p.org_id=jc.org_id AND p.job_id=jc.job_id
                  AND p.policy_id=jc.policy_id
                WHERE jc.org_id=? AND jc.job_id=? AND jc.job_candidate_id=?""" + clause, params)

    def _validate_pipeline_formal_result(self, conn, ctx, job_id, result):
        from engine.llm_reviewer import llm_source_map
        from engine.policy_llm_reviewer import verify_tier3_evaluation
        from schemas import ResumeData
        value = result.model_dump(mode="json") if isinstance(result, PolicyPipelineResult) else result
        output = PolicyPipelineResult.model_validate(value)
        check_snapshot(output.model_dump(mode="json"))
        if output.status not in FORMAL_STATUSES:
            raise ValueError("Only formal pipeline statuses can be stored")
        truth = self._binding_truth(conn, ctx, job_id, output.job_candidate_id, output.policy_id)
        if (output.candidate_id, output.candidate_key, output.policy_version) != (
                truth["candidate_id"], truth["candidate_key"], truth["policy_version"]):
            raise ValueError("Pipeline result identity does not match the candidate binding")
        if (output.policy_hash != truth["policy_hash"]
                or output.resume_id != truth["resume_id"]
                or output.resume_content_hash != truth["resume_content_hash"]):
            raise ValueError("Pipeline result policy/resume identity mismatch")
        policy = JobScoringPolicy.model_validate_json(truth["policy_json"])
        expected_dims = [item.dimension_id for item in policy.scoring_dimensions]
        if output.status in ("HARD_FILTERED", "DATA_INCOMPLETE") and output.semantic is not None:
            raise ValueError("Hard-filtered/incomplete results cannot carry semantic scores")
        if output.semantic is not None:
            semantic = output.semantic
            if (semantic.get("policy_hash") != output.policy_hash
                    or semantic.get("resume_content_hash") != output.resume_content_hash
                    or semantic.get("policy_version") != output.policy_version):
                raise ValueError("Tier2 payload identity does not match the binding")
            semantic_dims = [item.get("dimension_id") for item in semantic.get("dimension_scores", [])]
            if semantic_dims and semantic_dims != expected_dims:
                raise ValueError("Tier2 dimensions must equal the bound policy dimensions")
        if output.tier3 is not None:
            resume_row = self.one(conn, """SELECT normalized_resume_json FROM resume_snapshots
                WHERE org_id=? AND candidate_id=? AND resume_id=?""",
                                  (ctx.org_id, output.candidate_id, output.resume_id))
            resume = ResumeData.model_validate_json(resume_row["normalized_resume_json"])
            tier3 = verify_tier3_evaluation(policy, output.tier3, llm_source_map(resume))
            if (tier3.policy_hash, tier3.resume_content_hash, tier3.policy_version,
                    tier3.job_candidate_id) != (output.policy_hash, output.resume_content_hash,
                                                output.policy_version, output.job_candidate_id):
                raise ValueError("Tier3 payload identity does not match the binding")
        if output.status == "DEEP_SCORED":
            if output.scoring_mode != "deep_mode":
                raise ValueError("DEEP_SCORED results require deep_mode")
            if output.tier3 is None or output.total_score is None or output.routing_suggestion is None:
                raise ValueError("DEEP_SCORED requires Tier3, total score and routing")
            if output.total_score != output.tier3.get("total_score") \
                    or output.routing_suggestion != output.tier3.get("routing_suggestion"):
                raise ValueError("DEEP_SCORED totals must come from the Tier3 payload")
        else:
            if output.scoring_mode == "fast_mode" and output.semantic is not None:
                raise ValueError("fast_mode formal results cannot carry Tier2 payloads")
            if output.routing_suggestion is not None or output.tier3 is not None:
                raise ValueError("Only DEEP_SCORED may carry routing/Tier3 in a formal result")
            if output.status in ("HARD_FILTERED", "DATA_INCOMPLETE") and output.semantic is not None:
                raise ValueError("Hard-filtered/incomplete results cannot carry semantic scores")
        if output.status == "COARSE_ONLY" and output.total_score is not None:
            preliminary = output.preliminary or {}
            if output.total_score != preliminary.get("preliminary_score"):
                raise ValueError("COARSE_ONLY total must equal the preliminary score")
        return output

    def save_pipeline_result(self, ctx, job_id, result):
        value = result.model_dump(mode="json") if isinstance(result, PolicyPipelineResult) else result
        with self.tx(ctx, job_id, "evaluate") as conn:
            output = self._validate_pipeline_formal_result(conn, ctx, job_id, value)
            existing = conn.execute("""SELECT * FROM pipeline_evaluations WHERE org_id=? AND job_id=?
                AND job_candidate_id=? AND policy_id=? AND policy_version=? AND resume_id=?
                AND scoring_mode=?""",
                                    (ctx.org_id, job_id, output.job_candidate_id, output.policy_id,
                                     output.policy_version, output.resume_id, output.scoring_mode)).fetchone()
            if existing is not None:
                return dict(existing)
            evaluation_id = uid()
            conn.execute("""INSERT INTO pipeline_evaluations
                (org_id,job_id,pipeline_evaluation_id,job_candidate_id,candidate_id,candidate_key,
                 policy_id,policy_version,policy_hash,resume_id,resume_content_hash,pipeline_version,
                 scoring_mode,scoring_status,total_score,routing_suggestion,
                 tier0_json,tier1_json,tier2_json,tier3_json,review_reasons_json,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (ctx.org_id, job_id, evaluation_id, output.job_candidate_id,
                          output.candidate_id, output.candidate_key, output.policy_id,
                          output.policy_version, output.policy_hash, output.resume_id,
                          output.resume_content_hash, output.pipeline_version, output.scoring_mode,
                          output.status, output.total_score, output.routing_suggestion,
                          json_dump(output.preliminary or {}),
                          json_dump({"dimensions": (output.preliminary or {}).get("dimension_scores", [])}),
                          json_dump(output.semantic) if output.semantic is not None else None,
                          json_dump(output.tier3) if output.tier3 is not None else None,
                          json_dump(output.review_reasons), ctx.user_id))
            self.audit(conn, ctx, "PIPELINE_EVALUATION_SAVED", evaluation_id,
                       {"job_id": job_id, "status": output.status,
                        "policy_id": output.policy_id, "policy_version": output.policy_version})
            return self.one(conn, """SELECT * FROM pipeline_evaluations
                WHERE org_id=? AND job_id=? AND pipeline_evaluation_id=?""",
                            (ctx.org_id, job_id, evaluation_id))

    def get_pipeline_result(self, ctx, job_id, evaluation_id):
        with self.read(ctx, job_id, "view") as conn:
            return self.one(conn, """SELECT * FROM pipeline_evaluations
                WHERE org_id=? AND job_id=? AND pipeline_evaluation_id=?""",
                            (ctx.org_id, job_id, evaluation_id))

    def latest_pipeline_result(self, ctx, job_id, job_candidate_id):
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM job_candidates WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                     (ctx.org_id, job_id, job_candidate_id))
            row = conn.execute("""SELECT * FROM pipeline_evaluations
                WHERE org_id=? AND job_id=? AND job_candidate_id=?
                ORDER BY (scoring_mode='deep_mode') DESC, created_at DESC,
                         pipeline_evaluation_id DESC LIMIT 1""",
                               (ctx.org_id, job_id, job_candidate_id)).fetchone()
            return dict(row) if row else None

    def pipeline_result_for_mode(self, ctx, job_id, job_candidate_id, scoring_mode):
        if scoring_mode not in ("fast_mode", "deep_mode"):
            raise ValueError("Unsupported scoring mode")
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM job_candidates WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                     (ctx.org_id, job_id, job_candidate_id))
            row = conn.execute("""SELECT * FROM pipeline_evaluations
                WHERE org_id=? AND job_id=? AND job_candidate_id=? AND scoring_mode=?
                ORDER BY created_at DESC, pipeline_evaluation_id DESC LIMIT 1""",
                               (ctx.org_id, job_id, job_candidate_id, scoring_mode)).fetchone()
            return dict(row) if row else None

    def list_pipeline_results(self, ctx, job_id, job_candidate_id):
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM job_candidates WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                     (ctx.org_id, job_id, job_candidate_id))
            return [dict(row) for row in conn.execute("""SELECT * FROM pipeline_evaluations
                WHERE org_id=? AND job_id=? AND job_candidate_id=?
                ORDER BY created_at, pipeline_evaluation_id""",
                                                       (ctx.org_id, job_id, job_candidate_id))]

    # --------------------------------------------------------------- attempts
    def append_pipeline_result_attempt(self, ctx, job_id, result, *, status="SUCCEEDED",
                                       metrics=None):
        value = result.model_dump(mode="json") if isinstance(result, PolicyPipelineResult) else result
        output = PolicyPipelineResult.model_validate(value)
        if status not in ("SUCCEEDED", "TECHNICAL_FAILED"):
            raise ValueError("Unsupported pipeline attempt status")
        check_snapshot(metrics if metrics is not None else {})
        error = output.technical_failure or {}
        error_type = error.get("error_type") if status == "TECHNICAL_FAILED" else None
        if status == "TECHNICAL_FAILED" and not error_type:
            raise ValueError("TECHNICAL_FAILED requires a technical_failure payload")
        with self.tx(ctx, job_id, "evaluate") as conn:
            attempt_id = uid()
            evaluation_id = None
            if status == "SUCCEEDED":
                row = conn.execute("""SELECT pipeline_evaluation_id FROM pipeline_evaluations
                    WHERE org_id=? AND job_id=? AND job_candidate_id=? AND policy_id=? AND policy_version=?
                      AND resume_id=? AND scoring_mode=?""",
                                   (ctx.org_id, job_id, output.job_candidate_id, output.policy_id,
                                    output.policy_version, output.resume_id, output.scoring_mode)).fetchone()
                evaluation_id = row["pipeline_evaluation_id"] if row else None
            conn.execute("""INSERT INTO pipeline_attempts
                (org_id,job_id,attempt_id,job_candidate_id,policy_id,policy_version,policy_hash,
                 resume_id,resume_content_hash,scoring_mode,status,pipeline_evaluation_id,
                 error_type,error_message,result_summary_json,metrics_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (ctx.org_id, job_id, attempt_id, output.job_candidate_id, output.policy_id,
                          output.policy_version, output.policy_hash, output.resume_id,
                          output.resume_content_hash, output.scoring_mode, status, evaluation_id,
                          error_type, error.get("message"),
                          json_dump({"status": output.status, "total_score": output.total_score,
                                     "routing_suggestion": output.routing_suggestion}),
                          json_dump(metrics or {})))
            self.audit(conn, ctx, "PIPELINE_ATTEMPT_APPENDED", attempt_id,
                       {"status": status, "evaluation_id": evaluation_id})
            return self.one(conn, """SELECT * FROM pipeline_attempts
                WHERE org_id=? AND job_id=? AND attempt_id=?""",
                            (ctx.org_id, job_id, attempt_id))

    def list_pipeline_attempts(self, ctx, job_id, job_candidate_id):
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM job_candidates WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                     (ctx.org_id, job_id, job_candidate_id))
            return [dict(row) for row in conn.execute("""SELECT * FROM pipeline_attempts
                WHERE org_id=? AND job_id=? AND job_candidate_id=?
                ORDER BY created_at, attempt_id""",
                                                       (ctx.org_id, job_id, job_candidate_id))]

    # ------------------------------------------------------- reviews/contact
    def append_pipeline_review(self, ctx, job_id, evaluation_id, action, reason=""):
        if action not in REVIEW_ACTIONS:
            raise ValueError("Unsupported HR review action")
        if not isinstance(reason, str) or len(reason) > 2000:
            raise ValueError("Review reason must be a string up to 2000 characters")
        with self.tx(ctx, job_id, "review") as conn:
            self.one(conn, "SELECT 1 FROM pipeline_evaluations WHERE org_id=? AND job_id=? AND pipeline_evaluation_id=?",
                     (ctx.org_id, job_id, evaluation_id))
            event_id = uid()
            conn.execute("""INSERT INTO pipeline_hr_reviews
                (org_id,job_id,review_event_id,pipeline_evaluation_id,action,reason,actor_id)
                VALUES(?,?,?,?,?,?,?)""",
                         (ctx.org_id, job_id, event_id, evaluation_id, action, reason, ctx.user_id))
            self.audit(conn, ctx, "PIPELINE_HR_REVIEW", event_id,
                       {"evaluation_id": evaluation_id, "action": action})
            return self.one(conn, """SELECT * FROM pipeline_hr_reviews
                WHERE org_id=? AND job_id=? AND review_event_id=?""",
                            (ctx.org_id, job_id, event_id))

    def list_pipeline_reviews(self, ctx, job_id, evaluation_id):
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM pipeline_evaluations WHERE org_id=? AND job_id=? AND pipeline_evaluation_id=?",
                     (ctx.org_id, job_id, evaluation_id))
            return [dict(row) for row in conn.execute("""SELECT * FROM pipeline_hr_reviews
                WHERE org_id=? AND job_id=? AND pipeline_evaluation_id=?
                ORDER BY created_at, review_event_id""",
                                                       (ctx.org_id, job_id, evaluation_id))]

    def append_pipeline_contact_event(self, ctx, job_id, evaluation_id, action, note=""):
        if action not in CONTACT_ACTIONS:
            raise ValueError("Unsupported contact action")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Contact note must be a string up to 2000 characters")
        with self.tx(ctx, job_id, "review") as conn:
            self.one(conn, "SELECT 1 FROM pipeline_evaluations WHERE org_id=? AND job_id=? AND pipeline_evaluation_id=?",
                     (ctx.org_id, job_id, evaluation_id))
            event_id = uid()
            conn.execute("""INSERT INTO pipeline_contact_events
                (org_id,job_id,contact_event_id,pipeline_evaluation_id,action,note,actor_id)
                VALUES(?,?,?,?,?,?,?)""",
                         (ctx.org_id, job_id, event_id, evaluation_id, action, note, ctx.user_id))
            self.audit(conn, ctx, "PIPELINE_CONTACT_EVENT", event_id,
                       {"evaluation_id": evaluation_id, "action": action})
            return self.one(conn, """SELECT * FROM pipeline_contact_events
                WHERE org_id=? AND job_id=? AND contact_event_id=?""",
                            (ctx.org_id, job_id, event_id))

    def list_pipeline_contact_events(self, ctx, job_id, evaluation_id):
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM pipeline_evaluations WHERE org_id=? AND job_id=? AND pipeline_evaluation_id=?",
                     (ctx.org_id, job_id, evaluation_id))
            return [dict(row) for row in conn.execute("""SELECT * FROM pipeline_contact_events
                WHERE org_id=? AND job_id=? AND pipeline_evaluation_id=?
                ORDER BY created_at, contact_event_id""",
                                                       (ctx.org_id, job_id, evaluation_id))]

    # ------------------------------------------------------- listing/ranking
    def list_job_candidates(self, ctx, job_id, *, limit=50, offset=0, status=None,
                            min_score=None):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer in [1,200]")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM jobs WHERE org_id=? AND job_id=?", (ctx.org_id, job_id))
            rows = [dict(row) for row in conn.execute("""SELECT jc.* FROM job_candidates jc
                WHERE jc.org_id=? AND jc.job_id=? ORDER BY jc.first_seen_at, jc.job_candidate_id""",
                                                      (ctx.org_id, job_id))]
        results = {}
        for row in rows:
            with self.read(ctx, job_id, "view") as conn:
                found = conn.execute("""SELECT * FROM pipeline_evaluations
                    WHERE org_id=? AND job_id=? AND job_candidate_id=?
                    ORDER BY (scoring_mode='deep_mode') DESC, created_at DESC,
                             pipeline_evaluation_id DESC LIMIT 1""",
                                     (ctx.org_id, job_id, row["job_candidate_id"])).fetchone()
                results[row["job_candidate_id"]] = dict(found) if found else None
        enriched = []
        for row in rows:
            latest = results.get(row["job_candidate_id"])
            if status is not None and (latest or {}).get("scoring_status") != status:
                continue
            if min_score is not None and ((latest or {}).get("total_score") or -1) < min_score:
                continue
            enriched.append({"job_candidate": row, "latest_evaluation": latest})
        return {"total": len(enriched), "items": enriched[offset:offset + limit]}

    def job_candidate_ranking(self, ctx, job_id, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer in [1,500]")
        with self.read(ctx, job_id, "view") as conn:
            self.one(conn, "SELECT 1 FROM jobs WHERE org_id=? AND job_id=?", (ctx.org_id, job_id))
            rows = [dict(row) for row in conn.execute("""SELECT e.pipeline_evaluation_id,
                    e.job_candidate_id, e.candidate_key, e.total_score, e.scoring_status,
                    e.routing_suggestion, e.policy_id, e.policy_version, e.scoring_mode, e.created_at
                FROM pipeline_evaluations e
                JOIN (SELECT job_candidate_id,
                             ROW_NUMBER() OVER (
                                 PARTITION BY job_candidate_id
                                 ORDER BY (scoring_mode='deep_mode') DESC,
                                          created_at DESC, pipeline_evaluation_id DESC) AS rn
                      FROM pipeline_evaluations
                      WHERE org_id=? AND job_id=?) ranked
                  ON ranked.job_candidate_id=e.job_candidate_id AND ranked.rn=1
                WHERE e.org_id=? AND e.job_id=?
                ORDER BY (e.total_score IS NULL), e.total_score DESC, e.created_at
                LIMIT ?""", (ctx.org_id, job_id, ctx.org_id, job_id, limit))]
        return rows
