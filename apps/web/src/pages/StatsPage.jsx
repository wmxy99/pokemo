import React, { useEffect, useState } from 'react'
import { api } from '../api.js'

/**
 * 统计报告页：汇总本 owner 的批量任务（F7 报告导出/预览）
 * 说明：胜率矩阵跨卡组对位需多次任务，此处按任务聚合展示 + 一键导出。
 */
export default function StatsPage({ onMsg, jobs, setJobs }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const list = (await api.listBatch()).jobs || []
      setJobs(list)
      const done = list.filter((j) => j.status === 'done' || j.status === 'cancelled')
      const detailed = await Promise.all(done.map((j) =>
        api.batchStatus(j.id).then((d) => ({ ...j, result: d.result, params: d.params }))
          .catch(() => null)))
      setRows(detailed.filter(Boolean))
    } catch (e) { onMsg('加载统计失败：' + e.message, 'err') } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const totalGames = rows.reduce((s, r) => s + (r.result?.n || 0), 0)
  const avgWin = rows.length
    ? (rows.reduce((s, r) => s + (r.result?.win_rate_a || 0), 0) / rows.length).toFixed(3)
    : '—'

  async function exportCsv(id) {
    try {
      const csv = await api.report(id, 'csv')
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${id}.csv`
      a.click()
      URL.revokeObjectURL(a.href)
      onMsg('CSV 已下载（含 Unofficial 声明见 md 报告）', 'ok')
    } catch (e) { onMsg('导出失败：' + e.message, 'err') }
  }

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="card">
        <h3>汇总 <span className="pill sm">Unofficial community data（PRD C1）</span></h3>
        <div className="stats">
          <div className="stat"><div className="n">{rows.length}</div><div className="l">已完成任务</div></div>
          <div className="stat"><div className="n">{totalGames}</div><div className="l">累计对局</div></div>
          <div className="stat"><div className="n">{avgWin}</div><div className="l">平均 A 方胜率</div></div>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="ghost" onClick={load} disabled={loading}>{loading ? '加载中…' : '刷新'}</button>
        </div>
      </div>

      <div className="card">
        <h3>任务明细（可用于平衡性对比）</h3>
        <table className="tbl">
          <thead>
            <tr>
              <th>任务</th><th>卡组对位（A vs B）</th><th>n</th><th>A 胜率</th>
              <th>平均回合</th><th>状态</th><th>导出</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono sm">{r.id}</td>
                <td className="mono sm">{r.params?.deck_a} vs {r.params?.deck_b}</td>
                <td className="mono">{r.result?.n ?? '—'}</td>
                <td className="mono" style={{ color: (r.result?.win_rate_a ?? 0) >= 0.5 ? 'var(--brand-deep)' : 'var(--ok)' }}>
                  {r.result?.win_rate_a ?? '—'}
                </td>
                <td className="mono">{r.result?.avg_turns ?? '—'}</td>
                <td className="sm">{r.status}</td>
                <td>
                  <button className="pill sm" onClick={() => exportCsv(r.id)}>CSV</button>
                  <button className="pill sm" style={{ marginLeft: 6 }}
                          onClick={() => api.report(r.id, 'md').then((t) => onMsg('报告已拉取（见批量页预览）', 'ok')).catch((e) => onMsg(e.message, 'err'))}>
                    MD
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan="7" className="muted sm">暂无已完成任务：去「批量对战」提交 ≥N 局任务后回到本页</td></tr>
            )}
          </tbody>
        </table>
        <p className="sm muted">
          胜率颜色遵循中文数据惯例（红=我方占优）。跨卡组「胜率矩阵」可按卡组池连续提交任务后横向对比（F7 P1 增补）。
        </p>
      </div>
    </div>
  )
}
