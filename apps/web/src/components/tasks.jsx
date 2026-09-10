import React from 'react'
import { StatusBadge, Pill } from './ui.jsx'
import { t } from '../i18n.js'

/** 任务卡片（UI 规范 §5.3）：进度条 8px + mono n/total + ETA + 二次确认取消 */
export function TaskCard({ job, progress, onCancel, onView, onReport, etaText }) {
  if (!job) return null
  const p = progress || job.progress || {}
  const pct = p.n ? Math.round((p.done / p.n) * 100) : 0
  const running = job.status === 'running' || job.status === 'queued'
  return (
    <div className={`card task ${job.status === 'failed' ? 'task-failed' : ''}`}>
      <div className="zt">
        <b className="mono sm">{job.id}</b>
        <StatusBadge status={job.status} />
      </div>
      {p.n ? (<>
        <div className="bar" role="progressbar" aria-valuenow={p.done} aria-valuemax={p.n}
          aria-label={`任务进度 ${p.done}/${p.n}`}><i style={{ width: pct + '%' }} /></div>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <span className="sm mono">{p.done}/{p.n}（{pct}%）</span>
          {running && <span className="sm muted">ETA {etaText || '估算中…'}</span>}
        </div>
      </>) : <p className="sm muted">等待调度…（乐观 ETA 将在 1s 内出现）</p>}
      {job.result && (
        <div className="row sm">
          <Pill>A 胜率 <b className="mono">{job.result.win_rate_a ?? '—'}</b></Pill>
          <Pill>n <b className="mono">{job.result.n ?? '—'}</b></Pill>
          <Pill>均回合 <b className="mono">{job.result.avg_turns ?? '—'}</b></Pill>
        </div>
      )}
      <div className="row" style={{ marginTop: 8 }}>
        {onView && <button className="ghost sm" onClick={() => onView(job)}>查看</button>}
        {running && onCancel && (
          <button className="ghost sm" onClick={() => {
            if (window.confirm(t('confirm.cancel_task'))) onCancel(job.id)
          }}>{t('btn.cancel')}</button>
        )}
        {['done', 'cancelled'].includes(job.status) && onReport && (<>
          <button className="ghost sm" onClick={() => onReport(job.id, 'md')}>报告 md</button>
          <button className="ghost sm" onClick={() => onReport(job.id, 'csv')}>CSV</button>
        </>)}
      </div>
    </div>
  )
}

/** 任务表格（历史/调度队列） */
export function TaskTable({ jobs, onView, extraCols }) {
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th scope="col">任务</th>
          <th scope="col">状态</th>
          <th scope="col">创建时间</th>
          {extraCols}
          <th scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        {(jobs || []).map((j) => (
          <tr key={j.id}>
            <td className="mono sm">{j.id}</td>
            <td><StatusBadge status={j.status} /></td>
            <td className="sm muted mono">{new Date((j.created || 0) * 1000).toLocaleString()}</td>
            {extraCols && extraCols.map((c, i) => <td key={i} className="sm mono">{c.render(j)}</td>)}
            <td><button className="pill sm" onClick={() => onView && onView(j)}>查看</button></td>
          </tr>
        ))}
        {(jobs || []).length === 0 && (
          <tr><td colSpan={4 + (extraCols ? extraCols.length : 0)} className="muted sm">
            暂无任务（guest 任务归 guest 账号）
          </td></tr>
        )}
      </tbody>
    </table>
  )
}

/** 配额提示条（F6：单任务 ≤500 / 并发 ≤2 / 日配额 ≤10） */
export function QuotaHint({ used, limit = 10 }) {
  const pct = Math.min(100, Math.round(((used || 0) / limit) * 100))
  return (
    <div className="row quota">
      <Pill tone={used >= limit ? 'brand' : 'muted'}>
        日配额 <b className="mono">{used ?? '—'}</b>/{limit}
      </Pill>
      <Pill>单任务 ≤ 500 局</Pill>
      <Pill>并发 ≤ 2</Pill>
      <div className="bar" style={{ width: 120 }} role="progressbar"
        aria-valuenow={used || 0} aria-valuemax={limit}><i style={{ width: pct + '%' }} /></div>
    </div>
  )
}
