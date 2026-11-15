-- Stage 3 compiler facts extend, rather than rewrite, deployed Stage 2 history.
CREATE TABLE policy_compilation_runs (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, run_id TEXT NOT NULL,
 run_kind TEXT NOT NULL CHECK(run_kind IN ('LLM','VALIDATION')),
 parent_run_id TEXT,
 source_json TEXT NOT NULL CHECK(json_valid(source_json) AND json_type(source_json)='object'),
 input_hash TEXT NOT NULL,
 draft_id TEXT, draft_revision INTEGER, draft_hash TEXT,
 policy_json TEXT CHECK(policy_json IS NULL OR (json_valid(policy_json) AND json_type(policy_json)='object')),
 policy_hash TEXT,
 status TEXT NOT NULL CHECK(status IN ('SUCCEEDED','FAILED')),
 publishable INTEGER NOT NULL CHECK(publishable IN (0,1)),
 diagnostics_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(diagnostics_json) AND json_type(diagnostics_json)='array'),
 compiler_version TEXT NOT NULL, prompt_version TEXT NOT NULL, validator_version TEXT NOT NULL,
 policy_schema_version TEXT NOT NULL, model_version TEXT NOT NULL,
 metrics_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metrics_json) AND json_type(metrics_json)='object'),
 error_type TEXT,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,run_id), UNIQUE(org_id,job_id,run_id),
 CHECK((status='FAILED' AND publishable=0 AND error_type IS NOT NULL AND length(trim(error_type))>0)
    OR (status='SUCCEEDED' AND error_type IS NULL AND draft_id IS NOT NULL
        AND draft_revision IS NOT NULL AND draft_revision>0 AND draft_hash IS NOT NULL
        AND policy_json IS NOT NULL AND policy_hash IS NOT NULL)),
 CHECK((draft_id IS NULL AND draft_revision IS NULL AND draft_hash IS NULL)
    OR (draft_id IS NOT NULL AND draft_revision IS NOT NULL AND draft_hash IS NOT NULL)),
 CHECK((run_kind='LLM' AND parent_run_id IS NULL) OR (run_kind='VALIDATION' AND parent_run_id IS NOT NULL)),
 CHECK(parent_run_id IS NULL OR parent_run_id<>run_id),
 FOREIGN KEY(org_id,job_id) REFERENCES jobs(org_id,job_id),
 FOREIGN KEY(org_id,job_id,draft_id) REFERENCES policy_drafts(org_id,job_id,draft_id),
 FOREIGN KEY(org_id,job_id,parent_run_id) REFERENCES policy_compilation_runs(org_id,job_id,run_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE policy_confirmations (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, confirmation_id TEXT NOT NULL,
 draft_id TEXT NOT NULL, draft_revision INTEGER NOT NULL, draft_hash TEXT NOT NULL,
 policy_hash TEXT NOT NULL, run_id TEXT NOT NULL, confirmed_by TEXT NOT NULL,
 confirmed_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,confirmation_id), UNIQUE(org_id,job_id,confirmation_id),
 FOREIGN KEY(org_id,job_id,draft_id) REFERENCES policy_drafts(org_id,job_id,draft_id),
 FOREIGN KEY(org_id,job_id,run_id) REFERENCES policy_compilation_runs(org_id,job_id,run_id),
 FOREIGN KEY(org_id,confirmed_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE TABLE policy_version_compilations (
 org_id TEXT NOT NULL, job_id TEXT NOT NULL, policy_id TEXT NOT NULL,
 run_id TEXT NOT NULL, confirmation_id TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,policy_id),
 FOREIGN KEY(org_id,job_id,policy_id) REFERENCES job_policy_versions(org_id,job_id,policy_id),
 FOREIGN KEY(org_id,job_id,run_id) REFERENCES policy_compilation_runs(org_id,job_id,run_id),
 FOREIGN KEY(org_id,job_id,confirmation_id) REFERENCES policy_confirmations(org_id,job_id,confirmation_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);
CREATE INDEX idx_compiler_draft ON policy_compilation_runs(org_id,job_id,draft_id,draft_revision);
CREATE INDEX idx_confirmations_draft ON policy_confirmations(org_id,job_id,draft_id,draft_revision);

CREATE TRIGGER generic_policy_protocol_matches BEFORE INSERT ON job_policy_versions
WHEN (json_extract(NEW.policy_json,'$.schema_version')='job_scoring_policy_v1'
      AND NEW.policy_schema_version<>'job_scoring_policy_v1')
 OR (NEW.policy_schema_version='job_scoring_policy_v1'
      AND json_extract(NEW.policy_json,'$.schema_version') IS NOT 'job_scoring_policy_v1')
BEGIN SELECT RAISE(ABORT,'generic policy payload and storage protocol must match'); END;
CREATE TRIGGER generic_policy_dimensions_valid BEFORE INSERT ON job_policy_versions
WHEN NEW.policy_schema_version='job_scoring_policy_v1' AND (
 json_type(NEW.policy_json,'$.scoring_dimensions') IS NOT 'array'
 OR json_array_length(NEW.policy_json,'$.scoring_dimensions')<1
 OR EXISTS(SELECT 1 FROM json_each(NEW.policy_json,'$.scoring_dimensions') d
   WHERE COALESCE(json_type(d.value,'$.weight'),'null') NOT IN ('integer','real')
     OR json_extract(d.value,'$.weight')<=0 OR json_extract(d.value,'$.weight')>1
     OR json_type(d.value,'$.dimension_id') IS NOT 'text'
     OR length(trim(json_extract(d.value,'$.dimension_id')))=0)
 OR abs(COALESCE((SELECT SUM(json_extract(value,'$.weight'))
   FROM json_each(NEW.policy_json,'$.scoring_dimensions')),-1)-1)>0.000001
 OR (SELECT COUNT(DISTINCT json_extract(value,'$.dimension_id'))
   FROM json_each(NEW.policy_json,'$.scoring_dimensions'))<>json_array_length(NEW.policy_json,'$.scoring_dimensions'))
BEGIN SELECT RAISE(ABORT,'generic policy dimensions require unique IDs and positive weights summing to one'); END;

CREATE TRIGGER compilation_matches_draft BEFORE INSERT ON policy_compilation_runs
WHEN NEW.status='SUCCEEDED' AND NOT EXISTS(SELECT 1 FROM policy_drafts d
 WHERE d.org_id=NEW.org_id AND d.job_id=NEW.job_id AND d.draft_id=NEW.draft_id
 AND d.revision=NEW.draft_revision AND d.draft_hash=NEW.draft_hash
 AND d.policy_json=NEW.policy_json AND d.source_json=NEW.source_json)
BEGIN SELECT RAISE(ABORT,'successful compilation must match the exact current draft and source'); END;
CREATE TRIGGER confirmation_matches_validated_draft BEFORE INSERT ON policy_confirmations
WHEN NOT EXISTS(SELECT 1 FROM policy_drafts d JOIN policy_compilation_runs r
 ON r.org_id=d.org_id AND r.job_id=d.job_id AND r.draft_id=d.draft_id
 WHERE d.org_id=NEW.org_id AND d.job_id=NEW.job_id AND d.draft_id=NEW.draft_id
 AND d.revision=NEW.draft_revision AND d.draft_hash=NEW.draft_hash
 AND r.run_id=NEW.run_id AND r.status='SUCCEEDED' AND r.publishable=1
 AND r.draft_revision=d.revision AND r.draft_hash=d.draft_hash
 AND r.policy_json=d.policy_json AND r.source_json=d.source_json AND r.policy_hash=NEW.policy_hash)
BEGIN SELECT RAISE(ABORT,'confirmation requires the current complete validated draft hash'); END;
CREATE TRIGGER confirmation_publisher_permission BEFORE INSERT ON policy_confirmations
WHEN NOT EXISTS(SELECT 1 FROM organization_memberships m JOIN users u USING(user_id)
 JOIN organizations o USING(org_id) WHERE m.org_id=NEW.org_id AND m.user_id=NEW.confirmed_by
 AND m.state='ACTIVE' AND u.state='ACTIVE' AND o.state='ACTIVE'
 AND (m.org_role='ORG_ADMIN' OR EXISTS(SELECT 1 FROM job_access_grants g
   WHERE g.org_id=NEW.org_id AND g.job_id=NEW.job_id AND g.user_id=NEW.confirmed_by
   AND g.can_view_resume=1 AND g.can_publish_policy=1 AND g.revoked_at IS NULL)))
BEGIN SELECT RAISE(ABORT,'draft confirmation requires active publication permission'); END;
CREATE TRIGGER version_compilation_matches BEFORE INSERT ON policy_version_compilations
WHEN NOT EXISTS(SELECT 1 FROM job_policy_versions v
 JOIN policy_compilation_runs r ON r.org_id=v.org_id AND r.job_id=v.job_id
 JOIN policy_confirmations c ON c.org_id=v.org_id AND c.job_id=v.job_id AND c.run_id=r.run_id
 WHERE v.org_id=NEW.org_id AND v.job_id=NEW.job_id AND v.policy_id=NEW.policy_id
 AND r.run_id=NEW.run_id AND c.confirmation_id=NEW.confirmation_id
 AND v.policy_schema_version='job_scoring_policy_v1'
 AND r.status='SUCCEEDED' AND r.publishable=1
 AND v.source_draft_id=r.draft_id AND v.draft_revision=r.draft_revision AND v.source_draft_hash=r.draft_hash
 AND v.source_snapshot_json=r.source_json AND v.policy_json=r.policy_json AND v.policy_hash=r.policy_hash
 AND c.draft_id=r.draft_id AND c.draft_revision=r.draft_revision AND c.draft_hash=r.draft_hash
 AND c.policy_hash=r.policy_hash)
BEGIN SELECT RAISE(ABORT,'version requires its exact validated compilation and HR confirmation'); END;
CREATE TRIGGER generic_policy_publication_requires_compiler BEFORE INSERT ON policy_publications
WHEN EXISTS(SELECT 1 FROM job_policy_versions v
 WHERE v.org_id=NEW.org_id AND v.job_id=NEW.job_id AND v.policy_id=NEW.policy_id
 AND v.policy_schema_version='job_scoring_policy_v1')
 AND NOT EXISTS(SELECT 1 FROM policy_version_compilations l
 WHERE l.org_id=NEW.org_id AND l.job_id=NEW.job_id AND l.policy_id=NEW.policy_id)
BEGIN SELECT RAISE(ABORT,'generic policy publication requires validated compiler and HR confirmation facts'); END;

CREATE TRIGGER no_replace_policy_compilation_runs BEFORE INSERT ON policy_compilation_runs
WHEN EXISTS(SELECT 1 FROM policy_compilation_runs WHERE org_id=NEW.org_id AND run_id=NEW.run_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_compilation_runs'); END;
CREATE TRIGGER no_update_policy_compilation_runs BEFORE UPDATE ON policy_compilation_runs
BEGIN SELECT RAISE(ABORT,'immutable policy_compilation_runs'); END;
CREATE TRIGGER no_delete_policy_compilation_runs BEFORE DELETE ON policy_compilation_runs
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_compilation_runs'); END;
CREATE TRIGGER no_replace_policy_confirmations BEFORE INSERT ON policy_confirmations
WHEN EXISTS(SELECT 1 FROM policy_confirmations WHERE org_id=NEW.org_id AND confirmation_id=NEW.confirmation_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_confirmations'); END;
CREATE TRIGGER no_update_policy_confirmations BEFORE UPDATE ON policy_confirmations
BEGIN SELECT RAISE(ABORT,'immutable policy_confirmations'); END;
CREATE TRIGGER no_delete_policy_confirmations BEFORE DELETE ON policy_confirmations
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_confirmations'); END;
CREATE TRIGGER no_replace_policy_version_compilations BEFORE INSERT ON policy_version_compilations
WHEN EXISTS(SELECT 1 FROM policy_version_compilations WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND policy_id=NEW.policy_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_version_compilations'); END;
CREATE TRIGGER no_update_policy_version_compilations BEFORE UPDATE ON policy_version_compilations
BEGIN SELECT RAISE(ABORT,'immutable policy_version_compilations'); END;
CREATE TRIGGER no_delete_policy_version_compilations BEFORE DELETE ON policy_version_compilations
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_version_compilations'); END;
