import React from 'react'
import { Pill, LogBlock } from './ui.jsx'
import { t } from '../i18n.js'

/** 统计与报告组件（UI 规范 §5.4 胜率矩阵 / §5.5 导出条） */

export function WinrateMatrix({ rows }) {
  const decksA = [...new Set(rows.map((r) => r.deck_a))]
  const decksB = [...new Set(rows.map((r) => r.deck_b))]
  const cell = (a, b) => rows.find((r) => r.deck_a === a && r.deck_b === b)
  return (
    <div>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <b>胜率矩阵（我方 \\ 对手）</b>
        <Pill tone="warn">{t('tip.unofficial')}</Pill>
      </div>
      <table className="tbl matrix" aria-describedby="matrix-note">
        <thead>
          <tr>
            <th scope="col">我方 \ 对手</th>
            {decksB.map((b) => <th key={b} scope="col" className="mono">{b}</th>)}
          </tr>
        </thead>
        <tbody>
          {decksA.map((a) => (
            <tr key={a}>
              <th scope="row" className="mono">{a}</th>
              {decksB.map((b) => {
                if (a === b) return <td key={b} className="diag" title="自对自身无意义">—</td>
                const c = cell(a, b)
                if (!c) return <td key={b} className="muted">·</td>
                const tone = c.win_rate_a >= 0.5 ? 'win' : 'lose'
                return (
                  <td key={b} className={`mono ${tone}`}
                    title={`95%CI ±${c.ci95} · n=${c.n}（样本量标注）`}>
                    {(c.win_rate_a * 100).toFixed(1)}%
                    <span className="sm muted"> n{c.n}</span>
                  </td>
                )
              })}
            </tr>
          ))}
          {decksA.length === 0 && (
            <tr><td colSpan={decksB.length + 1} className="muted sm">暂无对位数据（完成批量任务后生成）</td></tr>
          )}
        </tbody>
      </table>
      <p id="matrix-note" className="sm muted">
        hover 单元格可查看 95%CI 与样本量；n&lt;30 时置信区间较宽，勿据此下结论（PRD §7.1 K1 口径）。
      </p>
    </div>
  )
}

export function ExportBar({ onExport, n, preview }) {
  return (
    <div>
      <div className="row exportbar">
        <button className="ghost sm" onClick={() => onExport('csv')}>CSV</button>
        <button className="ghost sm" onClick={() => onExport('md')}>Markdown</button>
        <button className="ghost sm" onClick={() => onExport('json')}>JSON</button>
        <button className="ghost sm" onClick={() => onExport('copy')}>复制分享文本</button>
        <span className="pill sm mono">n = {n ?? '—'}</span>
        <Pill tone="warn">{t('tip.unofficial')}</Pill>
      </div>
      {preview !== undefined && (<>
        <span className="sm muted">MD 预览（用于复制校对）</span>
        <LogBlock lines={preview || '（选择任务后展示导出样本）'} max={200} />
      </>)}
    </div>
  )
}

export function KvTable({ title, rows }) {
  return (
    <div>
      {title && <h3>{title}</h3>}
      <table className="tbl"><tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><td className="muted sm">{k}</td><td className="mono sm">{v ?? '—'}</td></tr>
        ))}
      </tbody></table>
    </div>
  )
}
