import React from 'react'
import { VERBS, verbLabel } from '../verbs.js'
import { disp } from '../names.js'
import { splitSequence, sequenceTip } from '../sequences.js'
import { t } from '../i18n.js'

function VerbIcon({ verb, size = 16 }) {
  const d = (VERBS[verb] || {}).icon || VERBS.pass.icon
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
  )
}

/**
 * 文字棋盘与动作面板（UI 规范 §5.1/§5.2 · §4.1.1）
 * - chip 52×64 / 后备 40×50；HP 血条 8px，满血绿、低血（>50% 伤害）红
 * - aria-label="<牌名> HP x/y"；血条 role=progressbar
 */

export function PokeChip({ p, small }) {
  if (!p) return <div className={`chip chip-empty ${small ? 'sm' : ''}`} aria-label="空位"><span className="muted sm">—</span></div>
  const hp = p.hp ?? 0
  const dmg = p.dmg ?? 0
  const left = Math.max(0, hp - dmg)
  const ko = left === 0
  return (
    <div className={`chip ${small ? 'sm' : ''} ${ko ? 'ko' : ''}`}
      title={`${disp(p)} HP ${left}/${hp}`}
      aria-label={`${disp(p)} HP ${left}/${hp}`}>
      <span className="chip-name">{disp(p)}</span>
    </div>
  )
}

export function HPRow({ p, compact }) {
  if (!p) return null
  const hp = p.hp ?? 0
  const dmg = p.dmg ?? 0
  const left = Math.max(0, hp - dmg)
  const pct = hp ? Math.round((left / hp) * 100) : 0
  const low = hp ? dmg / hp > 0.5 : false
  return (
    <div className="hprow">
      <div className="bar" role="progressbar" aria-valuenow={left} aria-valuemin="0"
        aria-valuemax={hp} aria-label={`HP ${left}/${hp}`}>
        <i className={low ? 'low' : 'full'} style={{ width: pct + '%' }} />
      </div>
      <span className="mono sm">{left}/{hp}{p.energy != null ? ` · 能 ${p.energy}` : ''}</span>
      {!compact && (p.status || []).length > 0 && (
        <span className="sm muted">{(p.status || []).join('/')}</span>
      )}
    </div>
  )
}

export function Zone({ title, st, side, showHand }) {
  if (!st) return null
  const bench = st.bench || []
  return (
    <section className="zone" aria-label={title}>
      <header className="zt">
        <span>{title}</span>
        <span className="mono">
          奖赏 {st.prizes_left ?? '-'} · 牌库 {st.deck_count ?? '-'} · 弃牌 {st.discard_count ?? '-'}
          {showHand ? ` · 手牌 ${st.hand_count ?? (st.hand || []).length}` : ''}
        </span>
      </header>
      <div className="board-row">
        <div>
          <div className="chip-wrap"><PokeChip p={st.active} /></div>
          <HPRow p={st.active} />
        </div>
        <div className="bench">
          <span className="sm muted">后备 {bench.length}</span>
          <div className="bench-list">
            {bench.slice(0, 5).map((b, i) => (
              <div key={i} className="bench-item">
                <PokeChip p={b} small />
              </div>
            ))}
            {bench.length === 0 && <span className="sm muted">（空）</span>}
          </div>
        </div>
      </div>
      {side === 'me' && (st.hand || []).length > 0 && (
        <div className="hand sm muted">
          手牌：{(st.hand || []).map((c) => disp(c)).join('、')}
        </div>
      )}
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
