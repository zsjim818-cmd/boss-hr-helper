CREATE TABLE organizations (
 org_id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','DISABLED')),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE users (
 user_id TEXT PRIMARY KEY NOT NULL, auth_subject TEXT NOT NULL UNIQUE,
 display_name TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','DISABLED')),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE organization_memberships (
 org_id TEXT NOT NULL, user_id TEXT NOT NULL,
 org_role TEXT NOT NULL CHECK(org_role IN ('ORG_ADMIN','HR')),
 state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','DISABLED')),
 joined_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,user_id),
 FOREIGN KEY(org_id) REFERENCES organizations(org_id),
 FOREIGN KEY(user_id) REFERENCES users(user_id)
);
CREATE TABLE jobs (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, title TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'OPEN' CHECK(state IN ('OPEN','CLOSED')),
 created_by TEXT NOT NULL, current_policy_id TEXT, current_policy_version INTEGER,
 next_policy_version INTEGER NOT NULL DEFAULT 1 CHECK(typeof(next_policy_version)='integer' AND next_policy_version>0),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id),
 CHECK((current_policy_id IS NULL AND current_policy_version IS NULL) OR
       (current_policy_id IS NOT NULL AND current_policy_version IS NOT NULL)),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id),
 FOREIGN KEY(org_id,job_id,current_policy_id,current_policy_version)
   REFERENCES policy_publications(org_id,job_id,policy_id,policy_version)
   DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE job_access_grants (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, user_id TEXT NOT NULL,
 can_view_resume INTEGER NOT NULL DEFAULT 1 CHECK(can_view_resume IN (0,1)),
 can_edit_policy INTEGER NOT NULL DEFAULT 0 CHECK(can_edit_policy IN (0,1)),
 can_publish_policy INTEGER NOT NULL DEFAULT 0 CHECK(can_publish_policy IN (0,1)),
 can_collect INTEGER NOT NULL DEFAULT 0 CHECK(can_collect IN (0,1)),
 can_score INTEGER NOT NULL DEFAULT 0 CHECK(can_score IN (0,1)),
 can_review INTEGER NOT NULL DEFAULT 0 CHECK(can_review IN (0,1)),
 can_export INTEGER NOT NULL DEFAULT 0 CHECK(can_export IN (0,1)),
 can_manage_grants INTEGER NOT NULL DEFAULT 0 CHECK(can_manage_grants IN (0,1)),
 granted_by TEXT NOT NULL,
 granted_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')), revoked_at TEXT,
 PRIMARY KEY(org_id,job_id,user_id),
 FOREIGN KEY(org_id,job_id) REFERENCES jobs(org_id,job_id),
 FOREIGN KEY(org_id,user_id) REFERENCES organization_memberships(org_id,user_id),
 FOREIGN KEY(org_id,granted_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE execution_profiles (
 org_id TEXT NOT NULL, profile_id TEXT NOT NULL,
 profile_json TEXT NOT NULL CHECK(json_valid(profile_json) AND json_type(profile_json)='object'),
 profile_hash TEXT NOT NULL, created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,profile_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE policy_drafts (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, draft_id TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1 CHECK(typeof(revision)='integer' AND revision>0),
 source_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(source_json) AND json_type(source_json)='object'),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json) AND json_type(policy_json)='object'),
 diagnostics_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(diagnostics_json) AND json_type(diagnostics_json)='array'),
 draft_hash TEXT NOT NULL, edited_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 updated_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,draft_id),
 FOREIGN KEY(org_id,job_id) REFERENCES jobs(org_id,job_id),
 FOREIGN KEY(org_id,edited_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE job_policy_versions (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL CHECK(typeof(policy_version)='integer' AND policy_version>0),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json) AND json_type(policy_json)='object'),
 policy_hash TEXT NOT NULL, original_jd TEXT NOT NULL DEFAULT '', hr_requirements TEXT NOT NULL DEFAULT '',
 execution_profile_id TEXT NOT NULL, policy_schema_version TEXT NOT NULL DEFAULT 'storage_snapshot_v1',
 source_draft_id TEXT, draft_revision INTEGER, source_draft_hash TEXT,
 source_snapshot_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(source_snapshot_json) AND json_type(source_snapshot_json)='object'),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,policy_id), UNIQUE(org_id,job_id,policy_version),
 UNIQUE(org_id,job_id,policy_id,policy_version),
 UNIQUE(org_id,job_id,policy_id,policy_version,execution_profile_id),
 CHECK((source_draft_id IS NULL AND draft_revision IS NULL AND source_draft_hash IS NULL) OR
       (source_draft_id IS NOT NULL AND draft_revision IS NOT NULL AND draft_revision>0 AND source_draft_hash IS NOT NULL)),
 FOREIGN KEY(org_id,job_id) REFERENCES jobs(org_id,job_id),
 FOREIGN KEY(org_id,execution_profile_id) REFERENCES execution_profiles(org_id,profile_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id),
 FOREIGN KEY(org_id,job_id,source_draft_id) REFERENCES policy_drafts(org_id,job_id,draft_id)
);
CREATE TABLE policy_publications (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL, execution_profile_id TEXT NOT NULL,
 confirmation_hash TEXT NOT NULL, published_by TEXT NOT NULL,
 published_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,policy_id), UNIQUE(org_id,job_id,policy_id,policy_version),
 FOREIGN KEY(org_id,job_id,policy_id,policy_version,execution_profile_id)
   REFERENCES job_policy_versions(org_id,job_id,policy_id,policy_version,execution_profile_id),
 FOREIGN KEY(org_id,published_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE policy_activation_events (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, event_id TEXT NOT NULL,
 from_policy_id TEXT, to_policy_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT 'publish',
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,event_id), UNIQUE(org_id,job_id,to_policy_id),
 FOREIGN KEY(org_id,job_id,from_policy_id) REFERENCES policy_publications(org_id,job_id,policy_id),
 FOREIGN KEY(org_id,job_id,to_policy_id) REFERENCES policy_publications(org_id,job_id,policy_id),
 FOREIGN KEY(org_id,actor_id) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE audit_events (
 org_id TEXT NOT NULL, event_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 action TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
 details_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(details_json) AND json_type(details_json)='object'),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,event_id),
 FOREIGN KEY(org_id,actor_id) REFERENCES organization_memberships(org_id,user_id)
);
CREATE INDEX idx_jobs_creator ON jobs(org_id,created_by);
CREATE INDEX idx_policy_profile ON job_policy_versions(org_id,execution_profile_id);
CREATE INDEX idx_audit_created ON audit_events(org_id,created_at);
