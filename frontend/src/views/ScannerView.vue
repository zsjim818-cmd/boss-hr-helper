<template>
  <div>
    <el-row :gutter="20">
      <el-col :span="10">
        <el-card shadow="never">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-weight: 600;">步骤 1：批次与简历导入</span>
              <el-space>
                <el-button size="small" type="success" plain :loading="scanning"
                           :disabled="!batch" @click="runBrowserScan">
                  Mock 浏览器扫描
                </el-button>
                <el-button size="small" type="primary" plain :loading="creatingBatch"
                           :disabled="!store.currentPolicy" @click="createBatch">
                  创建批次
                </el-button>
              </el-space>
            </div>
          </template>

          <el-alert v-if="!store.currentPolicy" type="warning" show-icon :closable="false"
                    title="当前岗位尚未发布 Policy，无法创建批次。请先在【岗位与策略】发布。" />
          <template v-else>
            <el-descriptions :column="2" border size="small" style="margin-bottom: 12px;">
              <el-descriptions-item label="锁定 Policy 版本">
                v{{ store.currentPolicy.policy_version as number }}
              </el-descriptions-item>
              <el-descriptions-item label="批次状态">
                {{ batch ? batch.status : '未创建' }}
              </el-descriptions-item>
              <el-descriptions-item label="批次 ID" :span="2">
                {{ batch?.batch_id || '—' }}
              </el-descriptions-item>
            </el-descriptions>

            <div v-if="scanStatus || scanReport" style="margin-bottom: 12px;">
              <el-descriptions :column="4" border size="small">
                <el-descriptions-item label="Mock 扫描状态">{{ scanStatus || '—' }}</el-descriptions-item>
                <el-descriptions-item label="采集">{{ scanReport?.collected ?? '—' }}</el-descriptions-item>
                <el-descriptions-item label="导入">{{ scanReport?.imported ?? '—' }}</el-descriptions-item>
                <el-descriptions-item label="复用/失败">
                  {{ scanReport ? `${scanReport.reused} / ${scanReport.failed}` : '—' }}
                </el-descriptions-item>
              </el-descriptions>
              <el-alert v-for="(issue, index) in scanReport?.issues || []" :key="index"
                        type="warning" show-icon :closable="false" style="margin-top: 6px;"
                        :title="`${issue.code}: ${issue.message}`" />
              <el-alert v-if="scanError" type="error" show-icon :closable="false"
                        style="margin-top: 6px;" :title="scanError" />
              <div v-if="scanReport?.source" style="font-size: 12px; color: #909399;">
                数据源：{{ scanReport.source }} · {{ scanReport.browser || '—' }}
                <span v-if="scanReport.page_url"> · {{ scanReport.page_url }}</span>
              </div>
            </div>

            <el-form label-position="top">
              <el-form-item label="候选人 Key">
                <el-input v-model="candidateKey" placeholder="唯一稳定标识，例如 cand-java-01" />
              </el-form-item>
              <el-form-item label="标准简历模板">
                <el-select v-model="templateKey" style="width: 100%;" @change="applyTemplate">
                  <el-option v-for="tpl in templates" :key="tpl.key"
                             :label="tpl.label" :value="tpl.key" />
                </el-select>
              </el-form-item>
              <el-form-item label="简历 JSON（可编辑）">
                <el-input v-model="resumeJson" type="textarea" :rows="12" />
              </el-form-item>
              <el-button type="primary" :loading="importing" :disabled="!batch" @click="importCandidate">
                导入并封存简历
              </el-button>
            </el-form>
          </template>
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card shadow="never" style="margin-bottom: 16px;">
          <template #header><span style="font-weight: 600;">步骤 2：语义准备与 Tier 0~3 评分</span></template>
          <el-space wrap>
            <el-button size="small" :loading="preparing" :disabled="!selectedCandidate"
                       @click="prepareSemantic">
              语义资产准备（{{ store.runtime?.mode === 'live' ? '真实 BGE' : '确定性模式无需' }}）
            </el-button>
            <el-button size="small" type="warning" :loading="scoringFast" :disabled="!selectedCandidate"
                       @click="score('fast_mode')">快速初筛 (fast)</el-button>
            <el-button size="small" type="danger" :loading="scoringDeep" :disabled="!selectedCandidate"
                       @click="score('deep_mode')">深度评分 (Tier 0~3)</el-button>
            <el-button size="small" type="success" plain :loading="batchScoring"
                       :disabled="!candidates.length" @click="scoreBatch('fast_mode')">
              批量 Fast 初筛（未评分）
            </el-button>
            <el-button size="small" type="danger" plain :loading="batchScoring"
                       :disabled="!candidates.length" @click="scoreBatch('deep_mode')">
              批量 Deep 深评（未评分）
            </el-button>
            <el-tag v-if="task" :type="taskTagType">{{ task.status }}</el-tag>
          </el-space>

          <el-alert v-if="task?.error_type" type="error" show-icon :closable="false"
                    style="margin-top: 10px;"
                    :title="`${task.error_type}: ${task.error_message || ''}`" />
          <el-descriptions v-if="evaluation" :column="4" border size="small" style="margin-top: 12px;">
            <el-descriptions-item label="评分模式">{{ evaluation.scoring_mode }}</el-descriptions-item>
            <el-descriptions-item label="结果状态">
              <el-tag :type="statusTagType(evaluation.scoring_status)" size="small">
                {{ evaluation.scoring_status }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="总分">{{ evaluation.total_score ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="路由建议">{{ evaluation.routing_suggestion || '—' }}</el-descriptions-item>
          </el-descriptions>
          <el-col v-if="evaluation?.review_reasons_json?.length" style="margin-top: 8px;">
            <el-alert type="info" :closable="false" show-icon
                      :title="evaluation.review_reasons_json.join('；')" />
          </el-col>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div style="display: flex; justify-content: space-between;">
              <span style="font-weight: 600;">本岗位候选人</span>
              <span v-if="selectedCandidate" style="font-size: 12px; color: #409eff;">
                已选：{{ selectedCandidate.job_candidate.candidate_key }}
              </span>
              <el-button size="small" @click="refreshCandidates">刷新</el-button>
            </div>
          </template>
          <el-table :data="candidates" size="small" border highlight-current-row
                    row-style="cursor: pointer;" @row-click="selectCandidate">
            <el-table-column label="候选人" width="160">
              <template #default="{ row }">{{ row.job_candidate.candidate_key }}</template>
            </el-table-column>
            <el-table-column label="最新状态" width="150">
              <template #default="{ row }">
                <el-tag v-if="row.latest_evaluation"
                        :type="statusTagType(row.latest_evaluation.scoring_status)" size="small">
                  {{ row.latest_evaluation.scoring_status }}
                </el-tag>
                <el-tag v-else type="info" size="small">未评分</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="总分" width="80">
              <template #default="{ row }">{{ row.latest_evaluation?.total_score ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="模式" width="90">
              <template #default="{ row }">{{ row.latest_evaluation?.scoring_mode || '—' }}</template>
            </el-table-column>
            <el-table-column label="路由" width="150">
              <template #default="{ row }">{{ row.latest_evaluation?.routing_suggestion || '—' }}</template>
            </el-table-column>
            <el-table-column prop="job_candidate.policy_version" label="Policy版本" width="100" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, ApiError } from '@/api'
import { captureScope, scopeValid } from '@/lib/requestGate'
import { useAppStore } from '@/stores/useAppStore'
import type { Batch, BrowserScanResult, CandidateListItem, ImportResult,
  PipelineEvaluation, PipelineTask, ScoringMode, PipelineStatus } from '@/types'

const store = useAppStore()
const creatingBatch = ref(false)
const importing = ref(false)
const preparing = ref(false)
const scanning = ref(false)
const scoringFast = ref(false)
const scoringDeep = ref(false)
const batchScoring = ref(false)
const batch = ref<Batch | null>(null)
const imported = ref<ImportResult | null>(null)
const selectedCandidate = ref<CandidateListItem | null>(null)
const scanReport = ref<BrowserScanResult | null>(null)
const scanStatus = ref('')
const scanError = ref('')
const candidateKey = ref('cand-demo-01')
const templateKey = ref('java')
const resumeJson = ref('')
const task = ref<PipelineTask | null>(null)
const evaluation = ref<PipelineEvaluation | null>(null)
const candidates = ref<CandidateListItem[]>([])

const javaResume = {
  name: '演示候选人（Java）', degree: '本科', work_years: '5年',
  job_status: '离职-随时到岗', active_status: '今日活跃',
  expectation: { city: '北京', salary: '12K/月', position: 'Java工程师' },
  skills: 'Java, SQL, Spring Boot, 接口自动化测试',
  work_experience: [{ company: '演示支付公司', position: 'Java工程师',
    responsibility: '负责订单支付后台Java服务开发，设计SQL事务与幂等约束，使用Spring Boot提供接口并编写接口自动化测试。',
    keywords: [] }],
  project_experience: [], education: [{ school: '演示大学', degree: '本科' }],
  certifications: [], advantage: ''
}
const salesResume = {
  name: '演示候选人（销售）', degree: '本科', work_years: '4年',
  job_status: '离职-随时到岗', active_status: '今日活跃',
  expectation: { city: '北京', salary: '12K/月', position: '企业销售' },
  skills: '企业客户销售, CRM, 合同谈判, 销售漏斗',
  work_experience: [{ company: '演示软件公司', position: '企业销售',
    responsibility: '负责企业客户需求访谈与方案沟通，独立推进合同谈判与签约，维护销售漏斗并跟进回款。',
    keywords: [] }],
  project_experience: [], education: [{ school: '演示大学', degree: '本科' }],
  certifications: [], advantage: ''
}
const aiResume = {
  name: '演示候选人（AI）', degree: '硕士', work_years: '2年',
  job_status: '离职-随时到岗', active_status: '今日活跃',
  expectation: { city: '北京', salary: '12K/月', position: 'AI应用工程师' },
  skills: 'Python, LangGraph, RAG, Playwright',
  work_experience: [{ company: '演示科技公司', position: 'AI应用工程师',
    responsibility: '使用LangGraph搭建智能体工作流，构建RAG知识库问答，编写Playwright自动化脚本。',
    keywords: [] }],
  project_experience: [], education: [{ school: '演示大学', degree: '硕士' }],
  certifications: [], advantage: ''
}

const templates = [
  { key: 'java', label: 'Java 后端候选人', value: javaResume },
  { key: 'sales', label: '企业销售候选人', value: salesResume },
  { key: 'ai', label: 'AI 自动化候选人', value: aiResume }
]

function applyTemplate() {
  const template = templates.find((item) => item.key === templateKey.value)
  if (template) resumeJson.value = JSON.stringify(template.value, null, 2)
}

function statusTagType(status: string) {
  if (status === 'DEEP_SCORED') return 'success'
  if (status === 'HARD_FILTERED') return 'danger'
  if (status === 'COARSE_ONLY') return 'warning'
  if (status === 'NEEDS_REVIEW') return 'warning'
  if (status === 'TECHNICAL_FAILED' || status === 'DATA_INCOMPLETE') return 'danger'
  return 'info'
}

const taskTagType = computed(() => {
  if (!task.value) return 'info'
  if (task.value.status === 'SUCCEEDED') return 'success'
  if (task.value.status === 'FAILED') return 'danger'
  return 'warning'
})

async function createBatch() {
  if (!store.activeJobId) return
  const scope = captureScope(store.activeJobId, store.dataEpoch)
  creatingBatch.value = true
  try {
    const created = await api.createBatch(scope.jobId)
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    batch.value = created
    ElMessage.success('批次已创建（Policy 版本已锁定）')
  } catch (error) {
    if (scopeValid(scope, store.activeJobId, store.dataEpoch)) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  } finally {
    creatingBatch.value = false
  }
}

async function runBrowserScan() {
  if (!batch.value) return
  // Local Mock scan only: it writes through the same repository/batch binding.
  const scope = captureScope(store.activeJobId, store.dataEpoch)
  const batchId = batch.value.batch_id
  scanning.value = true
  scanReport.value = null
  scanStatus.value = ''
  scanError.value = ''
  try {
    const report = await api.browserScan(scope.jobId, batchId,
                                         { pages: 2, page_size: 5 })
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    scanReport.value = report
    const status = await api.browserScanStatus(scope.jobId, batchId)
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    scanStatus.value = status.status
    ElMessage.success(
      `Mock 扫描完成：采集 ${report.collected}，导入 ${report.imported}，复用 ${report.reused}`)
    await refreshCandidates()
  } catch (error) {
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    scanStatus.value = 'FAILED'
    scanError.value = error instanceof ApiError ? error.message : String(error)
  } finally {
    scanning.value = false
  }
}

async function importCandidate() {
  if (!batch.value) return
  const epoch = store.dataEpoch
  importing.value = true
  try {
    const resume = JSON.parse(resumeJson.value || '{}')
    const result = await api.importCandidate(store.activeJobId, batch.value.batch_id, {
      candidate_key: candidateKey.value,
      resume,
      source: 'manual'
    })
    if (store.dataEpoch !== epoch) return
    imported.value = result
    ElMessage.success(result.is_new ? '简历已导入并封存' : '候选人已存在（复用绑定）')
    await refreshCandidates()
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  } finally {
    importing.value = false
  }
}

async function prepareSemantic() {
  const candidate = selectedCandidate.value
  if (!store.activeJobId || !candidate) return
  const jobId = store.activeJobId
  const epoch = store.dataEpoch
  const jcid = candidate.job_candidate.job_candidate_id
  preparing.value = true
  try {
    const result = await api.semanticPrepare(jobId, jcid, batch.value?.batch_id)
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return
    ElMessage.success(`语义准备：${result.status}`)
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  } finally {
    preparing.value = false
  }
}

async function runScore(jcid: string, mode: ScoringMode, epoch: number) {
  if (!store.activeJobId) return null
  const jobId = store.activeJobId
  const started = await api.startScoring(jobId, jcid, {
    scoring_mode: mode,
    batch_id: batch.value?.batch_id,
    idempotency_key: batch.value
      ? `${batch.value.batch_id}:${jcid}:${mode}`
      : undefined
  })
  if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return null
  if (started.reused && started.evaluation) return started.evaluation
  if (!started.task) return null
  task.value = started.task
  for (let i = 0; i < 720; i += 1) {
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return null
    const body = await api.task(jobId, started.task.pipeline_task_id)
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return null
    task.value = body.task
    if (body.task.status === 'SUCCEEDED') return body.evaluation
    if (body.task.status === 'FAILED') {
      throw new Error(`${body.task.error_type}: ${body.task.error_message || ''}`)
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error('评分任务仍在运行，请稍后在审核看板查看结果')
}

async function score(mode: ScoringMode) {
  const candidate = selectedCandidate.value
  if (!store.activeJobId || !candidate) return
  const epoch = store.dataEpoch
  const jcid = candidate.job_candidate.job_candidate_id
  const busy = mode === 'fast_mode' ? scoringFast : scoringDeep
  busy.value = true
  evaluation.value = null
  task.value = null
  try {
    const result = await runScore(jcid, mode, epoch)
    if (result) {
      if (selectedCandidate.value?.job_candidate.job_candidate_id === jcid) {
        evaluation.value = result
      }
      ElMessage.success('评分完成')
      await refreshCandidates()
    }
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  } finally {
    busy.value = false
  }
}

async function scoreBatch(mode: ScoringMode) {
  if (!store.activeJobId) return
  const targets = candidates.value.filter((item) => !item.latest_evaluation)
  if (!targets.length) {
    ElMessage.info('当前没有未评分候选人')
    return
  }
  const epoch = store.dataEpoch
  batchScoring.value = true
  let succeeded = 0
  let failed = 0
  try {
    for (const candidate of targets) {
      if (store.dataEpoch !== epoch) break
      try {
        const result = await runScore(
          candidate.job_candidate.job_candidate_id, mode, epoch)
        if (result) succeeded += 1
      } catch {
        failed += 1
      }
    }
    if (store.dataEpoch === epoch) {
      ElMessage.success(`批量评分完成：成功 ${succeeded}，失败 ${failed}`)
      await refreshCandidates()
    }
  } finally {
    batchScoring.value = false
  }
}

function selectCandidate(row: CandidateListItem) {
  selectedCandidate.value = row
  evaluation.value = null
  task.value = null
}

async function refreshCandidates() {
  if (!store.activeJobId) return
  const jobId = store.activeJobId
  const epoch = store.dataEpoch
  try {
    const result = await api.listCandidates(jobId, { limit: 50 })
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return
    candidates.value = result.items
    const current = selectedCandidate.value
    const stillPresent = current && result.items.find(
      (item) => item.job_candidate.job_candidate_id
        === current.job_candidate.job_candidate_id)
    if (stillPresent) {
      selectedCandidate.value = stillPresent
    } else {
      // Prefer the first unscored candidate, otherwise the first row.
      selectedCandidate.value = result.items.find((item) => !item.latest_evaluation)
        || result.items[0] || null
      evaluation.value = null
      task.value = null
    }
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  }
}

watch(() => store.dataEpoch, () => {
  // Job/identity switch: clear job-scoped batch/candidate/scan/task state.
  batch.value = null
  imported.value = null
  selectedCandidate.value = null
  task.value = null
  evaluation.value = null
  candidates.value = []
  scanReport.value = null
  scanStatus.value = ''
  scanError.value = ''
  void refreshCandidates()
})

onMounted(() => {
  applyTemplate()
  void refreshCandidates()
})
</script>
