-- Sprint 3: allow one formal result per (binding, resume, scoring_mode) so a
-- candidate can be fast-screened and later deep-scored without overwriting
-- immutable rows.  009 is not edited; the three pipeline result tables are
-- rebuilt from staging tables (no renames, so foreign keys stay deterministic).
PRAGMA defer_foreign_keys=ON;

CREATE TABLE pipeline_evaluations_stage AS SELECT * FROM pipeline_evaluations;
CREATE TABLE pipeline_hr_reviews_stage AS SELECT * FROM pipeline_hr_reviews;
CREATE TABLE pipeline_contact_events_stage AS SELECT * FROM pipeline_contact_events;

DROP TRIGGER no_update_pipeline_evaluations;
DROP TRIGGER no_delete_pipeline_evaluations;
DROP TRIGGER no_replace_pipeline_evaluations;
DROP TRIGGER pipeline_review_requires_evaluation;
DROP TRIGGER no_update_pipeline_hr_reviews;
DROP TRIGGER no_delete_pipeline_hr_reviews;
DROP TRIGGER no_replace_pipeline_hr_reviews;
DROP TRIGGER pipeline_contact_requires_evaluation;
DROP TRIGGER no_update_pipeline_contact_events;
DROP TRIGGER no_delete_pipeline_contact_events;
DROP TRIGGER no_replace_pipeline_contact_events;

DROP INDEX IF EXISTS ix_pipeline_evaluations_candidate;
DROP INDEX IF EXISTS ix_pipeline_evaluations_ranking;
DROP INDEX IF EXISTS ix_pipeline_evaluations_mode;
DROP INDEX IF EXISTS ix_pipeline_hr_reviews_evaluation;
DROP INDEX IF EXISTS ix_pipeline_contact_events_evaluation;
DROP TABLE pipeline_hr_reviews;
DROP TABLE pipeline_contact_events;
DROP TABLE pipeline_evaluations;

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
 UNIQUE(org_id,job_id,job_candidate_id,policy_id,policy_version,resume_id,scoring_mode),
 CHECK((scoring_status='DEEP_SCORED' AND total_score IS NOT NULL AND routing_suggestion IS NOT NULL
        AND scoring_mode='deep_mode')
    OR (scoring_status<>'DEEP_SCORED' AND routing_suggestion IS NULL)),
 CHECK((scoring_status IN ('DEEP_SCORED','COARSE_ONLY','NEEDS_REVIEW')) OR tier2_json IS NOT NULL
    OR scoring_status IN ('HARD_FILTERED','DATA_INCOMPLETE'))
);
CREATE INDEX ix_pipeline_evaluations_candidate
ON pipeline_evaluations(org_id,job_id,job_candidate_id,created_at);
CREATE INDEX ix_pipeline_evaluations_ranking
ON pipeline_evaluations(org_id,job_id,total_score DESC);
CREATE INDEX ix_pipeline_evaluations_mode
ON pipeline_evaluations(org_id,job_id,job_candidate_id,scoring_mode,created_at);

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

INSERT INTO pipeline_evaluations
 (org_id,job_id,pipeline_evaluation_id,job_candidate_id,candidate_id,candidate_key,
  policy_id,policy_version,policy_hash,resume_id,resume_content_hash,pipeline_version,
  scoring_mode,scoring_status,total_score,routing_suggestion,tier0_json,tier1_json,
  tier2_json,tier3_json,review_reasons_json,created_by,created_at)
SELECT org_id,job_id,pipeline_evaluation_id,job_candidate_id,candidate_id,candidate_key,
  policy_id,policy_version,policy_hash,resume_id,resume_content_hash,pipeline_version,
  scoring_mode,scoring_status,total_score,routing_suggestion,tier0_json,tier1_json,
  tier2_json,tier3_json,review_reasons_json,created_by,created_at
FROM pipeline_evaluations_stage;

INSERT INTO pipeline_hr_reviews
 (org_id,job_id,review_event_id,pipeline_evaluation_id,action,reason,actor_id,created_at)
SELECT org_id,job_id,review_event_id,pipeline_evaluation_id,action,reason,actor_id,created_at
FROM pipeline_hr_reviews_stage;

INSERT INTO pipeline_contact_events
 (org_id,job_id,contact_event_id,pipeline_evaluation_id,action,note,actor_id,created_at)
SELECT org_id,job_id,contact_event_id,pipeline_evaluation_id,action,note,actor_id,created_at
FROM pipeline_contact_events_stage;

DROP TABLE pipeline_evaluations_stage;
DROP TABLE pipeline_hr_reviews_stage;
DROP TABLE pipeline_contact_events_stage;

CREATE TRIGGER no_update_pipeline_evaluations BEFORE UPDATE ON pipeline_evaluations
BEGIN SELECT RAISE(ABORT,'immutable pipeline_evaluations'); END;
CREATE TRIGGER no_delete_pipeline_evaluations BEFORE DELETE ON pipeline_evaluations
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for pipeline_evaluations'); END;
CREATE TRIGGER no_replace_pipeline_evaluations BEFORE INSERT ON pipeline_evaluations
WHEN EXISTS(SELECT 1 FROM pipeline_evaluations WHERE org_id=NEW.org_id AND job_id=NEW.job_id
  AND (pipeline_evaluation_id=NEW.pipeline_evaluation_id
    OR (job_candidate_id=NEW.job_candidate_id AND policy_id=NEW.policy_id
        AND policy_version=NEW.policy_version AND resume_id=NEW.resume_id
        AND scoring_mode=NEW.scoring_mode)))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing pipeline_evaluations'); END;

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
