-- Sprint 5 Batch 2: workbench persistence.
--
-- Additive only.  001-011 are frozen and already applied.  This migration
--   * persists the HR page/precheck confirmation on scan_batches,
--   * persists the full browser scan counters plus a run token,
--   * adds an append-only version chain for sealed candidate inputs so a
--     PARTIAL resume can be completed and re-scored without rewriting audit,
--   * rejects cookie/token/security_id material at the database boundary.
PRAGMA defer_foreign_keys=ON;

-- ------------------------------------------------- batch precheck confirmation
ALTER TABLE scan_batches ADD COLUMN platform_job_id TEXT;
ALTER TABLE scan_batches ADD COLUMN filter_summary_json TEXT NOT NULL DEFAULT '[]'
  CHECK(json_valid(filter_summary_json)
        AND json_type(filter_summary_json) IN ('array','object'));
ALTER TABLE scan_batches ADD COLUMN filters_confirmed_at TEXT;
ALTER TABLE scan_batches ADD COLUMN confirmed_by TEXT;

CREATE TRIGGER scan_batch_confirmation_insert_guard BEFORE INSERT ON scan_batches
WHEN (NEW.filters_confirmed_at IS NULL) <> (NEW.confirmed_by IS NULL)
  OR (NEW.confirmed_by IS NOT NULL AND NOT EXISTS(
      SELECT 1 FROM organization_memberships m
      WHERE m.org_id=NEW.org_id AND m.user_id=NEW.confirmed_by AND m.state='ACTIVE'))
BEGIN SELECT RAISE(ABORT,'invalid batch precheck confirmation actor/time'); END;

CREATE TRIGGER scan_batch_confirmation_guard BEFORE UPDATE ON scan_batches
WHEN NEW.platform_job_id IS NOT OLD.platform_job_id
  OR NEW.filter_summary_json IS NOT OLD.filter_summary_json
  OR NEW.filters_confirmed_at IS NOT OLD.filters_confirmed_at
  OR NEW.confirmed_by IS NOT OLD.confirmed_by
BEGIN
  SELECT RAISE(ABORT,'batch precheck confirmation requires both time and actor')
  WHERE (NEW.filters_confirmed_at IS NULL) <> (NEW.confirmed_by IS NULL);
  SELECT RAISE(ABORT,'batch precheck confirmation actor is not an active member')
  WHERE NEW.confirmed_by IS NOT NULL AND NOT EXISTS(
      SELECT 1 FROM organization_memberships m
      WHERE m.org_id=NEW.org_id AND m.user_id=NEW.confirmed_by AND m.state='ACTIVE');
END;

-- ------------------------------------------------------- scan run counters
ALTER TABLE browser_scan_runs ADD COLUMN pages_read INTEGER NOT NULL DEFAULT 0
  CHECK(pages_read>=0);
ALTER TABLE browser_scan_runs ADD COLUMN cards_seen INTEGER NOT NULL DEFAULT 0
  CHECK(cards_seen>=0);
ALTER TABLE browser_scan_runs ADD COLUMN cards_deduped INTEGER NOT NULL DEFAULT 0
  CHECK(cards_deduped>=0);
ALTER TABLE browser_scan_runs ADD COLUMN details_opened INTEGER NOT NULL DEFAULT 0
  CHECK(details_opened>=0);
ALTER TABLE browser_scan_runs ADD COLUMN details_completed INTEGER NOT NULL DEFAULT 0
  CHECK(details_completed>=0);
ALTER TABLE browser_scan_runs ADD COLUMN details_partial INTEGER NOT NULL DEFAULT 0
  CHECK(details_partial>=0);
ALTER TABLE browser_scan_runs ADD COLUMN prescreen_skipped INTEGER NOT NULL DEFAULT 0
  CHECK(prescreen_skipped>=0);
ALTER TABLE browser_scan_runs ADD COLUMN stop_reason TEXT;
ALTER TABLE browser_scan_runs ADD COLUMN run_token TEXT NOT NULL DEFAULT ''
  CHECK(typeof(run_token)='text');

-- A token identifies one run generation; an old async callback can never win a
-- conditional UPDATE once a newer run has replaced the token.
CREATE UNIQUE INDEX ux_browser_scan_runs_run_token
ON browser_scan_runs(org_id,job_id,batch_id,run_token) WHERE run_token<>'';

-- --------------------------------------------- versioned sealed input snapshots
CREATE TABLE job_candidate_input_versions (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL,
 input_version INTEGER NOT NULL CHECK(typeof(input_version)='integer' AND input_version>=1),
 candidate_id TEXT NOT NULL,
 policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL,
 resume_id TEXT NOT NULL,
 sealed_by TEXT NOT NULL,
 sealed_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 superseded_at TEXT,
 superseded_by TEXT,
 supersede_reason TEXT NOT NULL DEFAULT '',
 is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
 PRIMARY KEY(org_id,job_id,job_candidate_id,input_version),
 UNIQUE(org_id,job_id,job_candidate_id,resume_id),
 CHECK((is_current=1 AND superseded_at IS NULL AND superseded_by IS NULL)
    OR (is_current=0 AND superseded_at IS NOT NULL)),
 FOREIGN KEY(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version)
   REFERENCES job_candidates(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version),
 FOREIGN KEY(org_id,candidate_id,resume_id) REFERENCES resume_snapshots(org_id,candidate_id,resume_id),
 FOREIGN KEY(org_id,sealed_by) REFERENCES organization_memberships(org_id,user_id),
 FOREIGN KEY(org_id,superseded_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE UNIQUE INDEX ux_job_candidate_input_current
ON job_candidate_input_versions(org_id,job_id,job_candidate_id) WHERE is_current=1;
CREATE INDEX ix_job_candidate_input_versions_resume
ON job_candidate_input_versions(org_id,candidate_id,resume_id);

-- Every legacy v11 sealed input becomes version 1 and stays current.
INSERT INTO job_candidate_input_versions
 (org_id,job_id,job_candidate_id,input_version,candidate_id,policy_id,
  policy_version,resume_id,sealed_by,sealed_at,superseded_at,superseded_by,
  supersede_reason,is_current)
SELECT i.org_id,i.job_id,i.job_candidate_id,1,i.candidate_id,i.policy_id,
       i.policy_version,i.resume_id,i.sealed_by,i.sealed_at,NULL,NULL,'',1
FROM job_candidate_inputs i;

CREATE TRIGGER input_version_requires_complete_acquired_resume
BEFORE INSERT ON job_candidate_input_versions
WHEN NOT EXISTS(
      SELECT 1 FROM resume_snapshots r
      WHERE r.org_id=NEW.org_id AND r.candidate_id=NEW.candidate_id
        AND r.resume_id=NEW.resume_id AND r.completeness='COMPLETE'
        AND r.is_scorable=1 AND json_array_length(r.collection_errors_json)=0
        AND json_array_length(r.data_issues_json)=0)
  OR NOT EXISTS(
      SELECT 1 FROM candidate_acquisitions a
      WHERE a.org_id=NEW.org_id AND a.job_id=NEW.job_id
        AND a.job_candidate_id=NEW.job_candidate_id
        AND a.candidate_id=NEW.candidate_id AND a.resume_id=NEW.resume_id)
BEGIN SELECT RAISE(ABORT,'input version requires a complete scorable acquired resume'); END;

CREATE TRIGGER input_version_sequence_guard
BEFORE INSERT ON job_candidate_input_versions
WHEN NEW.input_version <> (
      SELECT COALESCE(MAX(input_version),0)+1 FROM job_candidate_input_versions
      WHERE org_id=NEW.org_id AND job_id=NEW.job_id
        AND job_candidate_id=NEW.job_candidate_id)
BEGIN SELECT RAISE(ABORT,'input versions must form a gapless append-only sequence'); END;

CREATE TRIGGER input_version_no_replace
BEFORE INSERT ON job_candidate_input_versions
WHEN EXISTS(
      SELECT 1 FROM job_candidate_input_versions
      WHERE org_id=NEW.org_id AND job_id=NEW.job_id
        AND job_candidate_id=NEW.job_candidate_id
        AND input_version=NEW.input_version)
BEGIN SELECT RAISE(ABORT,'input versions are immutable; no replacement'); END;

-- Only the current -> superseded transition may UPDATE, and only the supersede
-- columns may change.  Historical input versions can never be rewritten.
CREATE TRIGGER input_version_immutable
BEFORE UPDATE ON job_candidate_input_versions
WHEN NEW.org_id IS NOT OLD.org_id
  OR NEW.job_id IS NOT OLD.job_id
  OR NEW.job_candidate_id IS NOT OLD.job_candidate_id
  OR NEW.input_version IS NOT OLD.input_version
  OR NEW.candidate_id IS NOT OLD.candidate_id
  OR NEW.policy_id IS NOT OLD.policy_id
  OR NEW.policy_version IS NOT OLD.policy_version
  OR NEW.resume_id IS NOT OLD.resume_id
  OR NEW.sealed_by IS NOT OLD.sealed_by
  OR NEW.sealed_at IS NOT OLD.sealed_at
  OR NOT (OLD.is_current=1 AND NEW.is_current=0
          AND OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'immutable sealed input version history'); END;

CREATE TRIGGER no_delete_job_candidate_input_versions
BEFORE DELETE ON job_candidate_input_versions
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for sealed input versions'); END;

-- Read side used by scoring/embeddings: current version, legacy fallback.
CREATE VIEW job_candidate_inputs_current AS
SELECT org_id,job_id,job_candidate_id,input_version,candidate_id,policy_id,
       policy_version,resume_id,sealed_by,sealed_at,is_current
FROM job_candidate_input_versions WHERE is_current=1
UNION ALL
SELECT i.org_id,i.job_id,i.job_candidate_id,1,i.candidate_id,i.policy_id,
       i.policy_version,i.resume_id,i.sealed_by,i.sealed_at,1
FROM job_candidate_inputs i
WHERE NOT EXISTS(
      SELECT 1 FROM job_candidate_input_versions v
      WHERE v.org_id=i.org_id AND v.job_id=i.job_id
        AND v.job_candidate_id=i.job_candidate_id AND v.is_current=1);

-- ------------------------------------------------ credential-free persistence
-- Short-lived platform session material must never reach a sealed resume row.
-- Keys are inspected recursively through json_tree, independent of nesting.
CREATE TRIGGER resume_snapshots_no_credentials
BEFORE INSERT ON resume_snapshots
BEGIN
  SELECT RAISE(ABORT,'resume snapshot must not persist cookie/token/security_id')
  WHERE EXISTS(
    SELECT 1 FROM json_tree(NEW.raw_resume_json)
    WHERE key IS NOT NULL AND type <> 'null'
      AND NOT (type='text' AND trim(COALESCE(atom,''))='')
      AND (
      lower(key) IN ('cookie','cookies','set-cookie','authorization',
                     'access_token','refresh_token','id_token','session_token',
                     'security_id','csrf','csrf_token','xsrf_token')
      OR lower(key) LIKE '%cookie%'
      OR lower(key) LIKE '%security_id%'
      OR lower(key) LIKE '%access_token%'
      OR lower(key) LIKE '%refresh_token%'
      OR lower(key) LIKE '%session_token%'));
  SELECT RAISE(ABORT,'resume snapshot must not persist cookie/token/security_id')
  WHERE EXISTS(
    SELECT 1 FROM json_tree(NEW.normalized_resume_json)
    WHERE key IS NOT NULL AND type <> 'null'
      AND NOT (type='text' AND trim(COALESCE(atom,''))='')
      AND (
      lower(key) IN ('cookie','cookies','set-cookie','authorization',
                     'access_token','refresh_token','id_token','session_token',
                     'security_id','csrf','csrf_token','xsrf_token')
      OR lower(key) LIKE '%cookie%'
      OR lower(key) LIKE '%security_id%'
      OR lower(key) LIKE '%access_token%'
      OR lower(key) LIKE '%refresh_token%'
      OR lower(key) LIKE '%session_token%'));
END;

CREATE TRIGGER browser_binding_no_credentials_insert
BEFORE INSERT ON browser_account_bindings
WHEN instr(lower(NEW.profile_id),'cookie')>0
  OR instr(lower(NEW.profile_id),'authorization')>0
  OR instr(lower(NEW.profile_id),'access_token')>0
  OR instr(lower(NEW.profile_id),'refresh_token')>0
BEGIN SELECT RAISE(ABORT,'browser binding must not persist credentials'); END;

CREATE TRIGGER browser_binding_no_credentials_update
BEFORE UPDATE ON browser_account_bindings
WHEN instr(lower(NEW.profile_id),'cookie')>0
  OR instr(lower(NEW.profile_id),'authorization')>0
  OR instr(lower(NEW.profile_id),'access_token')>0
  OR instr(lower(NEW.profile_id),'refresh_token')>0
BEGIN SELECT RAISE(ABORT,'browser binding must not persist credentials'); END;
