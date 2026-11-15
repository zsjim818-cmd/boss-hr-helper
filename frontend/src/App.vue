<template>
  <el-container style="min-height: 100vh;">
    <el-aside width="230px" style="background-color: #001529; color: #fff; display: flex; flex-direction: column;">
      <div style="padding: 20px 16px; font-size: 17px; font-weight: 700; border-bottom: 1px solid #002140; display: flex; align-items: center; gap: 8px;">
        <el-icon :size="22" color="#00bebd"><Cpu /></el-icon>
        <span>Boss HR 智能筛选台</span>
      </div>

      <el-menu
        :default-active="route.path"
        router
        background-color="#001529"
        text-color="rgba(255,255,255,0.7)"
        active-text-color="#00bebd"
        style="border-right: none; flex: 1;"
      >
        <el-menu-item index="/policy">
          <el-icon><SetUp /></el-icon>
          <span>1. 岗位与策略工作台</span>
        </el-menu-item>
        <el-menu-item index="/scanner">
          <el-icon><Aim /></el-icon>
          <span>2. 批次导入与评分台</span>
        </el-menu-item>
        <el-menu-item index="/review">
          <el-icon><Checked /></el-icon>
          <span>3. HR 审核与排名看板</span>
        </el-menu-item>
      </el-menu>

      <div style="padding: 14px; font-size: 12px; color: rgba(255,255,255,0.5); border-top: 1px solid #002140;">
        <div>运行模式: {{ store.runtime?.mode === 'live' ? '真实模型' : '确定性联调' }}</div>
        <div style="margin-top: 4px;">Tier 0 → 1 → 2 → 3 通用流水线</div>
      </div>
    </el-aside>

    <el-container>
      <el-header style="background: #fff; border-bottom: 1px solid #e4e7ed; display: flex; flex-direction: column; padding: 8px 24px; height: auto;">
        <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px;">
          <div style="display: flex; align-items: center; gap: 10px;">
            <span style="font-size: 14px; color: #606266; font-weight: 500;">当前岗位:</span>
            <el-select :model-value="store.activeJobId" style="width: 260px;"
                       placeholder="请先创建岗位" @change="store.selectJob">
              <el-option v-for="job in store.jobs" :key="job.job_id"
                         :label="job.title" :value="job.job_id" />
            </el-select>
            <el-tag v-if="store.currentPolicy" type="success" effect="plain" size="small">
              已发布 v{{ store.currentPolicy.policy_version }}
            </el-tag>
            <el-tag v-else type="warning" effect="plain" size="small">未发布策略</el-tag>
          </div>

          <div v-if="showIdentitySelector" style="display: flex; align-items: center; gap: 8px;">
            <el-tag type="info" size="small">本地身份（受信头）</el-tag>
            <el-select :model-value="identityLabel" style="width: 320px;" @change="applyIdentityLabel">
              <el-option v-for="item in store.identities" :key="item.org_id + ':' + item.user_id"
                         :label="`${item.org_id.slice(0, 8)}… / ${item.user_id.slice(0, 8)}… (${item.org_role})`"
                         :value="item.org_id + ':' + item.user_id" />
            </el-select>
          </div>
          <el-tag v-else-if="store.runtime" type="warning" effect="dark" size="small">
            真实部署：身份由认证网关注入
          </el-tag>
        </div>

        <el-alert v-if="store.backendError" :title="store.backendError" type="error"
                  show-icon :closable="true" style="margin-top: 6px;"
                  @close="store.clearError" />
        <el-alert v-else-if="store.runtime?.mode === 'deterministic'"
                  title="确定性联调模式：编译器/Tier2/Tier3 均为本地 Mock，分数不代表真实模型质量"
                  type="info" show-icon :closable="false" style="margin-top: 6px;" />
      </el-header>

      <el-main style="padding: 20px 24px;">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/useAppStore'

const route = useRoute()
const store = useAppStore()

const showIdentitySelector = computed(
  () => store.runtime?.auth_mode === 'trusted-headers')

const identityLabel = computed(() => `${store.orgId}:${store.userId}`)

function applyIdentityLabel(value: string) {
  const [org, user] = value.split(':')
  store.applyIdentity(org, user)
  void store.refreshJobs()
}

onMounted(() => { void store.bootstrap() })
</script>
