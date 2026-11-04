import React from 'react'
import { t } from '../i18n.js'

/** 基础 UI 组件（UI 规范 §4 四态 / §6 反馈 / §8 a11y） */

export function Spinner({ label }) {
  return (
    <span className="spin" role="status" aria-live="polite">
      <svg className="ring" width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="9" fill="none" stroke="#E2E8F0" strokeWidth="3" />
        <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="#DC2626" strokeWidth="3"
          strokeLinecap="round" />
      </svg>
      {label && <span className="sm mono">{label}</span>}
    </span>
  )
}

export function Pill({ tone = 'default', children, title }) {
  return <span className={`pill pill-${tone}`} title={title}>{children}</span>
}

/** 状态徽标：排队/运行中/成功/失败/已取消（§5.3 色码） */
export function StatusBadge({ status }) {
  const map = {
    running: { tone: 'warn', text: '运行中' },
    queued: { tone: 'muted', text: '排队' },
    done: { tone: 'ok', text: '成功' },
    failed: { tone: 'brand', text: '失败' },
    cancelled: { tone: 'muted', text: '已取消' },
    open: { tone: 'warn', text: '进行中' },
  }
  const m = map[status] || { tone: 'muted', text: status || '未知' }
  return (
    <Pill tone={m.tone}>
      <i className="dot" style={{ background: 'currentColor' }} aria-hidden="true" />
      <span>{m.text}</span>
    </Pill>
  )
}

/** SSE 连接状态指示（心跳/断线续传）：live / 重连中 / 轮询兜底 */
export function SsePill({ state }) {
  const map = {
    connecting: { label: '连接中…', tone: 'muted' },
    live: { label: '实时', tone: 'ok' },
    reconnecting: { label: '重连中 · 自动续传', tone: 'warn' },
    polling: { label: '轮询兜底', tone: 'warn' },
    done: { label: '已结束', tone: 'muted' },
  }
  const m = map[state] || map.connecting
  return (
    <span className={`pill pill-${m.tone} sse-pill`}
      data-sse-state={state}
      title="任务进度通过 SSE 推送；断线自动按 Last-Event-ID 续传">
      <i className={`dot ${state === 'live' ? 'pulse' : ''}`}
        style={{ background: 'currentColor' }} aria-hidden="true" />
      <span>{m.label}</span>
    </span>
  )
}

/** 页内分段标签：同一导航入口下的子页面切换（保留各子页面的挂载状态） */
export function SegTabs({ items, value, onChange, label }) {
  return (
    <div className="seg-tabs" role="tablist" aria-label={label}>
      {items.map((it) => (
        <button key={it.v} role="tab" aria-selected={value === it.v}
          onClick={() => onChange(it.v)}>{it.label}</button>
      ))}
    </div>
  )
}

export function ErrBar({ code, message, onRetry, retryLabel }) {
  return (
    <div className="alert err" role="alert">
      <code className="mono">{code}</code>
      <span>{message}</span>
      {onRetry && <button className="ghost sm" onClick={onRetry}>{retryLabel || t('btn.retry')}</button>}
    </div>
  )
}

export function EmptyState({ text, cta, onCta }) {
  return (
    <div className="empty">
      <svg width="72" height="52" viewBox="0 0 72 52" aria-hidden="true">
        <rect x="2" y="10" width="26" height="32" rx="4" fill="none" stroke="#CBD5E1" strokeWidth="2" />
        <rect x="34" y="4" width="34" height="42" rx="4" fill="none" stroke="#E2E8F0" strokeWidth="2" strokeDasharray="4 4" />
        <circle cx="15" cy="26" r="5" fill="none" stroke="#DC2626" strokeWidth="2" />
      </svg>
      <p className="muted">{text}</p>
      {cta && <button className="primary" onClick={onCta}>{cta}</button>}
    </div>
  )
}

export function StatCard({ n, label, tone, id }) {
  return (
    <div className="stat" id={id}>
      <div className={`n mono ${tone || ''}`}>{n ?? '—'}</div>
      <div className="l">{label}</div>
    </div>
  )
}

/** 深色 mono 日志块（回合日志 / 训练曲线 / MD 预览） */
export function LogBlock({ lines, label, live, max = 260 }) {
  const text = Array.isArray(lines) ? lines.join('\n') : (lines || '')
  return (
    <pre className="log" style={{ maxHeight: max }}
      role={live ? 'log' : undefined} aria-live={live ? 'polite' : undefined}
      aria-label={label}>{text || '—'}</pre>
  )
}

export function CopyButton({ text, label, onDone }) {
  return (
    <button className="ghost sm" onClick={async () => {
      try { await navigator.clipboard.writeText(text) } catch { /* 忽略 */ }
      onDone && onDone()
    }}>{label || '复制'}</button>
  )
}

export function Toast({ items }) {
  if (!items || !items.length) return null
  return (
    <div className="toasts" aria-live="polite">
      {items.map((x) => <div key={x.id} className={`toast ${x.kind || 'ok'}`}>{x.text}</div>)}
    </div>
  )
}

export function useToasts() {
  const [items, setItems] = React.useState([])
  const push = React.useCallback((text, kind = 'ok') => {
    const id = Math.random().toString(36).slice(2)
    setItems((s) => [...s, { id, text, kind }])
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), 3000)
  }, [])
  return [items, push]
}

/**
 * 页面头部：统一的「标题 + 一句话说明 + 标签」结构
 *
 * 为什么统一：此前各页面有的用 h2、有的用 h3，且把 PRD 内部编号（F2/M6/K1）
 * 直接写进标题，访客看不懂。现在把标题层级收敛到 h2，内部编号一律降级为
 * 次级标签（title 里给全称），说明文字统一 12px 灰色。
 */
export function PageHead({ title, desc, tags = [], right }) {
  return (
    <div className="page-head">
      <div className="ph-main">
        <h2>{title}</h2>
        {tags.length > 0 && (
          <span className="ph-tags">
            {tags.map((x, i) => (
              <Pill key={i} tone="muted" title={x.title || x.label || x}>{x.label || x}</Pill>
            ))}
          </span>
        )}
      </div>
      {desc && <p className="page-desc">{desc}</p>}
      {right && <div className="ph-right">{right}</div>}
    </div>
  )
}

/** 卡片内的小节说明（统一 12px 灰字，与 PageHead 的 desc 呼应） */
export function Hint({ children }) {
  return <p className="hint">{children}</p>
}
