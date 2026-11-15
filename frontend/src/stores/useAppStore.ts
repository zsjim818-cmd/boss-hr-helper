// Sprint 3.1 application state: runtime mode, mock identity, jobs, error banner.
// Every job/identity switch invalidates in-flight requests so a late response
// from the previous scope can never overwrite the new scope's UI.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, ApiError, getIdentity, setIdentity } from '@/api'
import { RequestGate, loadIfCurrent } from '@/lib/requestGate'
import type { Job, LocalIdentity, RuntimeMode } from '@/types'

const ACTIVE_JOB_KEY = 'hr_console_active_job'

export const useAppStore = defineStore('appStore', () => {
  const runtime = ref<RuntimeMode | null>(null)
  const identities = ref<LocalIdentity[]>([])
  const orgId = ref(getIdentity().orgId)
  const userId = ref(getIdentity().userId)
  const jobs = ref<Job[]>([])
  const activeJobId = ref(localStorage.getItem(ACTIVE_JOB_KEY) || '')
  const currentPolicy = ref<Record<string, unknown> | null>(null)
  const backendError = ref<string | null>(null)
  const backendConnected = ref(false)

  // Scope gates: one for job-scoped data, one for identity-scoped data.
  const jobGate = new RequestGate()
  const identityGate = new RequestGate()
  // Monotonic epoch exposed to views; increments on job or identity switch.
  const dataEpoch = ref(0)

  const activeJob = computed<Job | null>(
    () => jobs.value.find((job) => job.job_id === activeJobId.value) || null)

  function _bumpEpoch() {
    dataEpoch.value += 1
    jobGate.invalidate()
    identityGate.invalidate()
  }

  function applyIdentity(org: string, user: string) {
    const changed = org !== orgId.value || user !== userId.value
    orgId.value = org
    userId.value = user
    setIdentity(org, user)
    if (!changed) return
    // Identity switch: clear every identity-scoped value before reloading.
    _bumpEpoch()
    jobs.value = []
    currentPolicy.value = null
    activeJobId.value = ''
    localStorage.removeItem(ACTIVE_JOB_KEY)
    void refreshJobs()
  }

  async function bootstrap() {
    backendError.value = null
    try {
      runtime.value = await api.runtimeMode()
      backendConnected.value = true
      if (runtime.value.auth_mode === 'trusted-headers') {
        try {
          identities.value = (await api.localIdentities()).identities
          if ((!orgId.value || !userId.value) && identities.value.length > 0) {
            const preferred = identities.value.find((item) => item.org_role === 'ORG_ADMIN')
            applyIdentity((preferred || identities.value[0]).org_id,
                          (preferred || identities.value[0]).user_id)
            return
          }
        } catch { /* demo identities are optional */ }
      }
      await refreshJobs()
    } catch (error) {
      backendConnected.value = false
      backendError.value = error instanceof ApiError ? error.message : String(error)
    }
  }

  async function refreshJobs() {
    const gate = identityGate
    const applied = await loadIfCurrent(gate, () => api.listJobs(), (result) => {
      jobs.value = result.jobs
      if (jobs.value.length && !jobs.value.some((job) => job.job_id === activeJobId.value)) {
        activeJobId.value = jobs.value[0].job_id
        localStorage.setItem(ACTIVE_JOB_KEY, activeJobId.value)
      }
    }).catch((error) => {
      backendError.value = error instanceof ApiError ? error.message : String(error)
      return false
    })
    if (applied) await refreshCurrentPolicy()
  }

  function selectJob(jobId: string) {
    if (jobId === activeJobId.value) {
      void refreshCurrentPolicy()
      return
    }
    activeJobId.value = jobId
    localStorage.setItem(ACTIVE_JOB_KEY, jobId)
    // Job switch: invalidate job-scoped requests and drop stale policy/job data.
    _bumpEpoch()
    currentPolicy.value = null
    void refreshCurrentPolicy()
  }

  async function refreshCurrentPolicy() {
    if (!activeJobId.value) {
      currentPolicy.value = null
      return
    }
    const jobId = activeJobId.value
    const gate = jobGate
    await loadIfCurrent(gate, () => api.currentPolicy(jobId), (value) => {
      if (jobId !== activeJobId.value) return
      currentPolicy.value = value
    }).catch((error) => {
      if (error instanceof ApiError && error.status === 404) {
        if (jobId === activeJobId.value) currentPolicy.value = null
        return false
      }
      backendError.value = error instanceof ApiError ? error.message : String(error)
      return false
    })
  }

  function clearError() {
    backendError.value = null
  }

  return {
    runtime, identities, orgId, userId, jobs, activeJobId, activeJob,
    currentPolicy, backendError, backendConnected, dataEpoch,
    bootstrap, refreshJobs, selectJob, refreshCurrentPolicy, applyIdentity, clearError
  }
})
