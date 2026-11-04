import React, { useState } from 'react'
import BattlePage from './BattlePage.jsx'
import BatchPage from './BatchPage.jsx'
import { SegTabs } from '../components/ui.jsx'

/** 对战中心：单局对战与批量对战合并为一个导航入口 */
const TABS = [
  { v: 'single', label: '单局对战' },
  { v: 'batch', label: '批量对战' },
]

export default function BattleCenterPage({ onMsg, onGoDecks }) {
  const [tab, setTab] = useState('single')
  return (
    <div className="grid" style={{ gap: 16 }}>
      <SegTabs items={TABS} value={tab} onChange={setTab} label="对战模式" />
      {/* 两个子页面始终挂载，仅切换显隐：切走再切回不会丢掉进行中的对局/任务 */}
      <div style={{ display: tab === 'single' ? undefined : 'none' }}>
        <BattlePage onMsg={onMsg} onGoDecks={onGoDecks} />
      </div>
      <div style={{ display: tab === 'batch' ? undefined : 'none' }}>
        <BatchPage onMsg={onMsg} />
      </div>
    </div>
  )
}
