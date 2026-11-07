import React, { useEffect, useRef, useState } from 'react'
import { api, DECKS } from '../api.js'
import { t } from '../i18n.js'
import { verbLabel } from '../verbs.js'
import { Zone, ActionPanel, CommandBar, TurnLog, SeedBar } from '../components/battle.jsx'
import { CardZoom } from '../components/card.jsx'
import { CommentaryPanel } from '../components/commentary.jsx'
import { actionContext } from '../names.js'
import { planForHand, passAction, describeActionTarget, isFieldTarget, targetSide } from '../plays.js'
import { flyCard, floatText, fieldSlotEl, handCardEl } from '../anim.js'
import { Spinner, EmptyState, ErrBar } from '../components/ui.jsx'
import { navigate } from '../router.jsx'

const K3_MS = 2000   // K3 兜底下限：>2s 禁用动作 + 提示（UI 规范 §4.1.1）
const REPLAY_MS = 520  // 对手每个动作的回放停留时长（太短看不清、太长拖沓）
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export default function BattlePage({ onMsg, onGoDecks, battleId }) {
  const [deckA, setDeckA] = useState('lightning')
  const [deckB, setDeckB] = useState('fire')
  const [myDecks, setMyDecks] = useState([])
  const [aiIters, setAiIters] = useState(0)
  const [state, setState] = useState(null)
  const [log, setLog] = useState([])
  const [busy, setBusy] = useState(false)
  const [slow, setSlow] = useState(false)
  const [err, setErr] = useState(null)
  const [commands, setCommands] = useState([])     // 引擎指令清单（中文）
  const [cmdErr, setCmdErr] = useState(null)       // 指令解析失败（含歧义候选）
  const [zoomCard, setZoomCard] = useState(null)   // 详情大卡（点不可用的牌 / 场上卡）
  const [hitName, setHitName] = useState(null)     // 本回合受伤的宝可梦（抖动提示）
  const [selHand, setSelHand] = useState(-1)       // 选中的手牌索引（出牌用）
  const [pending, setPending] = useState(null)     // 等待选目标的动作
  const [replaying, setReplaying] = useState(false) // 对手动作回放中
  const [framedReplay, setFramedReplay] = useState(false) // 回放是否带真实中间帧（逐帧）
  const prevDmg = useRef(new Map())
  const slowTimer = useRef(null)

  useEffect(() => {
    api.myDecks().then((r) => setMyDecks(r.decks || [])).catch(() => {})
  }, [])

  // ── 深链接恢复：`/battles/<id>` 直接回到那一局 ────────────────────────
  // URL 是唯一真相：刷新页面、把链接发给别人、点浏览器后退，都回到同一局。
  // 对局会话存在服务端内存（store.battle_sessions），服务重启后会 404 ——
  // 这种情况明确说「链接已失效」并留出口，而不是停在空白棋盘上。
  const loadedRef = useRef(null)
  useEffect(() => {
    if (!battleId) { loadedRef.current = null; return undefined }
    if (loadedRef.current === battleId) return undefined
    // 刚由本页创建的对局：state 里已经是这一局，不必再拉一次（会覆盖回合日志）
    if (state && state.id === battleId) { loadedRef.current = battleId; return undefined }
    let alive = true
    setBusy(true); setErr(null)
    api.battleState(battleId)
      .then((r) => {
        if (!alive) return
        loadedRef.current = battleId
        prevDmg.current = new Map()      // 恢复局面时不得沿用手上的战损基线
        setState({
          id: r.id, seed: r.seed, player_side: r.player_side,
          view: r.view, winner: r.winner, finished: r.finished,
        })
        setCommands(r.commands || [])
        setLog([`… 已从链接恢复对局 ${r.id}（seed=${r.seed}）`])
        setPending(null); setSelHand(-1); setZoomCard(null); setCmdErr(null)
        setReplaying(false); setFramedReplay(false); setHitName(null)
      })
      .catch((e) => {
        if (!alive) return
        setErr({
          code: e.status || 'ERR',
          message: e.status === 404
            ? '该对局已失效（服务重启会清空会话内存），请新开一局'
            : e.message,
        })
      })
      .finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [battleId])   // eslint-disable-line react-hooks/exhaustive-deps

  // 战损演出：对比上一帧，让刚受伤的宝可梦抖一下**并飘出伤害数字**；
  // 血量归零则额外飘「已倒下」。纯视觉，不改数据。
  //
  // ⚠ 身份必须用**位置**（side + pos）而不是卡名：同一套牌里常有同名宝可梦
  //   （例如对手场上两只「火苗兽」），用 name 当 key 会互相覆盖，
  //   导致伤害比较错乱、飘字丢失（踩过）。
  //
  // ⚠ 回放期间必须挂起**除非是逐帧回放**：无中间帧时（旧接口/退化路径）引擎一次只返回
  //   最终局面，若当帧就飘字会出现「伤害先跳、AI 动作后播」的错序——那种情况等回放结束
  //   （replaying → false）再比对，prevDmg 仍是动作前的值，差值依然正确。
  //   有中间帧（framedReplay）时则**不要挂起**：让伤害飘字跟着每一帧走，棋盘真正动起来。
  useEffect(() => {
    const v = state?.view
    if (!v || (replaying && !framedReplay)) return undefined
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
  }, [state, replaying, framedReplay])

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
      const steps = r.ai_steps || []
      // 开局若 AI 先手，把它的动作逐帧放出来（先展示第一帧，避免「先闪终局再回退」）
      setState({ ...r, view: (steps[0] && steps[0].view) || r.view })
      setLog([]); setCommands(r.commands || [])
      setCmdErr(null)
      setPending(null); setSelHand(-1); setZoomCard(null)
      setReplaying(false); setHitName(null); setFramedReplay(false)
      prevDmg.current = new Map()   // 新对局必须清空战损基线，否则会出现错误伤害数
      onMsg(`对局已创建 seed=${r.seed}`, 'ok')
      loadedRef.current = r.id
      // 地址栏跟随对局：刷新、复制链接、走后都会回到这一局（覆盖旧 id，避免历史里堆积失效对局）
      navigate(`/battles/${r.id}`, { replace: true })
      if (steps.length) {
        const ctx = actionContext(r.view)
        await replayFrames(steps, (x) => verbLabel(x, ctx))
        setState((s) => ({ ...s, view: r.view }))   // 收口到最终完整局面
      }
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) }
    finally { setBusy(false) }
  }

  /**
   * 逐帧回放一串帧（`{action?, view?, note?}`）。
   *
   * - 带 `view` 的帧 → 应用为局面（棋盘真的动起来，伤害飘字跟着每帧走）
   * - 不带 `view` → 退化为只按节奏播报 `action`（旧接口 / 无中间态时）
   *
   * 同一个循环既用于**对手动作**（`ai_steps`），也用于**我方多步指令**
   * （`steps[*].view`，只带 view 不带 action —— 日志已在提交时批量写好）。
   */
  async function replayFrames(frames, zh) {
    if (!frames || frames.length === 0) return
    const framed = frames.some((x) => x && x.view)
    if (framed) setFramedReplay(true)
    setReplaying(true)
    try {
      for (const f of frames) {
        await sleep(REPLAY_MS)
        if (f.note) { setLog((l) => [...l, `… ${f.note}`]); continue }
        const x = f.action
        if (x) setLog((l) => [...l, `< AI：${x.verb === 'attack' ? '攻击' : zh(x)}`])
        if (f.view) {
          // 中间帧只更新局面，不碰 winner/finished —— 避免中途提前亮胜负
          setState((s) => ({ ...s, view: f.view }))
        }
      }
    } finally {
      setReplaying(false)
      setFramedReplay(false)
    }
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
      setCommands(r.commands || [])
      setLog((l) => [...l, `> 我方：${zh(a)}`])
      // ① 我方动作后的局面（回放第一帧，让玩家立刻看到自己的操作生效）
      if (r.player_view) setState((s) => ({ ...s, view: r.player_view }))
      // ② 对手动作逐帧回放（有中间态则棋盘跟着动，无则退化为只播报）
      await replayFrames(r.ai_steps || (r.ai_actions || []).map((x) => ({ action: x })), zh)
      // ③ 收口：最终完整局面（含 legal / 胜负）
      setState((s) => ({ ...s, view: r.view, winner: r.winner, finished: r.winner != null }))
      if (r.winner != null) {
        setLog((l) => [...l,
          `== 终局：${r.winner === (state.player_side ?? 0) ? '我方胜' : 'AI 胜'} ==`])
      }
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) }
    finally { clearTimeout(slowTimer.current); setSlow(false); setBusy(false); setReplaying(false); setFramedReplay(false) }
  }

  /** 动作的目标落点（出牌飞行用）：按归一化后的区域 + 槽位判断打到哪张卡上 */
  function targetElFor(a) {
    if (a.target === undefined) {
      // 无目标（上基础宝可梦到后备 / 一次性道具）→ 落到我方后备区
      return document.querySelector('.zone.side-me .bench-list-cards')
        || document.querySelector('.zone.side-me')
    }
    const info = describeActionTarget(a, view)
    if (isFieldTarget(info.where)) {
      const side = targetSide(info.where)
      return fieldSlotEl(side, info.slot) || document.querySelector(`.zone.side-${side}`)
    }
    return document.querySelector('.zone.side-me')   // 弃牌堆 / 牌库：退化为本区
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
      setCommands(r.commands || [])
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
      ].filter(Boolean))
      // 我方多步指令也逐帧演出（steps[*].view，只带 view 不带 action —— 日志已写好），
      // 随后接对手动作逐帧回放 → 最终收口（与「点牌出牌」同一套演出）。
      // 单步（steps 长度 1）时直接用 player_view 立即呈现，不做 pacing（点一下就出牌的手感）。
      const ownFrames = steps.filter((st) => st.ok && st.view).map((st) => ({ view: st.view }))
      if (ownFrames.length > 1) {
        await replayFrames(ownFrames, (x) => verbLabel(x, ctx))
      } else if (r.player_view) {
        setState((s) => ({ ...s, view: r.player_view }))
      }
      await replayFrames(r.ai_steps || (r.ai_actions || []).map((x) => ({ action: x })),
        (x) => verbLabel(x, ctx))
      setState((s) => ({ ...s, view: r.view, winner: r.winner, finished: r.winner != null }))
      if (r.winner != null) {
        setLog((l) => [...l,
          `== 终局：${r.winner === (state.player_side ?? 0) ? '我方胜' : 'AI 胜'} ==`])
      }
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
    } finally { clearTimeout(slowTimer.current); setSlow(false); setBusy(false); setReplaying(false); setFramedReplay(false) }
  }

  const view = state?.view
  const legal = view?.legal || []
  const finished = state?.finished || state?.winner != null
  const pass = passAction(legal)

  // ── 点牌出牌 ──────────────────────────────────────────────
  // 规则：**能出的牌单击即出**（无需再输入指令）；不能出的牌单击看详情。
  // 需要选目标的动作（进化对象 / 附能量目标 / 拉对手后备 / 手牌特性）进入
  // 「待选目标」态，场上可选位置高亮，点它即完成 —— 与真实打牌的「点牌 → 指目标」一致。
  //
  // ⚠ 高亮 / 点选一律用归一化后的 `slot`（0 = 战斗位，i+1 = 后备 i），
  //   与 Zone 的槽位一致；引擎的原始 target 只在提交动作时使用。
  const fieldTargets = pending ? pending.targets.filter((t) => isFieldTarget(t.where)) : []
  const myHighlight = fieldTargets.filter((t) => targetSide(t.where) === 'me').map((t) => t.slot)
  const oppHighlight = fieldTargets.filter((t) => targetSide(t.where) === 'opp').map((t) => t.slot)
  // 弃牌堆 / 牌库 这类目标不在场上，用按钮列表选
  const listedTargets = pending ? pending.targets.filter((t) => !isFieldTarget(t.where)) : []

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

  // 场上卡：待选目标时点是执行；否则看详情
  function pickField(card, slot, side) {
    if (!card) return
    if (pending) {
      const hit = pending.targets.find((t) => t.slot === slot && targetSide(t.where) === side)
      if (hit) { doTarget(hit); return }
      onMsg('这里不是可选目标', 'err')
      return
    }
    setZoomCard(card)
  }

  return (
    <div className="grid battle">
      {/* 左：对局配置 */}
      <div className="card">
        <h3>对局配置</h3>
        <label htmlFor="dx">我方卡组</label>
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
        {err && !state && (
          <div className="row">
            <button className="ghost sm" onClick={() => navigate('/battles', { replace: true })}>
              返回新建对局
            </button>
          </div>
        )}
        {slow && <div className="alert warn" role="alert">决策超过 {K3_MS / 1000}s，仍在计算</div>}

        {!view && !busy && (
          <EmptyState text={t('empty.battle')} cta="去编排卡组" onCta={onGoDecks} />
        )}

        {view && (<>
          {replaying && (
            <div className="replay-bar" role="status" aria-live="polite">
              <i className="dot" aria-hidden="true" />
              对手正在行动 —— 逐个动作回放中
            </div>
          )}

          {pending && (
            <div className="target-bar" role="region" aria-label="选择目标">
              <b className="sm">选择目标：</b>
              {listedTargets.map((t, i) => (
                <button key={i} className="chip-btn" onClick={() => doTarget(t)}>{t.label}</button>
              ))}
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

          <CardZoom card={zoomCard} onClose={() => setZoomCard(null)} />
        </>)}
      </div>

      {/* 右：操作区（出牌提示 / 结束回合 / 指令输入 / 合法动作 / 解说 / 回合日志） */}
      <div className="card">
        <h3>对战操作</h3>
        {view && (<>
          {/* 结束回合：真实对战的节奏锚点，放在最显眼处 */}
          {pass && (
            <div className="row" style={{ marginTop: 12 }}>
              <button className="primary" disabled={busy || finished}
                onClick={() => act(pass)}>
                结束回合（交给对手）
              </button>
            </div>
          )}

          {/* 指令输入与完整动作表：作为备选入口收起，主交互是「点牌出牌」 */}
          <details className="cmd-drawer">
            <summary>指令输入</summary>
            <CommandBar commands={commands} disabled={finished} busy={busy}
              onSubmit={submitCommand} error={cmdErr} />
          </details>

          <details className="cmd-drawer">
            <summary>全部合法动作（{legal.length} 项）</summary>
            <ActionPanel legal={legal} disabled={busy || finished} busy={busy}
              ctx={actionContext(view)}
              onAct={act}
              hint={finished ? '对局已结束' : '（等待对手 / 无可用动作）'} />
          </details>
          <CommentaryPanel battleId={state.id} turn={view.turn} onMsg={onMsg} />
          <TurnLog lines={log} />
        </>)}
      </div>
    </div>
  )
}
