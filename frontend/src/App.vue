<template>
  <el-container class="app-container">
    <el-header class="app-header">
      <div class="header-left">
        <el-icon class="logo-icon"><TrendCharts /></el-icon>
        <h1 class="app-title">商品期货 Trading Agents 系统</h1>
        <el-tag size="small" type="info">AI驱动决策</el-tag>
      </div>
      <div class="header-right">
        <SystemStatus />
      </div>
    </el-header>
    <el-container>
      <el-aside width="220px" class="app-aside">
        <el-menu
          :default-active="activeMenu"
          router
          class="sidebar-menu"
        >
          <el-menu-item index="/data">
            <el-icon><DataAnalysis /></el-icon>
            <span>数据管理</span>
          </el-menu-item>
          <el-menu-item index="/update">
            <el-icon><Refresh /></el-icon>
            <span>数据更新</span>
          </el-menu-item>
          <el-menu-item index="/analysis">
            <el-icon><Setting /></el-icon>
            <span>分析配置</span>
          </el-menu-item>
          <el-menu-item index="/results">
            <el-icon><Document /></el-icon>
            <span>分析结果</span>
          </el-menu-item>
        </el-menu>
      </el-aside>
      <el-main class="app-main">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import SystemStatus from '@/components/SystemStatus.vue'

const route = useRoute()
const activeMenu = computed(() => route.path)
</script>

<style scoped>
.app-container {
  height: 100vh;
}
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  color: #fff;
  padding: 0 24px;
  height: 60px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.logo-icon {
  font-size: 24px;
  color: #409eff;
}
.app-title {
  font-size: 18px;
  margin: 0;
  font-weight: 600;
}
.app-aside {
  background: #f5f7fa;
  border-right: 1px solid #e4e7ed;
}
.sidebar-menu {
  border-right: none;
  height: 100%;
}
.app-main {
  background: #f0f2f5;
  padding: 20px;
  overflow-y: auto;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
