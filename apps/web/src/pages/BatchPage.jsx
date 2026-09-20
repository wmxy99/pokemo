import React, { useEffect, useRef, useState } from 'react'
import { api, DECKS, shortDeck } from '../api.js'
import { t } from '../i18n.js'
import { TaskCard, TaskTable, QuotaHint } from '../components/tasks.jsx'
import { EmptyState, LogBlock, ErrBar, Pill, SsePill } from '../components/ui.jsx'
import { openJobStream } from '../sse.js'

/**
 * 批量对战（PRD F6/F7）
 * - 单对位任务：卡组 A × 卡组 B × N 局
 * - **卡组池矩阵任务**：decks_a × decks_b 全对位 × games_each（直出 F7 胜率矩阵）
 * - 进行中任务：SSE 实时进度（EventSource），失败自动回退轮询；可取消（保留已完成 K4）
 */
export default function BatchPage({ onMsg }) {
  const [mode, setMode] = useState('pair')          // pair | pool
  const [deckA, setDeckA] = useState('lightning')
  const [deckB, setDeckB] = useState('draco')
  const [poolA, setPoolA] = useState(['lightning', 'fire'])
  const [poolB, setPoolB] = useState(['draco', 'psychic'])
  const [gamesEach, setGamesEach] = useState(60)
  const [games, setGames] = useState(100)
  const [seed, setSeed] = useState(20260910)
  const [jobs, setJobs] = useState([])
  const [myDecks, setMyDecks] = useState([])
  const [detail, setDetail] = useState(null)
  const [report, setReport] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [sseState, setSseState] = useState('connecting')
  const esRef = useRef(null)

  async function refresh() {
    try { setJobs((await api.listBatch()).jobs || []) } catch { /* 忽略 */ }
  }
  useEffect(() => {
    refresh()
    api.myDecks().then((r) => setMyDecks(r.decks || [])).catch(() => {})
    const t2 = setInterval(refresh, 5000)
    return () => clearInterval(t2)
  }, [])

  // 运行中任务：优先 SSE 实时进度（心跳 + 断线续传），异常回退轮询
  useEffect(() => {
    if (!detail || detail.status !== 'running') return undefined
    let fallbackTimer = null
    const stream = openJobStream({
      jobId: detail.id,
      url: api.streamUrl(`/batch/jobs/${detail.id}/stream`),
      onUpdate: (snap) => setDetail((d) => (d && d.id === snap.id ? { ...d, ...snap } : d)),
      onEnd: () => { setSseState('done'); setDetail((d) => (d ? { ...d } : d)); refresh() },
      onState: setSseState,
      onFallback: () => {
        fallbackTimer = setInterval(() => {
          api.batchStatus(detail.id).then((s) => {
            setDetail(s)
            if (['done', 'failed', 'cancelled'].includes(s.status)) {
              clearInterval(fallbackTimer); refresh()
            }
          }).catch(() => {})
        }, 2000)
      },
    })
    esRef.current = stream
    return () => {
      if (esRef.current) { esRef.current.close(); esRef.current = null }
      if (fallbackTimer) clearInterval(fallbackTimer)
    }
  }, [detail && detail.id, detail && detail.status])   // eslint-disable-line react-hooks/exhaustive-deps

  async function submit() {
    setBusy(true); setErr(null); setReport('')
    try {
      if (mode === 'pool') {
        if (!poolA.length || !poolB.length) throw new Error('请至少各选 1 个卡组')
        const r = await api.submitPool({
          decks_a: poolA, decks_b: poolB, games_each: Number(gamesEach),
          seed_start: Number(seed),
        })
        onMsg(`卡组池任务已提交 ${r.id}（${r.pairs} 对位 / ${r.total_games} 局）`, 'ok')
        setDetail(await api.batchStatus(r.id))
      } else {
        const payload = { deck_a: deckA, deck_b: deckB, games: Number(games), seed_start: Number(seed) }
        if (deckA.startsWith('deck_')) { payload.deck_a_id = deckA; delete payload.deck_a }
        if (deckB.startsWith('deck_')) { payload.deck_b_id = deckB; delete payload.deck_b }
        const r = await api.submitBatch(payload)
        onMsg(`任务已提交 ${r.id}（≤500 局 · 并发≤2 · 日配额≤10）`, 'ok')
        setDetail(await api.batchStatus(r.id))
      }
      refresh()
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) } finally { setBusy(false) }
  }

  async function cancel(id) {
    try {
      await api.cancelBatch(id)
      onMsg('已请求取消（保留已完成局，K4）', 'ok')
      setDetail((d) => d && { ...d, status: 'cancelling' })
      refresh()
    } catch (e) { onMsg('取消失败：' + e.message, 'err') }
  }

  async function loadReport(id, fmt) {
    try { setReport(await api.report(id, fmt)); onMsg(`报告（${fmt}）已加载`, 'ok') }
    catch (e) { onMsg('报告不可用：' + e.message, 'err') }
  }

  const toggle = (list, setList, v) => setList(
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  const p = detail?.progress || {}
  const pct = p.n ? Math.round((p.done / p.n) * 100) : 0
  const cells = detail?.result?.cells || []
  const quotaUsed = jobs.filter((j) => {
    const t0 = new Date(); t0.setHours(0, 0, 0, 0)
    return (j.created || 0) >= t0.getTime() / 1000
  }).length
  const running = jobs.filter((j) => ['running', 'queued'].includes(j.status))
  const history = jobs.filter((j) => !['running', 'queued'].includes(j.status))
  const deckOptions = [...DECKS, ...myDecks.filter((d) => d.valid)
    .map((d) => ({ id: d.id, label: `我的：${d.name}` }))]

  return (
    <div className="grid cols-2">
      <div className="card">
        <h3>
          新建任务
          <div className="seg" role="tablist">
            <button role="tab" aria-selected={mode === 'pair'}
              onClick={() => setMode('pair')}>单对位</button>
            <button role="tab" aria-selected={mode === 'pool'}
              onClick={() => setMode('pool')}>卡组池矩阵</button>
          </div>
        </h3>
        <QuotaHint used={quotaUsed} />

        {mode === 'pair' ? (<>
          <div className="row">
            <div><label htmlFor="ba">{t('lbl.deck_mine')}</label>
              <select id="ba" value={deckA} onChange={(e) => setDeckA(e.target.value)}>
                {deckOptions.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
              </select></div>
            <div><label htmlFor="bb">{t('lbl.deck_opp')}</label>
              <select id="bb" value={deckB} onChange={(e) => setDeckB(e.target.value)}>
                {deckOptions.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
              </select></div>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <div><label htmlFor="bg">{t('lbl.games')}</label>
              <input id="bg" type="number" min="1" max="500" value={games}
                onChange={(e) => setGames(e.target.value)} style={{ width: 110 }} /></div>
            <div><label htmlFor="bs">{t('lbl.seed')}</label>
              <input id="bs" type="number" value={seed}
                onChange={(e) => setSeed(e.target.value)} style={{ width: 150 }} /></div>
          </div>
        </>) : (<>
          <label>我方卡组池（多选，最多 6）</label>
          <div className="preset-row">
            {deckOptions.map((d) => (
              <button key={d.id} className={`preset ${poolA.includes(d.id) ? 'on' : ''}`}
                onClick={() => toggle(poolA, setPoolA, d.id)}>
                <b>{poolA.includes(d.id) ? '✓ ' : ''}{d.label}</b>
              </button>
            ))}
          </div>
          <label style={{ marginTop: 10 }}>对手卡组池（多选，最多 6）</label>
          <div className="preset-row">
            {deckOptions.map((d) => (
              <button key={d.id} className={`preset ${poolB.includes(d.id) ? 'on' : ''}`}
                onClick={() => toggle(poolB, setPoolB, d.id)}>
                <b>{poolB.includes(d.id) ? '✓ ' : ''}{d.label}</b>
              </button>
            ))}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <div><label htmlFor="ge">每对位局数</label>
              <input id="ge" type="number" min="1" max="500" value={gamesEach}
                onChange={(e) => setGamesEach(e.target.value)} style={{ width: 110 }} /></div>
            <div><label htmlFor="ps">{t('lbl.seed')}</label>
              <input id="ps" type="number" value={seed}
                onChange={(e) => setSeed(e.target.value)} style={{ width: 150 }} /></div>
            <Pill tone="warn">
              共 {poolA.length}×{poolB.length}×{gamesEach} = {poolA.length * poolB.length * Number(gamesEach)} 局（上限 2000）
            </Pill>
          </div>
        </>)}

        <p className="sm muted">先后手由引擎按 seed 交替决定，以消除先手优势对胜率的干扰；正式验收建议使用 200 局样本。</p>
        <div className="row">
          <button className="primary" disabled={busy} onClick={submit}>{t('btn.submit')}</button>
          <button className="ghost" onClick={() => {
            setMode('pool'); setPoolA(['lightning', 'fire'])
            setPoolB(['draco', 'psychic']); setGamesEach(60)
            onMsg('已载入示例：2×2 卡组池 × 60 局')
          }}>填入示例池</button>
          <button className="ghost" onClick={refresh}>刷新</button>
        </div>
        {err && <ErrBar code={err.code} message={err.message} onRetry={submit} />}
      </div>

      <div className="card">
        <h3>任务进度
          {detail?.status === 'running' && <Pill tone="ok">SSE 实时</Pill>}
        </h3>
        {!detail && <EmptyState text={t('empty.tasks')} cta={t('btn.submit')} onCta={submit} />}
        {detail && (<>
          <div className="zt"><b className="mono sm">{detail.id}</b>
            <span className="pill">{detail.status}</span>
            {detail.status === 'running' && (
              <SsePill state={sseState} />)}
          </div>
          {p.n ? (<>
            <div className="bar" role="progressbar" aria-valuenow={p.done} aria-valuemax={p.n}>
              <i style={{ width: pct + '%' }} /></div>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span className="sm mono">{p.done}/{p.n}（{pct}%）</span>
              {p.pair && <span className="sm muted">当前对位：{p.pair}</span>}
            </div>
          </>) : <p className="sm muted">等待调度…（乐观 ETA 将在 1s 内出现）</p>}
          {detail.result && !cells.length && (
            <div className="row sm">
              <Pill>A 胜率 <b className="mono">{detail.result.win_rate_a ?? '—'}</b></Pill>
              <Pill>n <b className="mono">{detail.result.n ?? '—'}</b></Pill>
              <Pill>均回合 <b className="mono">{detail.result.avg_turns ?? '—'}</b></Pill>
            </div>)}
          {cells.length > 0 && (
            <table className="tbl" style={{ marginTop: 8 }}>
              <thead><tr>
                <th scope="col">我方 \\ 对手</th><th scope="col">n</th>
                <th scope="col">A 胜率</th><th scope="col">95%CI</th>
                <th scope="col">均回合</th>
              </tr></thead>
              <tbody>
                {cells.map((c, i) => (
                  <tr key={i}>
                    <td className="sm">{c.deck_a_zh || shortDeck(c.deck_a)}
                      {' vs '}
                      {c.deck_b_zh || shortDeck(c.deck_b)}</td>
                    <td className="mono">{c.n}</td>
                    <td className={`mono ${c.win_rate_a >= 0.5 ? 'win' : 'lose'}`}>
                      {(c.win_rate_a * 100).toFixed(1)}%</td>
                    <td className="mono sm">±{c.ci95}</td>
                    <td className="mono">{c.avg_turns}</td>
                  </tr>
                ))}
              </tbody>
            </table>)}
          <div className="row" style={{ marginTop: 10 }}>
            {['running', 'cancelling'].includes(detail.status) && (
              <button className="ghost sm" onClick={() => {
                if (window.confirm(t('confirm.cancel_task'))) cancel(detail.id)
              }}>{t('btn.cancel')}</button>)}
            {['done', 'cancelled'].includes(detail.status) && (<>
              <button className="ghost sm" onClick={() => loadReport(detail.id, 'md')}>报告 md</button>
              <button className="ghost sm" onClick={() => loadReport(detail.id, 'csv')}>CSV</button>
              <button className="ghost sm" onClick={() => loadReport(detail.id, 'json')}>JSON</button>
            </>)}
          </div>
          {report && <LogBlock lines={report} label="报告预览" max={240} />}
        </>)}
      </div>

      <div className="card" style={{ gridColumn: '1 / -1' }}>
        <h3>任务列表（近 50） <span className="pill sm">运行中任务自动 SSE 实时刷新</span></h3>
        <TaskTable jobs={[...running, ...history]}
          onView={(j) => { setReport(''); api.batchStatus(j.id).then(setDetail) }}
          extraCols={[{ label: '类型', render: (j) => (j.params && j.params.pool) ? '池矩阵' : '单对位' }]} />
        {running.length > 0 && <div className="row" style={{ marginTop: 10 }}>
          {running.map((j) => (
            <TaskCard key={j.id} job={j} progress={detail && detail.id === j.id ? detail.progress : j.progress}
              etaText="剩余局数 × 经验单局时"
              onView={(x) => api.batchStatus(x.id).then(setDetail)}
              onCancel={cancel} />
          ))}
        </div>}
      </div>
    </div>
  )
}
