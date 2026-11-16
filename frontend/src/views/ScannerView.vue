<template>
  <div>
    <!-- ------------------------------------------------ session card -->
    <el-card shadow="never" style="margin-bottom: 16px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 600;">HR 浏览器与登录会话</span>
          <el-space>
            <el-tag :type="sessionTagType" size="small">{{ session?.state || '未加载' }}</el-tag>
            <el-button size="small" :loading="sessionLoading" @click="loadSession">刷新状态</el-button>
          </el-space>
        </div>
      </template>
      <el-alert v-if="session" type="info" :closable="false" show-icon
                style="margin-bottom: 10px;"
                :title="`数据源：${session.source_label} · 真实契约未核验：${session.real_contract_verified ? '是' : '否'} · 真实 PII 存储未就绪：${session.real_pii_storage_ready ? '否' : '是'}`" />
      <el-alert v-if="session?.state === 'REAL_CONTRACT_UNVERIFIED'"
                type="warning" show-icon :closable="false" style="margin-bottom: 10px;"
                title="BOSS_READONLY：真实页面采集尚未获得真机契约与授权，已硬禁用；请使用 MOCK / CDP_MOCK 完成本地验收。" />
      <el-descriptions v-if="session" :column="4" border size="small">
        <el-descriptions-item label="连接">{{ session.connected ? '已连接' : '未连接' }}</el-descriptions-item>
        <el-descriptions-item label="Profile">{{ session.profile_id || '—' }}</el-descriptions-item>
        <el-descriptions-item label="页面标题">{{ session.page_title || '—' }}</el-descriptions-item>
        <el-descriptions-item label="推荐页 iframe">{{ session.iframe_detected ? '已检测' : '未检测' }}</el-descriptions-item>
        <el-descriptions-item label="页面岗位">{{ session.page_job_title || '—' }}</el-descriptions-item>
        <el-descriptions-item label="平台岗位 ID">{{ session.platform_job_id || '—' }}</el-descriptions-item>
        <el-descriptions-item label="可见卡片">{{ session.visible_cards }}</el-descriptions-item>
        <el-descriptions-item label="页面 URL" :span="1">{{ session.page_url || '—' }}</el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="session?.error_code" type="error" show-icon :closable="false"
                style="margin-top: 8px;"
                :title="`${session.error_code}: ${session.error_message || ''}`" />
      <el-space wrap style="margin-top: 10px;">
        <el-button size="small" type="primary" :loading="sessionLoading"
                   :disabled="!session || session.state === 'READY' || session.state === 'DISABLED'"
                   @click="connectBrowser">连接/召唤专用浏览器</el-button>
        <el-button size="small" type="success" :loading="sessionLoading"
                   :disabled="!store.activeJobId || !session || session.state === 'DISABLED'"
                   @click="openRecommendPage">打开/定位推荐页</el-button>
        <el-button size="small" :loading="sessionLoading" @click="loadSession">我已完成登录/验证，重新检测</el-button>
      </el-space>
    </el-card>

    <el-row :gutter="20">
      <el-col :span="10">
        <el-card shadow="never">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-weight: 600;">批次与页面预检</span>
              <el-space>
                <el-select v-model="batchId" size="small" style="width: 190px;"
                           placeholder="选择/历史批次" @change="selectBatch">
                  <el-option v-for="item in batches" :key="item.batch_id"
                             :label="`${item.batch_id.slice(0, 8)} · v${item.policy_version} · ${item.status}`"
                             :value="item.batch_id" />
                </el-select>
                <el-button size="small" type="primary" plain :loading="creatingBatch"
                           :disabled="!store.currentPolicy" @click="createBatch">新建批次</el-button>
              </el-space>
            </div>
          </template>

          <el-alert v-if="!store.currentPolicy" type="warning" show-icon :closable="false"
                    title="当前岗位尚未发布 Policy，无法创建批次。" />
          <template v-else>
            <el-descriptions :column="2" border size="small" style="margin-bottom: 12px;">
              <el-descriptions-item label="批次锁定 Policy">
                <el-tag type="success" size="small">
                  v{{ batch?.policy_version ?? (store.currentPolicy?.policy_version as number) }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="批次状态">{{ batch?.status || '未创建' }}</el-descriptions-item>
              <el-descriptions-item label="批次 ID" :span="2">{{ batch?.batch_id || '—' }}</el-descriptions-item>
            </el-descriptions>

            <el-divider content-position="left">页面岗位与筛选预检</el-divider>
            <el-space wrap style="margin-bottom: 8px;">
              <el-button size="small" :loading="precheckLoading" :disabled="!session?.page_url"
                         @click="runPrecheck">读取页面岗位与筛选</el-button>
              <el-button size="small" :disabled="!precheck" @click="runPrecheck">重新读取</el-button>
            </el-space>
            <el-alert v-if="precheckError" type="error" show-icon :closable="false"
                      style="margin-bottom: 8px;" :title="precheckError" />
            <template v-if="precheck">
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item label="页面岗位">{{ precheck.page_job_title || '—' }}</el-descriptions-item>
                <el-descriptions-item label="系统岗位">{{ precheck.job_title }}</el-descriptions-item>
                <el-descriptions-item label="平台岗位 ID">{{ precheck.platform_job_id || '—' }}</el-descriptions-item>
                <el-descriptions-item label="可见卡片">{{ precheck.visible_cards }}</el-descriptions-item>
                <el-descriptions-item label="筛选摘要" :span="2">{{ precheck.filter_summary || '—' }}</el-descriptions-item>
              </el-descriptions>
              <el-alert v-for="(warning, index) in precheck.warnings" :key="index"
                        type="warning" show-icon :closable="false" style="margin-top: 8px;"
                        :title="warning" />
              <el-checkbox v-model="precheckConfirmed" :disabled="precheck.job_mismatch"
                           style="margin-top: 10px;" @change="persistConfirmation">
                我已核对页面岗位与筛选条件，确认开始只读扫描
              </el-checkbox>
              <el-tag v-if="precheck.confirmed_at" type="success" size="small"
                      style="margin-left: 10px;">
                确认已持久化（F5/重启后保留）
              </el-tag>
            </template>
            <el-alert v-else type="info" show-icon :closable="false" style="margin-top: 8px;"
                      title="请先打开推荐页并读取预检结果；未完成预检时不能开始扫描。" />

            <el-divider content-position="left">扫描参数与模式</el-divider>
            <el-form label-position="top" size="small">
              <el-row :gutter="8">
                <el-col :span="8"><el-form-item label="最大页数">
                  <el-input-number v-model="scanPages" :min="1" :max="20" style="width: 100%;" />
                </el-form-item></el-col>
                <el-col :span="8"><el-form-item label="最大详情数">
                  <el-input-number v-model="maxDetails" :min="1" :max="200" style="width: 100%;" />
                </el-form-item></el-col>
                <el-col :span="8"><el-form-item label="最长运行(秒)">
                  <el-input-number v-model="maxDuration" :min="5" :max="3600" style="width: 100%;" />
                </el-form-item></el-col>
              </el-row>
              <el-form-item label="扫描完成后">
                <el-radio-group v-model="scanMode">
                  <el-radio value="scan_only">仅扫描入库</el-radio>
                  <el-radio value="auto_fast">自动执行 Fast 初筛</el-radio>
                </el-radio-group>
              </el-form-item>
            </el-form>

            <el-space wrap style="margin-bottom: 8px;">
              <el-button size="small" type="success" :loading="scanning"
                         :disabled="!canScan" @click="runBrowserScan">
                {{ scanning ? '扫描中…' : '开始只读扫描' }}
              </el-button>
              <el-button size="small" type="danger" plain :disabled="!scanning"
                         @click="cancelScan">停止扫描</el-button>
            </el-space>

            <div v-if="progress || scanStatus || scanReport" style="margin-bottom: 10px;">
              <el-progress :percentage="progressPercent"
                           :status="progressStatus" :stroke-width="12" />
              <el-descriptions :column="4" border size="small" style="margin-top: 8px;">
                <el-descriptions-item label="扫描状态">
                  <el-tag size="small">{{ scanStatus || progress?.status || '—' }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="阶段">{{ progress?.stage || scanStatus || '—' }}</el-descriptions-item>
                <el-descriptions-item label="已读页">{{ progress?.pages_read ?? '—' }}</el-descriptions-item>
                <el-descriptions-item label="列表/去重">
                  {{ progress ? `${progress.listed || 0} / ${progress.deduplicated || 0}` : '—' }}
                </el-descriptions-item>
                <el-descriptions-item label="导入/复用/失败">
                  {{ progress ? `${progress.imported || 0} / ${progress.reused || 0} / ${progress.failed || 0}` : '—' }}
                </el-descriptions-item>
                <el-descriptions-item label="详情完成/部分">
                  {{ progress ? `${progress.detail_completed || 0} / ${progress.detail_partial || 0}` : '—' }}
                </el-descriptions-item>
                <el-descriptions-item label="预筛跳过">{{ progress?.preview_skipped ?? '—' }}</el-descriptions-item>
                <el-descriptions-item label="终止原因">{{ progress?.stop_reason || scanReport?.stop_reason || '—' }}</el-descriptions-item>
                <el-descriptions-item label="数据源">{{ sourceLabel(scanReport?.source || session?.source) }}</el-descriptions-item>
              </el-descriptions>
              <el-alert v-for="(issue, index) in scanReport?.issues || []" :key="index"
                        type="warning" show-icon :closable="false" style="margin-top: 6px;"
                        :title="`${issue.code}: ${issue.message}`" />
              <el-alert v-if="scanErrorText" type="error" show-icon :closable="false"
                        style="margin-top: 6px;" :title="scanErrorText" />
              <el-space wrap v-if="scanErrorCode" style="margin-top: 8px;">
                <el-button size="small" @click="loadSession">重连/重新检测</el-button>
                <el-button size="small" @click="runPrecheck">重新预检</el-button>
                <el-button size="small" type="primary" :disabled="!canScan" @click="runBrowserScan">重试扫描</el-button>
              </el-space>
            </div>

            <el-collapse>
              <el-collapse-item title="手动导入单条候选人（备用）" name="manual">
                <el-form label-position="top" size="small">
                  <el-form-item label="候选人 Key">
                    <el-input v-model="candidateKey" placeholder="唯一稳定标识" />
                  </el-form-item>
                  <el-form-item label="标准简历模板">
                    <el-select v-model="templateKey" style="width: 100%;" @change="applyTemplate">
                      <el-option v-for="tpl in templates" :key="tpl.key"
                                 :label="tpl.label" :value="tpl.key" />
                    </el-select>
                  </el-form-item>
                  <el-form-item label="简历 JSON">
                    <el-input v-model="resumeJson" type="textarea" :rows="8" />
                  </el-form-item>
                  <el-button :loading="importing" :disabled="!batch" @click="importCandidate">
                    导入并封存
                  </el-button>
                </el-form>
              </el-collapse-item>
            </el-collapse>
          </template>
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card shadow="never" style="margin-bottom: 16px;">
          <template #header><span style="font-weight: 600;">候选人评分与运营</span></template>
          <el-space wrap>
            <el-button size="small" :loading="preparing" :disabled="!selectedCandidate"
                       @click="prepareSemantic">语义资产准备</el-button>
            <el-button size="small" type="warning" :loading="scoringFast"
                       :disabled="!selectedCandidate" @click="score('fast_mode')">Fast 初筛</el-button>
            <el-button size="small" type="danger" :loading="scoringDeep"
                       :disabled="!selectedCandidate" @click="score('deep_mode')">Deep 深评</el-button>
            <el-button size="small" type="success" plain :loading="batchScoring"
                       :disabled="!candidates.length" @click="scoreBatch('fast_mode')">
              批量 Fast{{ selectedCandidates.length ? ` (已选 ${selectedCandidates.length})` : '' }}
            </el-button>
            <el-button size="small" type="danger" plain :loading="batchScoring"
                       :disabled="!candidates.length" @click="scoreBatch('deep_mode')">
              批量 Deep{{ selectedCandidates.length ? ` (已选 ${selectedCandidates.length})` : '' }}
            </el-button>
            <el-tag v-if="task" :type="taskTagType">{{ task.status }}</el-tag>
          </el-space>
          <el-alert v-if="task?.error_type" type="error" show-icon :closable="false"
                    style="margin-top: 10px;"
                    :title="`${task.error_type}: ${task.error_message || ''}`" />
          <el-descriptions v-if="evaluation" :column="4" border size="small" style="margin-top: 12px;">
            <el-descriptions-item label="模式">{{ evaluation.scoring_mode }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusTagType(evaluation.scoring_status)" size="small">
                {{ evaluation.scoring_status }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="总分">{{ evaluation.total_score ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="路由">{{ evaluation.routing_suggestion || '—' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-weight: 600;">批次候选人（共 {{ candidateTotal }} 人）</span>
              <el-space>
                <el-button v-if="selectedCandidates.length" size="small" type="danger"
                           @click="deleteSelectedCandidates">
                  批量删除 (已选 {{ selectedCandidates.length }})
                </el-button>
                <el-input v-model="candidateKeyword" size="small" clearable
                          placeholder="搜索姓名 / candidate_key" style="width: 220px;"
                          @keyup.enter="searchCandidates" @clear="searchCandidates" />
                <el-button size="small" @click="searchCandidates">搜索</el-button>
                <el-tag v-if="selectedCandidate" size="small" type="info">
                  已选 {{ selectedCandidate.job_candidate.candidate_key }}
                </el-tag>
                <el-button size="small" @click="refreshCandidates">刷新</el-button>
              </el-space>
            </div>
          </template>
          <el-radio-group v-model="filterTab" size="small" style="margin-bottom: 10px;">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="unscored">未评分</el-radio-button>
            <el-radio-button value="fast">Fast</el-radio-button>
            <el-radio-button value="deep">Deep</el-radio-button>
            <el-radio-button value="review">待核验/失败</el-radio-button>
          </el-radio-group>
          <el-table ref="candidateTable" :data="candidates" size="small" border
                    highlight-current-row row-style="cursor: pointer;"
                    @row-click="selectCandidate" @selection-change="handleCandidateSelection"
                    @sort-change="handleCandidateSort">
            <el-table-column type="selection" width="44" />
            <el-table-column label="候选人" prop="candidate" sortable="custom" width="170">
              <template #default="{ row }">
                <div>{{ row.resume_summary?.name || row.job_candidate.candidate_key }}</div>
                <div style="font-size: 11px; color: #909399;">{{ row.job_candidate.candidate_key }}</div>
              </template>
            </el-table-column>
            <el-table-column label="学历" prop="degree" sortable="custom" width="90">
              <template #default="{ row }">{{ row.resume_summary?.degree || '—' }}</template>
            </el-table-column>
            <el-table-column label="年限" prop="work_years" sortable="custom" width="80">
              <template #default="{ row }">{{ row.resume_summary?.work_years || '—' }}</template>
            </el-table-column>
            <el-table-column label="期望薪资" prop="salary" sortable="custom" width="100">
              <template #default="{ row }">{{ row.resume_summary?.salary || '—' }}</template>
            </el-table-column>
            <el-table-column label="封存" prop="sealed" sortable="custom" width="80">
              <template #default="{ row }">
                <el-tag :type="row.sealed ? 'success' : 'info'" size="small">
                  {{ row.sealed ? 'Sealed' : '未封存' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最新状态" prop="status" sortable="custom" width="140">
              <template #default="{ row }">
                <el-tag v-if="row.latest_evaluation"
                        :type="statusTagType(row.latest_evaluation.scoring_status)" size="small">
                  {{ row.latest_evaluation.scoring_status }}
                </el-tag>
                <el-tag v-else type="info" size="small">未评分</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="总分" prop="score" sortable="custom" width="80">
              <template #default="{ row }">{{ row.latest_evaluation?.total_score ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="路由" prop="routing" sortable="custom" width="130">
              <template #default="{ row }">{{ row.latest_evaluation?.routing_suggestion || '—' }}</template>
            </el-table-column>
            <el-table-column label="Policy" prop="policy" sortable="custom" width="80">
              <template #default="{ row }">v{{ row.job_candidate.policy_version }}</template>
            </el-table-column>
            <el-table-column label="联系" width="100">
              <template #default="{ row }">
                <el-tag v-if="row.contact_summary?.contacted" type="success" size="small">
                  已联系 {{ row.contact_summary.contacted_by.join('、') }}
                </el-tag>
                <el-tag v-else type="info" size="small">未联系</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link type="primary"
                           @click.stop="openResume(row)">查看简历</el-button>
                <el-button size="small" link type="danger"
                           @click.stop="deleteCandidate(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination v-model:current-page="candidatePage"
                         v-model:page-size="candidatePageSize"
                         :page-sizes="[10, 20, 50]"
                         :total="candidateTotal"
                         layout="prev, pager, next, sizes, total"
                         style="margin-top: 10px; justify-content: flex-end;"
                         @current-change="refreshCandidates" />
        </el-card>
      </el-col>
    </el-row>

    <el-drawer v-model="resumeDrawerVisible" size="55%"
               :title="resumeDetail ? `简历详情：${resumeDetail.job_candidate.candidate_key}` : '简历详情'">
      <template v-if="resumeDetail">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="姓名">{{ resumeDetail.resume.name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="学历">{{ resumeDetail.resume.degree || '—' }}</el-descriptions-item>
          <el-descriptions-item label="年限">{{ resumeDetail.resume.work_years || '—' }}</el-descriptions-item>
          <el-descriptions-item label="期望">
            {{ expectationText(resumeDetail.resume.expectation) }}
          </el-descriptions-item>
          <el-descriptions-item label="完整度">{{ resumeDetail.completeness }}</el-descriptions-item>
          <el-descriptions-item label="封存">
            {{ resumeDetail.sealed ? '已封存' : '未封存' }}
          </el-descriptions-item>
        </el-descriptions>
        <el-alert v-if="resumeDetail.collection_errors?.length" type="error" show-icon
                  :closable="false" style="margin-top: 8px;"
                  :title="resumeDetail.collection_errors.join('；')" />
        <el-alert v-for="(issue, index) in resumeDetail.data_issues || []" :key="index"
                  type="warning" show-icon :closable="false" style="margin-top: 6px;"
                  :title="`${issue.path || ''}: ${issue.message || issue.kind || ''}`" />

        <el-divider content-position="left">个人优势</el-divider>
        <div>{{ resumeDetail.resume.advantage || '—' }}</div>
        <el-divider content-position="left">工作经历</el-divider>
        <el-table :data="resumeDetail.resume.work_experience || []" size="small" border>
          <el-table-column prop="company" label="公司" width="160" />
          <el-table-column prop="position" label="职位" width="140" />
          <el-table-column prop="responsibility" label="职责/证据" />
        </el-table>
        <el-divider content-position="left">项目经历</el-divider>
        <el-table :data="resumeDetail.resume.project_experience || []" size="small" border>
          <el-table-column prop="name" label="项目" width="180" />
          <el-table-column prop="role" label="角色" width="120" />
          <el-table-column prop="description" label="描述" />
        </el-table>
        <el-divider content-position="left">教育背景</el-divider>
        <el-table :data="resumeDetail.resume.education || []" size="small" border>
          <el-table-column prop="school" label="学校" width="200" />
          <el-table-column prop="degree" label="学历" width="120" />
          <el-table-column prop="major" label="专业" />
        </el-table>
        <el-divider content-position="left">技能与采集元数据</el-divider>
        <div>技能：{{ resumeDetail.resume.skills || '—' }}</div>
        <pre style="white-space: pre-wrap; font-size: 12px;">{{ JSON.stringify(resumeDetail.resume._meta || {}, null, 2) }}</pre>
        <el-space wrap style="margin-top: 12px;">
          <el-button size="small" :loading="reextracting" type="primary" plain
                     @click="reextractResume">
            重新提取详情并更新快照
          </el-button>
          <el-button size="small" :loading="preparing" @click="prepareSemantic">语义准备</el-button>
          <el-button size="small" type="warning" :loading="scoringFast" @click="score('fast_mode')">Fast 初筛</el-button>
          <el-button size="small" type="danger" :loading="scoringDeep" @click="score('deep_mode')">Deep 深评</el-button>
        </el-space>
      </template>
    </el-drawer>

    <el-dialog v-model="batchResultVisible" title="批量评分结果" width="50%;">
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item label="成功">{{ batchResult.success }}</el-descriptions-item>
        <el-descriptions-item label="跳过">{{ batchResult.skipped }}</el-descriptions-item>
        <el-descriptions-item label="失败">{{ batchResult.failed }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="batchResult.failures" size="small" border style="margin-top: 10px;">
        <el-table-column prop="candidate_key" label="候选人" width="200" />
        <el-table-column prop="reason" label="失败原因" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, ApiError } from '@/api'
import { captureScope, scopeValid } from '@/lib/requestGate'
import { useAppStore } from '@/stores/useAppStore'
import type {
  Batch, BrowserPrecheck, BrowserScanResult, BrowserSession, CandidateListItem,
  ImportResult, PipelineEvaluation, PipelineTask, ResumeSnapshotResponse,
  ScanProgress, ScoringMode
} from '@/types'

const store = useAppStore()
const creatingBatch = ref(false)
const importing = ref(false)
const preparing = ref(false)
const scanning = ref(false)
const scoringFast = ref(false)
const scoringDeep = ref(false)
const batchScoring = ref(false)
const batch = ref<Batch | null>(null)
const batches = ref<Batch[]>([])
const batchId = ref('')
const imported = ref<ImportResult | null>(null)
const selectedCandidate = ref<CandidateListItem | null>(null)
const candidates = ref<CandidateListItem[]>([])
const candidateKey = ref('cand-demo-01')
const templateKey = ref('xujianing')
const resumeJson = ref('')
const task = ref<PipelineTask | null>(null)
const evaluation = ref<PipelineEvaluation | null>(null)
const session = ref<BrowserSession | null>(null)
const sessionLoading = ref(false)
const precheck = ref<BrowserPrecheck | null>(null)
const precheckLoading = ref(false)
const precheckConfirmed = ref(false)
const precheckError = ref('')
const scanPages = ref(2)
const maxDetails = ref(20)
const maxDuration = ref(300)
const scanMode = ref<'scan_only' | 'auto_fast'>('scan_only')
const scanReport = ref<BrowserScanResult | null>(null)
const scanStatus = ref('')
const scanErrorCode = ref('')
const scanErrorText = ref('')
const progress = ref<ScanProgress | null>(null)
const progressTimer = ref<number | null>(null)
const filterTab = ref('all')
const candidatePage = ref(1)
const candidatePageSize = ref(10)
const candidateTotal = ref(0)
const candidateKeyword = ref('')
const selectedCandidates = ref<CandidateListItem[]>([])
const candidateTable = ref<any>(null)
const candidateSortBy = ref('')
const candidateSortOrder = ref<'asc' | 'desc'>('asc')
const reextracting = ref(false)
const resumeDrawerVisible = ref(false)
const resumeDetail = ref<ResumeSnapshotResponse | null>(null)
const batchResultVisible = ref(false)
const batchResult = ref<{ success: number; skipped: number; failed: number;
  failures: Array<{ candidate_key: string; reason: string }> }>({
  success: 0, skipped: 0, failed: 0, failures: []
})

const xujianingResume = {
  "name": "许嘉宁",
  "age": "24岁",
  "degree": "硕士研究生",
  "work_years": "0.3年",
  "job_status": "在校-可实习",
  "active_status": "刚刚活跃",
  "expectation": {
    "position": "AI自动化开发专员",
    "city": "深圳",
    "salary": "9-11K"
  },
  "advantage": "计算机硕士在读，曾于美团参与Agent工程，负责点位评估、知识检索和多Agent提取，实践GeoHash、Elasticsearch和Milvus。参与Overleaf Copilot开发，设计多文件修改、编译自修复、Tracing及约400题评测集。400题×3 trials回归中，单轮pass@1从84.8%升至97.5%，多轮从73.4%升至94%，总体pass³从60.5%升至84.2%。",
  "skills": "Agent工程：LLM推理、Tool Calling、多Agent调度、跨项目RAG/知识检索、向量化检索、Tracing、工具调用与工作流控制。后端与数据：Agent相关业务接口开发、RPC依赖关系分析、GeoHash、Elasticsearch空间查询、Milvus、结构化知识、并发任务协调、CompletableFuture。评测与改进：多轮Agent任务、LaTeX编译与日志错误分析、自动修复循环、约400题评测集、400题×3 trials、pass@1及pass³指标、失败案例分析。其他：了解工业安全数据流转、工控蜜罐与高并发日志处理的业务场景。",
  "work_experience": [
    {
      "company": "美团",
      "position": "后端开发实习生（业务研发平台/外卖技术部）",
      "job_type": "实习",
      "start": "2026.05",
      "end": "2026.08",
      "responsibility": "深度参与商品运营场景的后端Agent工程建设，负责袋鼠参谋Agent点位评估、跨项目知识检索、多Agent知识提取及业务后端链路开发，覆盖Agent工具调用、知识检索、工作流设计与后端并发优化等方向。针对袋鼠参谋Agent开店选址点位评估场景，扩展POI空间检索能力：在已有Agent意图链路上，将用户的POI、半径等信息转化为结构化查询条件，通过GeoHash编码结合半径构建ES空间查询；针对POI无法解析场景增加Agent缺失检测与澄清兜底，解决原有AOI名称查询无法覆盖具体点位及异常输入的问题。构建面向Agent编码的跨项目知识检索Skill：解析RPC等服务调用关系生成项目依赖拓扑，并为项目沉淀结构化服务知识、向量化写入Milvus；Agent根据当前项目AppKey检索关联服务，通过多路召回获取上下游调用关系与业务上下文，为跨项目代码编写提供相关服务知识。构建多Agent商品知识提取Pipeline，挖掘“什么样的品能卖得好”：基于模型选品与BD实际选品差异，结合商品、商家及城市×蜂窝等多维属性，针对LLM易产生弱规律、过拟合的问题，引入多视角Agent投票+留出集验证+时空约束，将非结构化分析结果转化为可追溯、带置信度与时效的结构化知识。独立完成BD工作台后端从0到1建设：实现混排推荐、反馈落库等核心接口；针对多门店场景下10个上游RPC串行聚合导致接口高耗时的问题，采用自定义线程池+CompletableFuture并行扇出，并统一超时预算与字段级优雅降级，主链路耗时由上游耗时累加降低至最长单上游耗时量级。",
      "keywords": [
        "Agent",
        "GeoHash",
        "Elasticsearch",
        "Milvus",
        "RAG",
        "多Agent",
        "CompletableFuture",
        "后端开发"
      ]
    }
  ],
  "project_experience": [
    {
      "name": "2026年OSPP中科院开源之夏：Overleaf LaTeX协作平台Copilot助手",
      "role": "Agent开发",
      "start": "2025.09",
      "end": "2026.04",
      "description": "项目描述：面向在线LaTeX平台Overleaf开发Copilot Agent，围绕论文编辑场景构建工具调用、上下文管理、编译验证与错误自修复，并构建评测数据集，结合Tracing和失败案例分析，系统迭代优化Agent。设计面向Overleaf的Coding Agent，实现多文件LaTeX项目的自主修改与验证：基于LLM推理与Tool Calling实现论文文件读取、修改和编译，并设计Agent工作流管理多步任务执行，使Agent能够根据项目上下文完成跨文件修改。针对代码修改后无法直接判断结果是否正确的问题，构建“修改—编译—错误诊断—修复—再编译”的自主修复流程；将LaTeX编译结果与错误日志作为Agent后续决策依据，通过状态管理、重试与终止策略控制多轮执行，使Agent能够根据编译反馈定位并修复错误。构建Agent执行过程的结构化Tracing，统一记录LLM调用、工具调用、文件修改、编译结果及状态转移，将一次Agent任务串联为完整Trace，用于定位失败步骤、分析错误类型并支持后续策略优化。构建约400题Agent评测数据集，围绕编译修复、结构修改、语义修改和无需修改四类任务，覆盖三档难度及多轮交互；根据Agent实际运行中的失败案例持续补充数据，形成用于回归测试和能力评估的固定基准。基于评测数据集与Trace失败案例持续迭代Prompt、工作流、工具设计及错误处理策略，通过400题×3 trials回归评测，单轮对话pass@1从84.8%提升至97.5%，多轮对话pass@1从73.4%提升至94%，总体pass³从60.5%提升至84.2%。"
    },
    {
      "name": "工控蜜罐态势感知平台",
      "role": "项目开发",
      "start": "2024.10",
      "end": "2025.06",
      "description": "项目描述：国家重点研发计划支撑的分布式工控蜜罐基座。针对高并发攻击与海量日志场景，支持工控协议主动探测、重构高可用数据削峰、准实时溯源检索及离线态势感知体系，保障安全数据秒级流转与全局策略高效下发。"
    }
  ],
  "education": [
    {
      "school": "华中科技大学",
      "major": "计算机科学与技术",
      "degree": "硕士研究生",
      "start": "2024.09",
      "end": "2027.07",
      "school_tags": [
        "全日制硕士",
        "GPA 3.62",
        "专业前15%"
      ]
    },
    {
      "school": "武汉科技大学",
      "major": "计算机科学与技术",
      "degree": "本科",
      "start": "2020.09",
      "end": "2024.06",
      "school_tags": [
        "统招本科",
        "GPA 3.61",
        "专业前30%"
      ]
    }
  ],
  "certifications": []
}

const linhaoranResume = {
  "name": "林昊然",
  "age": "21岁",
  "degree": "本科",
  "work_years": "0年",
  "job_status": "在校-可实习",
  "active_status": "刚刚活跃",
  "expectation": {
    "position": "AI自动化开发专员",
    "city": "深圳",
    "salary": "9-11K"
  },
  "advantage": "华工软件工程本科在读，兼具Java后端与Agent开发经验。基于FastAPI、Redis、Chroma和Ollama构建学生支持Agent，设计Runtime Harness、版本化Memory、Hybrid RAG和MCP受控动作。36条冻结集Recall@4 100%、MRR 0.876；输入减少13.54%。另有优惠券项目经验，熟悉Redis Lua、RocketMQ和Redisson。",
  "skills": "Java基础：熟悉集合、泛型、异常、IO与JUC；掌握线程池、锁、CAS/AQS，理解JVM内存结构与GC。后端框架：熟悉Spring Boot、MyBatis-Plus；理解IoC、AOP、事务与RESTful分层设计。数据库：熟悉MySQL索引、事务隔离、MVCC与锁；能使用EXPLAIN进行SQL、表结构优化。Redis：熟悉常用数据结构、持久化；具备缓存穿透/击穿/雪崩治理、Lua原子操作、分布式锁与缓存一致性实践。消息队列：具备RocketMQ异步解耦、削峰、延迟消息、消费幂等、失败重试与死信处理实践。工程能力：Git、Maven、Docker、Apifox、JMeter。AI应用：Python、FastAPI、SQLAlchemy、MySQL、Redis、Chroma、Ollama、BM25、Hybrid RAG、MCP、Runtime Harness、Trace。",
  "work_experience": [],
  "project_experience": [
    {
      "name": "高并发优惠券分发与核销系统",
      "role": "后端开发",
      "start": "2026.03",
      "end": "2026.06",
      "description": "基于Spring Cloud Alibaba构建优惠券分发与核销系统，以Nacos实现服务注册发现、Gateway统一路由；按商家、模板、用户与结算拆分服务，覆盖建券、兑换、批量发券、核销与退款。技术栈：Spring Boot 3、Spring Cloud Alibaba（Nacos）、Spring Cloud Gateway、MySQL、Redis、Redisson、Lua、RocketMQ、ShardingSphere、EasyExcel、XXL-Job。热点券兑换链路设计：基于Redis Lua原子完成库存校验、单用户领取次数校验与预扣库存；通过RocketMQ异步创建用户券，结合MySQL条件扣减、唯一索引与消费幂等控制，降低高并发下超发与重复写入风险。实现批量发券链路：EasyExcel流式解析名单，待发用户暂存Redis Set并分批投递RocketMQ；消费者批量落券，配合任务状态、Redis幂等、XXL-Job扫描和失败记录，降低重复投递及大文件对同步接口的影响。实现用户券结算与状态流转：通过Redis ZSet与Pipeline批量读取用户券索引和模板缓存，结合CompletableFuture计算优惠金额；以Redisson锁、数据库条件更新保障锁券、核销与退款并发正确性，并用延迟消息处理过期。按建券与查券主访问路径，使用ShardingSphere自定义Hash分片，模板按shop_number、用户券按user_id分库分表；创建后预热缓存，结合BloomFilter、空值缓存和分布式锁双检治理穿透、击穿，并以延迟消息更新模板结算状态。完成JMeter性能压测与全链路验证：结算查询8万次、约7600QPS、错误率0%、P99 2ms；异步领券4万次、约1838QPS、HTTP错误率0%；完成5000用户批量发券，核验MQ消费和用户券最终落库。"
    },
    {
      "name": "SCUT Campus Compass——华工学生支持与个人发展Agent",
      "role": "Agent应用开发",
      "start": "2026.05",
      "end": "至今",
      "description": "基于FastAPI、MySQL、Redis、Chroma与Ollama构建面向华工学生的支持型Agent，以公开可核验资料覆盖科研、学业、生活与活动场景；提供可追溯、可复现评测的Agent链路。技术栈：Python、FastAPI、SQLAlchemy、MySQL、Redis、Chroma、Ollama、BM25、Hybrid RAG、MCP。Runtime Harness与Blackboard协作：设计单次请求级Runtime Harness，串联会话、Agent Runtime、Trace与后置动作；以追加式Blackboard支持有界、顺序的多Agent任务认领与artifact聚合，拆分领域路由、风险判断、上下文装配和安全审查。版本化上下文Memory：Redis滑动窗口+LLM memory brief管理短期上下文；MySQL MemorySnapshot技术覆盖消息增量压缩、支持事实确认/撤销、作用域与级联删除。冻结回归中输入估算减少13.54%、恢复3/3。华工领域Hybrid RAG：基于BM25关键词检索与Chroma向量召回构建双路检索，经加权融合与启发式重排；为公开资料维护URL、版本hash、复核时间及ACTIVE/STALE状态，使回答可关联来源与资料状态。36条冻结集Recall@4 100%、MRR 0.876、本机检索P95 260ms。本地模型路由与可靠性：接入本地qwen2.5:7b，通过ModelRouter统一意图识别、风险判断、上下文摘要与回复生成等调用；实现三态熔断与SSE流式安全降级，首token输出后发生异常时不跨模型拼接回复，9/9组确定性故障注入通过。MCP与受控异步动作：将“支持交接请求”封装为受控MCP工具，用户显式发起并确认后创建持久化异步任务；通过策略校验、幂等控制、租约超时接管、失败重试与人工恢复保障任务可控。Trace与Engineering Harness：建立ConversationRun级跳转Trace、关联领域/风险决策、MemorySnapshot、RetrievalRun、ModelAttempt与ActionTask；Engineering Harness覆盖Mock回归、故障注入和本地HTTP E2E，并固化Git SHA、依赖/配置/数据集哈希及失败样例。"
    }
  ],
  "education": [
    {
      "school": "华南理工大学",
      "major": "软件工程",
      "degree": "本科",
      "start": "2024.09",
      "end": "2028.06",
      "school_tags": [
        "统招本科",
        "985",
        "211"
      ]
    }
  ],
  "certifications": [
    "大学英语四级",
    "大学英语六级",
    "校二等奖学金"
  ]
}

const zhouchengyuResume = {
  "name": "周承宇",
  "age": "22岁",
  "degree": "硕士研究生",
  "work_years": "0年",
  "job_status": "应届生-周内到岗",
  "active_status": "刚刚活跃",
  "expectation": {
    "position": "AI自动化开发专员",
    "city": "深圳",
    "salary": "9-11K"
  },
  "advantage": "长江大学人工智能硕士在读，求职Java后端实习。熟悉Spring Boot、Spring Cloud、MySQL、Redis、RabbitMQ和Elasticsearch。LocalLife项目完成9个控制器、30余个RESTful接口，以Redis Lua、Redisson和RabbitMQ实现秒杀；电商项目中实践Gateway鉴权、Seata事务、Sentinel限流。暂无Agent项目证据。",
  "skills": "熟悉Java基础语法、面向对象、集合框架、异常处理、泛型及Stream API；了解多线程、线程池、ThreadLocal和常用锁机制。熟悉Spring Boot、Spring MVC等开发框架，理解Spring IoC、AOP、Bean生命周期及事务传播机制；能够使用MyBatis-Plus完成数据持久化，具备Spring Security、JWT鉴权及角色权限控制实践。熟悉Spring Cloud Alibaba微服务体系，具备Nacos服务注册与配置管理、OpenFeign远程调用、Gateway统一路由及用户信息透传实践；了解Sentinel限流降级和Seata XA分布式事务的使用。熟悉MySQL、SQL、索引、事务和常见数据库设计方法；熟悉Redis常用数据结构、持久化与缓存策略，了解Redisson分布式锁及Lua原子操作。具备RabbitMQ实际使用经验，能够通过消息队列实现业务异步解耦和订单延迟处理；能够使用Elasticsearch完成商品数据索引和检索。熟悉RESTful API和常见后端分层架构；熟悉Git、Maven、JUnit、Mockito、Flyway、Docker Compose和常用Linux命令；通过CET-6。",
  "work_experience": [],
  "project_experience": [
    {
      "name": "LocalLife——本地商户发现与社交点评平台",
      "role": "后端开发",
      "start": "2026.03",
      "end": "2026.05",
      "description": "技术栈：Spring Boot、MyBatis-Plus、MySQL、Redis、Redisson、RabbitMQ、Lua。项目简介：面向本地生活消费场景设计的商户发现与内容互动平台，为用户提供验证码登录、附近商户查询、探店内容发布、关注与点赞、连续签到及限时优惠券抢购等功能。系统围绕热点数据访问和秒杀流量突增场景，引入Redis缓存、Lua原子校验、分布式锁及消息队列，降低数据库压力并提升请求处理能力。负责用户、商户、探店内容、社交关系及优惠券订单等核心模块的后端设计，完成9个业务控制器和30余个RESTful接口。基于Redis Hash构建无状态登录体系，以随机Token作为身份凭证，通过双层拦截器完成登录校验、用户上下文传递及登录有效期续期，支持服务多实例共享登录状态。封装通用缓存访问组件，采用Cache Aside保证商铺数据更新后的缓存一致性，并通过缓存空值和逻辑过期异步重建分别缓解缓存穿透与热点Key查询问题。设计优惠券异步秒杀链路，利用Lua脚本原子完成库存校验、库存预扣和重复购买判断，将数据库写入操作交由RabbitMQ异步处理，缩短秒杀接口同步执行链路。基于时间戳与Redis自增序列实现分布式订单ID；通过Redisson用户粒度分布式锁、数据库条件更新等机制控制重复下单与库存超卖风险。使用Redis GEO实现指定范围内商户的距离排序与分页查询，避免在关系型数据库中执行复杂的地理距离计算。基于Redis ZSet实现点赞用户排行和推模式Feed流，通过时间戳游标与偏移量完成关注动态的滚动分页。使用Redis Set维护用户关注关系并通过集合交集查询共同关注；使用Bitmap和BitField实现低存储成本的月度签到及连续签到统计。"
    },
    {
      "name": "优选购-分布式电商交易平台",
      "role": "后端开发",
      "start": "2026.05",
      "end": "2026.08",
      "description": "技术栈：Spring Boot、Spring Cloud Alibaba、Nacos、OpenFeign、Gateway、Sentinel、Seata、RabbitMQ、Elasticsearch、MySQL、MyBatis-Plus、Docker。项目简介：面向在线零售场景开发的微服务电商交易平台，围绕用户、商品、购物车、订单及支付等核心业务进行服务拆分，实现商品检索、购物车管理、订单创建、库存扣减、余额支付和订单超时处理等功能，并针对跨服务事务、异步消息和服务异常等问题进行了专项设计。按用户、商品、购物车、交易和支付等业务领域拆分微服务，使用Nacos实现服务注册与发现，基于OpenFeign完成服务间调用。使用Spring Cloud Gateway作为系统统一入口，结合JWT实现登录认证、用户信息透传和接口权限校验，并增加用户数据归属与内部接口访问控制。基于Seata XA模式保证创建订单、扣减库存和扣减余额等跨服务操作的事务一致性，当任意环节发生异常时统一回滚。使用RabbitMQ解耦核心交易流程，实现商品售出事件异步处理和订单延迟关单，并配置消费重试及异常消息处理策略。使用Elasticsearch建立商品搜索索引，完成约8.8万条商品数据初始化，将复杂商品检索从关系型数据库中拆分出来。基于Sentinel实现网关路由级流量控制，并为Feign调用配置降级策略；对扣库存、扣余额等写操作采用快速失败，防止静默降级产生业务脏数据。使用Docker在Linux虚拟机中部署MySQL、Nacos、Seata、RabbitMQ和Elasticsearch，完成本地微服务运行环境搭建。"
    }
  ],
  "education": [
    {
      "school": "长江大学",
      "major": "人工智能",
      "degree": "硕士研究生",
      "start": "2024.09",
      "end": "2027.06",
      "school_tags": [
        "全日制硕士"
      ]
    },
    {
      "school": "长江大学",
      "major": "网络工程",
      "degree": "本科",
      "start": "2020.09",
      "end": "2024.06",
      "school_tags": [
        "统招本科"
      ]
    }
  ],
  "certifications": [
    "大学英语六级"
  ]
}

const lichengzeResume = {
  "name": "李承泽",
  "age": "23岁",
  "degree": "本科",
  "work_years": "0.4年",
  "job_status": "应届生-随时到岗",
  "active_status": "刚刚活跃",
  "expectation": {
    "position": "AI自动化开发专员",
    "city": "深圳",
    "salary": "9-11K"
  },
  "advantage": "杭州师范大学电子信息工程本科毕业，熟悉STM32底层驱动、PWM、ADC DMA、SPI及实时系统。曾任硬件研发助理工程师，参与200余件组装维修、RV1106 Linux平台调试、PLC异常排查和PCB改版。独立完成FOC电机控制系统，包含20kHz电流环、双ADC采样与位置环；示波器实测ISR耗时33μs，角度分辨率0.022°。暂无大模型或Agent项目经验。",
  "skills": "具备从寄存器到应用层的完整嵌入式开发能力；STM32 HAL、PWM、ADC DMA、SPI、编码器、定时器、中断、C/C++、μC/OS实时系统、SBUS通信协议；熟悉示波器波形分析和软硬件联合调试。电机控制：FOC、SVPWM、Clarke/Park变换、PID、CMSIS-DSP。硬件：PCB布局改进、航空插头接口、Linux固件配置、PLC信号排障、驻场测试。熟练使用AI编程工具提效，能够独立完成从方案验证到系统调试的全流程。",
  "work_experience": [
    {
      "company": "杭州云意品质智能科技有限责任公司",
      "position": "硬件研发部-助理工程师",
      "job_type": "实习",
      "start": "2026.01",
      "end": "2026.05",
      "responsibility": "负责产品样机焊接与模块调试，完成200+件组装及板卡维修，更换RV1106主控等器件十余块，协助功能验证。负责RV1106 Linux平台固件烧录与配置，手动管理进程并进行测试，排查听筒与主板通信异常。外派至常州甲方现场，排查PLC信号触发异常，定位并解决产品OK/NG信号输出问题。外派至市内合作加工商驻场2周，跟进2款产品试产，监督生产进度与工艺质量。根据硬件工程师及试产反馈改进PCB布局：分立LED灯珠整合为独立小板（排线连接主板），航空插头独立为PCB模块（连接件连接主板），减少工序、提升效率；独立绘制听筒主板STM32烧录测试二合一工装PCB。",
      "keywords": [
        "STM32",
        "RV1106",
        "Linux",
        "PLC",
        "PCB",
        "硬件调试",
        "工装设计"
      ]
    }
  ],
  "project_experience": [
    {
      "name": "基于STM32G431的PMSM磁场定向控制（FOC）系统",
      "role": "独立开发-毕设",
      "start": "2026.03",
      "end": "2026.05",
      "description": "技术栈：STM32 HAL、FOC、SVPWM、PID、DMA、CMSIS-DSP、SPI、ADC。独立完成从底层驱动到应用层的完整FOC电机控制系统，支持5种运行模式（开环/电流/速度/位置闭环/阻尼模式），实现三级串级PID控制架构，运行于170MHz STM32G431CBU6（Cortex-M4F）平台。独立完成TIM1六路互补PWM（20kHz中心对齐）、ADC1/ADC2 DMA循环采样、SPI磁编码器等底层驱动开发，具备HAL库及寄存器级调试能力。设计ADC1（TIM1触发20kHz）+ADC2（TIM6触发1kHz）双ADC并行采样架构，采用DMA循环缓冲实现零CPU开销电流/电压采集，配合IIR低通滤波将采样噪声从±0.2A降至±0.05A。在20kHz PWM（50μs周期）硬实时约束下，电流环单次ISR内完成AS5048A编码器读取→Clarke/Park变换→双PI闭环→SVPWM调制全流程；示波器实测耗时33μs（ADC 6μs + SPI 10μs + 算法16μs），ISR CPU占用66%；完成编码器零点自动校准，角度分辨率0.022°。设计三级串级PID控制架构（电流环20kHz→速度环1kHz→位置环100Hz），速度/位置环以独立定时器ISR运行，计算负载可忽略；实现条件积分抗饱和、微分IIR低通滤波、setpoint加权，独立完成全链路参数整定；配合过流/过压/欠压/编码器异常检测与消抖，500ms自动恢复。采用应用层/控制层/驱动层/HAL层四层模块化架构，接口清晰，所有模块配备Doxygen风格注释及系统时序图文档。"
    }
  ],
  "education": [
    {
      "school": "杭州师范大学",
      "major": "电子信息工程",
      "degree": "本科",
      "start": "2021.09",
      "end": "2026.06",
      "school_tags": [
        "统招本科"
      ]
    }
  ],
  "certifications": [
    "2023年全国大学生电子设计竞赛省三等奖（电源类）"
  ]
}

const templates = [
  { key: 'xujianing', label: '05 许嘉宁 · 美团 Agent', value: xujianingResume },
  { key: 'linhaoran', label: '02 林昊然 · 华工 Agent', value: linhaoranResume },
  { key: 'zhouchengyu', label: '03 周承宇 · Java 后端', value: zhouchengyuResume },
  { key: 'lichengze', label: '06 李承泽 · 嵌入式', value: lichengzeResume }
]

const sessionTagType = computed(() => {
  const state = session.value?.state
  if (state === 'READY') return 'success'
  if (state === 'DISABLED' || state === 'REAL_CONTRACT_UNVERIFIED') return 'danger'
  return 'warning'
})
const canScan = computed(() => Boolean(
  batch.value && session.value?.state === 'READY' && precheck.value
  && !precheck.value.job_mismatch && precheckConfirmed.value && !scanning.value))
const tabParam = computed(() => (
  filterTab.value === 'review' ? 'needs_review_or_failed'
    : filterTab.value === 'all' ? undefined : filterTab.value))
const progressPercent = computed(() => {
  if (progress.value?.status === 'COMPLETED') return 100
  if (progress.value?.status === 'COMPLETED_WITH_ERRORS'
      || progress.value?.status === 'FAILED'
      || progress.value?.status === 'CANCELLED') return 100
  const pagesRead = progress.value?.pages_read || 0
  return Math.min(95, Math.round((pagesRead / Math.max(1, scanPages.value)) * 100))
})
const progressStatus = computed(() => {
  if (progress.value?.status === 'FAILED') return 'exception'
  if (progress.value?.status === 'COMPLETED') return 'success'
  return undefined
})
const taskTagType = computed(() => {
  if (!task.value) return 'info'
  if (task.value.status === 'SUCCEEDED') return 'success'
  if (task.value.status === 'FAILED') return 'danger'
  return 'warning'
})

watch(filterTab, () => {
  candidatePage.value = 1
  void refreshCandidates()
})
watch(candidatePageSize, () => {
  candidatePage.value = 1
  void refreshCandidates()
})

function applyTemplate() {
  const template = templates.find((item) => item.key === templateKey.value)
  if (template) resumeJson.value = JSON.stringify(template.value, null, 2)
}
function sourceLabel(source?: string | null) {
  if (source === 'boss_readonly') return 'BOSS_READONLY（未核验）'
  if (source === 'cdp_mock') return 'CDP_MOCK'
  if (source === 'mock') return 'MOCK'
  return source || '—'
}
function statusTagType(status: string) {
  if (status === 'DEEP_SCORED') return 'success'
  if (status === 'HARD_FILTERED') return 'danger'
  if (status === 'COARSE_ONLY' || status === 'NEEDS_REVIEW') return 'warning'
  if (status === 'TECHNICAL_FAILED' || status === 'DATA_INCOMPLETE') return 'danger'
  return 'info'
}
function expectationText(value: any) {
  if (!value) return '—'
  if (typeof value === 'string') return value
  return [value.city, value.salary, value.position].filter(Boolean).join(' / ') || '—'
}

function errorText(error: unknown) {
  const apiError = error as ApiError
  const code = apiError.state || apiError.errorType
  const map: Record<string, string> = {
    AUTH_REQUIRED: '需要人工在 BOSS 页面登录；完成后点击“重新检测”。',
    CAPTCHA_BLOCKED: '页面出现安全验证/滑块，请人工完成；系统不会绕过。',
    AUTH_OR_ANTI_BOT_BLOCKED: '平台拒绝或触发风控，请人工处理后再试。',
    REAL_CONTRACT_UNVERIFIED: '真实 BOSS 契约未核验，采集已硬禁用；请使用 MOCK/CDP_MOCK。',
    EMPTY_UNVERIFIED: '没有读到候选人，空页面不能视为成功；请检查岗位/筛选或重试。',
    BROWSER_CLOSED: '浏览器连接已断开，请重新连接。',
    SCAN_ALREADY_RUNNING: '该批次已有扫描在运行，请等待或先停止。',
    CANCELLED: '扫描已取消，已入库候选人保留。',
    MAX_DURATION: '扫描达到时间上限，已保留已处理结果。',
    FILTERS_UNCONFIRMED: '请先完成页面岗位/筛选预检并勾选确认。'
  }
  return code && map[code] ? `${code}: ${map[code]}` : (apiError.message || String(error))
}

async function loadSession() {
  sessionLoading.value = true
  try {
    session.value = await api.browserSession()
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    sessionLoading.value = false
  }
}

async function connectBrowser() {
  sessionLoading.value = true
  try {
    session.value = await api.browserSessionConnect(store.activeJobId || undefined)
    ElMessage.success('浏览器会话已连接')
    await loadSession()
  } catch (error) {
    ElMessage.error(errorText(error))
    await loadSession()
  } finally {
    sessionLoading.value = false
  }
}

async function openRecommendPage() {
  if (!store.activeJobId) return
  sessionLoading.value = true
  precheck.value = null
  precheckConfirmed.value = false
  try {
    session.value = await api.browserSessionNavigate(store.activeJobId)
    ElMessage.success('已定位推荐页（Mock/受控页面）')
    await runPrecheck()
  } catch (error) {
    ElMessage.error(errorText(error))
    await loadSession()
  } finally {
    sessionLoading.value = false
  }
}

async function runPrecheck() {
  if (!store.activeJobId) return
  precheckLoading.value = true
  precheckError.value = ''
  precheckConfirmed.value = false
  try {
    precheck.value = await api.browserPrecheck(store.activeJobId,
                                               batchId.value || undefined)
    if (precheck.value.job_mismatch) {
      precheckError.value = precheck.value.warnings.join('；')
    } else if (precheck.value.confirmed) {
      precheckConfirmed.value = true
    }
  } catch (error) {
    precheck.value = null
    precheckError.value = errorText(error)
  } finally {
    precheckLoading.value = false
  }
}

function restoredPrecheck(item: Batch): BrowserPrecheck {
  let summary = ''
  try {
    const parsed = JSON.parse(item.filter_summary_json || '[]')
    summary = typeof parsed === 'string' ? parsed : (parsed.summary || '')
  } catch {
    summary = ''
  }
  return {
    job_id: item.job_id,
    job_title: '',
    page_job_id: item.job_id,
    page_job_title: '',
    platform_job_id: item.platform_job_id || '',
    filter_summary: summary,
    visible_cards: 0,
    iframe_detected: true,
    job_mismatch: false,
    warnings: [],
    checked_at: item.filters_confirmed_at || '',
    source: session.value?.source || '',
    batch_id: item.batch_id,
    confirmed: true,
    confirmed_at: item.filters_confirmed_at,
    confirmed_by: item.confirmed_by
  }
}

async function persistConfirmation(value: unknown) {
  if (!value || !store.activeJobId || !batchId.value || !precheck.value) return
  const jobId = store.activeJobId
  try {
    const saved = await api.browserConfirmPrecheck(jobId, batchId.value, {
      platform_job_id: precheck.value.platform_job_id || '',
      filter_summary: precheck.value.filter_summary || '',
      visible_cards: precheck.value.visible_cards || 0
    })
    if (store.activeJobId !== jobId) return
    batch.value = saved
    precheck.value = {
      ...precheck.value,
      confirmed: true,
      confirmed_at: saved.filters_confirmed_at,
      confirmed_by: saved.confirmed_by
    }
    batches.value = batches.value.map((item) => (
      item.batch_id === saved.batch_id ? saved : item))
    ElMessage.success('预检确认已保存到批次（F5/重启后保留）')
  } catch (error) {
    precheckConfirmed.value = false
    ElMessage.error(errorText(error))
  }
}

async function loadBatches() {
  if (!store.activeJobId) return
  const jobId = store.activeJobId
  try {
    const result = await api.listBatches(jobId)
    if (store.activeJobId !== jobId) return
    batches.value = result.batches
    if (!batchId.value || !batches.value.some((item) => item.batch_id === batchId.value)) {
      batchId.value = batches.value[0]?.batch_id || ''
    }
    if (batchId.value) await selectBatch(batchId.value)
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function selectBatch(id: string) {
  const selected = batches.value.find((item) => item.batch_id === id) || null
  batch.value = selected
  batchId.value = id
  const persisted = Boolean(selected?.filters_confirmed_at)
  precheckConfirmed.value = persisted
  precheck.value = selected && persisted ? restoredPrecheck(selected) : null
  precheckError.value = ''
  scanReport.value = null
  scanStatus.value = ''
  scanErrorCode.value = ''
  scanErrorText.value = ''
  progress.value = null
  candidatePage.value = 1
  await Promise.all([loadScanStatus(), refreshCandidates()])
}

async function createBatch() {
  if (!store.activeJobId) return
  const scope = captureScope(store.activeJobId, store.dataEpoch)
  creatingBatch.value = true
  try {
    const created = await api.createBatch(scope.jobId)
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    batch.value = created
    batchId.value = created.batch_id
    precheck.value = null
    precheckConfirmed.value = false
    await loadBatches()
    ElMessage.success('批次已创建（Policy 版本已锁定）')
  } catch (error) {
    if (scopeValid(scope, store.activeJobId, store.dataEpoch)) {
      ElMessage.error(errorText(error))
    }
  } finally {
    creatingBatch.value = false
  }
}

async function loadScanStatus() {
  if (!store.activeJobId || !batchId.value) return
  const jobId = store.activeJobId
  const currentBatch = batchId.value
  try {
    const status = await api.browserScanStatus(jobId, currentBatch)
    if (store.activeJobId !== jobId || batchId.value !== currentBatch) return
    scanStatus.value = status.status
    scanReport.value = status.result
    progress.value = status.progress || progress.value
    if (status.error?.code) {
      scanErrorCode.value = status.error.code
      scanErrorText.value = status.error.message || status.error.code
    }
  } catch (error) {
    if (!(error instanceof ApiError && error.status === 404)) {
      scanErrorText.value = errorText(error)
    }
  }
}

function startProgressPolling() {
  stopProgressPolling()
  progressTimer.value = window.setInterval(async () => {
    if (!store.activeJobId || !batchId.value) return
    try {
      const status = await api.browserScanStatus(store.activeJobId, batchId.value)
      progress.value = status.progress || progress.value
      if (status.status !== 'RUNNING') stopProgressPolling()
    } catch {
      /* progress polling is best-effort */
    }
  }, 700)
}
function stopProgressPolling() {
  if (progressTimer.value !== null) {
    window.clearInterval(progressTimer.value)
    progressTimer.value = null
  }
}

async function runBrowserScan() {
  if (!batch.value || !precheck.value || !precheckConfirmed.value) return
  const scope = captureScope(store.activeJobId, store.dataEpoch)
  const currentBatch = batch.value.batch_id
  scanning.value = true
  scanReport.value = null
  scanStatus.value = 'RUNNING'
  scanErrorCode.value = ''
  scanErrorText.value = ''
  progress.value = { status: 'RUNNING', stage: 'starting' }
  startProgressPolling()
  try {
    const report = await api.browserScan(scope.jobId, currentBatch, {
      pages: scanPages.value,
      page_size: 5,
      filters_confirmed: true,
      platform_job_id: precheck.value.platform_job_id || undefined,
      filter_summary: precheck.value.filter_summary || undefined,
      max_details: maxDetails.value,
      max_duration_seconds: maxDuration.value
    })
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    scanReport.value = report
    scanStatus.value = 'COMPLETED'
    ElMessage.success(`扫描完成：导入 ${report.imported}，复用 ${report.reused}`)
    await refreshCandidates()
    if (scanMode.value === 'auto_fast') {
      await scoreBatch('fast_mode')
    }
  } catch (error) {
    if (!scopeValid(scope, store.activeJobId, store.dataEpoch)) return
    const apiError = error as ApiError
    scanErrorCode.value = apiError.state || apiError.errorType || 'SCAN_FAILED'
    scanErrorText.value = errorText(error)
    scanStatus.value = scanErrorCode.value
  } finally {
    stopProgressPolling()
    scanning.value = false
    await loadScanStatus()
  }
}

async function cancelScan() {
  if (!store.activeJobId || !batchId.value) return
  try {
    await api.browserCancel(store.activeJobId, batchId.value)
    ElMessage.warning('已请求停止扫描，等待当前检查点结束')
  } catch (error) {
    ElMessage.error(errorText(error))
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
    if (store.dataEpoch === epoch) ElMessage.error(errorText(error))
  } finally {
    importing.value = false
  }
}

function selectCandidate(row: CandidateListItem) {
  selectedCandidate.value = row
  evaluation.value = null
  task.value = null
}

async function openResume(row: CandidateListItem) {
  selectCandidate(row)
  if (!store.activeJobId) return
  try {
    resumeDetail.value = await api.candidateResume(
      store.activeJobId, row.job_candidate.job_candidate_id)
    resumeDrawerVisible.value = true
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

async function prepareSemantic() {
  const candidate = selectedCandidate.value
  if (!store.activeJobId || !candidate) return
  const jobId = store.activeJobId
  const epoch = store.dataEpoch
  preparing.value = true
  try {
    const result = await api.semanticPrepare(
      jobId, candidate.job_candidate.job_candidate_id, batch.value?.batch_id)
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return
    ElMessage.success(`语义准备：${result.status}`)
  } catch (error) {
    if (store.dataEpoch === epoch) ElMessage.error(errorText(error))
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
      ? `${batch.value.batch_id}:${jcid}:${mode}` : undefined
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
    if (store.dataEpoch === epoch) ElMessage.error(errorText(error))
  } finally {
    busy.value = false
  }
}

async function scoreBatch(mode: ScoringMode) {
  if (!store.activeJobId) return
  const epoch = store.dataEpoch
  let targets: CandidateListItem[] = []
  if (selectedCandidates.value.length) {
    targets = [...selectedCandidates.value]
  } else {
    try {
      const result = await api.listCandidates(store.activeJobId, {
        limit: 200,
        offset: 0,
        batch_id: batch.value?.batch_id,
        tab: tabParam.value
      })
      if (store.dataEpoch !== epoch) return
      targets = result.items.filter((item) => !item.latest_evaluation)
    } catch (error) {
      ElMessage.error(errorText(error))
      return
    }
  }
  if (!targets.length) {
    ElMessage.info(selectedCandidates.value.length
      ? '请选择需要评分的候选人'
      : '当前批次没有未评分候选人')
    return
  }
  const summary = { success: 0, skipped: 0, failed: 0, failures: [] as Array<{ candidate_key: string; reason: string }> }
  batchScoring.value = true
  try {
    for (const candidate of targets) {
      if (store.dataEpoch !== epoch) break
      try {
        const result = await runScore(candidate.job_candidate.job_candidate_id, mode, epoch)
        if (result) summary.success += 1
        else summary.skipped += 1
      } catch (error) {
        summary.failed += 1
        summary.failures.push({
          candidate_key: candidate.job_candidate.candidate_key,
          reason: errorText(error)
        })
      }
    }
    if (store.dataEpoch === epoch) {
      batchResult.value = summary
      batchResultVisible.value = true
      candidateTable.value?.clearSelection()
      selectedCandidates.value = []
      await refreshCandidates()
    }
  } finally {
    batchScoring.value = false
  }
}

function searchCandidates() {
  candidatePage.value = 1
  void refreshCandidates()
}

function handleCandidateSelection(rows: CandidateListItem[]) {
  selectedCandidates.value = rows
}

function handleCandidateSort({ prop, order }: { prop: string; order: string | null }) {
  candidateSortBy.value = order ? prop : ''
  candidateSortOrder.value = order === 'descending' ? 'desc' : 'asc'
  candidatePage.value = 1
  void refreshCandidates()
}

async function deleteCandidates(jobCandidateIds: string[]) {
  if (!store.activeJobId || !jobCandidateIds.length) return
  const jobId = store.activeJobId
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${jobCandidateIds.length} 名候选人？删除后列表与排名中将不再显示。`,
      '删除候选人', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch {
    return
  }
  try {
    const result = await api.deleteCandidates(jobId, jobCandidateIds)
    if (store.activeJobId !== jobId) return
    ElMessage.success(`已删除 ${result.deleted} 名候选人`)
    candidateTable.value?.clearSelection()
    selectedCandidates.value = []
    if (selectedCandidate.value
        && jobCandidateIds.includes(selectedCandidate.value.job_candidate.job_candidate_id)) {
      selectedCandidate.value = null
      evaluation.value = null
      task.value = null
    }
    await refreshCandidates()
  } catch (error) {
    ElMessage.error(errorText(error))
  }
}

function deleteCandidate(row: CandidateListItem) {
  void deleteCandidates([row.job_candidate.job_candidate_id])
}

function deleteSelectedCandidates() {
  void deleteCandidates(selectedCandidates.value.map(
    (item) => item.job_candidate.job_candidate_id))
}

async function refreshCandidates() {
  if (!store.activeJobId) return
  const jobId = store.activeJobId
  const epoch = store.dataEpoch
  const page = candidatePage.value
  try {
    const result = await api.listCandidates(jobId, {
      limit: candidatePageSize.value,
      offset: (page - 1) * candidatePageSize.value,
      batch_id: batch.value?.batch_id,
      tab: tabParam.value,
      keyword: candidateKeyword.value.trim() || undefined,
      sort_by: candidateSortBy.value || undefined,
      sort_order: candidateSortBy.value ? candidateSortOrder.value : undefined
    })
    if (store.dataEpoch !== epoch || store.activeJobId !== jobId) return
    candidates.value = result.items
    candidateTotal.value = result.total
    const current = selectedCandidate.value
    const stillPresent = current && result.items.find(
      (item) => item.job_candidate.job_candidate_id === current.job_candidate.job_candidate_id)
    if (stillPresent) {
      selectedCandidate.value = stillPresent
    } else {
      selectedCandidate.value = result.items.find((item) => !item.latest_evaluation)
        || result.items[0] || null
      evaluation.value = null
      task.value = null
    }
  } catch (error) {
    if (store.dataEpoch === epoch) ElMessage.error(errorText(error))
  }
}

async function reextractResume() {
  if (!store.activeJobId || !resumeDetail.value) return
  const jobId = store.activeJobId
  const jcid = resumeDetail.value.job_candidate.job_candidate_id
  reextracting.value = true
  try {
    const result = await api.reextractCandidate(jobId, jcid, batchId.value || undefined)
    if (store.activeJobId !== jobId) return
    await refreshCandidates()
    resumeDetail.value = await api.candidateResume(jobId, jcid)
    if (result.sealed && result.updated) {
      ElMessage.success(`${result.message}（输入版本 v${result.input_version}）`)
    } else {
      ElMessage.warning(result.message)
    }
  } catch (error) {
    ElMessage.error(errorText(error))
  } finally {
    reextracting.value = false
  }
}

watch(() => store.dataEpoch, () => {
  batch.value = null
  batchId.value = ''
  batches.value = []
  imported.value = null
  selectedCandidate.value = null
  candidates.value = []
  task.value = null
  evaluation.value = null
  session.value = null
  precheck.value = null
  precheckConfirmed.value = false
  scanReport.value = null
  scanStatus.value = ''
  scanErrorCode.value = ''
  scanErrorText.value = ''
  progress.value = null
  candidatePage.value = 1
  candidateTotal.value = 0
  candidateKeyword.value = ''
  stopProgressPolling()
  void loadSession()
  void loadBatches()
  void refreshCandidates()
})

watch(() => store.activeJobId, () => {
  precheck.value = null
  precheckConfirmed.value = false
  candidatePage.value = 1
  candidateTotal.value = 0
  candidateKeyword.value = ''
  void loadBatches()
  void refreshCandidates()
})

onMounted(() => {
  applyTemplate()
  void loadSession()
  void loadBatches()
})
</script>
