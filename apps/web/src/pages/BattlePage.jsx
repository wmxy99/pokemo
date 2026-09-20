import React, { useEffect, useRef, useState } from 'react'
import { api, DECKS } from '../api.js'
import { t } from '../i18n.js'
import { verbLabel } from '../verbs.js'
import { Zone, ActionPanel, CommandBar, TurnLog, SeedBar } from '../components/battle.jsx'
import { CardZoom } from '../components/card.jsx'
import { CommentaryPanel } from '../components/commentary.jsx'
import { actionContext } from '../names.js'
import { planForHand, passAction } from '../plays.js'
import { flyCard, floatText, fieldSlotEl, handCardEl } from '../anim.js'
import { KvTable } from '../components/stats.jsx'
import { Spinner, EmptyState, ErrBar } from '../components/ui.jsx'

const K3_MS = 2000   // K3 兜底下限：>2s 禁用动作 + 提示（UI 规范 §4.1.1）
const REPLAY_MS = 520  // 对手每个动作的回放停留时长（太短看不清、太长拖沓）
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

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
  const [zoomCard, setZoomCard] = useState(null)   // 详情大卡（点不可用的牌 / 场上卡）
  const [hitName, setHitName] = useState(null)     // 本回合受伤的宝可梦（抖动提示）
  const [selHand, setSelHand] = useState(-1)       // 选中的手牌索引（出牌用）
  const [pending, setPending] = useState(null)     // 等待选目标的动作
  const [replaying, setReplaying] = useState(false) // 对手动作回放中
  const prevDmg = useRef(new Map())
  const slowTimer = useRef(null)

  useEffect(() => {
    api.deckPresets().then((r) => setPresets(r.presets || [])).catch(() => {})
    api.myDecks().then((r) => setMyDecks(r.decks || [])).catch(() => {})
  }, [])

  // 战损演出：对比上一帧，让刚受伤的宝可梦抖一下**并飘出伤害数字**；
  // 血量归零则额外飘「已倒下」。纯视觉，不改数据。
  //
  // ⚠ 身份必须用**位置**（side + pos）而不是卡名：同一套牌里常有同名宝可梦
  //   （例如对手场上两只「火苗兽」），用 name 当 key 会互相覆盖，
  //   导致伤害比较错乱、飘字丢失（踩过）。
  useEffect(() => {
    const v = state?.view
    if (!v) return undefined
    const cur = new Map()
    const list = [
      ['me', 0, v.me_state?.active], ['opp', 0, v.opp_state?.active],
      ...(v.me_state?.bench || []).map((p, i) => ['me', i + 1, p]),
      ...(v.opp_state?.bench || []).map((p, i) => ['opp', i + 1, p]),
    ]
    list.forEach(([side, pos, p]) => {
      if (p) cur.set(`${side}-${pos}`, { dmg: p.dmg || 0, hp: p.hp || 0 })
    })
    let hitKey = null
    cur.forEach((now, key) => {
      const before = prevDmg.current.get(key)
      if (!before) return
      const i = key.indexOf('-')
      const side = key.slice(0, i)
      const pos = Number(key.slice(i + 1))
      if (before.dmg < now.dmg) {
        hitKey = key
        const el = fieldSlotEl(side, pos)
        if (el) floatText(el, `-${now.dmg - before.dmg}`, 'dmg')
      }
      if (before.hp - before.dmg > 0 && now.hp - now.dmg <= 0) {
        setTimeout(() => {
          const el = fieldSlotEl(side, pos)
          if (el) floatText(el, '已倒下', 'ko')
        }, 320)
      }
    })
    prevDmg.current = cur
    if (!hitKey) return undefined
    setHitName(hitKey)
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

  // 一次动作的完整演出：① 出牌飞行 → ② 应用新局面（触发战损飘字）→ ③ 对手动作逐条回放
  async function act(a, srcHandIdx = -1) {
    if (!state || busy) return
    // ① 飞行必须在 setState **之前**发起：视图更新后手牌会重排，源卡位置就取不到了
    if (srcHandIdx >= 0) {
      const src = handCardEl(srcHandIdx)
      const dst = targetElFor(a)
      if (src && dst) flyCard(src, dst)
    }
    setBusy(true); setErr(null)
    setSlow(false)
    slowTimer.current = setTimeout(() => setSlow(true), K3_MS)
    try {
      const r = await api.act(state.id, a)
      const ctx = actionContext(state.view)          // 提交前的局面（含中文名）
      const zh = (x) => verbLabel(x, ctx)
      setState((s) => ({ ...s, view: r.view, winner: r.winner, finished: r.winner != null }))
      setCommands(r.commands || [])
      setLog((l) => [...l, `> 我方：${zh(a)}`])
      const ai = r.ai_actions || []
      if (ai.length > 0) {
        // ③ 逐条回放对手动作。引擎一次只返回**最终局面**（没有中间态），
        //    所以这里按节奏逐条播报动作本身，让玩家看清 AI 做了什么；
        //    回放期间 busy 未释放，我方操作保持禁用。
        setReplaying(true)
        for (let i = 0; i < ai.length; i += 1) {
          await sleep(REPLAY_MS)
          const x = ai[i]
          setLog((l) => [...l, `< AI：${x.verb === 'attack' ? '攻击' : zh(x)}`])
        }
        setReplaying(false)
      }
      if (r.winner != null) {
        setLog((l) => [...l,
          `== 终局：${r.winner === (state.player_side ?? 0) ? '我方胜' : 'AI 胜'} ==`])
      }
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) }
    finally { clearTimeout(slowTimer.current); setSlow(false); setBusy(false); setReplaying(false) }
  }

  /** 动作的目标落点（出牌飞行用）：按 verb / tag 判断打到哪张卡上 */
  function targetElFor(a) {
    if (a.target === undefined) {
      // 无目标（上后备 / 一次性支援者）→ 落到我方后备区
      return document.querySelector('.zone.side-me .bench-list-cards')
        || document.querySelector('.zone.side-me')
    }
    const side = (a.verb === 'trainer' && a.tag === 'boss') ? 'opp' : 'me'
    return fieldSlotEl(side, a.target) || document.querySelector('.zone.side-me')
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
  const pass = passAction(legal)

  // ── 点牌出牌 ──────────────────────────────────────────────
  // 规则：**能出的牌单击即出**（无需再输入指令）；不能出的牌单击看详情。
  // 需要选目标的动作（进化对象 / 附能量目标 / 拉对手后备）进入「待选目标」态，
  // 场上可选位置高亮，点它即完成 —— 与真实打牌的「点牌 → 指目标」一致。
  const myHighlight = pending
    ? pending.targets.filter((t) => t.where === 'my-active' || t.where === 'my-bench')
      .map((t) => t.pos)
    : []
  const oppHighlight = pending
    ? pending.targets.filter((t) => t.where === 'opp-bench').map((t) => t.pos)
    : []
  // 弃牌堆 / 牌库 这类目标不在场上，用按钮列表选
  const listedTargets = pending
    ? pending.targets.filter((t) => t.where === 'discard' || t.where === 'deck')
    : []

  function pickHand(card, idx) {
    if (!view || busy || finished) return
    const plan = planForHand(idx, card, view.legal || [], view)
    if (plan.kind === 'none') {
      onMsg(plan.reason || '这张牌当前不可用', 'err')
      setZoomCard(card)          // 不可用 → 给玩家看完整卡面，而不是静默无反应
      return
    }
    if (plan.kind === 'instant') {
      setSelHand(idx)
      setPending(null)
      const to = plan.autoTarget ? `（目标：${plan.autoTarget.label}）` : ''
      onMsg(`出牌：${card.display || card.name_zh || card.name}${to}`)
      act(plan.action, idx)      // 带手牌索引 → 播放「从手牌飞到场上」
      return
    }
    setSelHand(idx)
    setPending(plan)
  }

  function doTarget(t) {
    const src = selHand                     // 飞行起点 = 刚才点的那张手牌
    setPending(null)
    setSelHand(-1)
    act(t.action, src)
  }

  function matchesSide(where, side) {
    if (where === 'my-active' || where === 'my-bench') return side === 'me'
    if (where === 'opp-bench') return side === 'opp'
    return false
  }

  // 场上卡：待选目标时点是执行；否则看详情
  function pickField(card, pos, side) {
    if (!card) return
    if (pending) {
      const hit = pending.targets.find((t) => t.pos === pos && matchesSide(t.where, side))
      if (hit) { doTarget(hit); return }
      onMsg('这里不是可选目标', 'err')
      return
    }
    setZoomCard(card)
  }

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
          {replaying && (
            <div className="replay-bar" role="status" aria-live="polite">
              <i className="dot" aria-hidden="true" />
              对手正在行动 —— 逐个动作回放中（回放结束前你的操作会暂时锁定）
            </div>
          )}

          {pending && (
            <div className="target-bar" role="region" aria-label="选择目标">
              <b className="sm">选择目标：</b>
              {listedTargets.length === 0 && (
                <span className="sm muted">点击场上高亮的卡牌即可完成</span>
              )}
              {listedTargets.map((t, i) => (
                <button key={i} className="chip-btn" onClick={() => doTarget(t)}>{t.label}</button>
              ))}
              <span className="sm muted">（也可点击手牌改选其它牌）</span>
              <button className="ghost sm"
                onClick={() => { setPending(null); setSelHand(-1) }}>取消</button>
            </div>
          )}

          <div className="grid" style={{ gap: 10 }}>
            <Zone title="对手（AI）" st={view.opp_state} side="opp" hitName={hitName}
              onPickCard={pickField} highlightPos={oppHighlight} dimOthers={!!pending} />
            <Zone title="我方" st={view.me_state} side="me" hitName={hitName}
              onPickCard={pickField} onPickHand={pickHand} pickedIdx={selHand}
              legal={legal} highlightPos={myHighlight} dimOthers={!!pending} />
          </div>

          <p className="sm muted" style={{ margin: '4px 2px' }}>
            <b>直接点手牌出牌</b>：能出的牌带绿色角标，单击立即执行；
            需要指定对象的（进化 / 附能量 / 拉对手后备）会提示你点选场上高亮的目标。
            点击不可用的牌或场上的牌可查看完整卡面（自绘示意图，不使用官方卡图 · 合规 C1/C5）。
          </p>
          <CardZoom card={zoomCard} onClose={() => setZoomCard(null)} />
          {/* 结束回合：真实对战的节奏锚点，放在最显眼处 */}
          {pass && (
            <div className="row" style={{ marginTop: 12 }}>
              <button className="primary" disabled={busy || finished}
                onClick={() => act(pass)}>
                结束回合（交给对手）
              </button>
              <span className="sm muted">也可直接点上方手牌出牌</span>
            </div>
          )}

          {/* 指令输入与完整动作表：作为备选入口收起，主交互是「点牌出牌」 */}
          <details className="cmd-drawer">
            <summary>指令输入（可选：直接输入中文招式名 / 卡名）</summary>
            <CommandBar commands={commands} disabled={finished} busy={busy}
              onSubmit={submitCommand} lastMatched={lastMatched} error={cmdErr} />
          </details>

          <details className="cmd-drawer">
            <summary>全部合法动作（{legal.length} 项，可点牌出牌的备选入口）</summary>
            <ActionPanel legal={legal} disabled={busy || finished} busy={busy}
              ctx={actionContext(view)}
              onAct={act}
              hint={finished ? '对局已结束' : '（等待对手 / 无可用动作）'} />
          </details>
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
