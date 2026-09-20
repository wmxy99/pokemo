import React from 'react'
import { Pill, LogBlock } from './ui.jsx'
import { t } from '../i18n.js'
import { shortDeck } from '../api.js'

/** 统计与报告组件（UI 规范 §5.4 胜率矩阵 / §5.5 导出条） */

// 热力色阶：低胜率=绿 → 中性 → 高胜率=品牌红（沿用 .win/.lose 语义，红=我方优势）
// 饱和度随样本量（n/30）衰减：n 小 → 颜色趋中性，避免"看起来很强其实只有 5 局"
const HEAT_LO = [22, 163, 74]        // #16A34A
const HEAT_MID = [248, 250, 252]     // #F8FAFC
const HEAT_HI = [220, 38, 38]        // #DC2626

export function heatStyle(wr, n) {
  const conf = Math.max(0, Math.min(1, (n || 0) / 30))
  const tv = Math.max(0, Math.min(1, wr || 0))
  const k = tv < 0.5 ? (0.5 - tv) / 0.5 : (tv - 0.5) / 0.5
  const edge = tv < 0.5 ? HEAT_LO : HEAT_HI
  const strength = k * (0.35 + 0.65 * conf)
  const rgb = [0, 1, 2].map((i) =>
    Math.round(HEAT_MID[i] + (edge[i] - HEAT_MID[i]) * strength))
  const dark = strength > 0.58
  return {
    background: `rgb(${rgb.join(',')})`,
    color: dark ? '#FFFFFF' : 'var(--ink)',
    // 深底时次级文字改用半透明白，保证对比度
    sub: dark ? 'rgba(255,255,255,.78)' : 'var(--muted)',
  }
}

function _agg(list) {
  const n = list.reduce((s, r) => s + (r.n || 0), 0)
  const wins = list.reduce((s, r) => s + (r.wins_a ?? (r.win_rate_a || 0) * (r.n || 0)), 0)
  return { n, wr: n ? wins / n : null }
}

export function WinrateMatrix({ rows }) {
  // 展示名：优先 API 下发的中文字段，其次本地短标签映射（id 保持英文作数据键）
  const zhA = (r) => r.deck_a_zh || shortDeck(r.deck_a)
  const zhB = (r) => r.deck_b_zh || shortDeck(r.deck_b)
  const decksA = [...new Set(rows.map((r) => r.deck_a))]
  const decksB = [...new Set(rows.map((r) => r.deck_b))]
  const nameOf = (id, list) => {
    const hit = list.find((r) => r.deck_a === id) || list.find((r) => r.deck_b === id)
    if (!hit) return shortDeck(id)
    return hit.deck_a === id ? zhA(hit) : zhB(hit)
  }
  const cell = (a, b) => rows.find((r) => r.deck_a === a && r.deck_b === b)
  const rowAgg = (a) => _agg(rows.filter((r) => r.deck_a === a))
  const colAgg = (b) => _agg(rows.filter((r) => r.deck_b === b))
  const hasData = rows.length > 0
  return (
    <div className="matrix-wrap">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <b>胜率矩阵（我方 \\ 对手）</b>
        <Pill tone="warn">{t('tip.unofficial')}</Pill>
      </div>

      {/* 图例：色阶 + 样本量饱和度说明 */}
      <div className="row heat-legend sm muted">
        <span className="mono">0%</span>
        <span className="heat-bar" aria-hidden="true" />
        <span className="mono">100%</span>
        <span className="heat-note">
          底色=胜率（红高绿低）；<b>颜色越淡=样本越少</b>（n&lt;30 置信度低）
        </span>
      </div>

      <div className="matrix-scroll">
        <table className="tbl matrix" aria-describedby="matrix-note">
          <caption className="sr">我方卡组（行）对对手卡组（列）的胜率矩阵，含样本量与 95% 置信区间</caption>
          <thead>
            <tr>
              <th scope="col">我方 \ 对手</th>
              {decksB.map((b) => <th key={b} scope="col">{nameOf(b, rows)}</th>)}
              <th scope="col" className="agg-h">行平均</th>
            </tr>
          </thead>
          <tbody>
            {decksA.map((a) => {
              const ra = rowAgg(a)
              return (
                <tr key={a}>
                  <th scope="row">{nameOf(a, rows)}</th>
                  {decksB.map((b) => {
                    if (a === b) {
                      return <td key={b} className="diag" title="自对自身无意义（对角线）">—</td>
                    }
                    const c = cell(a, b)
                    if (!c) return <td key={b} className="muted">·</td>
                    const pct = (c.win_rate_a * 100).toFixed(1)
                    const st = heatStyle(c.win_rate_a, c.n)
                    const label = `${nameOf(a, rows)} vs ${nameOf(b, rows)}：胜率 ${pct}%（n=${c.n}，95%CI ±${c.ci95}）`
                    return (
                      <td key={b} className="mono heat" style={{ background: st.background, color: st.color }}
                        tabIndex={0} role="cell" aria-label={label} title={label}>
                        <span className="pct">{pct}%</span>
                        <span className="sm cell-n" style={{ color: st.sub }}>n{c.n}</span>
                        <span className="sr">{`95% 置信区间 ±${c.ci95}`}</span>
                      </td>
                    )
                  })}
                  <td className="mono agg">
                    {ra.wr === null ? '—' : `${(ra.wr * 100).toFixed(1)}%`}
                    <span className="sm muted"> n{ra.n}</span>
                  </td>
                </tr>
              )
            })}
            {hasData && (
              <tr className="agg-row">
                <th scope="row" className="sm">列平均</th>
                {decksB.map((b) => {
                  const ca = colAgg(b)
                  return (
                    <td key={b} className="mono agg">
                      {ca.wr === null ? '—' : `${(ca.wr * 100).toFixed(1)}%`}
                      <span className="sm muted"> n{ca.n}</span>
                    </td>
                  )
                })}
                <td className="agg muted">—</td>
              </tr>
            )}
            {!hasData && (
              <tr><td colSpan={decksB.length + 2} className="muted sm">暂无对位数据（完成批量任务后生成）</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p id="matrix-note" className="sm muted">
        hover / 聚焦单元格可查看 95%CI 与样本量；行=我方卡组视角（A 侧），列=对手。
        n&lt;30 时置信区间较宽、底色更淡，请勿据此下结论。
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
