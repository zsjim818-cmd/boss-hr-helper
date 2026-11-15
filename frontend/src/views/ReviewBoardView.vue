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
          <el-select v-model="selectedEvaluationId" size="small" style="width: 320px;"
                     @change="selectEvaluation">
            <el-option v-for="item in detail.evaluations" :key="item.pipeline_evaluation_id"
                       :label="`${item.scoring_mode} · ${item.scoring_status} · ${item.total_score ?? '—'}`"
                       :value="item.pipeline_evaluation_id" />
          </el-select>
        </div>
      </template>

      <template v-if="activeEvaluation">
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
              <el-button size="small" @click="contact('CONTACTED')">已联系</el-button>
              <el-button size="small" type="warning" @click="contact('DECLINED')">不联系</el-button>
              <el-button size="small" @click="contact('PENDING')">待定</el-button>
            </div>
            <el-table :data="contacts" size="small" border>
              <el-table-column prop="action" label="动作" width="140" />
              <el-table-column prop="note" label="备注" />
              <el-table-column prop="actor_id" label="操作人" width="180" />
              <el-table-column prop="created_at" label="时间" width="220" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, ApiError } from '@/api'
import { useAppStore } from '@/stores/useAppStore'
import type { CandidateDetail, ContactAction, ContactEvent, PipelineEvaluation,
  PreliminaryPayload, RankingEntry, ReviewAction, ReviewEvent, Tier2Payload, Tier3Payload } from '@/types'

const store = useAppStore()
const ranking = ref<RankingEntry[]>([])
const detail = ref<CandidateDetail | null>(null)
const selectedEvaluationId = ref('')
const reviews = ref<ReviewEvent[]>([])
const contacts = ref<ContactEvent[]>([])
const reviewReason = ref('')

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
  try {
    ranking.value = (await api.ranking(store.activeJobId)).ranking
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  }
}

async function selectRanking(row: RankingEntry | null) {
  if (!row || !store.activeJobId) return
  try {
    detail.value = await api.candidateDetail(store.activeJobId, row.job_candidate_id)
    const preferred = detail.value.evaluations.find(
      (item) => item.pipeline_evaluation_id === row.pipeline_evaluation_id)
    selectedEvaluationId.value = (preferred || detail.value.evaluations[0])?.pipeline_evaluation_id || ''
    await selectEvaluation()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  }
}

async function selectEvaluation() {
  if (!store.activeJobId || !selectedEvaluationId.value) return
  try {
    reviews.value = (await api.listReviews(store.activeJobId, selectedEvaluationId.value)).reviews
    contacts.value = (await api.listContacts(store.activeJobId, selectedEvaluationId.value)).events
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  }
}

async function review(action: ReviewAction) {
  if (!store.activeJobId || !selectedEvaluationId.value) return
  try {
    await api.appendReview(store.activeJobId, selectedEvaluationId.value,
      { action, reason: reviewReason.value })
    reviewReason.value = ''
    await selectEvaluation()
    ElMessage.success('审核已记录')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  }
}

async function contact(action: ContactAction) {
  if (!store.activeJobId || !selectedEvaluationId.value) return
  try {
    await api.appendContact(store.activeJobId, selectedEvaluationId.value, { action, note: '' })
    await selectEvaluation()
    ElMessage.success('联系状态已记录')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  }
}

onMounted(() => { void loadRanking() })
</script>
