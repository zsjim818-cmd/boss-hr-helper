// Sprint 3 API client: typed calls against the generic pipeline service.
// There is NO mock/fallback data here on purpose: any backend failure surfaces
// as a thrown ApiError so the UI can show a real error banner.
import axios, { AxiosError } from 'axios'
import type {
  ApiErrorPayload, Batch, CandidateDetail, CandidateList, CompileResult,
  ContactAction, ContactEvent, ImportResult, Job, JobScoringPolicy, PipelineEvaluation,
  PolicyDraft, QualityReport, RankingEntry, ReviewAction, ReviewEvent,
  RuntimeMode, ScoreStartResult, ScoringMode, SemanticPrepareResult, TaskResult,
  LocalIdentity, PolicyDiagnostic
} from '@/types'

const http = axios.create({ baseURL: '/api', timeout: 180000 })

const ORG_KEY = 'hr_console_org'
const USER_KEY = 'hr_console_user'

export class ApiError extends Error {
  status: number
  errorType: string
  qualityIssues: ApiErrorPayload['quality_issues']
  constructor(message: string, status: number, errorType: string,
              qualityIssues?: ApiErrorPayload['quality_issues']) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.errorType = errorType
    this.qualityIssues = qualityIssues
  }
}

export function setIdentity(orgId: string, userId: string) {
  localStorage.setItem(ORG_KEY, orgId)
  localStorage.setItem(USER_KEY, userId)
}

export function getIdentity(): { orgId: string; userId: string } {
  return {
    orgId: localStorage.getItem(ORG_KEY) || '',
    userId: localStorage.getItem(USER_KEY) || ''
  }
}

function decorate(error: unknown): never {
  const axiosError = error as AxiosError<ApiErrorPayload>
  if (axiosError.response) {
    const payload = axiosError.response.data || {}
    const message = payload.message || payload.detail || axiosError.message
    throw new ApiError(message, axiosError.response.status,
      payload.error_type || 'HTTP_' + axiosError.response.status, payload.quality_issues)
  }
  throw new ApiError('无法连接后端服务（' + axiosError.message + '）', 0, 'NETWORK_ERROR')
}

http.interceptors.request.use((config) => {
  const { orgId, userId } = getIdentity()
  if (orgId) config.headers['X-Org-Id'] = orgId
  if (userId) config.headers['X-User-Id'] = userId
  return config
})

async function call<T>(promise: Promise<{ data: T }>): Promise<T> {
  try {
    return (await promise).data
  } catch (error) {
    decorate(error)
  }
}

export const api = {
  // health / runtime
  runtimeMode: () => call<RuntimeMode>(http.get('/runtime-mode')),
  localIdentities: () => call<{ identities: LocalIdentity[] }>(http.get('/local-identities')),

  // jobs
  listJobs: () => call<{ jobs: Job[] }>(http.get('/jobs')),
  createJob: (title: string) => call<Job>(http.post('/jobs', { title })),
  getJob: (jobId: string) => call<Job>(http.get(`/jobs/${jobId}`)),

  // policies
  compilePolicy: (jobId: string, payload: { job_title: string; jd_text: string; hr_requirements: string }) =>
    call<CompileResult>(http.post(`/jobs/${jobId}/policies/compile`, payload)),
  getDraft: (jobId: string, draftId: string) =>
    call<PolicyDraft>(http.get(`/jobs/${jobId}/policies/drafts/${draftId}`)),
  updateDraft: (jobId: string, draftId: string,
                payload: { policy: JobScoringPolicy; expected_revision: number; parent_run_id: string }) =>
    call<CompileResult>(http.patch(`/jobs/${jobId}/policies/drafts/${draftId}`, payload)),
  draftQuality: (jobId: string, draftId: string) =>
    call<QualityReport>(http.get(`/jobs/${jobId}/policies/drafts/${draftId}/quality`)),
  confirmDraft: (jobId: string, draftId: string, payload: { run_id: string; confirmation_hash: string }) =>
    call<Record<string, unknown>>(http.post(`/jobs/${jobId}/policies/drafts/${draftId}/confirm`, payload)),
  createVersion: (jobId: string, draftId: string,
                  payload: { run_id: string; confirmation_id: string; execution_profile?: Record<string, unknown> }) =>
    call<{ version: Record<string, unknown> }>(http.post(`/jobs/${jobId}/policies/drafts/${draftId}/versions`, payload)),
  publishPolicy: (jobId: string, policyId: string,
                  payload: { confirmation_hash: string; quality_override_reason?: string }) =>
    call<Record<string, unknown>>(http.post(`/jobs/${jobId}/policies/${policyId}/publish`, payload)),
  currentPolicy: (jobId: string) => call<Record<string, unknown>>(http.get(`/jobs/${jobId}/policies/current`)),
  policyHistory: (jobId: string, version: number) =>
    call<Record<string, unknown>>(http.get(`/jobs/${jobId}/policies/history`, { params: { version } })),

  // batches / candidates
  createBatch: (jobId: string) => call<Batch>(http.post(`/jobs/${jobId}/batches`)),
  getBatch: (jobId: string, batchId: string) => call<Batch>(http.get(`/jobs/${jobId}/batches/${batchId}`)),
  importCandidate: (jobId: string, batchId: string,
                    payload: { candidate_key: string; resume: Record<string, unknown>; source?: string }) =>
    call<ImportResult>(http.post(`/jobs/${jobId}/batches/${batchId}/candidates`, payload)),
  listCandidates: (jobId: string, params: { limit?: number; offset?: number; status?: string; min_score?: number }) =>
    call<CandidateList>(http.get(`/jobs/${jobId}/candidates`, { params })),
  candidateDetail: (jobId: string, jobCandidateId: string) =>
    call<CandidateDetail>(http.get(`/jobs/${jobId}/candidates/${jobCandidateId}`)),
  candidateEvaluations: (jobId: string, jobCandidateId: string) =>
    call<{ latest: PipelineEvaluation | null; history: PipelineEvaluation[] }>(
      http.get(`/jobs/${jobId}/candidates/${jobCandidateId}/evaluations`)),

  // semantic + scoring
  semanticPrepare: (jobId: string, jobCandidateId?: string) =>
    call<SemanticPrepareResult>(http.post(`/jobs/${jobId}/semantic-prepare`,
      jobCandidateId ? { job_candidate_id: jobCandidateId } : {})),
  startScoring: (jobId: string, jobCandidateId: string,
                 payload: { scoring_mode: ScoringMode; idempotency_key?: string }) =>
    call<ScoreStartResult>(http.post(`/jobs/${jobId}/candidates/${jobCandidateId}/score`, payload)),
  task: (jobId: string, taskId: string) => call<TaskResult>(http.get(`/jobs/${jobId}/tasks/${taskId}`)),
  ranking: (jobId: string, limit = 100) =>
    call<{ ranking: RankingEntry[] }>(http.get(`/jobs/${jobId}/ranking`, { params: { limit } })),

  // reviews / contact
  appendReview: (jobId: string, evaluationId: string, payload: { action: ReviewAction; reason: string }) =>
    call<ReviewEvent>(http.post(`/jobs/${jobId}/evaluations/${evaluationId}/reviews`, payload)),
  listReviews: (jobId: string, evaluationId: string) =>
    call<{ reviews: ReviewEvent[] }>(http.get(`/jobs/${jobId}/evaluations/${evaluationId}/reviews`)),
  appendContact: (jobId: string, evaluationId: string, payload: { action: ContactAction; note: string }) =>
    call<ContactEvent>(http.post(`/jobs/${jobId}/evaluations/${evaluationId}/contact`, payload)),
  listContacts: (jobId: string, evaluationId: string) =>
    call<{ events: ContactEvent[] }>(http.get(`/jobs/${jobId}/evaluations/${evaluationId}/contact`))
}

export type { PolicyDiagnostic }
