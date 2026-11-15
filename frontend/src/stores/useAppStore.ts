// Sprint 3 application state: runtime mode, mock identity, jobs, error banner.
// No mock business data lives here; everything is fetched from the API.
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, ApiError, getIdentity, setIdentity } from '@/api'
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

  const activeJob = computed<Job | null>(
    () => jobs.value.find((job) => job.job_id === activeJobId.value) || null)

  function applyIdentity(org: string, user: string) {
    orgId.value = org
    userId.value = user
    setIdentity(org, user)
  }

  async function bootstrap() {
    backendError.value = null
    try {
      runtime.value = await api.runtimeMode()
      backendConnected.value = true
      if (runtime.value.local_only) {
        try {
          identities.value = (await api.localIdentities()).identities
          if ((!orgId.value || !userId.value) && identities.value.length > 0) {
            const preferred = identities.value.find((item) => item.org_role === 'ORG_ADMIN')
            applyIdentity((preferred || identities.value[0]).org_id,
                          (preferred || identities.value[0]).user_id)
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
    try {
      jobs.value = (await api.listJobs()).jobs
      if (jobs.value.length && !jobs.value.some((job) => job.job_id === activeJobId.value)) {
        selectJob(jobs.value[0].job_id)
      }
      await refreshCurrentPolicy()
    } catch (error) {
      backendError.value = error instanceof ApiError ? error.message : String(error)
    }
  }

  function selectJob(jobId: string) {
    activeJobId.value = jobId
    localStorage.setItem(ACTIVE_JOB_KEY, jobId)
    void refreshCurrentPolicy()
  }

  async function refreshCurrentPolicy() {
    currentPolicy.value = null
    if (!activeJobId.value) return
    try {
      currentPolicy.value = await api.currentPolicy(activeJobId.value)
    } catch (error) {
      // 404 simply means "no published policy yet"; anything else is an error.
      if (error instanceof ApiError && error.status === 404) return
      backendError.value = error instanceof ApiError ? error.message : String(error)
    }
  }

  function clearError() {
    backendError.value = null
  }

  return {
    runtime, identities, orgId, userId, jobs, activeJobId, activeJob,
    currentPolicy, backendError, backendConnected,
    bootstrap, refreshJobs, selectJob, refreshCurrentPolicy, applyIdentity, clearError
  }
})
