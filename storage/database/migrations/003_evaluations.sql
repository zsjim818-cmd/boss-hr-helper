CREATE TABLE evaluation_tasks (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, task_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
 policy_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
 resume_id TEXT NOT NULL, execution_profile_id TEXT NOT NULL,
 scoring_mode TEXT NOT NULL CHECK(scoring_mode IN ('fast_mode','deep_mode')),
 idempotency_key TEXT NOT NULL,
 technical_state TEXT NOT NULL DEFAULT 'PENDING' CHECK(technical_state IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 updated_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,task_id), UNIQUE(org_id,idempotency_key),
 UNIQUE(org_id,job_id,job_candidate_id,scoring_mode),
 UNIQUE(org_id,job_id,task_id),
 UNIQUE(org_id,job_id,task_id,job_candidate_id,candidate_id,policy_id,policy_version,resume_id,execution_profile_id,scoring_mode),
 FOREIGN KEY(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version,resume_id)
   REFERENCES job_candidate_inputs(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version,resume_id),
 FOREIGN KEY(org_id,job_id,policy_id,policy_version,execution_profile_id)
   REFERENCES job_policy_versions(org_id,job_id,policy_id,policy_version,execution_profile_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE evaluation_attempts (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, task_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
 attempt_no INTEGER NOT NULL CHECK(typeof(attempt_no)='integer' AND attempt_no>0),
 status TEXT NOT NULL CHECK(status IN ('SUCCEEDED','FAILED')),
 result_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(result_json) AND json_type(result_json)='object'),
 error_type TEXT,
 metrics_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metrics_json) AND json_type(metrics_json)='object'),
 started_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 completed_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,attempt_id), UNIQUE(org_id,task_id,attempt_no),
 UNIQUE(org_id,job_id,task_id,attempt_id),
 FOREIGN KEY(org_id,job_id,task_id) REFERENCES evaluation_tasks(org_id,job_id,task_id),
 CHECK((status='FAILED' AND error_type IS NOT NULL AND length(trim(error_type))>0) OR
       (status='SUCCEEDED' AND error_type IS NULL))
);
CREATE TABLE evaluations (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, evaluation_id TEXT NOT NULL,
 task_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
 policy_id TEXT NOT NULL, policy_version INTEGER NOT NULL, resume_id TEXT NOT NULL,
 execution_profile_id TEXT NOT NULL,
 scoring_mode TEXT NOT NULL CHECK(scoring_mode IN ('fast_mode','deep_mode')),
 scoring_status TEXT NOT NULL CHECK(scoring_status IN ('COARSE_ONLY','HARD_FILTERED','DEEP_SCORED')),
 final_score REAL,
 tier0_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(tier0_json) AND json_type(tier0_json)='object'),
 tier1_score REAL NOT NULL DEFAULT 0 CHECK(tier1_score BETWEEN 0 AND 45),
 tier2_score REAL NOT NULL DEFAULT 0 CHECK(tier2_score BETWEEN 0 AND 55),
 dimensions_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(dimensions_json) AND json_type(dimensions_json)='object'),
 evidence_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(evidence_json) AND json_type(evidence_json)='array'),
 result_json TEXT NOT NULL CHECK(json_valid(result_json) AND json_type(result_json)='object'),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,evaluation_id), UNIQUE(org_id,job_id,evaluation_id), UNIQUE(org_id,task_id),
 UNIQUE(org_id,job_id,job_candidate_id,scoring_mode),
 FOREIGN KEY(org_id,job_id,task_id,job_candidate_id,candidate_id,policy_id,policy_version,resume_id,execution_profile_id,scoring_mode)
   REFERENCES evaluation_tasks(org_id,job_id,task_id,job_candidate_id,candidate_id,policy_id,policy_version,resume_id,execution_profile_id,scoring_mode),
 FOREIGN KEY(org_id,job_id,task_id,attempt_id)
   REFERENCES evaluation_attempts(org_id,job_id,task_id,attempt_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id),
 CHECK((scoring_status='DEEP_SCORED' AND scoring_mode='deep_mode' AND final_score IS NOT NULL
         AND typeof(final_score) IN ('real','integer') AND final_score BETWEEN 0 AND 100)
    OR (scoring_status IN ('COARSE_ONLY','HARD_FILTERED') AND final_score IS NULL))
);
CREATE TABLE hr_review_events (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, review_event_id TEXT NOT NULL,
 evaluation_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('APPROVE','REJECT','REQUEST_REVIEW','NOTE')),
 reason TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,review_event_id),
 FOREIGN KEY(org_id,job_id,evaluation_id) REFERENCES evaluations(org_id,job_id,evaluation_id),
 FOREIGN KEY(org_id,actor_id) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE retention_policy_versions (
 org_id TEXT NOT NULL, retention_version_id TEXT NOT NULL,
 rules_json TEXT NOT NULL CHECK(json_valid(rules_json) AND json_type(rules_json)='object'),
 created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,retention_version_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE data_operations (
 operation_id TEXT PRIMARY KEY NOT NULL, org_id TEXT,
 scope TEXT NOT NULL CHECK(scope IN ('ORG','INSTANCE')),
 kind TEXT NOT NULL CHECK(kind IN ('EXPORT','BACKUP','RESTORE','PURGE')),
 actor_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'REQUESTED'
   CHECK(status IN ('REQUESTED','APPROVED','RUNNING','COMPLETED','FAILED','CANCELLED')),
 manifest_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'),
 artifact_ref TEXT, expires_at TEXT,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 CHECK((scope='ORG' AND org_id IS NOT NULL) OR (scope='INSTANCE' AND org_id IS NULL)),
 FOREIGN KEY(org_id) REFERENCES organizations(org_id), FOREIGN KEY(actor_id) REFERENCES users(user_id)
);
CREATE INDEX idx_tasks_state ON evaluation_tasks(org_id,technical_state,created_at);
CREATE INDEX idx_attempt_task ON evaluation_attempts(org_id,job_id,task_id,attempt_no);
CREATE INDEX idx_evaluations_candidate ON evaluations(org_id,job_id,job_candidate_id,created_at);
CREATE INDEX idx_review_evaluation ON hr_review_events(org_id,job_id,evaluation_id,created_at);
