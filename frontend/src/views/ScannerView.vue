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
                       :disabled="!candidates.length" @click="scoreBatch('fast_mode')">批量 Fast</el-button>
            <el-button size="small" type="danger" plain :loading="batchScoring"
                       :disabled="!candidates.length" @click="scoreBatch('deep_mode')">批量 Deep</el-button>
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
          <el-table :data="candidates" size="small" border highlight-current-row
                    row-style="cursor: pointer;" @row-click="selectCandidate">
            <el-table-column label="候选人" width="170">
              <template #default="{ row }">
                <div>{{ row.resume_summary?.name || row.job_candidate.candidate_key }}</div>
                <div style="font-size: 11px; color: #909399;">{{ row.job_candidate.candidate_key }}</div>
              </template>
            </el-table-column>
            <el-table-column label="学历/年限" width="110">
              <template #default="{ row }">
                {{ row.resume_summary?.degree || '—' }} / {{ row.resume_summary?.work_years || '—' }}
              </template>
            </el-table-column>
            <el-table-column label="期望薪资" width="100">
              <template #default="{ row }">{{ row.resume_summary?.salary || '—' }}</template>
            </el-table-column>
            <el-table-column label="封存" width="70">
              <template #default="{ row }">
                <el-tag :type="row.sealed ? 'success' : 'info'" size="small">
                  {{ row.sealed ? 'Sealed' : '未封存' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最新状态" width="140">
              <template #default="{ row }">
                <el-tag v-if="row.latest_evaluation"
                        :type="statusTagType(row.latest_evaluation.scoring_status)" size="small">
                  {{ row.latest_evaluation.scoring_status }}
                </el-tag>
                <el-tag v-else type="info" size="small">未评分</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="总分" width="70">
              <template #default="{ row }">{{ row.latest_evaluation?.total_score ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="路由" width="120">
              <template #default="{ row }">{{ row.latest_evaluation?.routing_suggestion || '—' }}</template>
            </el-table-column>
            <el-table-column label="Policy" width="70">
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
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link type="primary"
                           @click.stop="openResume(row)">查看简历</el-button>
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
import { ElMessage } from 'element-plus'
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
const templateKey = ref('java')
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
const reextracting = ref(false)
const resumeDrawerVisible = ref(false)
const resumeDetail = ref<ResumeSnapshotResponse | null>(null)
const batchResultVisible = ref(false)
const batchResult = ref<{ success: number; skipped: number; failed: number;
  failures: Array<{ candidate_key: string; reason: string }> }>({
  success: 0, skipped: 0, failed: 0, failures: []
})

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
    session.value = await api.browserSessionConnect()
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
  const targets = candidates.value.filter((item) => !item.latest_evaluation)
  if (!targets.length) {
    ElMessage.info('当前没有未评分候选人')
    return
  }
  const epoch = store.dataEpoch
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
      keyword: candidateKeyword.value.trim() || undefined
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
