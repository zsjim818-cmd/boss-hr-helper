-- Sprint 2: generic policy pipeline results, async tasks and HR review.
-- Purely additive: no existing table, column, constraint or trigger changes.

CREATE TABLE pipeline_tasks (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 pipeline_task_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL,
 scoring_mode TEXT NOT NULL CHECK(scoring_mode IN ('fast_mode','deep_mode')),
 idempotency_key TEXT,
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
 pipeline_evaluation_id TEXT,
 error_type TEXT,
 error_message TEXT,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 updated_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,pipeline_task_id),
 CHECK((status='FAILED' AND error_type IS NOT NULL AND length(trim(error_type))>0)
   OR (status<>'FAILED' AND error_type IS NULL AND error_message IS NULL))
);
CREATE UNIQUE INDEX uq_pipeline_task_idempotency
ON pipeline_tasks(org_id,job_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX ix_pipeline_tasks_candidate
ON pipeline_tasks(org_id,job_id,job_candidate_id,created_at);

CREATE TABLE pipeline_evaluations (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 pipeline_evaluation_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL,
 candidate_key TEXT NOT NULL,
 policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL CHECK(typeof(policy_version)='integer' AND policy_version>=1),
 policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64),
 resume_id TEXT NOT NULL,
 resume_content_hash TEXT NOT NULL CHECK(length(resume_content_hash)=64),
 pipeline_version TEXT NOT NULL CHECK(length(trim(pipeline_version))>0),
 scoring_mode TEXT NOT NULL CHECK(scoring_mode IN ('fast_mode','deep_mode')),
 scoring_status TEXT NOT NULL
   CHECK(scoring_status IN ('HARD_FILTERED','DATA_INCOMPLETE','NEEDS_REVIEW','COARSE_ONLY','DEEP_SCORED')),
 total_score REAL CHECK(total_score IS NULL OR (typeof(total_score)='real' AND total_score BETWEEN 0 AND 100)),
 routing_suggestion TEXT CHECK(routing_suggestion IS NULL OR routing_suggestion IN ('DEEP_RECOMMEND','HUMAN_REVIEW','NOT_RECOMMENDED')),
 tier0_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(tier0_json) AND json_type(tier0_json)='object'),
 tier1_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(tier1_json) AND json_type(tier1_json)='object'),
 tier2_json TEXT CHECK(tier2_json IS NULL OR (json_valid(tier2_json) AND json_type(tier2_json)='object')),
 tier3_json TEXT CHECK(tier3_json IS NULL OR (json_valid(tier3_json) AND json_type(tier3_json)='object')),
 review_reasons_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(review_reasons_json) AND json_type(review_reasons_json)='array'),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,pipeline_evaluation_id),
 UNIQUE(org_id,job_id,job_candidate_id,policy_id,policy_version,resume_id),
 CHECK((scoring_status='DEEP_SCORED' AND total_score IS NOT NULL AND routing_suggestion IS NOT NULL)
    OR (scoring_status<>'DEEP_SCORED' AND routing_suggestion IS NULL)),
 CHECK((scoring_status IN ('DEEP_SCORED','COARSE_ONLY','NEEDS_REVIEW')) OR tier2_json IS NOT NULL OR scoring_status IN ('HARD_FILTERED','DATA_INCOMPLETE'))
);
CREATE INDEX ix_pipeline_evaluations_candidate
ON pipeline_evaluations(org_id,job_id,job_candidate_id,created_at);
CREATE INDEX ix_pipeline_evaluations_ranking
ON pipeline_evaluations(org_id,job_id,total_score DESC);

CREATE TABLE pipeline_attempts (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL,
 policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL,
 policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64),
 resume_id TEXT,
 resume_content_hash TEXT,
 scoring_mode TEXT NOT NULL CHECK(scoring_mode IN ('fast_mode','deep_mode')),
 status TEXT NOT NULL CHECK(status IN ('SUCCEEDED','TECHNICAL_FAILED')),
 pipeline_evaluation_id TEXT,
 error_type TEXT,
 error_message TEXT,
 result_summary_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(result_summary_json) AND json_type(result_summary_json)='object'),
 metrics_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metrics_json) AND json_type(metrics_json)='object'),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,attempt_id),
 CHECK((status='TECHNICAL_FAILED' AND error_type IS NOT NULL AND length(trim(error_type))>0)
    OR (status='SUCCEEDED' AND error_type IS NULL))
);
CREATE INDEX ix_pipeline_attempts_candidate
ON pipeline_attempts(org_id,job_id,job_candidate_id,created_at);

CREATE TABLE pipeline_hr_reviews (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 review_event_id TEXT NOT NULL,
 pipeline_evaluation_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('APPROVE','REJECT','REQUEST_REVIEW','NOTE')),
 reason TEXT NOT NULL DEFAULT '',
 actor_id TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,review_event_id),
 FOREIGN KEY(org_id,job_id,pipeline_evaluation_id)
   REFERENCES pipeline_evaluations(org_id,job_id,pipeline_evaluation_id)
);
CREATE INDEX ix_pipeline_hr_reviews_evaluation
ON pipeline_hr_reviews(org_id,job_id,pipeline_evaluation_id,created_at);

CREATE TABLE pipeline_contact_events (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 contact_event_id TEXT NOT NULL,
 pipeline_evaluation_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('CONTACTED','DECLINED','PENDING')),
 note TEXT NOT NULL DEFAULT '',
 actor_id TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,contact_event_id),
 FOREIGN KEY(org_id,job_id,pipeline_evaluation_id)
   REFERENCES pipeline_evaluations(org_id,job_id,pipeline_evaluation_id)
);
CREATE INDEX ix_pipeline_contact_events_evaluation
ON pipeline_contact_events(org_id,job_id,pipeline_evaluation_id,created_at);

-- Task lifecycle: monotonic states, immutable identity, no silent deletion.
CREATE TRIGGER no_update_pipeline_task_identity
BEFORE UPDATE OF org_id,job_id,pipeline_task_id,job_candidate_id,scoring_mode,idempotency_key,created_by,created_at
ON pipeline_tasks
BEGIN SELECT RAISE(ABORT,'immutable pipeline task identity'); END;
CREATE TRIGGER pipeline_task_lifecycle BEFORE UPDATE OF status ON pipeline_tasks
WHEN NOT ((OLD.status='PENDING' AND NEW.status IN ('RUNNING','FAILED'))
        OR (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED'))
        OR (OLD.status='FAILED' AND NEW.status='RUNNING'))
BEGIN SELECT RAISE(ABORT,'illegal pipeline task transition'); END;
CREATE TRIGGER no_delete_pipeline_tasks BEFORE DELETE ON pipeline_tasks
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for pipeline_tasks'); END;
CREATE TRIGGER no_replace_pipeline_tasks BEFORE INSERT ON pipeline_tasks
WHEN EXISTS(SELECT 1 FROM pipeline_tasks WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND pipeline_task_id=NEW.pipeline_task_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing pipeline_tasks'); END;

-- Formal generic results are immutable and uniquely bound.
CREATE TRIGGER no_update_pipeline_evaluations BEFORE UPDATE ON pipeline_evaluations
BEGIN SELECT RAISE(ABORT,'immutable pipeline_evaluations'); END;
CREATE TRIGGER no_delete_pipeline_evaluations BEFORE DELETE ON pipeline_evaluations
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for pipeline_evaluations'); END;
CREATE TRIGGER no_replace_pipeline_evaluations BEFORE INSERT ON pipeline_evaluations
WHEN EXISTS(SELECT 1 FROM pipeline_evaluations WHERE org_id=NEW.org_id AND job_id=NEW.job_id
  AND (pipeline_evaluation_id=NEW.pipeline_evaluation_id
    OR (job_candidate_id=NEW.job_candidate_id AND policy_id=NEW.policy_id
        AND policy_version=NEW.policy_version AND resume_id=NEW.resume_id)))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing pipeline_evaluations'); END;

-- Attempts: append-only.
CREATE TRIGGER no_update_pipeline_attempts BEFORE UPDATE ON pipeline_attempts
BEGIN SELECT RAISE(ABORT,'immutable pipeline_attempts'); END;
CREATE TRIGGER no_delete_pipeline_attempts BEFORE DELETE ON pipeline_attempts
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for pipeline_attempts'); END;
CREATE TRIGGER no_replace_pipeline_attempts BEFORE INSERT ON pipeline_attempts
WHEN EXISTS(SELECT 1 FROM pipeline_attempts WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND attempt_id=NEW.attempt_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing pipeline_attempts'); END;

-- HR reviews / contact events: append-only, must reference a formal evaluation.
CREATE TRIGGER pipeline_review_requires_evaluation BEFORE INSERT ON pipeline_hr_reviews
WHEN NOT EXISTS(SELECT 1 FROM pipeline_evaluations WHERE org_id=NEW.org_id AND job_id=NEW.job_id
  AND pipeline_evaluation_id=NEW.pipeline_evaluation_id)
BEGIN SELECT RAISE(ABORT,'HR review requires an existing pipeline evaluation'); END;
CREATE TRIGGER no_update_pipeline_hr_reviews BEFORE UPDATE ON pipeline_hr_reviews
BEGIN SELECT RAISE(ABORT,'immutable pipeline_hr_reviews'); END;
CREATE TRIGGER no_delete_pipeline_hr_reviews BEFORE DELETE ON pipeline_hr_reviews
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for pipeline_hr_reviews'); END;
CREATE TRIGGER no_replace_pipeline_hr_reviews BEFORE INSERT ON pipeline_hr_reviews
WHEN EXISTS(SELECT 1 FROM pipeline_hr_reviews WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND review_event_id=NEW.review_event_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing pipeline_hr_reviews'); END;

CREATE TRIGGER pipeline_contact_requires_evaluation BEFORE INSERT ON pipeline_contact_events
WHEN NOT EXISTS(SELECT 1 FROM pipeline_evaluations WHERE org_id=NEW.org_id AND job_id=NEW.job_id
  AND pipeline_evaluation_id=NEW.pipeline_evaluation_id)
BEGIN SELECT RAISE(ABORT,'contact event requires an existing pipeline evaluation'); END;
CREATE TRIGGER no_update_pipeline_contact_events BEFORE UPDATE ON pipeline_contact_events
BEGIN SELECT RAISE(ABORT,'immutable pipeline_contact_events'); END;
CREATE TRIGGER no_delete_pipeline_contact_events BEFORE DELETE ON pipeline_contact_events
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for pipeline_contact_events'); END;
CREATE TRIGGER no_replace_pipeline_contact_events BEFORE INSERT ON pipeline_contact_events
WHEN EXISTS(SELECT 1 FROM pipeline_contact_events WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND contact_event_id=NEW.contact_event_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing pipeline_contact_events'); END;
