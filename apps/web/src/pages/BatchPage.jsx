import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function BatchPage({ decks, onMsg, jobs, setJobs }) {
  const [deckA, setDeckA] = useState('lightning')
  const [deckB, setDeckB] = useState('fire')
  const [games, setGames] = useState(100)
  const [seed, setSeed] = useState(20260910)
  const [detail, setDetail] = useState(null)
  const [report, setReport] = useState('')
  const [busy, setBusy] = useState(false)

  async function refresh() {
    try { setJobs((await api.listBatch()).jobs) } catch { /* 忽略 */ }
  }
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 6000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 运行中任务自动轮询
  useEffect(() => {
    if (!detail || detail.status !== 'running') return
    const t = setTimeout(() => {
      api.batchStatus(detail.id).then(setDetail).catch(() => {})
    }, 1200)
    return () => clearTimeout(t)
  }, [detail])

  async function submit() {
    setBusy(true)
    try {
      const r = await api.submitBatch({
        deck_a: deckA, deck_b: deckB,
        games: Number(games), seed_start: Number(seed),
      })
      onMsg(`批量任务已提交：${r.id}（≤500 局/任务 · 并发≤2 · 日配额≤10）`, 'ok')
      setDetail(await api.batchStatus(r.id))
      refresh()
    } catch (e) { onMsg('提交失败：' + e.message, 'err') } finally { setBusy(false) }
  }

  async function cancel(id) {
    try {
      const r = await api.cancelBatch(id)
      onMsg('已请求取消（保留已完成局，K4）', 'ok')
      setDetail((d) => d && { ...d, status: r.status })
    } catch (e) { onMsg('取消失败：' + e.message, 'err') }
  }

  async function loadReport(id, fmt) {
    try { setReport(await api.report(id, fmt)); onMsg(`报告（${fmt}）已加载`, 'ok') }
    catch (e) { onMsg('报告不可用：' + e.message, 'err') }
  }

  const pct = detail?.progress?.n
    ? Math.round((detail.progress.done / detail.progress.n) * 100) : 0

  return (
    <div className="grid cols-2">
      <div className="card">
        <h3>新建批量任务 <span className="pill sm">单任务≤500 · 并发≤2 · 日配额≤10</span></h3>
        <div className="row">
          <div><label htmlFor="ba">我方卡组</label>
            <select id="ba" value={deckA} onChange={(e) => setDeckA(e.target.value)}>
              {decks.map((d) => <option key={d}>{d}</option>)}</select></div>
          <div><label htmlFor="bb">对手卡组</label>
            <select id="bb" value={deckB} onChange={(e) => setDeckB(e.target.value)}>
              {decks.map((d) => <option key={d}>{d}</option>)}</select></div>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <div><label htmlFor="bg">局数</label>
            <input id="bg" type="number" min="1" max="500" value={games}
                   onChange={(e) => setGames(e.target.value)} style={{ width: 110 }} /></div>
          <div><label htmlFor="bs">起始 seed</label>
            <input id="bs" type="number" value={seed}
                   onChange={(e) => setSeed(e.target.value)} style={{ width: 150 }} /></div>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy} onClick={submit}>提交任务</button>
          <button className="ghost" onClick={refresh}>刷新列表</button>
        </div>
        <p className="sm muted">AI vs AI（规则基线）· 结果含 A 方胜率与平均回合 · 完成后导出 md/csv/json</p>
      </div>

      <div className="card">
        <h3>任务进度</h3>
        {!detail && <p className="muted sm">从左侧提交，或点列表中的任务查看进度/报告</p>}
        {detail && (<>
          <div className="zt"><b className="mono">{detail.id}</b><span className="pill">{detail.status}</span></div>
          {detail.progress && (
            <div style={{ margin: '10px 0' }}>
              <div className="bar"><i style={{ width: pct + '%' }} /></div>
              <span className="sm mono">{detail.progress.done}/{detail.progress.n}（{pct}%）</span>
            </div>)}
          {detail.result && (
            <table className="tbl"><tbody>
              <tr><td>样本 n</td><td className="mono">{detail.result.n}</td></tr>
              <tr><td>A 胜率</td><td className="mono">{detail.result.win_rate_a}</td></tr>
              <tr><td>胜/负/平</td>
                <td className="mono">{detail.result.wins_a}/{detail.result.losses_a}/{detail.result.draws}</td></tr>
              <tr><td>平均回合</td><td className="mono">{detail.result.avg_turns}</td></tr>
              <tr><td>声明</td><td className="sm muted">{detail.result.declaration}</td></tr>
            </tbody></table>)}
          <div className="row" style={{ marginTop: 10 }}>
            {detail.status === 'running' &&
              <button className="ghost" onClick={() => cancel(detail.id)}>取消（保留已完成）</button>}
            {['done', 'cancelled'].includes(detail.status) && (<>
              <button className="ghost" onClick={() => loadReport(detail.id, 'md')}>报告 md</button>
              <button className="ghost" onClick={() => loadReport(detail.id, 'csv')}>CSV</button>
              <button className="ghost" onClick={() => loadReport(detail.id, 'json')}>JSON</button>
            </>)}
          </div>
          {report && <pre className="log" style={{ marginTop: 10 }}>{report}</pre>}
        </>)}
      </div>

      <div className="card" style={{ gridColumn: '1 / -1' }}>
        <h3>任务列表（近 50）</h3>
        <table className="tbl">
          <thead><tr><th>任务</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody>
            {(jobs || []).map((j) => (
              <tr key={j.id}>
                <td className="mono">{j.id}</td>
                <td><span className="dot" style={{ background: j.status === 'done' ? 'var(--ok)' : j.status === 'failed' ? 'var(--brand)' : 'var(--warn)' }} /> {j.status}</td>
                <td className="sm muted">{new Date(j.created * 1000).toLocaleString()}</td>
                <td><button className="pill sm" onClick={() => api.batchStatus(j.id).then(setDetail)}>查看</button></td>
              </tr>
            ))}
            {(jobs || []).length === 0 &&
              <tr><td colSpan="4" className="muted sm">暂无任务（游客任务归属 guest）</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
