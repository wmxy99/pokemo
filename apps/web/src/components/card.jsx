import React from 'react'
import {
  cc, typeColor, typeLabel, stageLabel, trainerKindLabel,
  trainerDesc, abilityDesc, statusLabel, cardKind, isTrainerCard,
} from '../cardtext.js'
import { handUsable } from '../plays.js'

/**
 * 卡牌可视化组件（自绘卡面 · 不使用任何官方卡图 —— 合规 C1/C5）
 *
 * 设计来源：真实 TCG 卡牌的信息结构（名称 / HP / 属性 / 招式费用·伤害·效果 / 弱点·抗性·撤退），
 * 全部用 CSS 几何与文字重绘，配色取自属性主色（cardtext.js 的 TYPE_COLOR）。
 *
 * 尺寸变体（CSS 控制，见 styles.css 的 .cf.*）：
 *   zoom  详情大卡（点击手牌弹出）· hand 手牌 · field 场上战斗位 · bench 后备 · mini 极简
 */

const pct = (left, max) => (max ? Math.max(0, Math.min(1, left / max)) : 1)

function hpTone(left, max) {
  const r = pct(left, max)
  if (r <= 0.25) return 'ko'
  if (r <= 0.5) return 'low'
  return 'full'
}

/** 能量费用圆点（cost_map = [[type, n], ...]） */
export function EnergyDots({ costMap, total, size = 'sm', label }) {
  const items = []
  ;(costMap || []).forEach(([t, n]) => {
    for (let i = 0; i < (n || 0); i++) items.push(t)
  })
  if (!items.length && total) {
    for (let i = 0; i < total; i++) items.push('colorless')
  }
  if (!items.length) {
    return <span className={`edots ${size} empty`} aria-hidden="true" />
  }
  return (
    <span className={`edots ${size}`} role="img"
      aria-label={label || `能量费用 ${items.length}`}>
      {items.map((t, i) => {
        const [fg] = typeColor(t)
        return <i key={i} className="edot" style={{ background: fg }} title={typeLabel(t)} />
      })}
    </span>
  )
}

function TypeBadge({ ptype, stage, ex, size }) {
  const [fg, bg] = typeColor(ptype)
  return (
    <span className={`cf-badge ${size}`} style={{ color: fg, borderColor: fg, background: bg }}
      title={[typeLabel(ptype), stageLabel(stage), ex ? 'EX' : null].filter(Boolean).join(' · ')}>
      {typeLabel(ptype)}
      {stage > 0 && <em className="cf-stage">{stageLabel(stage)}</em>}
      {ex && <em className="cf-ex">EX</em>}
    </span>
  )
}

/** 卡面主体：宝可梦 */
function PokemonFace({ c, size, dmg = 0, used = [] }) {
  const max = c.hp ?? 0
  const left = Math.max(0, max - dmg)
  const tone = hpTone(left, max)
  const [fg, bg] = typeColor(c.ptype)
  const attacks = c.attacks || []
  const abilities = c.abilities || []
  const showDetail = size === 'hand' || size === 'field' || size === 'zoom'
  return (
    <>
      <div className="cf-head" style={{ background: `linear-gradient(90deg, ${bg}, #fff)` }}>
        <span className="cf-name" title={c.display || c.name_zh || c.name}>
          {c.display || c.name_zh || c.name}
        </span>
        <span className={`cf-hp tone-${tone}`} title={`HP ${left}/${max}`}>
          <em>HP</em>{max}
          {dmg > 0 && <i className="cf-dmg" title={`已受伤 ${dmg}`}>{dmg}</i>}
        </span>
      </div>

      <div className="cf-art" style={{ background: bg }} aria-hidden="true">
        <span className="cf-art-glyph" style={{ color: fg }}>{typeLabel(c.ptype)}</span>
        <div className="cf-badges">
          <TypeBadge ptype={c.ptype} stage={c.stage} ex={c.ex} size={size} />
        </div>
        {showDetail && c.evolves_from && (
          <span className="cf-evo">进化自 {c.evolves_from_zh || c.evolves_from}</span>
        )}
        {size !== 'mini' && (
          <div className="cf-hpbar" role="progressbar" aria-valuenow={left}
            aria-valuemin="0" aria-valuemax={max} aria-label={`HP ${left}/${max}`}>
            <i className={tone} style={{ width: Math.round(pct(left, max) * 100) + '%' }} />
          </div>
        )}
      </div>

      {showDetail && (
        <div className="cf-body">
          {abilities.map((a, i) => (
            <div key={`ab${i}`} className={`cf-ability ${used.includes(a.name) ? 'used' : ''}`}>
              <span className="cf-ab-tag">特性</span>
              <b>{a.display || a.name_zh || a.name}</b>
              <p>{cc(abilityDesc(a.tag))}</p>
            </div>
          ))}
          {attacks.map((a, i) => (
            <div key={`at${i}`} className="cf-attack">
              <div className="cf-atk-line">
                <EnergyDots costMap={a.cost_map} total={a.cost} size="sm"
                  label={`${a.display || a.name} 费用`} />
                <b className="cf-atk-name">{a.display || a.name_zh || a.name}</b>
                {a.damage > 0 && <span className="cf-atk-dmg">{a.damage}</span>}
              </div>
              {(a.display_desc || a.desc_zh || a.desc) && (
                <p className="cf-atk-desc">{a.display_desc || a.desc_zh || a.desc}</p>
              )}
            </div>
          ))}
          {attacks.length === 0 && abilities.length === 0 && (
            <p className="cf-empty">（该宝可梦无招式数据）</p>
          )}
        </div>
      )}

      {showDetail && (
        <div className="cf-foot">
          <span title="弱点">弱 {c.weakness ? typeLabel(c.weakness) : '—'}</span>
          <span title="抗性">抗 {c.resistance ? typeLabel(c.resistance) : '—'}</span>
          <span title="撤退费用">退 {c.retreat ?? 0}</span>
        </div>
      )}
    </>
  )
}

function EnergyFace({ c, size }) {
  const [fg, bg] = typeColor(c.etype)
  return (
    <>
      <div className="cf-head" style={{ background: `linear-gradient(90deg, ${bg}, #fff)` }}>
        <span className="cf-name">{c.display || c.name_zh || c.name}</span>
      </div>
      <div className="cf-art big" style={{ background: bg }} aria-hidden="true">
        <span className="cf-art-glyph" style={{ color: fg }}>{typeLabel(c.etype)}</span>
        {size === 'zoom' && <span className="cf-evo">基本能量</span>}
      </div>
      {(size === 'hand' || size === 'field' || size === 'zoom') && (
        <div className="cf-body">
          <p className="cf-atk-desc">
            {size === 'zoom'
              ? `附着到宝可梦身上，为其提供 1 个${typeLabel(c.etype)}能量。`
              : '提供 1 个能量'}
          </p>
        </div>
      )}
    </>
  )
}

function TrainerFace({ c, size }) {
  const showDetail = size === 'hand' || size === 'field' || size === 'zoom'
  const kind = trainerKindLabel(c.type)
  return (
    <>
      <div className="cf-head" style={{ background: 'linear-gradient(90deg,#E2E8F0,#fff)' }}>
        <span className="cf-name">{c.display || c.name_zh || c.name}</span>
      </div>
      <div className="cf-art" style={{ background: '#F1F5F9' }} aria-hidden="true">
        <span className="cf-art-glyph trainer">{kind}</span>
        {c.tag === 'unsupported' && <span className="cf-warnmark">未建模</span>}
      </div>
      {showDetail && (
        <div className="cf-body">
          <div className="cf-ability trainer">
            <span className="cf-ab-tag">{kind}</span>
            <p>{trainerDesc(c.tag)}</p>
          </div>
        </div>
      )}
    </>
  )
}

/**
 * 单张卡面
 * state: { dmg, used, ko, attached } —— 场上卡才需要
 */
export function CardFace({ card, size = 'hand', state = {}, onClick, picked, dim,
                          usable, highlight }) {
  if (!card) {
    return <div className={`cf ${size} empty`} aria-label="空位"><span>—</span></div>
  }
  const { dmg = 0, used = [], ko = false, attached = 0 } = state
  // 类型判定统一走 cardtext.cardKind（含缺 type 时的兜底；曾因把 supporter/item
  // 误判为训练家之外的类别，把战斗位画成灰卡）
  const kind = cardKind(card)
  const cls = ['cf', size, kind, ko ? 'ko' : '', picked ? 'picked' : '', dim ? 'dim' : '',
    usable ? 'usable' : '', highlight ? 'highlight' : '']
    .filter(Boolean).join(' ')
  return (
    <div className={cls} onClick={onClick} role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      data-name={card.name || card.display}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } } : undefined}
      aria-label={`${card.display || card.name_zh || card.name}${max0(card, dmg)}`}>
      {kind === 'pokemon' && <PokemonFace c={card} size={size} dmg={dmg} used={used} />}
      {kind === 'energy' && <EnergyFace c={card} size={size} />}
      {kind === 'trainer' && <TrainerFace c={card} size={size} />}
      {ko && <span className="cf-ko-mark">已倒下</span>}
      {attached > 0 && (
        <span className="cf-attached" title={`已附着 ${attached} 个能量`}>{attached}</span>
      )}
    </div>
  )
}

function max0(card, dmg) {
  if (card.type !== 'pokemon' || !card.hp) return ''
  return ` HP ${Math.max(0, card.hp - dmg)}/${card.hp}`
}

/**
 * 手牌（"真实拿牌"布局）
 * - 负 margin 轻微叠压 + 按索引给出极小旋转角（--rot），像握在手里
 * - hover / focus：抬起并放大（CSS 处理），z-index 提升到最上层
 * - 点击：打开详情大卡（由调用方处理）
 */
export function HandFan({ cards = [], onPick, pickedIdx = -1, legal = [] }) {
  const n = cards.length
  return (
    <div className="hand-fan" role="group" aria-label={`手牌 ${n} 张`}>
      {cards.map((c, i) => {
        const mid = (n - 1) / 2
        const rot = n <= 1 ? 0 : (i - mid) * Math.min(2.2, 14 / n)
        const lift = n <= 1 ? 0 : Math.abs(i - mid) * 2.5
        // 「现在就能出」的牌加一个可见标记 —— 玩家不必逐个点开试
        const usable = handUsable(i, c, legal)
        return (
          <div key={i} className="hand-card"
            style={{ '--rot': `${rot}deg`, '--lift': `${lift}px`, zIndex: i + 1 }}>
            <CardFace card={c} size="hand" usable={usable} picked={pickedIdx === i}
              onClick={() => onPick && onPick(c, i)} />
          </div>
        )
      })}
      {n === 0 && <span className="sm muted">（手牌为空）</span>}
    </div>
  )
}

/** 详情浮层：把卡放大到可读尺寸 + 完整效果 */
export function CardZoom({ card, onClose }) {
  if (!card) return null
  return (
    <div className="zoom-mask" onClick={onClose} role="dialog" aria-modal="true"
      aria-label={`卡牌详情：${card.display || card.name_zh || card.name}`}>
      <div className="zoom-body" onClick={(e) => e.stopPropagation()}>
        <CardFace card={card} size="zoom" />
        <div className="zoom-side">
          <h4>{card.display || card.name_zh || card.name}</h4>
          <Kv label="类别" value={card.type === 'pokemon' ? '宝可梦'
            : card.type === 'energy' ? '能量' : trainerKindLabel(card.type)} />
          {card.type === 'pokemon' && <>
            <Kv label="HP" value={card.hp} />
            <Kv label="属性" value={typeLabel(card.ptype)} />
            <Kv label="阶段" value={stageLabel(card.stage) || '基础'} />
            {card.evolves_from && <Kv label="进化自" value={card.evolves_from_zh || card.evolves_from} />}
            <Kv label="弱点 / 抗性" value={`${card.weakness ? typeLabel(card.weakness) : '—'} / ${card.resistance ? typeLabel(card.resistance) : '—'}`} />
            <Kv label="撤退费用" value={card.retreat ?? 0} />
          </>}
          {(card.attacks || []).length > 0 && (
            <>
              <h5>招式</h5>
              {(card.attacks || []).map((a, i) => (
                <div key={i} className="zoom-move">
                  <div className="zoom-move-head">
                    <EnergyDots costMap={a.cost_map} total={a.cost} />
                    <b>{a.display || a.name_zh || a.name}</b>
                    {a.damage > 0 && <span className="cf-atk-dmg">{a.damage}</span>}
                  </div>
                  {(a.display_desc || a.desc_zh || a.desc) &&
                    <p>{a.display_desc || a.desc_zh || a.desc}</p>}
                </div>
              ))}
            </>
          )}
          {(card.abilities || []).length > 0 && (
            <>
              <h5>特性</h5>
              {(card.abilities || []).map((a, i) => (
                <div key={i} className="zoom-move">
                  <div className="zoom-move-head"><b>{a.display || a.name_zh || a.name}</b></div>
                  <p>{cc(abilityDesc(a.tag))}</p>
                </div>
              ))}
            </>
          )}
          {isTrainerCard(card) && (
            <>
              <h5>卡牌效果</h5>
              <p>{trainerDesc(card.tag)}</p>
            </>
          )}
          <p className="sm muted" style={{ marginTop: 10 }}>
            卡面为自绘示意图，不使用官方卡图（合规 C1/C5）。效果说明为自述转写。
          </p>
          <button className="ghost sm" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}

function Kv({ label, value }) {
  return (
    <div className="zoom-kv"><span>{label}</span><b>{String(value ?? '—')}</b></div>
  )
}

/** 场上宝可梦（带附着能量与特殊状态） */
export function FieldPokemon({ p, size = 'field', onClick, picked, dim, highlight }) {
  if (!p) {
    return (
      <div className={`cf ${size} empty slot`}><span>空位</span></div>
    )
  }
  const st = {
    dmg: p.dmg || 0,
    used: p.ability_used || [],
    ko: Math.max(0, (p.hp || 0) - (p.dmg || 0)) === 0,
    attached: p.energy || 0,
  }
  const status = (p.status || [])
  return (
    <div className="field-pk">
      <CardFace card={p} size={size} state={st} onClick={onClick} picked={picked}
        dim={dim} highlight={highlight} />
      {status.length > 0 && (
        <div className="status-row" aria-label="特殊状态">
          {status.map((s) => <span key={s} className={`st st-${s}`}>{statusLabel(s)}</span>)}
        </div>
      )}
    </div>
  )
}
