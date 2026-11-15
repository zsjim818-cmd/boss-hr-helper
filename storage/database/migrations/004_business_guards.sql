CREATE TRIGGER policy_version_sequence BEFORE INSERT ON job_policy_versions
WHEN NEW.policy_version <> COALESCE((SELECT MAX(policy_version)+1 FROM job_policy_versions
 WHERE org_id=NEW.org_id AND job_id=NEW.job_id),1)
BEGIN SELECT RAISE(ABORT,'policy version must advance by one within its job'); END;
CREATE TRIGGER policy_version_allocated AFTER INSERT ON job_policy_versions
BEGIN UPDATE jobs SET next_policy_version=NEW.policy_version+1
 WHERE org_id=NEW.org_id AND job_id=NEW.job_id; END;
CREATE TRIGGER policy_publication_gate BEFORE INSERT ON policy_publications
WHEN NOT EXISTS(SELECT 1 FROM job_policy_versions v JOIN jobs j USING(org_id,job_id)
 WHERE v.org_id=NEW.org_id AND v.job_id=NEW.job_id AND v.policy_id=NEW.policy_id
 AND v.policy_version=NEW.policy_version AND v.execution_profile_id=NEW.execution_profile_id
 AND ((v.source_draft_id IS NULL AND v.policy_hash=NEW.confirmation_hash)
   OR (v.source_draft_id IS NOT NULL AND v.source_draft_hash=NEW.confirmation_hash))
 AND j.state='OPEN'
 AND (j.current_policy_version IS NULL OR NEW.policy_version>j.current_policy_version)
 AND (v.source_draft_id IS NULL OR EXISTS(SELECT 1 FROM policy_drafts d
   WHERE d.org_id=v.org_id AND d.job_id=v.job_id AND d.draft_id=v.source_draft_id
   AND d.revision=v.draft_revision AND d.draft_hash=v.source_draft_hash
   AND d.policy_json=v.policy_json AND d.source_json=v.source_snapshot_json
   AND json_array_length(d.diagnostics_json)=0)))
BEGIN SELECT RAISE(ABORT,'publication requires matching confirmed policy and current draft'); END;
CREATE TRIGGER policy_publication_permission BEFORE INSERT ON policy_publications
WHEN NOT EXISTS(SELECT 1 FROM organization_memberships m JOIN users u USING(user_id)
 JOIN organizations o USING(org_id) WHERE m.org_id=NEW.org_id AND m.user_id=NEW.published_by
 AND m.state='ACTIVE' AND u.state='ACTIVE' AND o.state='ACTIVE'
 AND (m.org_role='ORG_ADMIN' OR EXISTS(SELECT 1 FROM job_access_grants g
   WHERE g.org_id=NEW.org_id AND g.job_id=NEW.job_id AND g.user_id=NEW.published_by
   AND g.can_publish_policy=1 AND g.revoked_at IS NULL)))
BEGIN SELECT RAISE(ABORT,'publisher lacks active publication permission'); END;
CREATE TRIGGER policy_activated AFTER INSERT ON policy_publications
BEGIN
 INSERT INTO policy_activation_events(org_id,job_id,event_id,from_policy_id,to_policy_id,actor_id)
 SELECT NEW.org_id,NEW.job_id,lower(hex(randomblob(16))),current_policy_id,NEW.policy_id,NEW.published_by
 FROM jobs WHERE org_id=NEW.org_id AND job_id=NEW.job_id;
 UPDATE jobs SET current_policy_id=NEW.policy_id,current_policy_version=NEW.policy_version
 WHERE org_id=NEW.org_id AND job_id=NEW.job_id;
END;
CREATE TRIGGER policy_pointer_forward BEFORE UPDATE OF current_policy_id,current_policy_version ON jobs
WHEN (NEW.current_policy_id IS NOT OLD.current_policy_id OR NEW.current_policy_version IS NOT OLD.current_policy_version)
 AND (NOT EXISTS(SELECT 1 FROM policy_publications p WHERE p.org_id=NEW.org_id AND p.job_id=NEW.job_id
   AND p.policy_id=NEW.current_policy_id AND p.policy_version=NEW.current_policy_version)
 OR (OLD.current_policy_version IS NOT NULL AND NEW.current_policy_version<=OLD.current_policy_version))
BEGIN SELECT RAISE(ABORT,'current policy requires a forward published version'); END;
CREATE TRIGGER policy_draft_revision BEFORE UPDATE ON policy_drafts
WHEN NEW.revision<>OLD.revision+1
BEGIN SELECT RAISE(ABORT,'draft update requires the next revision'); END;
CREATE TRIGGER batch_lock_current BEFORE INSERT ON scan_batches
WHEN NEW.status<>'RUNNING' OR NEW.completed_at IS NOT NULL OR NOT EXISTS(
 SELECT 1 FROM jobs WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND state='OPEN'
 AND current_policy_id=NEW.policy_id AND current_policy_version=NEW.policy_version)
BEGIN SELECT RAISE(ABORT,'new batch must lock the current published policy'); END;
CREATE TRIGGER batch_creator_permission BEFORE INSERT ON scan_batches
WHEN NOT EXISTS(SELECT 1 FROM organization_memberships m JOIN users u USING(user_id)
 JOIN organizations o USING(org_id) WHERE m.org_id=NEW.org_id AND m.user_id=NEW.created_by
 AND m.state='ACTIVE' AND u.state='ACTIVE' AND o.state='ACTIVE'
 AND (m.org_role='ORG_ADMIN' OR EXISTS(SELECT 1 FROM job_access_grants g
  WHERE g.org_id=NEW.org_id AND g.job_id=NEW.job_id AND g.user_id=NEW.created_by
  AND g.can_collect=1 AND g.revoked_at IS NULL)))
BEGIN SELECT RAISE(ABORT,'batch creator lacks collection permission'); END;
CREATE TRIGGER batch_lifecycle BEFORE UPDATE ON scan_batches
WHEN NOT ((NEW.status=OLD.status)
 OR (OLD.status='RUNNING' AND NEW.status IN ('PAUSED','COMPLETED','CANCELLED'))
 OR (OLD.status='PAUSED' AND NEW.status IN ('RUNNING','COMPLETED','CANCELLED')))
 OR (OLD.status IN ('COMPLETED','CANCELLED') AND NEW.completed_at IS NOT OLD.completed_at)
BEGIN SELECT RAISE(ABORT,'illegal scan batch lifecycle'); END;
CREATE TRIGGER association_matches_batch BEFORE INSERT ON job_candidates
WHEN NOT EXISTS(SELECT 1 FROM scan_batches b JOIN jobs j USING(org_id,job_id)
 WHERE b.org_id=NEW.org_id AND b.job_id=NEW.job_id AND b.batch_id=NEW.first_batch_id
 AND b.policy_id=NEW.policy_id AND b.policy_version=NEW.policy_version
 AND b.status='RUNNING' AND j.state='OPEN')
BEGIN SELECT RAISE(ABORT,'new candidate must bind its running first batch policy'); END;
CREATE TRIGGER acquisition_running_batch BEFORE INSERT ON candidate_acquisitions
WHEN NOT EXISTS(SELECT 1 FROM scan_batches b JOIN jobs j USING(org_id,job_id)
 WHERE b.org_id=NEW.org_id AND b.job_id=NEW.job_id AND b.batch_id=NEW.batch_id
 AND b.status='RUNNING' AND j.state='OPEN')
BEGIN SELECT RAISE(ABORT,'acquisition requires a running batch in the same job'); END;
CREATE TRIGGER seal_complete_acquired_resume BEFORE INSERT ON job_candidate_inputs
WHEN NOT EXISTS(SELECT 1 FROM resume_snapshots r WHERE r.org_id=NEW.org_id
 AND r.candidate_id=NEW.candidate_id AND r.resume_id=NEW.resume_id
 AND r.completeness='COMPLETE' AND r.is_scorable=1
 AND json_array_length(r.collection_errors_json)=0 AND json_array_length(r.data_issues_json)=0)
 OR NOT EXISTS(SELECT 1 FROM candidate_acquisitions a WHERE a.org_id=NEW.org_id
 AND a.job_id=NEW.job_id AND a.job_candidate_id=NEW.job_candidate_id
 AND a.candidate_id=NEW.candidate_id AND a.resume_id=NEW.resume_id)
BEGIN SELECT RAISE(ABORT,'input requires a complete scorable resume acquired for this candidate/job'); END;
CREATE TRIGGER task_initial_state BEFORE INSERT ON evaluation_tasks
WHEN NEW.technical_state<>'PENDING'
BEGIN SELECT RAISE(ABORT,'evaluation task must start pending'); END;
CREATE TRIGGER task_lifecycle BEFORE UPDATE OF technical_state ON evaluation_tasks
WHEN NOT (NEW.technical_state=OLD.technical_state
 OR (OLD.technical_state IN ('PENDING','FAILED') AND NEW.technical_state='RUNNING')
 OR (OLD.technical_state='RUNNING' AND NEW.technical_state IN ('SUCCEEDED','FAILED')))
 OR (NEW.technical_state='SUCCEEDED' AND NOT EXISTS(SELECT 1 FROM evaluation_attempts
   WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND status='SUCCEEDED'))
 OR (NEW.technical_state='FAILED' AND NOT EXISTS(SELECT 1 FROM evaluation_attempts
   WHERE org_id=NEW.org_id AND task_id=NEW.task_id AND status='FAILED'))
BEGIN SELECT RAISE(ABORT,'illegal evaluation task state or missing completed attempt'); END;
CREATE TRIGGER attempt_running_task BEFORE INSERT ON evaluation_attempts
WHEN NOT EXISTS(SELECT 1 FROM evaluation_tasks t WHERE t.org_id=NEW.org_id
 AND t.job_id=NEW.job_id AND t.task_id=NEW.task_id AND t.technical_state='RUNNING')
 OR NEW.attempt_no<>COALESCE((SELECT MAX(attempt_no)+1 FROM evaluation_attempts
   WHERE org_id=NEW.org_id AND task_id=NEW.task_id),1)
BEGIN SELECT RAISE(ABORT,'attempt requires a running task and sequential attempt number'); END;
CREATE TRIGGER evaluation_accepted_attempt BEFORE INSERT ON evaluations
WHEN NOT EXISTS(SELECT 1 FROM evaluation_attempts a JOIN evaluation_tasks t
 ON a.org_id=t.org_id AND a.job_id=t.job_id AND a.task_id=t.task_id
 WHERE a.org_id=NEW.org_id AND a.job_id=NEW.job_id AND a.task_id=NEW.task_id
 AND a.attempt_id=NEW.attempt_id AND a.status='SUCCEEDED' AND t.technical_state='SUCCEEDED'
 AND json(a.result_json)=json(NEW.result_json))
BEGIN SELECT RAISE(ABORT,'formal result requires the matching successful completed attempt'); END;
CREATE TRIGGER evaluation_payload_matches_columns BEFORE INSERT ON evaluations
WHEN json_extract(NEW.result_json,'$.candidate_key') IS NOT
 (SELECT candidate_key FROM job_candidates WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND job_candidate_id=NEW.job_candidate_id)
 OR json_extract(NEW.result_json,'$.scoring_mode') IS NOT NEW.scoring_mode
 OR json_extract(NEW.result_json,'$.scoring_status') IS NOT NEW.scoring_status
 OR json_extract(NEW.result_json,'$.total_score') IS NOT NEW.final_score
 OR json_extract(NEW.result_json,'$.debug_trace.tier1_capped_score') IS NOT NEW.tier1_score
 OR json_extract(NEW.result_json,'$.debug_trace.tier2_semantic_score') IS NOT NEW.tier2_score
 OR json_extract(NEW.result_json,'$.dimension_scores') IS NOT json(NEW.dimensions_json)
 OR json_extract(NEW.result_json,'$.evidence_details') IS NOT json(NEW.evidence_json)
BEGIN SELECT RAISE(ABORT,'formal result JSON does not match its relational columns'); END;
