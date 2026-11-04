import React, { useState } from 'react'
import DecksPage from './DecksPage.jsx'
import LibraryPage from './LibraryPage.jsx'
import { SegTabs } from '../components/ui.jsx'

/** 卡组中心：卡组管理与卡库合并为一个导航入口 */
const TABS = [
  { v: 'decks', label: '我的卡组' },
  { v: 'library', label: '卡库' },
]

export default function CardCenterPage({ onMsg, onGoBattle }) {
  const [tab, setTab] = useState('decks')
  return (
    <div className="grid" style={{ gap: 16 }}>
      <SegTabs items={TABS} value={tab} onChange={setTab} label="卡组与卡库" />
      {/* 保持挂载：导入中的卡表、筛选条件与分页在切换标签后不丢 */}
      <div style={{ display: tab === 'decks' ? undefined : 'none' }}>
        <DecksPage onMsg={onMsg} onGoBattle={onGoBattle} />
      </div>
      <div style={{ display: tab === 'library' ? undefined : 'none' }}>
        <LibraryPage onMsg={onMsg} />
      </div>
    </div>
  )
}
