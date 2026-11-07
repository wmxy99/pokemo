import React from 'react'
import BattlePage from './BattlePage.jsx'
import BatchPage from './BatchPage.jsx'
import { SegTabs } from '../components/ui.jsx'

/**
 * 对战中心：单局对战与批量对战合并为一个导航入口
 *
 * 子标签现在是**路由**（`/battles` ↔ `/batch`），不是组件内 state：
 * 这样「把批量任务发给同事」只需复制地址栏；子页仍保持挂载（显隐切换），
 * 切走再切回不会丢掉进行中的对局。
 */
const TABS = [
  { v: 'single', label: '单局对战', to: '/battles' },
  { v: 'batch', label: '批量对战', to: '/batch' },
]

export default function BattleCenterPage({ onMsg, onGoDecks, route }) {
  const isBatch = route.name === 'batch' || route.name === 'batchJob'
  const tab = isBatch ? 'batch' : 'single'
  const battleId = route.name === 'battle' ? route.params.id : null
  const jobId = route.name === 'batchJob' ? route.params.id : null

  return (
    <div className="grid" style={{ gap: 16 }}>
      <SegTabs items={TABS} value={tab} label="对战模式" />
      {/* 两个子页面始终挂载，仅切换显隐：切走再切回不会丢掉进行中的对局/任务 */}
      <div style={{ display: tab === 'single' ? undefined : 'none' }}>
        <BattlePage onMsg={onMsg} onGoDecks={onGoDecks} battleId={battleId} />
      </div>
      <div style={{ display: tab === 'batch' ? undefined : 'none' }}>
        <BatchPage onMsg={onMsg} jobId={jobId} />
      </div>
    </div>
  )
}
