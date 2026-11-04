import React from 'react'
import { api } from '../api.js'

/**
 * 对战解说（F5 内 · 模块 3）
 *
 * 合规 C4：AI 生成内容必须以徽标**显著标识**，不得与人工内容无标注混排——
 * 因此「AI 生成」徽标与免责提示是本组件的**固定部分**，不可省略。
 *
 * 可降级：后端未配置云端模型（或本地模式拦截外发）时自动使用离线模板，
 * 本组件仍正常展示，并在「来源」徽标上如实说明降级原因。
 */
export function CommentaryPanel({ battleId, turn, onMsg }) {
  const [busy, setBusy] = React.useState(false)
  const [res, setRes] = React.useState(null)
  const [err, setErr] = React.useState(null)

  async function run(backend) {
    setBusy(true); setErr(null)
    try {
      const r = await api.commentary(battleId, { backend, n_events: 8 })
      setRes(r)
      if (r.generated_by && r.generated_by !== 'llm') {
        onMsg?.('离线解说（未启用云端模型或已降级）')
      }
    } catch (e) {
      setErr(e.message)
    } finally { setBusy(false) }
  }

  const sourceText = (r) => {
    if (r.generated_by === 'llm') return `云端模型 ${r.model || ''}`.trim()
    if (r.generated_by === 'rule(privacy-blocked)') return '离线模板（本地模式拦截外发）'
    if (String(r.generated_by || '').includes('fallback')) return '离线模板（云端调用失败已降级）'
    return '离线模板'
  }

  return (
    <div className="commentary">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <b>对局解说 <span className="pill sm">模块 3 · 纯展示</span></b>
        <button className="ghost sm" disabled={busy} onClick={() => run('auto')}>
          {busy ? '生成中…' : res ? '重新解说' : '生成解说'}
        </button>
      </div>

      {err && (
        <div className="alert err" role="alert" style={{ marginTop: 8 }}>
          <code className="mono">ERR</code>
          <span>{err}</span>
        </div>
      )}

      {res && (
        <>
          {/* C4：AI 生成标识（固定展示，不可隐藏） */}
          <div className="row" style={{ marginTop: 8 }}>
            <span className="pill pill-brand" title="AI 生成内容已显著标识">
              AI 生成
            </span>
            <span className="pill pill-muted">来源：{sourceText(res)}</span>
            {res.elapsed_ms != null && (
              <span className="pill pill-muted">{res.elapsed_ms}ms</span>
            )}
            {res.endpoint && res.endpoint.external && (
              <span className="pill pill-warn">已脱敏外发 · 有审计</span>
            )}
          </div>
          <p className="cmt-text" style={{ margin: '8px 0 0' }}>{res.text}</p>
          <p className="sm muted" style={{ margin: '6px 0 0' }}>{res.ai_notice}</p>
          {res.errors && res.errors.length > 0 && (
            <details>
              <summary>降级原因（{res.errors.length}）</summary>
              <pre className="sm">{res.errors.join('\n')}</pre>
            </details>
          )}
          <details>
            <summary>本回合上下文（公开信息；对手手牌仅数量）</summary>
            <pre className="sm">{res.board_summary}{'\n\n'}{res.recent_events}</pre>
          </details>
        </>
      )}
    </div>
  )
}

export default CommentaryPanel
