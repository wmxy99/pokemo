import React, { useEffect, useRef, useState } from 'react'
import { api, DECKS } from '../api.js'
import { t } from '../i18n.js'
import { verbLabel } from '../verbs.js'
import { Zone, ActionPanel, CommandBar, TurnLog, SeedBar } from '../components/battle.jsx'
import { CardZoom } from '../components/card.jsx'
import { CommentaryPanel } from '../components/commentary.jsx'
import { actionContext } from '../names.js'
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
  const [commands, setCommands] = useState([])     // 引擎指令清单（中文）
  const [lastMatched, setLastMatched] = useState(null)
  const [cmdErr, setCmdErr] = useState(null)       // 指令解析失败（含歧义候选）
  const [zoomCard, setZoomCard] = useState(null)   // 点击手牌 → 详情大卡
  const [hitName, setHitName] = useState(null)     // 本回合受伤的宝可梦（抖动提示）
  const prevDmg = useRef(new Map())
  const slowTimer = useRef(null)

  useEffect(() => {
    api.deckPresets().then((r) => setPresets(r.presets || [])).catch(() => {})
    api.myDecks().then((r) => setMyDecks(r.decks || [])).catch(() => {})
  }, [])

  // 受伤提示：对比上一帧伤害值，让刚受伤的宝可梦抖一下（纯视觉，不改数据）
  useEffect(() => {
    const v = state?.view
    if (!v) return undefined
    const cur = new Map()
    const all = [v.me_state?.active, v.opp_state?.active,
      ...(v.me_state?.bench || []), ...(v.opp_state?.bench || [])]
    all.filter(Boolean).forEach((p) => cur.set(p.name, p.dmg || 0))
    let hit = null
    cur.forEach((dmg, name) => {
      if ((prevDmg.current.get(name) ?? 0) < dmg) hit = name
    })
    prevDmg.current = cur
    if (!hit) return undefined
    setHitName(hit)
    const tm = setTimeout(() => setHitName(null), 420)
    return () => clearTimeout(tm)
  }, [state])

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
      setState(r); setLog([]); setCommands(r.commands || [])
      setLastMatched(null); setCmdErr(null)
      onMsg(`对局已创建 seed=${r.seed}`, 'ok')
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
      setCommands(r.commands || [])
      const ctx = actionContext(state.view)          // 提交前的局面（含中文名）
      const zh = (x) => verbLabel(x, ctx)
      setLog((l) => [...l,
        `> 我方：${zh(a)}`,
        ...(r.ai_actions || []).map((x) => `< AI：${x.verb === 'attack' ? '攻击' : verbLabel(x)}`),
        r.winner != null ? `== 终局：${r.winner === (state.player_side ?? 0) ? '我方胜' : 'AI 胜'} ==` : null,
      ].filter(Boolean))
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) }
    finally { clearTimeout(slowTimer.current); setSlow(false); setBusy(false) }
  }

  // 自由文本指令：中文招式名 / 卡名 / 位置（如「幻影俯冲」「附能量到后备1」）
  async function submitCommand(text) {
    if (!state || busy) return
    setBusy(true); setErr(null); setCmdErr(null)
    setSlow(false)
    slowTimer.current = setTimeout(() => setSlow(true), K3_MS)
    try {
      const before = state.view
      const r = await api.actCommand(state.id, text)
      setState((s) => ({ ...s, view: r.view, winner: r.winner, finished: r.winner != null }))
      setCommands(r.commands || [])
      setLastMatched(r.matched || text)
      const ctx = actionContext(before)
      // 组合指令：逐步记录（单步时 steps 长度为 1，展示一致）
      const steps = r.steps || []
      const multi = steps.length > 1
      setLog((l) => [...l,
        ...steps.map((st, i) => (st.ok
          ? `> 我方${multi ? ` [${i + 1}/${steps.length}]` : ''}（指令「${st.input}」→ ${st.matched || '已执行'}）：${verbLabel(st.used_action || {}, ctx)}`
          : `! 我方${multi ? ` [${i + 1}/${steps.length}]` : ''} 指令「${st.input}」未执行：${st.error || '解析失败'}`)),
        (r.remaining || []).length
          ? `… 停止：${r.stopped || '已停止'}（剩余 ${r.remaining.length} 步：${r.remaining.join(' / ')}）`
          : null,
        ...(r.ai_actions || []).map((x) => `< AI：${x.verb === 'attack' ? '攻击' : verbLabel(x)}`),
        r.winner != null ? `== 终局：${r.winner === (state.player_side ?? 0) ? '我方胜' : 'AI 胜'} ==` : null,
      ].filter(Boolean))
    } catch (e) {
      // 歧义/无法识别 → 展示候选（引擎结构化 detail）
      const d = e.detail
      if (d && typeof d === 'object') {
        setCmdErr({ message: d.error || e.message, candidates: d.candidates || [] })
        if (d.candidates?.length) setCommands((c) => (c.length ? c : d.candidates))
      } else {
        setCmdErr({ message: e.message, candidates: [] })
      }
      setLog((l) => [...l, `! 指令「${text}」未执行：${(d && d.error) || e.message}`])
    } finally { clearTimeout(slowTimer.current); setSlow(false); setBusy(false) }
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

        <h3 style={{ marginTop: 16 }}>预设对手池 <span className="pill sm">真实 60 张卡表</span></h3>
        <div className="preset-row">
          {presets.map((p) => (
            <button key={p.id} className={`preset ${deckB === p.id ? 'on' : ''}`}
              title={`${p.note}｜60 卡真实卡表（可执行 ${(p.executable_rate * 100).toFixed(1)}%，占位 ${p.replaced.length} 类）`}
              onClick={() => { setDeckB(p.id); onMsg(`对手已设为 ${p.label}`) }}>
              <b>{p.id} · {p.label}</b>
              <small className="muted">{p.note}</small>
              <small className="muted mono">
                60 卡 · 可执行 {(p.executable_rate * 100).toFixed(1)}%
                {p.replaced.length > 0 && ` · 占位 ${p.replaced.reduce((s, r) => s + r.count, 0)} 张`}
              </small>
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
            <Zone title="对手（AI）" st={view.opp_state} side="opp" hitName={hitName}
              onPickCard={(c) => setZoomCard(c)} />
            <Zone title="我方" st={view.me_state} side="me" hitName={hitName}
              onPickCard={(c) => setZoomCard(c)} />
          </div>
          <p className="sm muted" style={{ margin: '4px 2px' }}>
            点击任意卡牌（手牌 / 场上 / 后备）查看完整卡面：招式费用、伤害与效果文本。
            卡面为自绘示意图，不使用官方卡图（合规 C1/C5）。
          </p>
          <CardZoom card={zoomCard} onClose={() => setZoomCard(null)} />
          <CommandBar commands={commands} disabled={finished} busy={busy}
            onSubmit={submitCommand} lastMatched={lastMatched} error={cmdErr} />
          <ActionPanel legal={legal} disabled={busy || finished} busy={busy}
            ctx={actionContext(view)}
            onAct={act}
            hint={finished ? '对局已结束' : '（等待对手 / 无可用动作）'} />
          <CommentaryPanel battleId={state.id} turn={view.turn} onMsg={onMsg} />
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
          <li>只显示我方手牌 —— 对手手牌数量可见、内容不可见</li>
          <li>同一 seed 可复现同一对局，便于复盘与公平比较</li>
          <li>K3 兜底：决策 &gt; 2s 提示，必要时降级 argmax（引擎侧策略）</li>
          <li>对局会话驻留服务内存，服务重启后需重开</li>
        </ul>
      </div>
    </div>
  )
}
