<template>
  <div>
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">岗位候选人排名（最新正式结果，Deep 优先）</span>
          <el-button size="small" @click="loadRanking">刷新</el-button>
        </div>
      </template>
      <el-alert v-if="!store.activeJobId" type="info" :closable="false" show-icon
                title="请先选择岗位" />
      <el-table v-else :data="ranking" size="small" border highlight-current-row
                @current-change="selectRanking">
        <el-table-column label="#" width="50">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column prop="candidate_key" label="候选人" width="180" />
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.scoring_status)" size="small">
              {{ row.scoring_status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="总分" width="90">
          <template #default="{ row }">{{ row.total_score ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="路由建议" width="160">
          <template #default="{ row }">{{ row.routing_suggestion || '—' }}</template>
        </el-table-column>
        <el-table-column label="模式" width="100">
          <template #default="{ row }">{{ row.scoring_mode }}</template>
        </el-table-column>
        <el-table-column label="Policy" width="110">
          <template #default="{ row }">v{{ row.policy_version }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" />
      </el-table>
    </el-card>

    <el-card v-if="detail" shadow="never">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">评估详情：{{ detail.job_candidate.candidate_key }}</span>
          <el-space v-if="detail" wrap>
            <el-button v-for="item in detail.evaluations" :key="item.pipeline_evaluation_id"
                       size="small"
                       :type="item.pipeline_evaluation_id === selectedEvaluationId ? 'primary' : 'default'"
                       @click="selectEvaluationById(item.pipeline_evaluation_id)">
              {{ item.scoring_mode }}
            </el-button>
          </el-space>
        </div>
      </template>

      <template v-if="activeEvaluation">
        <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;">
          <el-tag :type="contactSummary?.contacted ? 'success' : 'info'" size="small">
            {{ contactSummary?.contacted
              ? `已联系（HR：${(contactSummary.contacted_by || []).join('、')}）`
              : '尚未联系' }}
          </el-tag>
          <el-tag v-if="contactSummary?.current_user_contacted" type="success" size="small">
            当前账号已联系
          </el-tag>
          <el-tag v-if="contactSummary?.current_user_pending_dry_run" type="warning" size="small">
            当前账号已预演（PENDING）
          </el-tag>
          <el-tag v-else-if="contactSummary?.latest_pending_dry_run" type="info" size="small">
            团队预演：{{ contactSummary.latest_pending_dry_run.actor_id }}
          </el-tag>
          <el-tag v-if="contactSummary?.pending_dry_run_count"
                  type="info" size="small" effect="plain">
            预演记录 {{ contactSummary.pending_dry_run_count }} 条
          </el-tag>
        </div>

        <el-descriptions :column="4" border size="small" style="margin-bottom: 12px;">
          <el-descriptions-item label="状态">
            <el-tag :type="statusTagType(activeEvaluation.scoring_status)" size="small">
              {{ activeEvaluation.scoring_status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="总分">{{ activeEvaluation.total_score ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="路由建议">{{ activeEvaluation.routing_suggestion || '—' }}</el-descriptions-item>
          <el-descriptions-item label="Policy">v{{ activeEvaluation.policy_version }}</el-descriptions-item>
        </el-descriptions>

        <el-alert v-if="activeEvaluation.review_reasons_json?.length" type="info" show-icon
                  :closable="false" style="margin-bottom: 12px;"
                  :title="activeEvaluation.review_reasons_json.join('；')" />

        <el-tabs>
          <el-tab-pane label="Tier 0 / 1（硬条件与规则）">
            <el-descriptions :column="3" border size="small">
              <el-descriptions-item label="硬条件">
                {{ (activeEvaluation.tier0_json as PreliminaryPayload).hard_filter_status }}
              </el-descriptions-item>
              <el-descriptions-item label="初筛状态">
                {{ (activeEvaluation.tier0_json as PreliminaryPayload).scoring_status }}
              </el-descriptions-item>
              <el-descriptions-item label="规则分">
                {{ (activeEvaluation.tier0_json as PreliminaryPayload).preliminary_score ?? '—' }}
              </el-descriptions-item>
            </el-descriptions>
            <el-table :data="(activeEvaluation.tier0_json as PreliminaryPayload).dimension_scores || []"
                      size="small" border style="margin-top: 10px;">
              <el-table-column prop="dimension_id" label="维度" width="150" />
              <el-table-column prop="dimension_label" label="名称" width="150" />
              <el-table-column label="权重" width="80">
                <template #default="{ row }">{{ (row.dimension_weight * 100).toFixed(0) }}%</template>
              </el-table-column>
              <el-table-column prop="normalized_score" label="规则分" width="90" />
              <el-table-column label="缺失证据">
                <template #default="{ row }">{{ row.missing_evidence?.join('；') || '—' }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="Tier 2（语义）">
            <template v-if="activeEvaluation.tier2_json">
              <el-descriptions :column="3" border size="small">
                <el-descriptions-item label="语义状态">
                  {{ (activeEvaluation.tier2_json as Tier2Payload).scoring_status }}
                </el-descriptions-item>
                <el-descriptions-item label="语义分">
                  {{ (activeEvaluation.tier2_json as Tier2Payload).semantic_score ?? '—' }}
                </el-descriptions-item>
                <el-descriptions-item label="覆盖率">
                  {{ ((activeEvaluation.tier2_json as Tier2Payload).semantic_coverage * 100).toFixed(1) }}%
                </el-descriptions-item>
              </el-descriptions>
              <el-table :data="(activeEvaluation.tier2_json as Tier2Payload).dimension_scores || []"
                        size="small" border style="margin-top: 10px;">
                <el-table-column prop="dimension_id" label="维度" width="180" />
                <el-table-column prop="scoring_status" label="状态" width="200" />
                <el-table-column label="语义分">
                  <template #default="{ row }">{{ row.semantic_score ?? '—' }}</template>
                </el-table-column>
              </el-table>
            </template>
            <el-empty v-else description="该结果未执行 Tier 2" />
          </el-tab-pane>

          <el-tab-pane label="Tier 3（终审证据）">
            <template v-if="activeEvaluation.tier3_json">
              <el-descriptions :column="4" border size="small" style="margin-bottom: 10px;">
                <el-descriptions-item label="总分">
                  {{ (activeEvaluation.tier3_json as Tier3Payload).total_score }}
                </el-descriptions-item>
                <el-descriptions-item label="路由">
                  {{ (activeEvaluation.tier3_json as Tier3Payload).routing_suggestion }}
                </el-descriptions-item>
                <el-descriptions-item label="模型">
                  {{ (activeEvaluation.tier3_json as Tier3Payload).llm_model || '—' }}
                </el-descriptions-item>
                <el-descriptions-item label="缺失证据数">
                  {{ (activeEvaluation.tier3_json as Tier3Payload).missing_evidence.length }}
                </el-descriptions-item>
              </el-descriptions>
              <div v-for="dimension in (activeEvaluation.tier3_json as Tier3Payload).dimensions"
                   :key="dimension.dimension_id" style="margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 6px;">
                  <strong>{{ dimension.label }}</strong>
                  <el-tag size="small" type="info">权重 {{ (dimension.weight * 100).toFixed(0) }}%</el-tag>
                  <el-tag size="small" type="success">得分 {{ dimension.score }}</el-tag>
                  <el-tag size="small" :type="dimension.confidence >= 0.7 ? 'success' : 'warning'">
                    置信度 {{ dimension.confidence }}
                  </el-tag>
                </div>
                <el-table :data="dimension.evidence" size="small" border v-if="dimension.evidence.length">
                  <el-table-column prop="source_path" label="原文路径" width="240" />
                  <el-table-column prop="quote" label="逐字证据" />
                </el-table>
                <el-alert v-else type="warning" :closable="false" show-icon
                          title="该维度没有可核验的原文证据" style="margin-top: 4px;" />
                <div v-if="dimension.missing_evidence?.length" style="font-size: 12px; color: #b88230; margin-top: 4px;">
                  缺失证据：{{ dimension.missing_evidence.join('；') }}
                </div>
              </div>
            </template>
            <el-empty v-else description="该结果未执行 Tier 3（可能为快筛/待核验/硬性淘汰）" />
          </el-tab-pane>

          <el-tab-pane label="HR 审核">
            <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;">
              <el-button size="small" type="success" @click="review('APPROVE')">批准</el-button>
              <el-button size="small" type="danger" @click="review('REJECT')">拒绝</el-button>
              <el-button size="small" type="warning" @click="review('REQUEST_REVIEW')">要求复核</el-button>
              <el-button size="small" @click="review('NOTE')">备注</el-button>
              <el-input v-model="reviewReason" style="width: 360px;" size="small"
                        placeholder="审核原因/备注（可选）" />
            </div>
            <el-table :data="reviews" size="small" border>
              <el-table-column prop="action" label="动作" width="140" />
              <el-table-column prop="reason" label="原因" />
              <el-table-column prop="actor_id" label="审核人" width="180" />
              <el-table-column prop="created_at" label="时间" width="220" />
            </el-table>

            <el-divider content-position="left">联系状态（与审核分离，仅记录人工事实）</el-divider>
            <div style="display: flex; gap: 8px; margin-bottom: 10px;">
              <el-button size="small" :disabled="currentUserContacted"
                         @click="contact('CONTACTED')">已联系</el-button>
              <el-button size="small" type="warning" @click="contact('DECLINED')">不联系</el-button>
              <el-button size="small" @click="contact('PENDING')">待定</el-button>
              <el-tag v-if="contactSummary?.contacted" type="success" size="small">
                已存在 CONTACTED 记录（HR：{{ (contactSummary.contacted_by || []).join('、') }}）
              </el-tag>
              <el-tag v-else type="info" size="small">尚未联系</el-tag>
            </div>
            <el-table :data="contactSummary?.events || []" size="small" border>
              <el-table-column prop="action" label="动作" width="140" />
              <el-table-column prop="note" label="备注" />
              <el-table-column prop="actor_id" label="操作人" width="180" />
              <el-table-column prop="created_at" label="时间" width="220" />
            </el-table>

            <el-divider content-position="left">Greeting Dry-Run 预演（本地 Mock，仅预演不发送真实消息）</el-divider>
            <el-alert v-if="!approved" type="warning" show-icon :closable="false"
                      style="margin-bottom: 8px;"
                      :title="`最新审核状态为 ${latestReviewAction || '无'}，仅 APPROVE 可预演`" />
            <el-alert v-if="currentUserContacted" type="info" show-icon :closable="false"
                      style="margin-bottom: 8px;"
                      title="当前 HR 账号已联系该候选人（同岗位跨评估去重），不可重复联系或预演" />
            <el-alert type="warning" show-icon :closable="false" style="margin-bottom: 8px;"
                      title="NEEDS_RELOCATE：候选人可能已从推荐列表消失；再次人工联系前需在 BOSS 页面人工重新定位，系统不自动搜索或发送。" />
            <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;">
              <el-input v-model="greetingMessage" style="width: 420px;" size="small"
                        placeholder="预演招呼语（不会真实发送）" />
              <el-button size="small" type="primary" :loading="dryRunning"
                         :disabled="!approved || currentUserContacted"
                         @click="runGreetingDryRun">
                Greeting Dry-Run 预演
              </el-button>
            </div>
            <el-alert v-if="greeting" type="success" show-icon :closable="false"
                      style="margin-bottom: 8px;"
                      :title="`${greeting.state} · identity=${greeting.identity} · ${greeting.idempotent ? '复用既有预演记录' : '新增 PENDING 预演记录'}（未发送）`"
                      :description="greeting.message" />
            <el-alert v-if="greetingError" type="error" show-icon :closable="false"
                      style="margin-bottom: 8px;" :title="greetingError" />
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, ApiError } from '@/api'
import { captureScope, scopeValid } from '@/lib/requestGate'
import { useAppStore } from '@/stores/useAppStore'
import type { CandidateDetail, ContactAction, ContactSummary,
  GreetingDryRunResult, PipelineEvaluation, PreliminaryPayload, RankingEntry,
  ReviewAction, ReviewEvent, Tier2Payload, Tier3Payload } from '@/types'

const store = useAppStore()
const ranking = ref<RankingEntry[]>([])
const detail = ref<CandidateDetail | null>(null)
const detailToken = ref(0)
const selectedEvaluationId = ref('')
const reviews = ref<ReviewEvent[]>([])
const contactSummary = ref<ContactSummary | null>(null)
const reviewReason = ref('')
const greetingMessage = ref('您好，方便聊聊这个岗位的机会吗？')
const greeting = ref<GreetingDryRunResult | null>(null)
const greetingError = ref('')
const dryRunning = ref(false)

// The latest non-NOTE review action decides whether a rehearsal is allowed;
// the backend enforces the same rule, this only drives the UI affordance.
const latestReviewAction = computed(() => {
  let action = ''
  for (const event of reviews.value) {
    if (event.action !== 'NOTE') action = event.action
  }
  return action
})
const approved = computed(() => latestReviewAction.value === 'APPROVE')
// contact_summary is the single source of truth for candidate-level contact
// state; no second interpretation from per-evaluation events is kept.
const currentUserContacted = computed(() => contactSummary.value?.current_user_contacted === true)

const activeEvaluation = computed<PipelineEvaluation | null>(() => {
  if (!detail.value) return null
  return detail.value.evaluations.find(
    (item) => item.pipeline_evaluation_id === selectedEvaluationId.value) || null
})

function statusTagType(status: string) {
  if (status === 'DEEP_SCORED') return 'success'
  if (status === 'HARD_FILTERED') return 'danger'
  if (status === 'COARSE_ONLY' || status === 'NEEDS_REVIEW') return 'warning'
  if (status === 'TECHNICAL_FAILED' || status === 'DATA_INCOMPLETE') return 'danger'
  return 'info'
}

async function loadRanking() {
  if (!store.activeJobId) return
  const jobId = store.activeJobId
  const epoch = store.dataEpoch
  try {
    const result = await api.ranking(jobId)
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return
    ranking.value = result.ranking
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  }
}

async function refreshContactSummary(jobCandidateId?: string,
                                     token = detailToken.value) {
  const jcId = jobCandidateId || detail.value?.job_candidate.job_candidate_id
  if (!store.activeJobId || !jcId) return
  const jobId = store.activeJobId
  const epoch = store.dataEpoch
  try {
    const summary = await api.contactSummary(jobId, jcId)
    if (token !== detailToken.value) return
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return
    if (detail.value?.job_candidate.job_candidate_id !== jcId) return
    contactSummary.value = summary
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  }
}

async function selectRanking(row: RankingEntry | null) {
  if (!row || !store.activeJobId) return
  const jobId = store.activeJobId
  const epoch = store.dataEpoch
  const token = ++detailToken.value
  const jcId = row.job_candidate_id
  try {
    const value = await api.candidateDetail(jobId, jcId)
    // A later row click owns the panel now: drop this late response entirely.
    if (token !== detailToken.value) return
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return
    if (value.job_candidate.job_candidate_id !== jcId) return
    detail.value = value
    const preferred = value.evaluations.find(
      (item) => item.pipeline_evaluation_id === row.pipeline_evaluation_id)
    selectedEvaluationId.value = (preferred || value.evaluations[0])?.pipeline_evaluation_id || ''
    await selectEvaluation(token)
    await refreshContactSummary(jcId, token)
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  }
}

async function selectEvaluation(token = detailToken.value) {
  if (!store.activeJobId || !selectedEvaluationId.value) return
  greeting.value = null
  greetingError.value = ''
  const jobId = store.activeJobId
  const evaluationId = selectedEvaluationId.value
  const jcId = detail.value?.job_candidate.job_candidate_id
  const epoch = store.dataEpoch
  try {
    const reviewsBody = await api.listReviews(jobId, evaluationId)
    if (token !== detailToken.value) return
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId
        || selectedEvaluationId.value !== evaluationId) return
    if (detail.value?.job_candidate.job_candidate_id !== jcId) return
    reviews.value = reviewsBody.reviews
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  }
}

async function selectEvaluationById(evaluationId: string) {
  if (!evaluationId || evaluationId === selectedEvaluationId.value) return
  selectedEvaluationId.value = evaluationId
  await selectEvaluation()
}

async function review(action: ReviewAction) {
  if (!store.activeJobId || !selectedEvaluationId.value) return
  const epoch = store.dataEpoch
  try {
    await api.appendReview(store.activeJobId, selectedEvaluationId.value,
      { action, reason: reviewReason.value })
    if (store.dataEpoch !== epoch) return
    reviewReason.value = ''
    await selectEvaluation()
    ElMessage.success('审核已记录')
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  }
}

async function contact(action: ContactAction) {
  if (!store.activeJobId || !selectedEvaluationId.value) return
  const epoch = store.dataEpoch
  try {
    await api.appendContact(store.activeJobId, selectedEvaluationId.value, { action, note: '' })
    if (store.dataEpoch !== epoch) return
    await selectEvaluation()
    await refreshContactSummary()
    ElMessage.success('联系状态已记录')
  } catch (error) {
    if (store.dataEpoch === epoch) {
      ElMessage.error(error instanceof ApiError ? error.message : String(error))
    }
  }
}

async function runGreetingDryRun() {
  if (!store.activeJobId || !selectedEvaluationId.value) return
  // Snapshot the job/evaluation/epoch; re-validate after every await so a
  // rehearsal can never be recorded against a different job's candidate.
  const scope = captureScope(store.activeJobId, store.dataEpoch)
  const evaluationId = selectedEvaluationId.value
  const message = greetingMessage.value
  dryRunning.value = true
  greeting.value = null
  greetingError.value = ''
  try {
    const result = await api.greetingDryRun(scope.jobId, evaluationId, { message })
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)
        || selectedEvaluationId.value !== evaluationId) return
    // Refresh the tables first: selectEvaluation() clears the previous outcome.
    await selectEvaluation()
    await refreshContactSummary()
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    greeting.value = result
    ElMessage.success(result.idempotent
      ? '已复用既有预演记录（幂等，未重复写入）'
      : 'Dry-Run 预演完成（未发送真实消息）')
  } catch (error) {
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    const apiError = error as ApiError
    greetingError.value = apiError.state
      ? `${apiError.state}：${apiError.message}`
      : (error instanceof ApiError ? error.message : String(error))
  } finally {
    dryRunning.value = false
  }
}

watch(() => store.dataEpoch, () => {
  // Job/identity switch: drop all ranking/detail/review state and invalidate
  // any in-flight detail response from the previous scope.
  detailToken.value += 1
  ranking.value = []
  detail.value = null
  selectedEvaluationId.value = ''
  reviews.value = []
  contactSummary.value = null
  reviewReason.value = ''
  greeting.value = null
  greetingError.value = ''
  void loadRanking()
})

onMounted(() => { void loadRanking() })
</script>
