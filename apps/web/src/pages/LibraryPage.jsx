import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { Pill, StatCard, Spinner, EmptyState, ErrBar } from '../components/ui.jsx'
import { CardArt } from '../cardart.jsx'

/** 能量/属性代码 → 中文展示（与引擎 energy 代码同源） */
const ENERGY_LABEL = {
  grass: '草', fire: '火', water: '水', lightning: '雷', psychic: '超',
  fighting: '斗', darkness: '恶', metal: '钢', dragon: '龙', colorless: '无色',
  any: '任意', team_rocket: '火箭队',
}
const SUPERTYPES = [
  { v: '', label: '全部超类型' },
  { v: 'Pokemon', label: '宝可梦' },
  { v: 'Trainer', label: '训练家' },
  { v: 'Energy', label: '能量' },
]
const TYPES = ['', 'grass', 'fire', 'water', 'lightning', 'psychic',
  'fighting', 'darkness', 'metal', 'dragon', 'colorless']
const SUBTYPES = ['', 'Basic', 'Stage 1', 'Stage 2', 'Item', 'Supporter',
  'Pokémon Tool', 'Stadium', 'Basic Energy', 'Special Energy']
const SORTS = [
  { v: 'id', label: '默认（入库序）' },
  { v: 'name', label: '卡名 A→Z' },
  { v: 'hp', label: 'HP 高→低' },
  { v: 'series', label: '系列' },
]
const PAGE_SIZE = 30

const energyZh = (code) => ENERGY_LABEL[code] || code

function EnergyChips({ cost }) {
  if (!cost || !cost.length) return <span className="sm muted">—</span>
  return (
    <span className="energy-row">
      {cost.map((c, i) => (
        <span key={i} className={`echip e-${c}`} title={energyZh(c)}>{energyZh(c)}</span>
      ))}
    </span>
  )
}

function TypeChips({ types }) {
  if (!types || !types.length) return <span className="sm muted">—</span>
  return (
    <span className="energy-row">
      {types.map((c, i) => (
        <span key={i} className={`echip e-${c}`} title={energyZh(c)}>{energyZh(c)}</span>
      ))}
    </span>
  )
}

/** 卡详情（招式/特性/效果文本） */
function CardDetail({ card, onClose }) {
  if (!card) return null
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 14 }}>
        <CardArt card={card} className="lib-hero" />
        <div style={{ flex: 1, minWidth: 220 }}>
          <h3 style={{ margin: 0 }}>
            {card.display}
            {card.name_zh && <span className="sm muted"> · {card.name}</span>}
            {card.engine_ready
              ? <Pill tone="ok" title="引擎已建模，可直接用于对战">可对战</Pill>
              : <Pill tone="warn" title="卡库已收录，引擎未建模（可浏览/组卡，不可对战）">未建模</Pill>}
            <Pill tone="muted" title="程序生成的原创卡面艺术，非官方卡图">原创卡面</Pill>
          </h3>
          <p className="sm muted" style={{ margin: '4px 0 0' }}>
            {card.series} · {card.number}
            {card.name_ja ? ` · ${card.name_ja}` : ''}
            {(card.library?.rule_zh || card.library?.rule)
              ? ` · ${card.library?.rule_zh || card.library?.rule}` : ''}
            {card.library?.category ? ` · ${card.library.category}` : ''}
          </p>
        </div>
        <button className="ghost sm" onClick={onClose}>关闭</button>
      </div>

      {card.supertype === 'Pokemon' && (
        <div className="kv-grid sm">
          <span>HP <b className="mono">{card.hp ?? '—'}</b></span>
          <span>阶段 <b>{card.library?.subtype || '—'}</b></span>
          <span>属性 <TypeChips types={card.types} /></span>
          <span>弱点 <b>{card.library?.weakness ? energyZh(card.library.weakness) : '—'}</b></span>
          <span>抗性 <b>{card.library?.resistance ? energyZh(card.library.resistance) : '—'}</b></span>
          <span>撤退 <b className="mono">{card.library?.retreat ?? '—'}</b></span>
          <span>进化前 <b>{card.library?.evolves_from || '—'}</b></span>
        </div>
      )}

      {card.abilities?.length > 0 && (
        <>
          <h4 style={{ margin: '12px 0 4px' }}>特性</h4>
          {card.abilities.map((a, i) => (
            <div key={i} className="move-block">
              <div className="move-head">
                <b title={a.name}>{a.name_zh || a.name || '（未命名）'}</b>
                <Pill tone="info">特性</Pill>
              </div>
              <p className="sm" style={{ margin: '4px 0 0' }}>
                {a.effect_zh || a.effect || '—'}
              </p>
            </div>
          ))}
        </>
      )}

      {card.attacks?.length > 0 && (
        <>
          <h4 style={{ margin: '12px 0 4px' }}>招式</h4>
          {card.attacks.map((a, i) => (
            <div key={i} className="move-block">
              <div className="move-head">
                <EnergyChips cost={a.cost} />
                <b title={a.name}>{a.name_zh || a.name || '（未命名）'}</b>
                {a.damage != null && (
                  <span className="mono">
                    {a.damage}{a.damage_kind === 'multiplier' ? '×' : ''}
                    {a.damage_kind === 'variable' ? '（变值）' : ''}
                  </span>
                )}
              </div>
              {(a.effect_zh || a.effect) && (
                <p className="sm" style={{ margin: '4px 0 0' }}>
                  {a.effect_zh || a.effect}
                </p>
              )}
            </div>
          ))}
        </>
      )}

      {card.image_url && (
        <div className="card-img-wrap">
          <img src={card.image_url} alt={card.display} className="card-img" loading="lazy" />
          <p className="sm muted" style={{ margin: '4px 0 0' }}>
            图像版权归 Nintendo · Creatures Inc. · GAME FREAK inc. · The Pokémon Company 所有
          </p>
        </div>
      )}

      {card.effects_parsed && (
        <>
          <h4 style={{ margin: '12px 0 4px' }}>
            效果解析 <Pill tone="info" title="本内容由机器/AI 自动生成，仅供研究参考">AI 生成</Pill>
            <span className="sm muted"> · 后端 {card.effects_parsed.generated_by}</span>
          </h4>
          <p className="sm muted" style={{ margin: '2px 0 4px' }}>
            {card.effects_parsed.ai_notice}
          </p>
          <pre className="spec-pre sm">{JSON.stringify(card.effects_parsed.specs, null, 1)}</pre>
          {(card.effects_parsed.unsupported || []).length > 0 && (
            <p className="sm muted">
              引擎未建模动作 {card.effects_parsed.unsupported.length} 项：
              {card.effects_parsed.unsupported.slice(0, 6).join('、')}
            </p>
          )}
        </>
      )}

      {(card.rules_text_zh || card.library?.rules_text_zh || card.rules_text) && (
        <>
          <h4 style={{ margin: '12px 0 4px' }}>
            {card.supertype === 'Pokemon' ? '其他文本 / 规则' : '卡牌效果'}
          </h4>
          <p className="sm" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
            {card.rules_text_zh || card.library?.rules_text_zh || card.rules_text}
          </p>
        </>
      )}
      <p className="sm muted" style={{ marginTop: 10 }}>
        数据来源：{card.source}
      </p>
      {card.attribution && (
        <p className="sm muted">{card.attribution}</p>
      )}
    </div>
  )
}

export default function LibraryPage({ onMsg }) {
  const [q, setQ] = useState('')
  const [supertype, setSupertype] = useState('')
  const [type, setType] = useState('')
  const [subtype, setSubtype] = useState('')
  const [series, setSeries] = useState('')
  const [engineReady, setEngineReady] = useState('')
  const [sort, setSort] = useState('id')
  const [page, setPage] = useState(1)

  const [data, setData] = useState(null)
  const [stats, setStats] = useState(null)
  const [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  // 统计与系列选项（只取一次）
  useEffect(() => {
    api.libraryStats().then(setStats).catch(() => {})
  }, [])

  const query = useMemo(() => ({
    q, supertype, type, subtype, series,
    engine_ready: engineReady === '' ? undefined : engineReady,
    sort, page, page_size: PAGE_SIZE,
  }), [q, supertype, type, subtype, series, engineReady, sort, page])

  useEffect(() => {
    let alive = true
    setBusy(true); setErr(null)
    api.libraryCards({ ...query, q })
      .then((r) => { if (alive) setData(r) })
      .catch((e) => { if (alive) setErr({ code: e.status || 'ERR', message: e.message }) })
      .finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [query])

  // 条件变化 → 回到第 1 页
  useEffect(() => { setPage(1) },
    [q, supertype, type, subtype, series, engineReady, sort])

  const seriesOptions = useMemo(
    () => Object.keys(stats?.by_series || {}), [stats])

  const items = data?.items || []
  const total = data?.total ?? 0
  const pages = data?.pages ?? 1

  async function openDetail(id) {
    try {
      setDetail(await api.cardDetail(id))
    } catch (e) { onMsg?.(e.message, 'err') }
  }

  return (
    <div>
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <Pill tone="warn">{t('tip.unofficial')}</Pill>
      </div>

      {stats && (
        <div className="stat-row" style={{ marginTop: 10 }}>
          <StatCard n={stats.total} label="卡库总量" />
          <StatCard n={stats.by_supertype?.Pokemon ?? 0} label="宝可梦" />
          <StatCard n={stats.by_supertype?.Trainer ?? 0} label="训练家" />
          <StatCard n={stats.by_supertype?.Energy ?? 0} label="能量" />
          <StatCard n={stats.attacks_total} label="招式总数" />
          <StatCard n={`${(stats.engine_coverage * 100).toFixed(1)}%`}
            label={`引擎可对战（${stats.engine_ready}/${stats.total}）`} tone="warn" />
        </div>
      )}

      <p className="sm muted" style={{ margin: '8px 0 0' }}>
        <b>版权说明</b>：卡牌名称、规则文本与图像仅用于研究与演示，版权归
        Nintendo · Creatures Inc. · GAME FREAK inc. · The Pokémon Company 所有；
        本项目为非官方、非商业的社区研究项目，与权利方无关联。
        「<b>AI 解析</b>」标记的内容由机器自动生成，仅供研究参考，请以官方规则为准。
        {stats?.ai_notice ? ` ${stats.ai_notice}` : ''}
      </p>

      <div className="card" style={{ marginTop: 12 }}>
        <div className="row filter-row">
          <label htmlFor="lib-q" className="sm">搜索</label>
          <input id="lib-q" value={q} placeholder="卡名（中 / 英 / 日）"
            onChange={(e) => setQ(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
          <label htmlFor="lib-st" className="sm">超类型</label>
          <select id="lib-st" value={supertype} onChange={(e) => setSupertype(e.target.value)}>
            {SUPERTYPES.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
          </select>
          <label htmlFor="lib-ty" className="sm">属性</label>
          <select id="lib-ty" value={type} onChange={(e) => setType(e.target.value)}>
            {TYPES.map((x) => (
              <option key={x} value={x}>{x ? energyZh(x) : '全部属性'}</option>
            ))}
          </select>
          <label htmlFor="lib-sub" className="sm">子类型</label>
          <select id="lib-sub" value={subtype} onChange={(e) => setSubtype(e.target.value)}>
            {SUBTYPES.map((x) => (
              <option key={x} value={x}>{x || '全部子类型'}</option>
            ))}
          </select>
        </div>
        <div className="row filter-row">
          <label htmlFor="lib-se" className="sm">系列</label>
          <select id="lib-se" value={series} onChange={(e) => setSeries(e.target.value)}>
            <option value="">全部系列</option>
            {seriesOptions.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <label htmlFor="lib-er" className="sm">引擎</label>
          <select id="lib-er" value={engineReady} onChange={(e) => setEngineReady(e.target.value)}>
            <option value="">全部</option>
            <option value="true">仅可对战</option>
            <option value="false">仅未建模</option>
          </select>
          <label htmlFor="lib-sort" className="sm">排序</label>
          <select id="lib-sort" value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
          </select>
          <button className="ghost sm" onClick={() => {
            setQ(''); setSupertype(''); setType(''); setSubtype('')
            setSeries(''); setEngineReady(''); setSort('id'); setPage(1)
          }}>重置</button>
          {busy && <Spinner label="查询中…" />}
        </div>
        <p className="sm muted" style={{ margin: '6px 0 0' }}>
          共 <b className="mono">{total}</b> 张 · 第 <b className="mono">{page}</b>/<b className="mono">{pages}</b> 页
        </p>
      </div>

      {err && <ErrBar code={err.code} message={err.message} />}

      <div className="card" style={{ marginTop: 10 }}>
        {items.length === 0 && !busy
          ? <EmptyState text="没有匹配的卡" />
          : (
            <div className="tbl-scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th scope="col">卡面</th>
                  <th scope="col">卡名</th>
                  <th scope="col">系列</th>
                  <th scope="col">类型</th>
                  <th scope="col">属性</th>
                  <th scope="col">HP</th>
                  <th scope="col">招式</th>
                  <th scope="col">引擎</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id}>
                    <td><CardArt card={c} className="lib-thumb" /></td>
                    <td>
                      <b>{c.display}</b>
                      {c.name_zh && <span className="sm muted"> · {c.name}</span>}
                    </td>
                    <td className="mono sm">{c.series} {c.number}</td>
                    <td className="sm">{c.supertype === 'Pokemon' ? '宝可梦' : c.supertype === 'Trainer' ? '训练家' : '能量'}
                      <span className="sm muted">{c.subtype ? ` · ${c.subtype}` : ''}</span></td>
                    <td><TypeChips types={c.types} /></td>
                    <td className="mono">{c.hp ?? '—'}</td>
                    <td className="mono sm">{c.attacks_count}
                      {c.abilities_count > 0 ? ` +${c.abilities_count}特性` : ''}</td>
                    <td>{c.engine_ready
                      ? <Pill tone="ok">可对战</Pill>
                      : <Pill tone="warn">未建模</Pill>}
                      {c.ai_generated && (
                        <Pill tone="info" title="该卡的效果解析由机器/AI 自动生成，仅供研究参考">AI 解析</Pill>
                      )}</td>
                    <td>
                      <button className="pill sm" onClick={() => openDetail(c.id)}>详情</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        <div className="row" style={{ justifyContent: 'center', marginTop: 10 }}>
          <button className="ghost sm" disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}>上一页</button>
          <span className="sm muted mono">{page} / {pages}</span>
          <button className="ghost sm" disabled={page >= pages}
            onClick={() => setPage((p) => Math.min(pages, p + 1))}>下一页</button>
        </div>
      </div>

      <CardDetail card={detail} onClose={() => setDetail(null)} />
    </div>
  )
}
