-- Explicit INSERT guards also reject REPLACE before SQLite deletes an old row.

CREATE TRIGGER no_replace_organizations BEFORE INSERT ON organizations
WHEN EXISTS(SELECT 1 FROM organizations WHERE org_id=NEW.org_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing organizations'); END;

CREATE TRIGGER no_rekey_organizations BEFORE UPDATE ON organizations
WHEN NEW.org_id IS NOT OLD.org_id OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'immutable identity in organizations'); END;

CREATE TRIGGER no_delete_organizations BEFORE DELETE ON organizations
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for organizations'); END;

CREATE TRIGGER no_replace_users BEFORE INSERT ON users
WHEN EXISTS(SELECT 1 FROM users WHERE user_id=NEW.user_id OR auth_subject=NEW.auth_subject)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing users'); END;

CREATE TRIGGER no_rekey_users BEFORE UPDATE ON users
WHEN NEW.user_id IS NOT OLD.user_id OR NEW.auth_subject IS NOT OLD.auth_subject OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'immutable identity in users'); END;

CREATE TRIGGER no_delete_users BEFORE DELETE ON users
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for users'); END;

CREATE TRIGGER no_replace_organization_memberships BEFORE INSERT ON organization_memberships
WHEN EXISTS(SELECT 1 FROM organization_memberships WHERE org_id=NEW.org_id AND user_id=NEW.user_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing organization_memberships'); END;

CREATE TRIGGER no_rekey_organization_memberships BEFORE UPDATE ON organization_memberships
WHEN NEW.org_id IS NOT OLD.org_id OR NEW.user_id IS NOT OLD.user_id OR NEW.joined_at IS NOT OLD.joined_at
BEGIN SELECT RAISE(ABORT,'immutable identity in organization_memberships'); END;

CREATE TRIGGER no_delete_organization_memberships BEFORE DELETE ON organization_memberships
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for organization_memberships'); END;

CREATE TRIGGER no_replace_jobs BEFORE INSERT ON jobs
WHEN EXISTS(SELECT 1 FROM jobs WHERE org_id=NEW.org_id AND job_id=NEW.job_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing jobs'); END;

CREATE TRIGGER no_rekey_jobs BEFORE UPDATE ON jobs
WHEN NEW.org_id IS NOT OLD.org_id OR NEW.job_id IS NOT OLD.job_id OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'immutable identity in jobs'); END;

CREATE TRIGGER no_delete_jobs BEFORE DELETE ON jobs
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for jobs'); END;

CREATE TRIGGER no_replace_job_access_grants BEFORE INSERT ON job_access_grants
WHEN EXISTS(SELECT 1 FROM job_access_grants WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND user_id=NEW.user_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing job_access_grants'); END;

CREATE TRIGGER no_rekey_job_access_grants BEFORE UPDATE ON job_access_grants
WHEN NEW.org_id IS NOT OLD.org_id OR NEW.job_id IS NOT OLD.job_id OR NEW.user_id IS NOT OLD.user_id
BEGIN SELECT RAISE(ABORT,'immutable identity in job_access_grants'); END;

CREATE TRIGGER no_delete_job_access_grants BEFORE DELETE ON job_access_grants
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for job_access_grants'); END;

CREATE TRIGGER no_replace_execution_profiles BEFORE INSERT ON execution_profiles
WHEN EXISTS(SELECT 1 FROM execution_profiles WHERE org_id=NEW.org_id AND profile_id=NEW.profile_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing execution_profiles'); END;

CREATE TRIGGER no_update_execution_profiles BEFORE UPDATE ON execution_profiles
BEGIN SELECT RAISE(ABORT,'immutable execution_profiles'); END;

CREATE TRIGGER no_delete_execution_profiles BEFORE DELETE ON execution_profiles
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for execution_profiles'); END;

CREATE TRIGGER no_replace_policy_drafts BEFORE INSERT ON policy_drafts
WHEN EXISTS(SELECT 1 FROM policy_drafts WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND draft_id=NEW.draft_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_drafts'); END;

CREATE TRIGGER no_rekey_policy_drafts BEFORE UPDATE ON policy_drafts
WHEN NEW.org_id IS NOT OLD.org_id OR NEW.job_id IS NOT OLD.job_id OR NEW.draft_id IS NOT OLD.draft_id OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'immutable identity in policy_drafts'); END;

CREATE TRIGGER no_delete_policy_drafts BEFORE DELETE ON policy_drafts
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_drafts'); END;

CREATE TRIGGER no_replace_job_policy_versions BEFORE INSERT ON job_policy_versions
WHEN EXISTS(SELECT 1 FROM job_policy_versions WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND (policy_id=NEW.policy_id OR policy_version=NEW.policy_version))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing job_policy_versions'); END;

CREATE TRIGGER no_update_job_policy_versions BEFORE UPDATE ON job_policy_versions
BEGIN SELECT RAISE(ABORT,'immutable job_policy_versions'); END;

CREATE TRIGGER no_delete_job_policy_versions BEFORE DELETE ON job_policy_versions
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for job_policy_versions'); END;

CREATE TRIGGER no_replace_policy_publications BEFORE INSERT ON policy_publications
WHEN EXISTS(SELECT 1 FROM policy_publications WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND (policy_id=NEW.policy_id OR policy_version=NEW.policy_version))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_publications'); END;

CREATE TRIGGER no_update_policy_publications BEFORE UPDATE ON policy_publications
BEGIN SELECT RAISE(ABORT,'immutable policy_publications'); END;

CREATE TRIGGER no_delete_policy_publications BEFORE DELETE ON policy_publications
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_publications'); END;

CREATE TRIGGER no_replace_policy_activation_events BEFORE INSERT ON policy_activation_events
WHEN EXISTS(SELECT 1 FROM policy_activation_events WHERE org_id=NEW.org_id AND (event_id=NEW.event_id OR (job_id=NEW.job_id AND to_policy_id=NEW.to_policy_id)))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_activation_events'); END;

CREATE TRIGGER no_update_policy_activation_events BEFORE UPDATE ON policy_activation_events
BEGIN SELECT RAISE(ABORT,'immutable policy_activation_events'); END;

CREATE TRIGGER no_delete_policy_activation_events BEFORE DELETE ON policy_activation_events
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_activation_events'); END;

CREATE TRIGGER no_replace_audit_events BEFORE INSERT ON audit_events
WHEN EXISTS(SELECT 1 FROM audit_events WHERE org_id=NEW.org_id AND event_id=NEW.event_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing audit_events'); END;

CREATE TRIGGER no_update_audit_events BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT,'immutable audit_events'); END;

CREATE TRIGGER no_delete_audit_events BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for audit_events'); END;

CREATE TRIGGER no_replace_candidates BEFORE INSERT ON candidates
WHEN EXISTS(SELECT 1 FROM candidates WHERE org_id=NEW.org_id AND candidate_id=NEW.candidate_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing candidates'); END;

CREATE TRIGGER no_update_candidates BEFORE UPDATE ON candidates
BEGIN SELECT RAISE(ABORT,'immutable candidates'); END;

CREATE TRIGGER no_delete_candidates BEFORE DELETE ON candidates
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for candidates'); END;

CREATE TRIGGER no_replace_candidate_identity_aliases BEFORE INSERT ON candidate_identity_aliases
WHEN EXISTS(SELECT 1 FROM candidate_identity_aliases WHERE org_id=NEW.org_id AND source=NEW.source AND external_identity_hash=NEW.external_identity_hash)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing candidate_identity_aliases'); END;

CREATE TRIGGER no_update_candidate_identity_aliases BEFORE UPDATE ON candidate_identity_aliases
BEGIN SELECT RAISE(ABORT,'immutable candidate_identity_aliases'); END;

CREATE TRIGGER no_delete_candidate_identity_aliases BEFORE DELETE ON candidate_identity_aliases
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for candidate_identity_aliases'); END;

CREATE TRIGGER no_replace_resume_snapshots BEFORE INSERT ON resume_snapshots
WHEN EXISTS(SELECT 1 FROM resume_snapshots WHERE org_id=NEW.org_id AND candidate_id=NEW.candidate_id AND (resume_id=NEW.resume_id OR raw_hash=NEW.raw_hash))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing resume_snapshots'); END;

CREATE TRIGGER no_update_resume_snapshots BEFORE UPDATE ON resume_snapshots
BEGIN SELECT RAISE(ABORT,'immutable resume_snapshots'); END;

CREATE TRIGGER no_delete_resume_snapshots BEFORE DELETE ON resume_snapshots
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for resume_snapshots'); END;

CREATE TRIGGER no_replace_scan_batches BEFORE INSERT ON scan_batches
WHEN EXISTS(SELECT 1 FROM scan_batches WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND batch_id=NEW.batch_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing scan_batches'); END;

CREATE TRIGGER no_rekey_scan_batches BEFORE UPDATE ON scan_batches
WHEN NEW.org_id IS NOT OLD.org_id OR NEW.job_id IS NOT OLD.job_id OR NEW.batch_id IS NOT OLD.batch_id OR NEW.policy_id IS NOT OLD.policy_id OR NEW.policy_version IS NOT OLD.policy_version OR NEW.created_by IS NOT OLD.created_by OR NEW.started_at IS NOT OLD.started_at
BEGIN SELECT RAISE(ABORT,'immutable identity in scan_batches'); END;

CREATE TRIGGER no_delete_scan_batches BEFORE DELETE ON scan_batches
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for scan_batches'); END;

CREATE TRIGGER no_replace_job_candidates BEFORE INSERT ON job_candidates
WHEN EXISTS(SELECT 1 FROM job_candidates WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND (job_candidate_id=NEW.job_candidate_id OR candidate_id=NEW.candidate_id OR candidate_key=NEW.candidate_key))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing job_candidates'); END;

CREATE TRIGGER no_update_job_candidates BEFORE UPDATE ON job_candidates
BEGIN SELECT RAISE(ABORT,'immutable job_candidates'); END;

CREATE TRIGGER no_delete_job_candidates BEFORE DELETE ON job_candidates
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for job_candidates'); END;

CREATE TRIGGER no_replace_candidate_acquisitions BEFORE INSERT ON candidate_acquisitions
WHEN EXISTS(SELECT 1 FROM candidate_acquisitions WHERE org_id=NEW.org_id AND acquisition_id=NEW.acquisition_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing candidate_acquisitions'); END;

CREATE TRIGGER no_update_candidate_acquisitions BEFORE UPDATE ON candidate_acquisitions
BEGIN SELECT RAISE(ABORT,'immutable candidate_acquisitions'); END;

CREATE TRIGGER no_delete_candidate_acquisitions BEFORE DELETE ON candidate_acquisitions
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for candidate_acquisitions'); END;

CREATE TRIGGER no_replace_job_candidate_inputs BEFORE INSERT ON job_candidate_inputs
WHEN EXISTS(SELECT 1 FROM job_candidate_inputs WHERE org_id=NEW.org_id AND job_id=NEW.job_id AND job_candidate_id=NEW.job_candidate_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing job_candidate_inputs'); END;

CREATE TRIGGER no_update_job_candidate_inputs BEFORE UPDATE ON job_candidate_inputs
BEGIN SELECT RAISE(ABORT,'immutable job_candidate_inputs'); END;

CREATE TRIGGER no_delete_job_candidate_inputs BEFORE DELETE ON job_candidate_inputs
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for job_candidate_inputs'); END;

CREATE TRIGGER no_replace_evaluation_tasks BEFORE INSERT ON evaluation_tasks
WHEN EXISTS(SELECT 1 FROM evaluation_tasks WHERE org_id=NEW.org_id AND (task_id=NEW.task_id OR idempotency_key=NEW.idempotency_key OR (job_id=NEW.job_id AND job_candidate_id=NEW.job_candidate_id AND scoring_mode=NEW.scoring_mode)))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing evaluation_tasks'); END;

CREATE TRIGGER no_rekey_evaluation_tasks BEFORE UPDATE ON evaluation_tasks
WHEN NEW.org_id IS NOT OLD.org_id OR NEW.job_id IS NOT OLD.job_id OR NEW.task_id IS NOT OLD.task_id OR NEW.job_candidate_id IS NOT OLD.job_candidate_id OR NEW.candidate_id IS NOT OLD.candidate_id OR NEW.policy_id IS NOT OLD.policy_id OR NEW.policy_version IS NOT OLD.policy_version OR NEW.resume_id IS NOT OLD.resume_id OR NEW.execution_profile_id IS NOT OLD.execution_profile_id OR NEW.scoring_mode IS NOT OLD.scoring_mode OR NEW.idempotency_key IS NOT OLD.idempotency_key OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'immutable identity in evaluation_tasks'); END;

CREATE TRIGGER no_delete_evaluation_tasks BEFORE DELETE ON evaluation_tasks
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for evaluation_tasks'); END;

CREATE TRIGGER no_replace_evaluation_attempts BEFORE INSERT ON evaluation_attempts
WHEN EXISTS(SELECT 1 FROM evaluation_attempts WHERE org_id=NEW.org_id AND (attempt_id=NEW.attempt_id OR (task_id=NEW.task_id AND attempt_no=NEW.attempt_no)))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing evaluation_attempts'); END;

CREATE TRIGGER no_update_evaluation_attempts BEFORE UPDATE ON evaluation_attempts
BEGIN SELECT RAISE(ABORT,'immutable evaluation_attempts'); END;

CREATE TRIGGER no_delete_evaluation_attempts BEFORE DELETE ON evaluation_attempts
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for evaluation_attempts'); END;

CREATE TRIGGER no_replace_evaluations BEFORE INSERT ON evaluations
WHEN EXISTS(SELECT 1 FROM evaluations WHERE org_id=NEW.org_id AND (evaluation_id=NEW.evaluation_id OR task_id=NEW.task_id OR (job_id=NEW.job_id AND job_candidate_id=NEW.job_candidate_id AND scoring_mode=NEW.scoring_mode)))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing evaluations'); END;

CREATE TRIGGER no_update_evaluations BEFORE UPDATE ON evaluations
BEGIN SELECT RAISE(ABORT,'immutable evaluations'); END;

CREATE TRIGGER no_delete_evaluations BEFORE DELETE ON evaluations
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for evaluations'); END;

CREATE TRIGGER no_replace_hr_review_events BEFORE INSERT ON hr_review_events
WHEN EXISTS(SELECT 1 FROM hr_review_events WHERE org_id=NEW.org_id AND review_event_id=NEW.review_event_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing hr_review_events'); END;

CREATE TRIGGER no_update_hr_review_events BEFORE UPDATE ON hr_review_events
BEGIN SELECT RAISE(ABORT,'immutable hr_review_events'); END;

CREATE TRIGGER no_delete_hr_review_events BEFORE DELETE ON hr_review_events
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for hr_review_events'); END;

CREATE TRIGGER no_replace_retention_policy_versions BEFORE INSERT ON retention_policy_versions
WHEN EXISTS(SELECT 1 FROM retention_policy_versions WHERE org_id=NEW.org_id AND retention_version_id=NEW.retention_version_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing retention_policy_versions'); END;

CREATE TRIGGER no_update_retention_policy_versions BEFORE UPDATE ON retention_policy_versions
BEGIN SELECT RAISE(ABORT,'immutable retention_policy_versions'); END;

CREATE TRIGGER no_delete_retention_policy_versions BEFORE DELETE ON retention_policy_versions
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for retention_policy_versions'); END;

CREATE TRIGGER no_replace_data_operations BEFORE INSERT ON data_operations
WHEN EXISTS(SELECT 1 FROM data_operations WHERE operation_id=NEW.operation_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing data_operations'); END;

CREATE TRIGGER no_rekey_data_operations BEFORE UPDATE ON data_operations
WHEN NEW.operation_id IS NOT OLD.operation_id OR NEW.org_id IS NOT OLD.org_id OR NEW.scope IS NOT OLD.scope OR NEW.kind IS NOT OLD.kind OR NEW.actor_id IS NOT OLD.actor_id OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'immutable identity in data_operations'); END;

CREATE TRIGGER no_delete_data_operations BEFORE DELETE ON data_operations
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for data_operations'); END;

CREATE TRIGGER no_replace_schema_migrations BEFORE INSERT ON schema_migrations
WHEN EXISTS(SELECT 1 FROM schema_migrations WHERE version=NEW.version OR name=NEW.name)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing schema_migrations'); END;

CREATE TRIGGER no_update_schema_migrations BEFORE UPDATE ON schema_migrations
BEGIN SELECT RAISE(ABORT,'immutable schema_migrations'); END;

CREATE TRIGGER no_delete_schema_migrations BEFORE DELETE ON schema_migrations
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for schema_migrations'); END;
