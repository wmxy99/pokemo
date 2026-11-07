import React from 'react'
import DecksPage from './DecksPage.jsx'
import LibraryPage from './LibraryPage.jsx'
import { SegTabs } from '../components/ui.jsx'

/**
 * 卡组中心：卡组管理与卡库合并为一个导航入口
 *
 * 子标签走路由（`/decks` ↔ `/library`），卡库的筛选条件走 query string
 * （`/library?q=皮卡丘&series=SSP&page=2`）—— 可收藏、可分享、刷新不丢。
 */
const TABS = [
  { v: 'decks', label: '我的卡组', to: '/decks' },
  { v: 'library', label: '卡库', to: '/library' },
]

export default function CardCenterPage({ onMsg, onGoBattle, route }) {
  const tab = route.name === 'library' ? 'library' : 'decks'
  const deckId = route.name === 'deck' ? route.params.id : null

  return (
    <div className="grid" style={{ gap: 16 }}>
      <SegTabs items={TABS} value={tab} label="卡组与卡库" />
      {/* 保持挂载：导入中的卡表、筛选条件与分页在切换标签后不丢 */}
      <div style={{ display: tab === 'decks' ? undefined : 'none' }}>
        <DecksPage onMsg={onMsg} onGoBattle={onGoBattle} deckId={deckId} />
      </div>
      <div style={{ display: tab === 'library' ? undefined : 'none' }}>
        <LibraryPage onMsg={onMsg} route={route} />
      </div>
    </div>
  )
}
