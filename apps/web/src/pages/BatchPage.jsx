import React, { useEffect, useState } from 'react'
import { api, DECKS } from '../api.js'
import { t } from '../i18n.js'
import { TaskCard, TaskTable, QuotaHint } from '../components/tasks.jsx'
import { EmptyState, LogBlock, ErrBar } from '../components/ui.jsx'

export default function BatchPage({ onMsg }) {
  const [deckA, setDeckA] = useState('lightning')
  const [deckB, setDeckB] = useState('draco')
  const [games, setGames] = useState(100)
  const [seed, setSeed] = useState(20260910)
  const [jobs, setJobs] = useState([])
  const [detail, setDetail] = useState(null)
  const [report, setReport] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  async function refresh() {
    try { setJobs((await api.listBatch()).jobs || []) } catch { /* 忽略 */ }
  }
  useEffect(() => { refresh(); const t2 = setInterval(refresh, 5000); return () => clearInterval(t2) }, [])
  useEffect(() => {
    if (!detail || detail.status !== 'running') return
    const t2 = setTimeout(() => api.batchStatus(detail.id).then(setDetail).catch(() => {}), 1500)
    return () => clearTimeout(t2)
  }, [detail])

  async function submit() {
    setBusy(true); setErr(null)
    try {
      const r = await api.submitBatch({ deck_a: deckA, deck_b: deckB, games: Number(games), seed_start: Number(seed) })
      onMsg(`任务已提交 ${r.id}（≤500 局 · 并发≤2 · 日配额≤10）`, 'ok')
      setDetail(await api.batchStatus(r.id)); refresh()
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) } finally { setBusy(false) }
  }

  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued')
  const history = jobs.filter((j) => !['running', 'queued'].includes(j.status))
  const quotaUsed = jobs.filter((j) => {
    const t0 = new Date(); t0.setHours(0, 0, 0, 0)
    return (j.created || 0) >= t0.getTime() / 1000
  }).length

  return (
    <div className="grid cols-2">
      <div className="card">
        <h3>新建批量任务</h3>
        <QuotaHint used={quotaUsed} />
        <div className="row">
          <div><label htmlFor="ba">{t('lbl.deck_mine')}</label>
            <select id="ba" value={deckA} onChange={(e) => setDeckA(e.target.value)}>
              {DECKS.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}</select></div>
          <div><label htmlFor="bb">{t('lbl.deck_opp')}</label>
            <select id="bb" value={deckB} onChange={(e) => setDeckB(e.target.value)}>
              {DECKS.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}</select></div>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <div><label htmlFor="bg">{t('lbl.games')}</label>
            <input id="bg" type="number" min="1" max="500" value={games}
              onChange={(e) => setGames(e.target.value)} style={{ width: 110 }} /></div>
          <div><label htmlFor="bs">{t('lbl.seed')}</label>
            <input id="bs" type="number" value={seed}
              onChange={(e) => setSeed(e.target.value)} style={{ width: 150 }} /></div>
        </div>
        <p className="sm muted">先后手由引擎按 seed 交替决定（PRD §7.1 双盲口径需 200 局样本）</p>
        <div className="row">
          <button className="primary" disabled={busy} onClick={submit}>{t('btn.submit')}</button>
          <button className="ghost" onClick={() => { setDeckA('lightning'); setDeckB('draco'); setGames(200); onMsg('已载入预设：我的卡组 × Dragapult 系 ×200') }}>
            试试：× Dragapult 系 ×200
          </button>
          <button className="ghost" onClick={refresh}>刷新</button>
        </div>
        {err && <ErrBar code={err.code} message={err.message} onRetry={submit} />}
      </div>

      <div className="card">
        <h3>进行中任务</h3>
        {running.length === 0 && <EmptyState text={t('empty.tasks')} cta={t('btn.submit')} onCta={submit} />}
        {running.map((j) => (
          <TaskCard key={j.id} job={j} progress={detail && detail.id === j.id ? detail.progress : j.progress}
            etaText="剩余局数 × 经验单局时"
            onView={(x) => api.batchStatus(x.id).then(setDetail)} 
            onCancel={async (id) => { await api.cancelBatch(id); onMsg('已请求取消（保留已完成局）'); refresh() }} />
        ))}
        {detail && (detail.status === 'running') && (
          <p className="sm muted">进度条为乐观 ETA（剩余局数 × 经验平均单局时）；取消仅停止后续调度（K4）。</p>
        )}
        {report && <LogBlock lines={report} label="报告预览" max={220} />}
      </div>

      <div className="card" style={{ gridColumn: '1 / -1' }}>
        <h3>历史任务 <span className="pill sm">报告 md/csv 可导出</span></h3>
        <TaskTable jobs={history} onView={(j) => api.batchStatus(j.id).then(setDetail)} />
        {detail?.result && (
          <div className="row" style={{ marginTop: 10 }}>
            <button className="ghost sm" onClick={() => api.report(detail.id, 'md').then(setReport)}>报告 md</button>
            <button className="ghost sm" onClick={() => api.report(detail.id, 'csv').then(setReport)}>CSV</button>
            <button className="ghost sm" onClick={() => api.report(detail.id, 'json').then(setReport)}>JSON</button>
          </div>
        )}
      </div>
    </div>
  )
}
