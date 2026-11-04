import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { StatCard, Pill, ErrBar, LogBlock, Spinner, SsePill } from '../components/ui.jsx'
import { TaskTable } from '../components/tasks.jsx'
import { openJobStream } from '../sse.js'

/**
 * 后台页面（UI 规范 §4.2）
 * - TrainingPage（M2/RL）：数据总览 + 训练任务提交/监控/日志/取消
 * - ModelConfigPage：见 ModelConfig.jsx
 */

/** 数据总览：原仪表盘内嵌到 RL 训练页（KPI / 近 7 日） */
function TrainingOverview({ jobs = [] }) {
  const [sum, setSum] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api.statsSummary().then(setSum).catch((e) => setErr({ code: e.status || 'ERR', message: e.message }))
  }, [])

  const k = sum?.kpi || {}
  const max7 = Math.max(1, ...(sum?.recent7d || []).map((d) => d.tasks))
  return (
    <div className="grid" style={{ gap: 16 }}>
      {err && <ErrBar code={err.code} message={err.message} onRetry={() => window.location.reload()} />}

      <div className="card">
        <h3>关键指标概览</h3>
        <div className="stats">
          <StatCard n={k.total_games ?? '—'} label="累计对局（本人）" />
          <StatCard n={k.tasks ?? '—'} label="已完成批量任务" />
          <StatCard n={k.avg_turns ?? '—'} label="批量平均回合" />
          <StatCard n={jobs.length} label="训练任务" />
        </div>
      </div>

      <div className="card">
        <h3>批量对局统计（近 7 日）</h3>
        <div className="spark">
          {(sum?.recent7d || []).map((d) => (
            <div key={d.date} className="spark-col" title={`${d.date}：${d.tasks} 个任务`}>
              <i style={{ height: `${Math.round((d.tasks / max7) * 100)}%` }} />
              <span className="sm muted mono">{d.date.slice(5)}</span>
            </div>
          ))}
          {!sum && <span className="sm muted">加载中…</span>}
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="ghost sm" disabled title="导出能力预留，后续版本提供">导出 CSV（开发中）</button>
          <button className="ghost sm" disabled title="导出能力预留，后续版本提供">导出 MD（开发中）</button>
          <button className="ghost sm" disabled title="导出能力预留，后续版本提供">导出 JSON（开发中）</button>
        </div>
      </div>
    </div>
  )
}

export function TrainingPage({ onMsg, authed }) {
  const [jobs, setJobs] = useState([])
  const [detail, setDetail] = useState(null)
  const [form, setForm] = useState({ stage1_episodes: 200, stage2_episodes: 0, stage3_episodes: 0, bc_episodes: 40, features: 'legacy' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [sseState, setSseState] = useState('connecting')

  async function load() { try { setJobs((await api.listTrain()).jobs || []) } catch { /* 忽略 */ } }
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t) }, [])
  // 实时进度：SSE（心跳 + 断线续传）优先，异常自动回退轮询（2s）
  useEffect(() => {
    if (!detail || detail.status !== 'running') return undefined
    let timer = null
    const stream = openJobStream({
      jobId: detail.id,
      url: api.streamUrl(`/train/jobs/${detail.id}/stream`, { lines: 60 }),
      onUpdate: (snap) => setDetail((d) => (d ? { ...d, ...snap } : d)),
      onEnd: () => { setSseState('done'); load() },
      onState: setSseState,
      onFallback: () => {
        timer = setInterval(() => {
          api.trainStatus(detail.id).then((s) => {
            setDetail(s)
            if (s.status !== 'running') { clearInterval(timer); load() }
          }).catch(() => {})
        }, 2000)
      },
    })
    return () => {
      stream.close()
      if (timer) clearInterval(timer)
    }
  }, [detail && detail.id, detail && detail.status])   // eslint-disable-line react-hooks/exhaustive-deps

  async function submit() {
    setBusy(true); setErr(null)
    try {
      const r = await api.submitTrain({ ...form, stage1_episodes: Number(form.stage1_episodes), stage2_episodes: Number(form.stage2_episodes), stage3_episodes: Number(form.stage3_episodes), bc_episodes: Number(form.bc_episodes) })
      onMsg(`训练任务已启动 ${r.id}（子进程 run_k1_longtrain.py）`)
      setDetail(await api.trainStatus(r.id)); load()
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) } finally { setBusy(false) }
  }

  const curve = (detail?.result?.curve || []).map((c) => `${c.at}  win=${c.win_rate}`).join('\n')
  return (
    <div className="grid" style={{ gap: 16 }}>
      <TrainingOverview jobs={jobs} />
      <div className="grid cols-2">
      <div className="card">
        <h3>RL 训练任务（PPO 课程） <Pill>BC → vs 基线 → 冻结快照 → MCTS 蒸馏</Pill></h3>
        {!authed && <div className="alert warn" role="alert">训练管理需要登录账号：未登录提交会被拒绝（403）。</div>}
        <div className="row">
          <div><label htmlFor="s1">阶段1 vs 基线（局）</label>
            <input id="s1" type="number" value={form.stage1_episodes} style={{ width: 120 }}
              onChange={(e) => setForm({ ...form, stage1_episodes: e.target.value })} /></div>
          <div><label htmlFor="s2">阶段2 冻结快照</label>
            <input id="s2" type="number" value={form.stage2_episodes} style={{ width: 110 }}
              onChange={(e) => setForm({ ...form, stage2_episodes: e.target.value })} /></div>
          <div><label htmlFor="s3">阶段3 MCTS 蒸馏</label>
            <input id="s3" type="number" value={form.stage3_episodes} style={{ width: 110 }}
              onChange={(e) => setForm({ ...form, stage3_episodes: e.target.value })} /></div>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <div><label htmlFor="fe">特征域</label>
            <select id="fe" value={form.features} onChange={(e) => setForm({ ...form, features: e.target.value })}>
              <option value="legacy">legacy（51 维）</option>
              <option value="full">full（59 维 hand+meta）</option>
            </select></div>
          <div><label htmlFor="bc">BC 预热局数</label>
            <input id="bc" type="number" value={form.bc_episodes} style={{ width: 100 }}
              onChange={(e) => setForm({ ...form, bc_episodes: e.target.value })} /></div>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy || !authed} onClick={submit}>
            {busy ? <Spinner label="提交中…" /> : '启动训练'}</button>
          <button className="ghost" onClick={load}>刷新列表</button>
        </div>
        {err && <ErrBar code={err.code} message={err.message} onRetry={submit} />}

        <h3 style={{ marginTop: 14 }}>长训手册（README 6 条 · 折叠）</h3>
        <details className="sm muted">
          <summary>按投入排序的冲刺 70% 建议</summary>
          <ol style={{ paddingLeft: 18 }}>
            <li>大样本评估降噪（n≥100）</li>
            <li>更久训练（stage1 4 万+）</li>
            <li>lr 余弦衰减 / entropy 0.05 / clip 0.3</li>
            <li>hidden 128 + 特征域 full</li>
            <li>快照更频繁 + MCTS 教练</li>
            <li>200 局双盲验收协议</li>
          </ol>
        </details>
      </div>

      <div className="card">
        <h3>监控 {detail && <span className="pill sm mono">{detail.id} · {detail.status}</span>}</h3>
        {!detail && <p className="sm muted">暂无任务</p>}
        {detail && (<>
          <div className="row">
            {detail.status === 'running' && (<>
              <SsePill state={sseState} />
              <button className="ghost sm" onClick={async () => { await api.cancelTrain(detail.id); onMsg('已终止训练子进程') }}>取消训练</button>
            </>)}
            <button className="ghost sm" onClick={() => api.trainLog(detail.id, 80).then((r) => setDetail({ ...detail, log_tail: r.lines }))}>刷新日志</button>
          </div>
          {curve && <LogBlock label="训练曲线" lines={curve} max={140} />}
          <LogBlock label="训练日志" lines={detail.log_tail || []} live max={220} />
        </>)}
      </div>

      <div className="card" style={{ gridColumn: '1 / -1' }}>
        <h3>训练任务列表</h3>
        <TaskTable jobs={jobs} onView={(j) => api.trainStatus(j.id).then(setDetail)} />
      </div>
      </div>
    </div>
  )
}
