-- Sprint 5 Batch 4: soft-delete tombstones for immutable candidate history.
-- 001-012 are frozen.  Sealed inputs/evaluations are immutable audit records, so
-- UI deletion is represented by a tombstone that API listings filter out.
PRAGMA defer_foreign_keys=ON;

CREATE TABLE job_candidate_deletions (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 job_candidate_id TEXT NOT NULL,
 deleted_by TEXT NOT NULL,
 deleted_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,job_candidate_id),
 FOREIGN KEY(org_id,job_id,job_candidate_id)
   REFERENCES job_candidates(org_id,job_id,job_candidate_id),
 FOREIGN KEY(org_id,deleted_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE INDEX ix_job_candidate_deletions_job
ON job_candidate_deletions(org_id,job_id,deleted_at);
