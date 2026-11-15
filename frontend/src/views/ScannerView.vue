<template>
  <div>
    <el-row :gutter="20">
      <el-col :span="10">
        <el-card shadow="never">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-weight: 600;">步骤 1：批次与简历导入</span>
              <el-button size="small" type="primary" plain :loading="creatingBatch"
                         :disabled="!store.currentPolicy" @click="createBatch">
                创建批次
              </el-button>
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
            <el-button size="small" :loading="preparing" :disabled="!imported"
                       @click="prepareSemantic">
              语义资产准备（{{ store.runtime?.mode === 'live' ? '真实 BGE' : '确定性模式无需' }}）
            </el-button>
            <el-button size="small" type="warning" :loading="scoringFast" :disabled="!imported"
                       @click="score('fast_mode')">快速初筛 (fast)</el-button>
            <el-button size="small" type="danger" :loading="scoringDeep" :disabled="!imported"
                       @click="score('deep_mode')">深度评分 (Tier 0~3)</el-button>
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
              <el-button size="small" @click="refreshCandidates">刷新</el-button>
            </div>
          </template>
          <el-table :data="candidates" size="small" border>
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
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, ApiError } from '@/api'
import { useAppStore } from '@/stores/useAppStore'
import type { Batch, CandidateListItem, ImportResult, PipelineEvaluation,
  PipelineTask, ScoringMode, PipelineStatus } from '@/types'

const store = useAppStore()
const creatingBatch = ref(false)
const importing = ref(false)
const preparing = ref(false)
const scoringFast = ref(false)
const scoringDeep = ref(false)
const batch = ref<Batch | null>(null)
const imported = ref<ImportResult | null>(null)
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
  creatingBatch.value = true
  try {
    batch.value = await api.createBatch(store.activeJobId)
    ElMessage.success('批次已创建（Policy 版本已锁定）')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  } finally {
    creatingBatch.value = false
  }
}

async function importCandidate() {
  if (!batch.value) return
  importing.value = true
  try {
    const resume = JSON.parse(resumeJson.value || '{}')
    imported.value = await api.importCandidate(store.activeJobId, batch.value.batch_id, {
      candidate_key: candidateKey.value,
      resume,
      source: 'manual'
    })
    ElMessage.success(imported.value.is_new ? '简历已导入并封存' : '候选人已存在（复用绑定）')
    await refreshCandidates()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  } finally {
    importing.value = false
  }
}

async function prepareSemantic() {
  if (!store.activeJobId || !imported.value) return
  preparing.value = true
  try {
    const result = await api.semanticPrepare(
      store.activeJobId, imported.value.job_candidate.job_candidate_id)
    ElMessage.success(`语义准备：${result.status}`)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  } finally {
    preparing.value = false
  }
}

async function score(mode: ScoringMode) {
  if (!store.activeJobId || !imported.value) return
  const busy = mode === 'fast_mode' ? scoringFast : scoringDeep
  busy.value = true
  evaluation.value = null
  task.value = null
  try {
    const started = await api.startScoring(
      store.activeJobId, imported.value.job_candidate.job_candidate_id,
      { scoring_mode: mode })
    if (started.reused && started.evaluation) {
      evaluation.value = started.evaluation
      ElMessage.success('已有正式结果，直接复用')
      await refreshCandidates()
      return
    }
    if (started.task) {
      task.value = started.task
      await pollTask(started.task.pipeline_task_id)
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  } finally {
    busy.value = false
  }
}

async function pollTask(taskId: string) {
  if (!store.activeJobId) return
  for (let i = 0; i < 240; i += 1) {
    const body = await api.task(store.activeJobId, taskId)
    task.value = body.task
    if (body.task.status === 'SUCCEEDED') {
      evaluation.value = body.evaluation
      ElMessage.success('评分完成')
      await refreshCandidates()
      return
    }
    if (body.task.status === 'FAILED') {
      ElMessage.error(`${body.task.error_type}: ${body.task.error_message || ''}`)
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  ElMessage.warning('任务仍在运行，可稍后在审核看板查看结果')
}

async function refreshCandidates() {
  if (!store.activeJobId) return
  try {
    const result = await api.listCandidates(store.activeJobId, { limit: 50 })
    candidates.value = result.items
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  }
}

onMounted(() => {
  applyTemplate()
  void refreshCandidates()
})
</script>
