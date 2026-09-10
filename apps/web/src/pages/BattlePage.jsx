import React, { useEffect, useRef, useState } from 'react'
import { api, DECKS } from '../api.js'
import { t } from '../i18n.js'
import { Zone, ActionPanel, TurnLog, SeedBar } from '../components/battle.jsx'
import { KvTable } from '../components/stats.jsx'
import { Spinner, EmptyState, ErrBar } from '../components/ui.jsx'

const K3_MS = 2000   // K3 兜底下限：>2s 禁用动作 + 提示（UI 规范 §4.1.1）

export default function BattlePage({ onMsg, onGoDecks }) {
  const [deckA, setDeckA] = useState('lightning')
  const [deckB, setDeckB] = useState('fire')
  const [presets, setPresets] = useState([])
  const [myDecks, setMyDecks] = useState([])
  const [aiIters, setAiIters] = useState(0)
  const [state, setState] = useState(null)
  const [log, setLog] = useState([])
  const [busy, setBusy] = useState(false)
  const [slow, setSlow] = useState(false)
  const [err, setErr] = useState(null)
  const slowTimer = useRef(null)

  useEffect(() => {
    api.deckPresets().then((r) => setPresets(r.presets || [])).catch(() => {})
    api.myDecks().then((r) => setMyDecks(r.decks || [])).catch(() => {})
  }, [])

  async function create(deckAVal = deckA, deckBVal = deckB) {
    setBusy(true); setErr(null)
    try {
      // F3 打通：下拉项若为已导入卡组 id（deck_ 前缀）→ 走 deck_a_id/deck_b_id
      const payload = { ai_iters: Number(aiIters) }
      if (String(deckAVal).startsWith('deck_')) payload.deck_a_id = deckAVal
      else payload.deck_a = deckAVal
      if (String(deckBVal).startsWith('deck_')) payload.deck_b_id = deckBVal
      else payload.deck_b = deckBVal
      const r = await api.createBattle(payload)
      setState(r); setLog([]); onMsg(`对局已创建 seed=${r.seed}`, 'ok')
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) }
    finally { setBusy(false) }
  }

  async function act(a) {
    if (!state || busy) return
    setBusy(true); setErr(null)
    setSlow(false)
    slowTimer.current = setTimeout(() => setSlow(true), K3_MS)
    try {
      const r = await api.act(state.id, a)
      setState((s) => ({ ...s, view: r.view, winner: r.winner, finished: r.winner != null }))
      setLog((l) => [...l,
        `> 我方：${a.verb}${a.index != null ? ' #' + a.index : ''}${a.attack != null ? ' atk' + a.attack : ''}`,
        ...(r.ai_actions || []).map((x) => `< AI：${x.verb}${x.index != null ? ' #' + x.index : ''}`),
        r.winner != null ? `== 终局：${r.winner === (state.player_side ?? 0) ? '我方胜' : 'AI 胜'} ==` : null,
      ].filter(Boolean))
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) }
    finally { clearTimeout(slowTimer.current); setSlow(false); setBusy(false) }
  }

  const view = state?.view
  const legal = view?.legal || []
  const finished = state?.finished || state?.winner != null
  const info = view ? [
    ['回合 / 阶段', `${view.turn} · ${view.phase}`],
    ['我方奖赏 / 牌库', `${view.me_state?.prizes_left ?? '-'} / ${view.me_state?.deck_count ?? '-'}`],
    ['对手奖赏 / 牌库', `${view.opp_state?.prizes_left ?? '-'} / ${view.opp_state?.deck_count ?? '-'}`],
    ['我方弃牌', view.me_state?.discard_count ?? '-'],
    ['合法动作数', legal.length],
  ] : []

  return (
    <div className="grid battle">
      {/* 左：配置 + 预设对手池 */}
      <div className="card">
        <h3>对局配置</h3>
        <label htmlFor="dx">我方卡组（AI 代理你的卡组）</label>
        <select id="dx" value={deckA} onChange={(e) => setDeckA(e.target.value)}>
          {DECKS.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
          {myDecks.filter((d) => d.valid).map((d) => (
            <option key={d.id} value={d.id}>{`我的：${d.name}`}</option>
          ))}
        </select>
        <label htmlFor="dy">对手卡组</label>
        <select id="dy" value={deckB} onChange={(e) => setDeckB(e.target.value)}>
          {DECKS.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
          {myDecks.filter((d) => d.valid).map((d) => (
            <option key={d.id} value={d.id}>{`我的：${d.name}`}</option>
          ))}
        </select>
        <label htmlFor="di">AI 强度</label>
        <select id="di" value={aiIters} onChange={(e) => setAiIters(e.target.value)}>
          <option value={0}>0 · 规则基线 AI（白盒）</option>
          <option value={5}>5 · MCTS 轻</option>
          <option value={12}>12 · MCTS 中</option>
          <option value={30}>30 · MCTS 重（较慢）</option>
        </select>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy} onClick={() => create()}>
            {state ? t('btn.restart') : t('btn.start')}
          </button>
          {state && <SeedBar seed={state.seed} onCopied={(s) => onMsg(`已复制 seed ${s}`)} />}
        </div>

        <h3 style={{ marginTop: 16 }}>预设 Meta 对手池 <span className="pill sm">PRD §10</span></h3>
        <div className="preset-row">
          {presets.map((p) => (
            <button key={p.id} className={`preset ${deckB === p.demo_deck ? 'on' : ''}`}
              title={`${p.note} · 卡池映射：${p.demo_deck}`}
              onClick={() => { setDeckB(p.demo_deck); onMsg(`对手已设为 ${p.label}`) }}>
              <b>{p.id} · {p.label}</b>
              <small className="muted">{p.note}</small>
            </button>
          ))}
          {presets.length === 0 && <span className="sm muted">预设加载中…</span>}
        </div>
      </div>

      {/* 中：棋盘 + 动作 + 日志 */}
      <div className="card">
        <h3>
          对局画面
          {view && <span className="pill">回合 {view.turn} · {view.phase}</span>}
          {finished && <span className="pill pill-brand">已结束：{state.winner === (state.player_side ?? 0) ? '我方胜' : 'AI 胜'}</span>}
          {busy && <Spinner label={t('state.thinking')} />}
        </h3>

        {err && <ErrBar code={err.code} message={err.message} onRetry={() => create()} />}
        {slow && <div className="alert warn" role="alert">决策超过 {K3_MS / 1000}s，仍在计算（K3 兜底：可将 AI 强度调低）</div>}

        {!view && !busy && (
          <EmptyState text={t('empty.battle')} cta="去导入卡组" onCta={onGoDecks} />
        )}

        {view && (<>
          <div className="grid" style={{ gap: 10 }}>
            <Zone title="对手（AI）" st={view.opp_state} side="opp" />
            <Zone title="我方" st={view.me_state} side="me" showHand />
          </div>
          <ActionPanel legal={legal} disabled={busy || finished} busy={busy}
            onAct={act}
            hint={finished ? '对局已结束' : '（等待对手 / 无可用动作）'} />
          <TurnLog lines={log} />
        </>)}
      </div>

      {/* 右：信息 + 说明 */}
      <div className="card">
        <h3>对局信息</h3>
        {view ? <KvTable rows={info} /> : <p className="sm muted">开始对局后显示奖赏/牌库/弃牌与合法动作数。</p>}
        <h3 style={{ marginTop: 12 }}>规则要点</h3>
        <ul className="sm muted" style={{ paddingLeft: 18 }}>
          <li>{t('tip.legal')}</li>
          <li>只显示我方手牌（对手手牌不可见，PRD F5）</li>
          <li>同 seed 可复现同一对局（PRD §7.2 可复现性）</li>
          <li>K3 兜底：决策 &gt; 2s 提示，必要时降级 argmax（引擎侧策略）</li>
          <li>对局会话驻留服务内存，服务重启后需重开</li>
        </ul>
      </div>
    </div>
  )
}
