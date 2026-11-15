<template>
  <div>
    <el-row :gutter="20">
      <el-col :span="9">
        <el-card shadow="never">
          <template #header>
            <span style="font-weight: 600;">步骤 1：岗位与 JD 输入</span>
          </template>

          <el-form label-position="top">
            <el-form-item label="创建新岗位">
              <div style="display: flex; gap: 8px; width: 100%;">
                <el-input v-model="newJobTitle" placeholder="例如：Java后端开发工程师" />
                <el-button type="primary" plain :loading="creating" @click="createJob">创建</el-button>
              </div>
            </el-form-item>
            <el-form-item label="招聘岗位名称（编译用）">
              <el-input v-model="form.job_title" placeholder="与所选岗位一致" />
            </el-form-item>
            <el-form-item label="岗位 JD 原文">
              <el-input v-model="form.jd_text" type="textarea" :rows="7"
                        placeholder="粘贴岗位职责与任职要求..." />
            </el-form-item>
            <el-form-item label="HR 补充筛选要求（自然语言）">
              <el-input v-model="form.hr_requirements" type="textarea" :rows="4"
                        placeholder="例如：必须有可核验的项目实现；不要工作年限限制..." />
            </el-form-item>
            <el-button type="primary" size="large" style="width: 100%;" :loading="compiling"
                       :disabled="!store.activeJobId" @click="compile">
              编译 Policy 草稿（{{ store.runtime?.mode === 'live' ? '真实 Qwen' : '确定性 Mock' }}）
            </el-button>
          </el-form>
        </el-card>
      </el-col>

      <el-col :span="15">
        <el-card shadow="never">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-weight: 600;">步骤 2：质量门控与发布</span>
              <div v-if="draft">
                <el-tag size="small" style="margin-right: 8px;">草稿 v{{ draft.revision }}</el-tag>
                <el-button type="success" :loading="publishing"
                           :disabled="!canPublish" @click="confirmAndVersion">
                  确认并创建不可变版本
                </el-button>
              </div>
            </div>
          </template>

          <el-empty v-if="!draft" description="尚未编译草稿" />
          <div v-else>
            <el-descriptions :column="3" border size="small" style="margin-bottom: 12px;">
              <el-descriptions-item label="草稿哈希">{{ shortHash(draft.draft_hash) }}</el-descriptions-item>
              <el-descriptions-item label="Publishable">
                <el-tag :type="compileResult?.publishable ? 'success' : 'danger'" size="small">
                  {{ compileResult?.publishable ? 'YES' : 'NO' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="质量状态">
                <el-tag :type="qualityTagType" size="small">{{ quality?.status || '—' }}</el-tag>
              </el-descriptions-item>
            </el-descriptions>

            <el-alert v-if="quality?.status === 'BLOCKED'" type="error" show-icon :closable="false"
                      title="质量门控 BLOCKED：默认禁止发布。仅 ORG_ADMIN 可填写豁免原因后发布（写入审计）。" />
            <el-alert v-else-if="quality?.status === 'WARNING'" type="warning" show-icon :closable="false"
                      title="质量门控 WARNING：可发布，但请人工确认下列问题。" />

            <div v-if="quality" style="margin: 12px 0;">
              <el-space wrap>
                <el-tag type="info">语义要求 {{ quality.semantic_requirement_count }}</el-tag>
                <el-tag type="success">直接覆盖 {{ quality.directly_covered_count }}</el-tag>
                <el-tag type="warning">宽泛覆盖 {{ quality.broad_only_count }}</el-tag>
                <el-tag type="danger">缺少正向锚点 {{ quality.missing_positive_count }}</el-tag>
                <el-tag type="info">未分类 {{ quality.unclassified_requirement_count }}</el-tag>
                <el-tag type="danger">孤儿组 {{ quality.orphan_group_count }}</el-tag>
                <el-tag v-if="quality.positive_coverage_rate !== null" type="success">
                  直接覆盖率 {{ (quality.positive_coverage_rate * 100).toFixed(1) }}%
                </el-tag>
                <el-tag v-else type="warning">覆盖率 INDETERMINATE</el-tag>
              </el-space>
            </div>

            <el-tabs v-if="quality && quality.issues.length" style="margin-top: 8px;">
              <el-tab-pane :label="`质量问题 (${quality.issues.length})`">
                <el-table :data="quality.issues" size="small" border>
                  <el-table-column label="严重度" width="90">
                    <template #default="{ row }">
                      <el-tag :type="row.severity === 'ERROR' ? 'danger' : 'warning'" size="small">
                        {{ row.severity }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="code" label="代码" width="230" />
                  <el-table-column label="对象" width="180">
                    <template #default="{ row }">{{ row.requirement_id || row.group_id || '—' }}</template>
                  </el-table-column>
                  <el-table-column prop="message" label="说明" />
                  <el-table-column prop="suggested_action" label="建议动作" width="260" />
                </el-table>
              </el-tab-pane>
            </el-tabs>

            <el-tabs v-if="policy" style="margin-top: 8px;">
              <el-tab-pane :label="`评分维度 (${policy.scoring_dimensions.length})`">
                <el-table :data="policy.scoring_dimensions" size="small" border>
                  <el-table-column prop="dimension_id" label="维度ID" width="150" />
                  <el-table-column prop="label" label="名称" width="150" />
                  <el-table-column label="权重" width="80">
                    <template #default="{ row }">{{ (row.weight * 100).toFixed(0) }}%</template>
                  </el-table-column>
                  <el-table-column label="Rubric">
                    <template #default="{ row }">
                      <div v-for="band in row.rubric" :key="band.min_score" style="font-size: 12px;">
                        {{ band.min_score }}–{{ band.max_score }}：{{ band.description }}
                      </div>
                    </template>
                  </el-table-column>
                </el-table>
              </el-tab-pane>
              <el-tab-pane :label="`要求组 ALL/ANY (${policy.requirement_groups.length})`">
                <el-table :data="policy.requirement_groups" size="small" border>
                  <el-table-column prop="group_id" label="组ID" width="140" />
                  <el-table-column prop="label" label="名称" width="170" />
                  <el-table-column label="算子/优先级" width="160">
                    <template #default="{ row }">
                      <el-tag :type="row.operator === 'ANY' ? 'warning' : 'info'" size="small">
                        {{ row.operator }} / {{ row.priority }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="成员">
                    <template #default="{ row }">{{ row.members.join(' , ') }}</template>
                  </el-table-column>
                </el-table>
              </el-tab-pane>
              <el-tab-pane :label="`原子要求 (${policy.requirements.length})`">
                <el-table :data="policy.requirements" size="small" border max-height="320">
                  <el-table-column prop="requirement_id" label="ID" width="150" />
                  <el-table-column prop="label" label="标签" width="180" />
                  <el-table-column prop="priority" label="优先级" width="100" />
                  <el-table-column prop="requirement_type" label="类型" width="120" />
                  <el-table-column prop="source_text" label="来源原文" />
                </el-table>
              </el-tab-pane>
              <el-tab-pane label="阈值">
                <el-descriptions :column="3" border size="small">
                  <el-descriptions-item label="粗排通过">{{ policy.thresholds.coarse_pass }}</el-descriptions-item>
                  <el-descriptions-item label="深度复核">{{ policy.thresholds.deep_review }}</el-descriptions-item>
                  <el-descriptions-item label="深度推荐">{{ policy.thresholds.deep_recommend }}</el-descriptions-item>
                </el-descriptions>
              </el-tab-pane>
            </el-tabs>

            <div v-if="versionRecord" style="margin-top: 12px;">
              <el-alert type="success" show-icon :closable="false"
                        :title="`已创建版本 v${versionRecord.policy_version}（policy_id=${versionRecord.policy_id}）`" />
              <div style="margin-top: 10px;">
                <el-input v-model="overrideReason" style="width: 420px;"
                          :placeholder="quality?.status === 'BLOCKED'
                            ? '质量门控豁免原因（仅 ORG_ADMIN 生效）' : '发布备注（可选）'" />
                <el-button type="danger" style="margin-left: 10px;" :loading="publishing"
                           @click="publish">
                  发布该版本
                </el-button>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api, ApiError } from '@/api'
import { useAppStore } from '@/stores/useAppStore'
import type { CompileResult, JobScoringPolicy, PolicyDraft, QualityReport } from '@/types'

const store = useAppStore()
const newJobTitle = ref('')
const creating = ref(false)
const compiling = ref(false)
const publishing = ref(false)
const compileResult = ref<CompileResult | null>(null)
const draft = ref<PolicyDraft | null>(null)
const quality = ref<QualityReport | null>(null)
const versionRecord = ref<{ policy_id: string; policy_version: number } | null>(null)
const overrideReason = ref('')
const form = reactive({ job_title: '', jd_text: '', hr_requirements: '' })

const policy = computed<JobScoringPolicy | null>(() => {
  const value = draft.value?.policy_json
  return value && typeof value === 'object' ? value : null
})
const canPublish = computed(() => Boolean(draft.value && compileResult.value?.compilation_run))
const qualityTagType = computed(() => {
  if (!quality.value) return 'info'
  return quality.value.status === 'PASS' ? 'success'
    : quality.value.status === 'WARNING' ? 'warning' : 'danger'
})

function shortHash(value: string | undefined) {
  return value ? value.slice(0, 12) + '…' : '—'
}

watch(() => store.activeJob, (job) => {
  if (job) form.job_title = job.title
}, { immediate: true })

async function createJob() {
  if (!newJobTitle.value.trim()) return ElMessage.warning('请输入岗位名称')
  creating.value = true
  try {
    const job = await api.createJob(newJobTitle.value.trim())
    await store.refreshJobs()
    store.selectJob(job.job_id)
    newJobTitle.value = ''
    ElMessage.success('岗位已创建')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  } finally {
    creating.value = false
  }
}

async function compile() {
  if (!store.activeJobId) return ElMessage.warning('请先创建/选择岗位')
  compiling.value = true
  versionRecord.value = null
  try {
    const result = await api.compilePolicy(store.activeJobId, { ...form })
    compileResult.value = result
    draft.value = result.draft
    quality.value = result.quality || null
    ElMessage.success('草稿已编译')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  } finally {
    compiling.value = false
  }
}

async function confirmAndVersion() {
  if (!draft.value || !compileResult.value) return
  publishing.value = true
  try {
    const confirmed = await api.confirmDraft(store.activeJobId, draft.value.draft_id, {
      run_id: compileResult.value.compilation_run.run_id,
      confirmation_hash: draft.value.draft_hash
    })
    const sealed = await api.createVersion(store.activeJobId, draft.value.draft_id, {
      run_id: compileResult.value.compilation_run.run_id,
      confirmation_id: confirmed.confirmation_id as string
    })
    versionRecord.value = sealed.version as { policy_id: string; policy_version: number }
    ElMessage.success('不可变版本已创建')
  } catch (error) {
    const apiError = error as ApiError
    ElMessage.error(apiError.message)
    if (apiError.qualityIssues?.length) {
      quality.value = { ...(quality.value as QualityReport), issues: apiError.qualityIssues }
    }
  } finally {
    publishing.value = false
  }
}

async function publish() {
  if (!draft.value || !versionRecord.value) return
  publishing.value = true
  try {
    await api.publishPolicy(store.activeJobId, versionRecord.value.policy_id, {
      confirmation_hash: draft.value.draft_hash,
      ...(overrideReason.value ? { quality_override_reason: overrideReason.value } : {})
    })
    await store.refreshCurrentPolicy()
    ElMessage.success('策略已发布')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : String(error))
  } finally {
    publishing.value = false
  }
}

onMounted(() => { if (!store.jobs.length) void store.bootstrap() })
</script>
