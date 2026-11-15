-- Sprint 4 Batch 2B: persistent browser bindings/scan runs, batch-bound scoring
-- tasks and structured contact events.  001-010 are frozen; this migration only
-- adds tables/columns and rebuilds pipeline_contact_events (rows preserved).
PRAGMA defer_foreign_keys=ON;

-- ---------------------------------------------------------------- bindings
CREATE TABLE browser_account_bindings (
 org_id TEXT NOT NULL,
 user_id TEXT NOT NULL,
 browser_account_id TEXT NOT NULL,
 cdp_port INTEGER NOT NULL CHECK(typeof(cdp_port)='integer' AND cdp_port BETWEEN 1 AND 65535),
 profile_id TEXT NOT NULL CHECK(length(trim(profile_id))>0),
 status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','DISABLED')),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 updated_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,user_id),
 UNIQUE(org_id,browser_account_id),
 UNIQUE(org_id,cdp_port),
 FOREIGN KEY(org_id,user_id) REFERENCES organization_memberships(org_id,user_id)
);

-- ---------------------------------------------------------------- scan runs
CREATE TABLE browser_scan_runs (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 batch_id TEXT NOT NULL,
 actor_user_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','COMPLETED_WITH_ERRORS','FAILED')),
 source TEXT NOT NULL DEFAULT '',
 browser_account_id TEXT,
 collected INTEGER NOT NULL DEFAULT 0 CHECK(collected>=0),
 imported INTEGER NOT NULL DEFAULT 0 CHECK(imported>=0),
 reused INTEGER NOT NULL DEFAULT 0 CHECK(reused>=0),
 failed INTEGER NOT NULL DEFAULT 0 CHECK(failed>=0),
 issues_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(issues_json) AND json_type(issues_json)='array'),
 error_code TEXT,
 error_message TEXT,
 started_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 finished_at TEXT,
 updated_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,batch_id,actor_user_id),
 CHECK((status='FAILED' AND error_code IS NOT NULL AND length(trim(error_code))>0)
    OR (status<>'FAILED')),
 FOREIGN KEY(org_id,job_id,batch_id) REFERENCES scan_batches(org_id,job_id,batch_id),
 FOREIGN KEY(org_id,actor_user_id) REFERENCES organization_memberships(org_id,user_id)
);
CREATE INDEX ix_browser_scan_runs_job
ON browser_scan_runs(org_id,job_id,batch_id,updated_at);

-- ------------------------------------------------------ task batch binding
ALTER TABLE pipeline_tasks ADD COLUMN batch_id TEXT;
CREATE INDEX ix_pipeline_tasks_batch
ON pipeline_tasks(org_id,job_id,batch_id);
CREATE TRIGGER no_update_pipeline_task_batch
BEFORE UPDATE OF batch_id ON pipeline_tasks
BEGIN SELECT RAISE(ABORT,'immutable pipeline task batch'); END;

-- ------------------------------------------------- structured contact events
CREATE TABLE pipeline_contact_events_stage AS SELECT * FROM pipeline_contact_events;

DROP TRIGGER pipeline_contact_requires_evaluation;
DROP TRIGGER no_update_pipeline_contact_events;
DROP TRIGGER no_delete_pipeline_contact_events;
DROP TRIGGER no_replace_pipeline_contact_events;
DROP INDEX IF EXISTS ix_pipeline_contact_events_evaluation;

DROP TABLE pipeline_contact_events;

CREATE TABLE pipeline_contact_events (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 contact_event_id TEXT NOT NULL,
 pipeline_evaluation_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('CONTACTED','DECLINED','PENDING')),
 mode TEXT NOT NULL DEFAULT 'LEGACY' CHECK(mode IN ('DRY_RUN','REAL_SEND','LEGACY')),
 browser_account_id TEXT,
 note TEXT NOT NULL DEFAULT '',
 actor_id TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,contact_event_id),
 FOREIGN KEY(org_id,job_id,pipeline_evaluation_id)
   REFERENCES pipeline_evaluations(org_id,job_id,pipeline_evaluation_id),
 FOREIGN KEY(org_id,job_id,job_candidate_id)
   REFERENCES job_candidates(org_id,job_id,job_candidate_id)
);

INSERT INTO pipeline_contact_events
 (org_id,job_id,contact_event_id,pipeline_evaluation_id,job_candidate_id,action,
  mode,browser_account_id,note,actor_id,created_at)
SELECT c.org_id,c.job_id,c.contact_event_id,c.pipeline_evaluation_id,e.job_candidate_id,
  c.action,
  CASE WHEN c.action='PENDING' AND c.note LIKE 'DRY_RUN:%' THEN 'DRY_RUN' ELSE 'LEGACY' END,
  NULL,c.note,c.actor_id,c.created_at
FROM pipeline_contact_events_stage c
JOIN pipeline_evaluations e ON e.org_id=c.org_id AND e.job_id=c.job_id
  AND e.pipeline_evaluation_id=c.pipeline_evaluation_id;

DROP TABLE pipeline_contact_events_stage;

CREATE INDEX ix_pipeline_contact_events_evaluation
ON pipeline_contact_events(org_id,job_id,pipeline_evaluation_id,created_at);
CREATE INDEX ix_pipeline_contact_events_candidate
ON pipeline_contact_events(org_id,job_id,job_candidate_id,created_at);

-- At most one REAL_SEND per (job, candidate, HR); different HR / job allowed.
CREATE UNIQUE INDEX ux_contact_real_send_once
ON pipeline_contact_events(org_id,job_id,job_candidate_id,actor_id)
WHERE mode='REAL_SEND';

CREATE TRIGGER pipeline_contact_requires_evaluation BEFORE INSERT ON pipeline_contact_events
WHEN NOT EXISTS(SELECT 1 FROM pipeline_evaluations WHERE org_id=NEW.org_id AND job_id=NEW.job_id
  AND pipeline_evaluation_id=NEW.pipeline_evaluation_id)
BEGIN SELECT RAISE(ABORT,'contact event requires an existing pipeline evaluation'); END;

CREATE TRIGGER pipeline_contact_candidate_matches_evaluation
BEFORE INSERT ON pipeline_contact_events
WHEN NOT EXISTS(SELECT 1 FROM pipeline_evaluations WHERE org_id=NEW.org_id AND job_id=NEW.job_id
  AND pipeline_evaluation_id=NEW.pipeline_evaluation_id
  AND job_candidate_id=NEW.job_candidate_id)
BEGIN SELECT RAISE(ABORT,'contact candidate does not match the evaluation'); END;

CREATE TRIGGER no_update_pipeline_contact_events BEFORE UPDATE ON pipeline_contact_events
BEGIN SELECT RAISE(ABORT,'immutable pipeline_contact_events'); END;
CREATE TRIGGER no_delete_pipeline_contact_events BEFORE DELETE ON pipeline_contact_events
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for pipeline_contact_events'); END;
CREATE TRIGGER no_replace_pipeline_contact_events BEFORE INSERT ON pipeline_contact_events
WHEN EXISTS(SELECT 1 FROM pipeline_contact_events WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND contact_event_id=NEW.contact_event_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing pipeline_contact_events'); END;
