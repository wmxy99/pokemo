import React, { useState } from 'react'
import { api } from '../api.js'

function PokeChip({ p }) {
  if (!p) return <span className="muted sm">—</span>
  const hp = p.hp ?? 0
  const dmg = p.dmg ?? 0
  const left = Math.max(0, hp - dmg)
  const pct = hp ? Math.round((left / hp) * 100) : 0
  return (
    <div>
      <div className="chip">{p.name || p.card_id || '?'}</div>
      <div className="sm mono">{left}/{hp} HP · 能 {p.energy ?? 0}</div>
      <div className="bar" role="progressbar" aria-valuenow={left} aria-valuemax={hp}>
        <i style={{ width: pct + '%', background: pct > 50 ? 'linear-gradient(90deg,#16A34A,#22C55E)' : undefined }} />
      </div>
    </div>
  )
}

function Side({ title, st }) {
  if (!st) return null
  return (
    <div className="zone">
      <div className="zt"><span>{title}</span><span>奖赏 {st.prizes_left ?? '-'} · 牌库 {st.deck_count ?? '-'}</span></div>
      <div className="row" style={{ marginTop: 8 }}>
        <PokeChip p={st.active} />
        <div className="sm muted">后备 {(st.bench || []).length} · 手牌 {st.hand_count ?? (st.hand || []).length}</div>
      </div>
    </div>
  )
}

export default function BattlePage({ decks, onMsg }) {
  const [deckA, setDeckA] = useState('lightning')
  const [deckB, setDeckB] = useState('fire')
  const [aiIters, setAiIters] = useState(0)
  const [bid, setBid] = useState(null)
  const [state, setState] = useState(null)
  const [log, setLog] = useState([])
  const [busy, setBusy] = useState(false)

  async function create() {
    setBusy(true)
    try {
      const r = await api.createBattle({ deck_a: deckA, deck_b: deckB, ai_iters: Number(aiIters) })
      setBid(r.id); setState(r); setLog([])
      onMsg(`对局已创建 seed=${r.seed}（可重放）`, 'ok')
    } catch (e) { onMsg('建局失败：' + e.message, 'err') } finally { setBusy(false) }
  }

  async function act(a) {
    if (!bid || busy) return
    setBusy(true)
    try {
      const r = await api.act(bid, a)
      setState((s) => ({ ...s, view: r.view, winner: r.winner, turn: r.view?.turn, finished: r.winner != null }))
      setLog((l) => [...l,
        `[我方] ${a.verb} ${a.index != null ? '#' + a.index : ''}`,
        ...(r.ai_actions || []).map((x) => `[AI] ${x.verb} ${x.index != null ? '#' + x.index : ''}`),
        r.winner != null ? `== 终局：${r.winner === (state?.player_side ?? 0) ? '你赢了' : 'AI 获胜'} ==` : null,
      ].filter(Boolean))
    } catch (e) { onMsg('动作被拒：' + e.message, 'err') } finally { setBusy(false) }
  }

  const view = state?.view
  const legal = view?.legal || []
  const finished = state?.finished || state?.winner != null

  return (
    <div className="grid battle">
      <div className="card">
        <h3>对局配置</h3>
        <label>我方卡组</label>
        <select value={deckA} onChange={(e) => setDeckA(e.target.value)}>{decks.map((d) => <option key={d}>{d}</option>)}</select>
        <label>AI 卡组</label>
        <select value={deckB} onChange={(e) => setDeckB(e.target.value)}>{decks.map((d) => <option key={d}>{d}</option>)}</select>
        <label>AI 强度（0=规则基线；&gt;0=MCTS 迭代）</label>
        <input type="number" min="0" max="200" value={aiIters} onChange={(e) => setAiIters(e.target.value)} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy} onClick={create}>{bid ? '重开对局' : '开始对局'}</button>
          {state && <button className="ghost" onClick={() => { navigator.clipboard?.writeText('seed=' + state.seed); onMsg('已复制 seed（对局可重放）', 'ok') }}>复制 seed</button>}
        </div>
        {state && <p className="sm muted">id {bid} · seed <span className="mono">{state.seed}</span> · 你是 Player {state.player_side}</p>}
      </div>

      <div className="card">
        <h3>对局画面 {view && <span className="pill">回合 {view.turn} · {view.phase}</span>}
          {finished && <span className="pill" style={{ borderColor: 'var(--brand)', color: 'var(--brand-deep)' }}>已结束</span>}
        </h3>
        {!view && <p className="muted">尚未开始：左侧选择卡组后「开始对局」（无需登录，游客可试玩）</p>}
        {view && (
          <>
            <div className="grid" style={{ gap: 10 }}>
              <Side title="对手（AI）" st={view.opp_state} />
              <Side title="我方" st={view.me_state} />
            </div>
            <h3 style={{ marginTop: 14 }}>合法动作（= 引擎 legal_actions，1:1）</h3>
            <div className="acts">
              {legal.length === 0 && <span className="muted sm">无可用动作（对手回合或已结束）</span>}
              {legal.map((a, i) => (
                <button key={i} className="act" disabled={busy || finished} onClick={() => act(a)}>
                  {a.verb}{a.index != null ? ` #${a.index}` : ''}{a.attack != null ? ` atk${a.attack}` : ''}
                  {a.target != null ? ` →${a.target}` : ''}
                </button>
              ))}
            </div>
          </>
        )}
        {log.length > 0 && (<>
          <h3 style={{ marginTop: 14 }}>回合日志</h3>
          <pre className="log">{log.join('\n')}</pre>
        </>)}
      </div>

      <div className="card">
        <h3>说明</h3>
        <ul className="sm muted" style={{ paddingLeft: 18 }}>
          <li>动作面板与引擎 <span className="mono">legal_actions</span> 完全一致（不一致即 bug，PRD F5）</li>
          <li>AI 对手：0 = 规则基线 AI（白盒）；&gt;0 = MCTS（引擎 rollout）</li>
          <li>同 seed 可复现同一对局（PRD §7.2 可复现性）</li>
          <li>对局会话驻留服务内存，服务重启后需重开</li>
        </ul>
      </div>
    </div>
  )
}
