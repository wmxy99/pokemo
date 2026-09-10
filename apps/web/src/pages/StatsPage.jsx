import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { StatCard, ErrBar, EmptyState, Pill } from '../components/ui.jsx'
import { WinrateMatrix, ExportBar } from '../components/stats.jsx'
import { TaskTable } from '../components/tasks.jsx'
import { t } from '../i18n.js'

export default function StatsPage({ onMsg, onGoBatch }) {
  const [sum, setSum] = useState(null)
  const [jobs, setJobs] = useState([])
  const [sel, setSel] = useState(null)
  const [preview, setPreview] = useState('')
  const [err, setErr] = useState(null)

  async function load() {
    setErr(null)
    try {
      setSum(await api.statsSummary())
      setJobs((await api.listBatch()).jobs || [])
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) }
  }
  useEffect(() => { load() }, [])

  async function exportAs(fmt) {
    const id = sel?.id || jobs.find((j) => j.status === 'done')?.id
    if (!id) { onMsg('暂无可导出的已完成任务', 'err'); return }
    if (fmt === 'copy') {
      const md = await api.report(id, 'md')
      try { await navigator.clipboard.writeText(md) } catch { /* 忽略 */ }
      onMsg('已复制 MD 分享文本（含 Unofficial 声明）')
      setPreview(md)
      return
    }
    const text = await api.report(id, fmt)
    setPreview(text)
    if (fmt !== 'md') {
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob); a.download = `${id}.${fmt}`; a.click()
      URL.revokeObjectURL(a.href)
      onMsg(`${fmt.toUpperCase()} 已下载`)
    }
  }

  const k = sum?.kpi || {}
  return (
    <div className="grid" style={{ gap: 16 }}>
      {err && <ErrBar code={err.code} message={err.message} onRetry={load} />}

      <div className="card">
        <h3>总览 KPI <Pill tone="warn">{t('tip.unofficial')}</Pill></h3>
        <div className="stats">
          <StatCard n={k.tasks ?? '—'} label="已完成任务" />
          <StatCard n={k.total_games ?? '—'} label="累计对局" />
          <StatCard n={k.win_rate_a != null ? (k.win_rate_a * 100).toFixed(1) + '%' : '—'}
            label="A 方总胜率" tone="win" />
          <StatCard n={k.avg_turns ?? '—'} label="平均回合" />
        </div>
        <p className="sm muted">{sum?.sample_note || '样本量见矩阵 tooltip'}</p>
        <div className="row" style={{ marginTop: 8 }}>
          <button className="ghost sm" onClick={load}>刷新</button>
          {(!k.tasks) && <button className="primary sm" onClick={onGoBatch}>去提交批量任务</button>}
        </div>
      </div>

      <div className="card">
        <WinrateMatrix rows={sum?.matrix || []} />
      </div>

      <div className="card">
        <h3>导出报告 <span className="pill sm">三格式一致（PRD F7）</span></h3>
        <ExportBar n={sel ? (jobs.find((j) => j.id === sel.id) || {}).result?.n : undefined}
          onExport={exportAs} preview={preview} />
        {!preview && <EmptyState text="选择下方任务后点「报告 md」预览，或直接导出 CSV/MD/JSON"
          cta="选择最近完成的任务" onCta={() => { const d = jobs.find((x) => x.status === 'done'); if (d) { setSel(d); api.report(d.id, 'md').then(setPreview) } else onMsg('暂无已完成任务', 'err') }} />}
      </div>

      <div className="card">
        <h3>任务明细</h3>
        <TaskTable jobs={jobs}
          onView={(j) => { setSel(j); api.report(j.id, 'md').then(setPreview).catch(() => {}) }} />
      </div>
    </div>
  )
}
