-- 006 was already applied to live-diagnosis files: append a new migration.
CREATE TRIGGER validation_lineage_matches_draft BEFORE INSERT ON policy_compilation_runs
WHEN NEW.run_kind='VALIDATION' AND NOT EXISTS(SELECT 1 FROM policy_compilation_runs parent
 WHERE parent.org_id=NEW.org_id AND parent.job_id=NEW.job_id AND parent.run_id=NEW.parent_run_id
 AND parent.draft_id=NEW.draft_id AND parent.draft_revision<NEW.draft_revision)
BEGIN SELECT RAISE(ABORT,'revalidation must retain an earlier compilation of the same draft'); END;
