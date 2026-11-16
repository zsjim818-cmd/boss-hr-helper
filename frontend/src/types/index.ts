// Sprint 3 typed contract for the generic policy pipeline API.
// Mirrors server/schemas.py + reports/phase1_7_sprint2/api_reference.md.
// NOTE: backend returns JSON columns already parsed (objects), not strings.

export type RuntimeModeName = 'deterministic' | 'live'
export type ScoringMode = 'fast_mode' | 'deep_mode'
export type PipelineStatus =
  | 'HARD_FILTERED'
  | 'DATA_INCOMPLETE'
  | 'NEEDS_REVIEW'
  | 'COARSE_ONLY'
  | 'DEEP_SCORED'
  | 'TECHNICAL_FAILED'
export type ReviewAction = 'APPROVE' | 'REJECT' | 'REQUEST_REVIEW' | 'NOTE'
export type ContactAction = 'CONTACTED' | 'DECLINED' | 'PENDING'

export interface RuntimeMode {
  mode: RuntimeModeName
  models_required: boolean
  auth_mode: 'trusted-headers' | 'gateway-required'
  local_only: boolean
  embedding_model_path?: string
  llm_model_name?: string
  notes?: string
}

export interface LocalIdentity {
  org_id: string
  user_id: string
  org_role: string
}

export interface Job {
  job_id: string
  org_id: string
  title: string
  state: string
  current_policy_id: string | null
  current_policy_version: number | null
  next_policy_version?: number
}

// ---------------------------------------------------------------- policy v1
export interface PolicyRequirement {
  requirement_id: string
  label: string
  source_type: 'JD' | 'HR' | 'INFERENCE'
  source_text: string
  priority: 'REQUIRED' | 'PREFERRED' | 'OPTIONAL'
  requirement_type: string
  needs_confirmation: boolean
}

export interface RequirementGroup {
  group_id: string
  label: string
  operator: 'ALL' | 'ANY'
  priority: 'REQUIRED' | 'PREFERRED' | 'OPTIONAL'
  members: string[]
  source_type: string
  source_text: string
  needs_confirmation: boolean
}

export interface RubricBand {
  min_score: number
  max_score: number
  description: string
}

export interface ScoringDimension {
  dimension_id: string
  label: string
  description: string
  requirement_ids: string[]
  weight: number
  rubric: RubricBand[]
}

export interface PolicyThresholds {
  coarse_pass: number
  deep_review: number
  deep_recommend: number
}

export interface JobScoringPolicy {
  schema_version: string
  job: { title: string; summary: string }
  sources: { jd_text: string; hr_requirements: string }
  requirements: PolicyRequirement[]
  requirement_groups: RequirementGroup[]
  keyword_groups: Array<{ group_id: string; label: string; requirement_ids: string[] }>
  semantic_anchors: Array<{ anchor_id: string; requirement_ids: string[]; polarity: string; text: string }>
  scoring_dimensions: ScoringDimension[]
  thresholds: PolicyThresholds
  diagnostics: Array<{ code: string; severity: string; message: string; requirement_ids: string[] }>
  provenance: Record<string, string>
}

// ------------------------------------------------------------ quality gate
export interface QualityIssue {
  code: string
  severity: 'ERROR' | 'WARNING'
  message: string
  requirement_id: string | null
  group_id: string | null
  anchor_ids: string[]
  dimension_ids: string[]
  suggested_action: string
}

export interface DimensionCoverage {
  dimension_id: string
  requirement_ids: string[]
  invalid_requirement_ids: string[]
  semantic_requirement_count: number
  direct_covered: number
  broad_only: number
  missing_positive: number
  direct_coverage_rate: number | null
}

export interface QualityReport {
  validator_version: string
  policy_schema_version: string
  policy_id: string | null
  policy_version: number | null
  policy_hash: string | null
  job_title: string
  status: 'PASS' | 'WARNING' | 'BLOCKED'
  coverage_status: 'DETERMINED' | 'INDETERMINATE'
  semantic_requirement_count: number
  directly_covered_count: number
  broad_only_count: number
  missing_positive_count: number
  unclassified_requirement_count: number
  orphan_group_count: number
  positive_coverage_rate: number | null
  dimension_coverage: DimensionCoverage[]
  issues: QualityIssue[]
}

export interface PolicyDiagnostic {
  code: string
  severity: 'ERROR' | 'CONFIRM' | 'WARNING'
  message: string
  requirement_ids: string[]
}

export interface PolicyDraft {
  draft_id: string
  job_id: string
  revision: number
  draft_hash: string
  policy_json: JobScoringPolicy
  diagnostics_json: PolicyDiagnostic[]
  source_json?: Record<string, unknown>
}

export interface CompilationRun {
  run_id: string
  status: string
  draft_id?: string
  draft_hash?: string
  error_type?: string | null
  created_at?: string
}

export interface CompileResult {
  draft: PolicyDraft
  compilation_run: CompilationRun
  diagnostics: PolicyDiagnostic[]
  publishable: boolean
  quality?: QualityReport
  metrics?: Record<string, unknown>
}

// ------------------------------------------------------------- candidates
export interface JobCandidate {
  job_candidate_id: string
  candidate_id: string
  candidate_key: string
  policy_id: string
  policy_version: number
  first_batch_id: string
  first_seen_at?: string
}

export interface ResumeSnapshot {
  resume_id: string
  completeness: string
  content_hash: string
  is_scorable?: boolean
}

export interface ImportResult {
  job_candidate: JobCandidate
  acquisition: Record<string, unknown>
  resume_snapshot: ResumeSnapshot | null
  sealed_input: Record<string, unknown> | null
  is_new: boolean
}

export interface Batch {
  batch_id: string
  job_id: string
  policy_id: string
  policy_version: number
  status: string
  created_at?: string
  started_at?: string
  completed_at?: string | null
}

// -------------------------------------------------------------- pipeline
export interface Tier2Dimension {
  dimension_id: string
  scoring_status: string
  semantic_score: number | null
}

export interface Tier2Payload {
  scoring_status: string
  semantic_score: number | null
  semantic_coverage: number
  dimension_scores: Tier2Dimension[]
  review_reasons: string[]
  debug_trace?: string[]
}

export interface Tier3Evidence {
  source_path: string
  quote: string
}

export interface Tier3Dimension {
  dimension_id: string
  label: string
  weight: number
  score: number
  confidence: number
  evidence: Tier3Evidence[]
  missing_evidence: string[]
}

export interface Tier3Payload {
  total_score: number
  routing_suggestion: 'DEEP_RECOMMEND' | 'HUMAN_REVIEW' | 'NOT_RECOMMENDED'
  thresholds: PolicyThresholds
  dimensions: Tier3Dimension[]
  review_reasons: string[]
  missing_evidence: string[]
  llm_model?: string | null
  policy_id: string
  policy_version: number
  policy_hash: string
}

export interface PreliminaryPayload {
  hard_filter_status: 'PASS' | 'FAIL' | 'UNKNOWN'
  scoring_status: string
  preliminary_score: number | null
  coarse_passed: boolean
  thresholds: PolicyThresholds
  score_kind?: string
  review_reasons: string[]
  dimension_scores: Array<{
    dimension_id: string
    dimension_label: string
    dimension_weight: number
    normalized_score: number
    matched_requirements: string[]
    missing_evidence: string[]
  }>
}

export interface PipelineEvaluation {
  pipeline_evaluation_id: string
  job_id: string
  job_candidate_id: string
  candidate_id: string
  candidate_key: string
  policy_id: string
  policy_version: number
  policy_hash: string
  resume_id: string
  resume_content_hash: string
  pipeline_version: string
  scoring_mode: ScoringMode
  scoring_status: Exclude<PipelineStatus, 'TECHNICAL_FAILED'>
  total_score: number | null
  routing_suggestion: 'DEEP_RECOMMEND' | 'HUMAN_REVIEW' | 'NOT_RECOMMENDED' | null
  tier0_json: PreliminaryPayload | Record<string, never>
  tier1_json: { dimensions?: unknown[] }
  tier2_json: Tier2Payload | null
  tier3_json: Tier3Payload | null
  review_reasons_json: string[]
  created_by?: string
  created_at?: string
}

export interface PipelineTask {
  pipeline_task_id: string
  job_candidate_id: string
  scoring_mode: ScoringMode
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  pipeline_evaluation_id: string | null
  error_type: string | null
  error_message: string | null
  created_at?: string
  updated_at?: string
}

export interface ScoreStartResult {
  reused?: boolean
  in_progress?: boolean
  task: PipelineTask | null
  evaluation?: PipelineEvaluation | null
}

export interface TaskResult {
  task: PipelineTask
  evaluation: PipelineEvaluation | null
}

export interface CandidateListItem {
  job_candidate: JobCandidate
  latest_evaluation: PipelineEvaluation | null
  resume_summary?: {
    name?: string
    degree?: string
    work_years?: string
    salary?: string
    completeness?: string
    resume_id?: string
  }
  sealed?: boolean
  contact_summary?: ContactSummary
}

export interface CandidateList {
  total: number
  items: CandidateListItem[]
}

export interface ResumeSnapshotResponse {
  job_candidate: JobCandidate
  sealed: boolean
  resume_id: string
  completeness: string
  resume: Record<string, any>
  data_issues: Array<Record<string, any>>
  collection_errors: string[]
  captured_at: string
}

export interface CandidateDetail {
  job_candidate: JobCandidate
  latest_evaluation: PipelineEvaluation | null
  evaluations: PipelineEvaluation[]
  attempts: Array<Record<string, unknown>>
}

export interface RankingEntry {
  pipeline_evaluation_id: string
  job_candidate_id: string
  candidate_key: string
  total_score: number | null
  scoring_status: string
  routing_suggestion: string | null
  policy_id: string
  policy_version: number
  scoring_mode: ScoringMode
  created_at: string
}

export interface ReviewEvent {
  review_event_id: string
  pipeline_evaluation_id: string
  action: ReviewAction
  reason: string
  actor_id: string
  created_at: string
}

export interface ContactEvent {
  contact_event_id: string
  pipeline_evaluation_id: string
  action: ContactAction
  note: string
  actor_id: string
  created_at: string
}

export interface SemanticPrepareResult {
  mode: RuntimeModeName
  status: string
  policy?: Record<string, unknown> | null
  candidate?: Record<string, unknown> | null
}

export interface BrowserScanIssue {
  code: string
  message: string
  recoverable: boolean
  identity?: string | null
}

export interface BrowserScanCandidate {
  identity: string
  candidate_key: string
  job_candidate_id: string
  policy_id: string
  policy_version: number
}

export interface BrowserScanResult {
  job_id: string
  batch_id: string
  collected: number
  listed?: number
  deduplicated?: number
  preview_skipped?: number
  detail_attempted?: number
  detail_completed?: number
  detail_partial?: number
  sealed?: number
  scored?: number
  review_pending?: number
  stop_reason?: string
  imported: number
  reused: number
  failed: number
  issues: BrowserScanIssue[]
  imported_candidates: BrowserScanCandidate[]
  source?: string | null
  browser?: string | null
  page_url?: string | null
  browser_account_id?: string | null
}

export interface BrowserScanStatus {
  status: 'COMPLETED' | 'COMPLETED_WITH_ERRORS' | 'FAILED' | 'CANCELLED' | string
  batch_id?: string | null
  source?: string | null
  result: BrowserScanResult | null
  error?: { code: string; message: string; recoverable?: boolean } | null
  progress?: ScanProgress | null
}

export interface ScanProgress {
  status: string
  stage?: string
  listed?: number
  collected?: number
  deduplicated?: number
  preview_skipped?: number
  detail_attempted?: number
  detail_completed?: number
  detail_partial?: number
  imported?: number
  reused?: number
  failed?: number
  pages_read?: number
  stop_reason?: string
  error_code?: string
  updated_at?: number
}

export type BrowserSessionState =
  'DISCONNECTED' | 'LOGIN_REQUIRED' | 'CAPTCHA_BLOCKED' | 'READY'
  | 'REAL_CONTRACT_UNVERIFIED' | 'DISABLED'

export interface BrowserSession {
  state: BrowserSessionState
  source: string
  source_label: string
  real_contract_verified: boolean
  real_pii_storage_ready: boolean
  profile_id?: string | null
  connected: boolean
  browser?: string
  page_title: string
  page_url: string
  iframe_detected: boolean
  platform_job_id: string
  page_job_id: string
  page_job_title: string
  filter_summary: string
  visible_cards: number
  checked_at?: string | null
  error_code?: string | null
  error_message?: string | null
  actions: string[]
}

export interface BrowserPrecheck {
  job_id: string
  job_title: string
  page_job_id: string
  page_job_title: string
  platform_job_id: string
  filter_summary: string
  visible_cards: number
  iframe_detected: boolean
  job_mismatch: boolean
  warnings: string[]
  checked_at: string
  source: string
}

export type GreetingDryRunState =
  'DRY_RUN_READY' | 'SENT' | 'NOT_APPROVED' | 'ALREADY_SENT' | 'NOT_FOUND'
  | 'IDENTITY_MISMATCH' | 'PAGE_UNCERTAIN' | 'UNSUPPORTED'

export interface GreetingDryRunResult {
  state: GreetingDryRunState
  identity: string
  message: string
  dry_run: boolean
  recorded: boolean
  idempotent?: boolean
  contact_event?: ContactEvent | null
}

export interface ContactSummaryEvent extends ContactEvent {
  scoring_mode: ScoringMode
}

export interface ContactSummary {
  job_candidate_id: string
  contacted: boolean
  contacted_by: string[]
  current_user_contacted: boolean
  latest_contact: ContactSummaryEvent | null
  latest_pending_dry_run: ContactSummaryEvent | null
  current_user_pending_dry_run: ContactSummaryEvent | null
  pending_dry_run_count: number
  events: ContactSummaryEvent[]
}

export interface ApiErrorPayload {
  error_type?: string
  message?: string
  detail?: string
  state?: string
  quality_issues?: QualityIssue[]
  errors?: Array<{ path: string; type: string }>
}
