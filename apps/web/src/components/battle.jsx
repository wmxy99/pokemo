import React from 'react'
import { VERBS, verbLabel } from '../verbs.js'
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
      title={`${p.name || p.card_id || '?'} HP ${left}/${hp}`}
      aria-label={`${p.name || p.card_id || '?'} HP ${left}/${hp}`}>
      <span className="chip-name">{p.name || p.card_id || '?'}</span>
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
          手牌：{(st.hand || []).map((c, i) => (c.name || c.card_id || '?')).join('、')}
        </div>
      )}
    </section>
  )
}

export function ActionPanel({ legal, disabled, busy, onAct, hint }) {
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
            aria-label={verbLabel(a)} onClick={() => onAct(a)}>
            <VerbIcon verb={a.verb} />
            <span>{verbLabel(a)}</span>
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
