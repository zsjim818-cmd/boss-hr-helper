CREATE TABLE candidates (
 org_id TEXT NOT NULL, candidate_id TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,candidate_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE candidate_identity_aliases (
 org_id TEXT NOT NULL, source TEXT NOT NULL, external_identity_hash TEXT NOT NULL,
 candidate_id TEXT NOT NULL,
 external_identity_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(external_identity_json) AND json_type(external_identity_json)='object'),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,source,external_identity_hash),
 FOREIGN KEY(org_id,candidate_id) REFERENCES candidates(org_id,candidate_id)
);
CREATE TABLE resume_snapshots (
 org_id TEXT NOT NULL, candidate_id TEXT NOT NULL, resume_id TEXT NOT NULL,
 raw_resume_json TEXT NOT NULL CHECK(json_valid(raw_resume_json) AND json_type(raw_resume_json)='object'),
 normalized_resume_json TEXT NOT NULL CHECK(json_valid(normalized_resume_json) AND json_type(normalized_resume_json)='object'),
 content_hash TEXT NOT NULL, raw_hash TEXT NOT NULL,
 completeness TEXT NOT NULL CHECK(completeness IN ('COMPLETE','PARTIAL','COLLECTION_FAILED')),
 is_scorable INTEGER NOT NULL DEFAULT 0 CHECK(is_scorable IN (0,1)),
 collection_errors_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(collection_errors_json) AND json_type(collection_errors_json)='array'),
 data_issues_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(data_issues_json) AND json_type(data_issues_json)='array'),
 mapper_version TEXT NOT NULL DEFAULT 'resume-v2', captured_by TEXT NOT NULL,
 captured_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 retention_version_id TEXT, expires_at TEXT,
 PRIMARY KEY(org_id,candidate_id,resume_id), UNIQUE(org_id,candidate_id,raw_hash),
 FOREIGN KEY(org_id,candidate_id) REFERENCES candidates(org_id,candidate_id),
 FOREIGN KEY(org_id,captured_by) REFERENCES organization_memberships(org_id,user_id),
 FOREIGN KEY(org_id,retention_version_id) REFERENCES retention_policy_versions(org_id,retention_version_id),
 CHECK(is_scorable=0 OR (completeness='COMPLETE' AND json_array_length(collection_errors_json)=0 AND json_array_length(data_issues_json)=0))
);
CREATE TABLE scan_batches (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 policy_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
 created_by TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','PAUSED','COMPLETED','CANCELLED')),
 started_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')), completed_at TEXT,
 PRIMARY KEY(org_id,job_id,batch_id),
 UNIQUE(org_id,job_id,batch_id,policy_id,policy_version),
 FOREIGN KEY(org_id,job_id) REFERENCES jobs(org_id,job_id),
 FOREIGN KEY(org_id,job_id,policy_id,policy_version) REFERENCES policy_publications(org_id,job_id,policy_id,policy_version),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id),
 CHECK((status IN ('RUNNING','PAUSED') AND completed_at IS NULL) OR
       (status IN ('COMPLETED','CANCELLED') AND completed_at IS NOT NULL))
);
CREATE TABLE job_candidates (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, job_candidate_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL, candidate_key TEXT NOT NULL,
 policy_id TEXT NOT NULL, policy_version INTEGER NOT NULL, first_batch_id TEXT NOT NULL,
 first_seen_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,job_candidate_id),
 UNIQUE(org_id,job_id,candidate_id), UNIQUE(org_id,job_id,candidate_key),
 UNIQUE(org_id,job_id,job_candidate_id,candidate_id),
 UNIQUE(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version),
 FOREIGN KEY(org_id,candidate_id) REFERENCES candidates(org_id,candidate_id),
 FOREIGN KEY(org_id,job_id,first_batch_id,policy_id,policy_version)
   REFERENCES scan_batches(org_id,job_id,batch_id,policy_id,policy_version)
);
CREATE TABLE candidate_acquisitions (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, acquisition_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL, candidate_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 resume_id TEXT, result TEXT NOT NULL DEFAULT 'CAPTURED', source TEXT NOT NULL DEFAULT 'manual',
 captured_by TEXT NOT NULL,
 captured_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,acquisition_id),
 FOREIGN KEY(org_id,job_id,job_candidate_id,candidate_id)
   REFERENCES job_candidates(org_id,job_id,job_candidate_id,candidate_id),
 FOREIGN KEY(org_id,job_id,batch_id) REFERENCES scan_batches(org_id,job_id,batch_id),
 FOREIGN KEY(org_id,candidate_id,resume_id) REFERENCES resume_snapshots(org_id,candidate_id,resume_id),
 FOREIGN KEY(org_id,captured_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE job_candidate_inputs (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, job_candidate_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL, policy_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
 resume_id TEXT NOT NULL, sealed_by TEXT NOT NULL,
 sealed_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,job_candidate_id),
 UNIQUE(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version,resume_id),
 FOREIGN KEY(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version)
   REFERENCES job_candidates(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version),
 FOREIGN KEY(org_id,candidate_id,resume_id) REFERENCES resume_snapshots(org_id,candidate_id,resume_id),
 FOREIGN KEY(org_id,sealed_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE INDEX idx_alias_candidate ON candidate_identity_aliases(org_id,candidate_id);
CREATE INDEX idx_resume_content ON resume_snapshots(org_id,candidate_id,content_hash);
CREATE INDEX idx_batches_status ON scan_batches(org_id,job_id,status,started_at);
CREATE INDEX idx_job_candidates_seen ON job_candidates(org_id,job_id,first_seen_at);
CREATE INDEX idx_job_candidates_batch ON job_candidates(org_id,job_id,first_batch_id);
CREATE INDEX idx_acquisitions_candidate ON candidate_acquisitions(org_id,job_id,job_candidate_id,captured_at);
CREATE INDEX idx_acquisitions_batch ON candidate_acquisitions(org_id,job_id,batch_id);
