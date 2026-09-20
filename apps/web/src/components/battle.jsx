import React from 'react'
import { VERBS, verbLabel } from '../verbs.js'
import { disp } from '../names.js'
import { splitSequence, sequenceTip } from '../sequences.js'
import { t } from '../i18n.js'
import { HandFan, FieldPokemon } from './card.jsx'

function VerbIcon({ verb, size = 16 }) {
  const d = (VERBS[verb] || {}).icon || VERBS.pass.icon
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
  )
}

/**
 * 奖赏卡堆：背面朝上的小卡，数量 = prizes_left
 * 数量减少时，**被取走的那几张**逐张翻转离场（先渲染「旧的张数」，其中减少的带 taken 动画，
 * 动画结束后回到真实张数）—— 让「对手拿走 1 张奖赏」这件事看得见。
 */
function PrizeStack({ n }) {
  const [taken, setTaken] = React.useState(0)
  const prev = React.useRef(n)
  React.useEffect(() => {
    if (n < prev.current) {
      const drop = prev.current - n
      setTaken(drop)
      prev.current = n
      const t = setTimeout(() => setTaken(0), 620)
      return () => clearTimeout(t)
    }
    prev.current = n
    return undefined
  }, [n])
  const total = Math.max(0, Math.min(12, taken > 0 ? n + taken : n))
  if (total === 0 && n === 0) {
    return <span className="prize-stack" aria-hidden="true"><span className="prize-count">已取完</span></span>
  }
  return (
    <span className="prize-stack" aria-hidden="true">
      {Array.from({ length: total }).map((_, i) => (
        <i key={i} className={`prize-card ${taken > 0 && i >= n ? 'taken' : ''}`} />
      ))}
    </span>
  )
}

/**
 * 区域（我方 / 对手）：战斗位 + 后备 + 手牌（UI 规范 §5.1/§5.2）
 *
 * 视觉：自绘卡面（components/card.jsx），不使用官方卡图（合规 C1/C5）。
 * 手牌用「真实拿牌」布局：叠压 + 微旋转，hover 抬起放大，点击弹出完整卡面。
 * 双方手牌均只显示我方内容（对手手牌数可见、内容不可见，PRD F5）。
 */

export function Zone({ title, st, side, onPickCard, onPickHand, pickedIdx, hitName, legal = [],
                       highlightPos = [], dimOthers = false }) {
  if (!st) return null
  const bench = st.bench || []
  const hand = st.hand || []
  // highlightPos 用引擎的 target 语义：0 = 战斗位，1.. = 后备
  const hl = (pos) => highlightPos.includes(pos)
  const dmp = (pos) => dimOthers && !hl(pos)
  return (
    <section className={`zone side-${side}`} aria-label={title}>
      <header className="zt">
        <span>{title}</span>
        <span className="mono">
          奖赏 {st.prizes_left ?? '-'}
          <PrizeStack n={st.prizes_left ?? 0} />
          {' · '}牌库 {st.deck_count ?? '-'} · 弃牌 {st.discard_count ?? '-'}
          {' · '}手牌 {side === 'me' ? (st.hand_count ?? hand.length) : (st.hand_count ?? '?')}
        </span>
      </header>
      <div className="table-side">
        <div className={`table-active field-slot ${hitName === `${side}-0` ? 'hit' : ''}`}>
          <FieldPokemon p={st.active} size="field" highlight={hl(0)} dim={dmp(0)}
            onClick={onPickCard ? () => onPickCard(st.active, 0, side) : undefined} />
        </div>
        <div className="table-bench">
          <span className="sm muted">后备 {bench.length}</span>
          <div className="bench-list-cards">
            {bench.map((b, i) => (
              <div key={`${b.name}-${i}`}
                className={`field-slot ${hitName === `${side}-${i + 1}` ? 'hit' : ''}`}>
                <FieldPokemon p={b} size="bench" highlight={hl(i + 1)} dim={dmp(i + 1)}
                  onClick={onPickCard ? () => onPickCard(b, i + 1, side) : undefined} />
              </div>
            ))}
            {bench.length === 0 && <span className="sm muted">（空）</span>}
          </div>
        </div>
      </div>
      {side === 'me' && (<>
        <HandFan cards={hand} onPick={onPickHand || onPickCard} pickedIdx={pickedIdx} legal={legal} />
        {/* 无障碍 + 既有 e2e 游标：手牌的文字化摘要（视觉隐藏） */}
        <span className="sr hand">手牌：{hand.map((c) => disp(c)).join('、')}</span>
      </>)}
    </section>
  )
}

export function ActionPanel({ legal, disabled, busy, onAct, hint, ctx }) {
  return (
    <section className="panel-actions" aria-label={t('tip.legal')} title={t('tip.legal')}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <b className="sm">{t('tip.legal')}</b>
        {busy && <span className="sm mono muted">{t('state.thinking')}</span>}
      </div>
      <div className="acts">
        {(legal || []).length === 0 && <span className="sm muted">{hint || '（无可用动作）'}</span>}
        {(legal || []).map((a, i) => (
          <button key={i} className="act" disabled={disabled}
            aria-label={verbLabel(a, ctx)}
            title={[verbLabel(a, ctx),
                    a.verb === 'attack' && ctx && ctx.attackDescs
                      ? ctx.attackDescs[a.attack] : ''].filter(Boolean).join(' · ')}
            onClick={() => onAct(a)}>
            <VerbIcon verb={a.verb} />
            <span>{verbLabel(a, ctx)}</span>
          </button>
        ))}
      </div>
    </section>
  )
}

export function TurnLog({ lines }) {
  const tail = (lines || []).slice(-8)
  return (<>
    <h3 style={{ marginTop: 12 }}>回合日志 <span className="pill sm">最近 8 条</span></h3>
    <pre className="log" role="log" aria-live="polite" aria-label="回合日志">{tail.join('\n') || '—'}</pre>
  </>)
}

export function SeedBar({ seed, onCopied }) {
  return (
    <div className="row">
      <span className="pill mono" title="同一 seed 可复现同一对局（PRD §7.2）">seed {seed}</span>
      <button className="ghost sm" onClick={async () => {
        try { await navigator.clipboard.writeText(String(seed)) } catch { /* 忽略 */ }
        onCopied && onCopied(seed)
      }}>{t('btn.copy_seed')}</button>
    </div>
  )
}


/**
 * 指令输入栏（UI 规范 §5.2 动作面板的补充交互 · F5 增强）
 *
 * 直接把**中文招式名 / 卡名 / 位置**作为指令下达：
 *   `幻影俯冲` · `高级球` · `附能量到后备1` · `结束回合`
 * 支持 datalist 自动补全（候选来自引擎 command_catalog，与合法动作同源）；
 * 歧义时展示候选供点选（绝不静默替玩家选目标）。
 */
export function CommandBar({ commands = [], disabled, busy, onSubmit, lastMatched, error }) {
  const [text, setText] = React.useState('')
  const listId = React.useMemo(
    () => 'cmd-list-' + Math.random().toString(36).slice(2, 8), [])
  const submit = (value) => {
    const v = (value != null ? value : text).trim()
    if (!v || disabled || busy) return
    onSubmit(v)
    setText('')
  }
  // 候选可能是「字符串」或「动作对象」——统一取可读文案，避免把对象当 React child
  const labelOf = (c) => (typeof c === 'string'
    ? c
    : (c && (c.label || c.display || c.name_zh || c.name)) || '')
  const cands = error && typeof error === 'object'
    ? (error.options && error.options.length
        ? error.options
        : (error.candidates || []).map(labelOf)).filter(Boolean) : []
  // 组合指令预览（真实解析以引擎为准）
  const steps = splitSequence(text)
  const isSeq = steps.length > 1
  return (
    <div className="cmdbar">
      <form className="row" onSubmit={(e) => { e.preventDefault(); submit() }}>
        <label htmlFor="cmd-in" className="sm">指令输入</label>
        <input id="cmd-in" list={listId} value={text} disabled={disabled || busy}
          placeholder="中文招式名 / 卡名，如「幻影俯冲」「高级球」「附能量到后备1」"
          onChange={(e) => setText(e.target.value)}
          aria-describedby="cmd-hint" style={{ flex: 1, minWidth: 180 }} />
        <datalist id={listId}>
          {commands.map((c, i) => <option key={i} value={c.label} />)}
        </datalist>
        <button type="submit" className="primary sm" disabled={disabled || busy || !text.trim()}>
          执行
        </button>
      </form>
      <p id="cmd-hint" className="sm muted" style={{ margin: '4px 0 0' }}>
        可直接输入中文指令；也可从下方候选点选（与合法动作一一对应）
        {lastMatched ? ` · 上次：${lastMatched}` : ''}
        <br />{sequenceTip()}
      </p>
      {isSeq && (
        <p className="sm" style={{ margin: '4px 6px 0' }} aria-live="polite">
          将按顺序执行 <b className="mono">{steps.length}</b> 步：
          {steps.map((x, i) => (
            <span key={i} className="seq-step">{i > 0 ? ' → ' : ''}{x}</span>
          ))}
          <span className="sm muted">
            （回合移交对手或某步失败即停止；实际解析以引擎为准）
          </span>
        </p>
      )}
      <div className="cmd-chips" role="group" aria-label="可用指令">
        {commands.slice(0, 10).map((c, i) => (
          <button key={i} type="button" className="chip-btn"
            disabled={disabled || busy}
            title={`点击执行：${c.label}`
              + ((c.action?.display_desc) ? ` · ${c.action.display_desc}` : '')}
            onClick={() => submit(c.label)}>{c.label}</button>
        ))}
      </div>
      {cands.length > 0 && (
        <div className="cmd-chips" role="group" aria-label="歧义候选">
          <span className="sm muted">请指明：</span>
          {cands.map((c, i) => (
            <button key={i} type="button" className="chip-btn warn"
              onClick={() => submit(c)}>{c}</button>
          ))}
        </div>
      )}
    </div>
  )
}
