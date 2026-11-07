import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { Pill, ErrBar, LogBlock, Spinner } from '../components/ui.jsx'
import { CardArt } from '../cardart.jsx'
import { navigate } from '../router.jsx'

const DECK_SIZE = 60
const MAX_COPIES = 4
const PAGE_SIZE = 24

const isBasicEnergy = (c) => c.supertype === 'Energy' && (
  /basic/i.test(c.subtype || '') || /basic|基本/i.test(`${c.name || ''} ${c.display || ''}`)
)
const isBasicPokemon = (c) => c.supertype === 'Pokemon' && /basic/i.test(c.subtype || '')

// 印刷身份键（与后端 `<卡名>@<SET> <number>` 同规则）：同名不同印刷可分别编排
function uidOf(c) {
  const set = String(c.series || '').trim().toUpperCase()
  const num = String(c.number || '').trim()
  return set && num ? `${c.name}@${set} ${num}` : c.name
}
function printingLabel(c) {
  const set = String(c.series || '').trim().toUpperCase()
  const num = String(c.number || '').trim()
  return set && num ? `${set} ${num}` : ''
}

function cardView(c) {
  return {
    id: c.id,
    name: c.name,
    uid: uidOf(c),
    series: c.series,
    number: c.number,
    display: c.display || c.name_zh || c.name,
    supertype: c.supertype,
    subtype: c.subtype,
    types: c.types,
    hp: c.hp,
    engine_ready: c.engine_ready,
  }
}

export default function DecksPage({ onMsg, onGoBattle, deckId }) {
  const [name, setName] = useState('新卡组')
  const [selected, setSelected] = useState([])
  const [q, setQ] = useState('')
  const [supertype, setSupertype] = useState('')
  const [onlyReady, setOnlyReady] = useState(true)
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [searchBusy, setSearchBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [res, setRes] = useState(null)
  const [decks, setDecks] = useState([])
  const [openingRes, setOpeningRes] = useState(null)

  async function load() {
    try { setDecks((await api.myDecks()).decks || []) } catch { /* 忽略 */ }
  }
  useEffect(() => { load() }, [])

  useEffect(() => {
    let alive = true
    const tm = setTimeout(() => {
      setSearchBusy(true)
      api.libraryCards({
        q, supertype, page, page_size: PAGE_SIZE, sort: 'name',
        engine_ready: onlyReady ? true : undefined,
      })
        .then((r) => { if (alive) setData(r) })
        .catch((e) => { if (alive) setErr({ code: e.status || 'ERR', message: e.message }) })
        .finally(() => { if (alive) setSearchBusy(false) })
    }, 260)
    return () => { alive = false; clearTimeout(tm) }
  }, [q, supertype, onlyReady, page])
  useEffect(() => { setPage(1) }, [q, supertype, onlyReady])

  const total = selected.reduce((s, e) => s + e.count, 0)
  const basicCount = selected.filter(isBasicPokemon).reduce((s, e) => s + e.count, 0)
  // 同名上限按**卡名**合计（不同印刷不能绕过 4 张上限）
  const countOf = (c) => {
    const e = selected.find((x) => x.uid === uidOf(c))
    return e ? e.count : 0
  }

  function addCard(c) {
    const v = cardView(c)
    setSelected((sel) => {
      const totalNow = sel.reduce((s, e) => s + e.count, 0)
      if (totalNow >= DECK_SIZE) return sel
      const i = sel.findIndex((e) => e.uid === v.uid)
      const cur = i >= 0 ? sel[i].count : 0
      const max = isBasicEnergy(v) ? DECK_SIZE : MAX_COPIES
      const sameName = sel.filter((e) => e.name === v.name)
        .reduce((s, e) => s + e.count, 0)
      if (cur >= max || sameName >= max) return sel
      if (i >= 0) {
        const next = [...sel]
        next[i] = { ...next[i], count: next[i].count + 1 }
        return next
      }
      return [...sel, { ...v, count: 1 }]
    })
  }

  function changeCount(uid, delta) {
    setSelected((sel) => {
      const i = sel.findIndex((e) => e.uid === uid)
      if (i < 0) return sel
      const e = sel[i]
      const next = e.count + delta
      const max = isBasicEnergy(e) ? DECK_SIZE : MAX_COPIES
      const sameName = sel.filter((x) => x.name === e.name)
        .reduce((s, x) => s + x.count, 0)
      const totalNow = sel.reduce((s, x) => s + x.count, 0)
      if (next > max || (delta > 0 && sameName >= max) ||
          (delta > 0 && totalNow >= DECK_SIZE)) return sel
      if (next <= 0) return sel.filter((x) => x.uid !== uid)
      const out = [...sel]
      out[i] = { ...e, count: next }
      return out
    })
  }

  async function saveDeck() {
    if (!selected.length) { onMsg('卡组为空', 'err'); return }
    setBusy(true); setErr(null); setRes(null)
    try {
      // 写入印刷（`数量 卡名 SET 编号`）：同名多印刷时后端按 uid 精确重建
      const text = selected.map((e) => (e.series && e.number
        ? `${e.count} ${e.name} ${String(e.series).toUpperCase()} ${e.number}`
        : `${e.count} ${e.name}`)).join('\n')
      const r = await api.importDeck(text, name.trim() || '新卡组')
      setRes(r)
      onMsg(r.ok ? '卡组已保存' : '卡组校验未通过', r.ok ? 'ok' : 'err')
      if (r.ok) load()
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) } finally { setBusy(false) }
  }

  async function editDeck(id) {
    // 已经是 URL 里的那一套 → 直接重载（否则点「编辑」毫无反应）；
    // 否则改 URL，由下面的 effect 统一加载（刷新/分享/后退走同一条路）
    if (deckId === id) {
      try { applyDeck(await api.getDeck(id)); onMsg('已载入到编排区') }
      catch (e) { onMsg(e.message, 'err') }
      return
    }
    navigate(`/decks/${id}`)
  }

  /** 把接口返回的卡组铺到编排区（深链接与「编辑」共用） */
  function applyDeck(d) {
    setName(d.name || '我的卡组')
    setSelected((d.cards || []).map((e) => {
      const pr = e.meta?.printing || {}
      return {
        name: e.name,
        uid: e.uid || e.meta?.uid || e.name,
        series: pr.set || '',
        number: pr.number || '',
        display: e.display || e.meta?.name_zh || e.name,
        supertype: e.meta?.supertype,
        subtype: e.meta?.subtype,
        types: e.meta?.types,
        count: e.count,
      }
    }))
    setRes(null); setOpeningRes(null)
  }

  // 深链接恢复：`/decks/<id>` 直接把该卡组铺进编排区
  useEffect(() => {
    if (!deckId) return undefined
    let alive = true
    api.getDeck(deckId)
      .then((d) => { if (alive) { applyDeck(d); onMsg(`已载入卡组「${d.name || deckId}」`) } })
      .catch((e) => { if (alive) onMsg(`卡组不可用：${e.message}`, 'err') })
    return () => { alive = false }
  }, [deckId])   // eslint-disable-line react-hooks/exhaustive-deps

  async function doOpening(id) {
    setBusy(true)
    try {
      const r = await api.opening(id, 1000)
      setOpeningRes(r)
      onMsg(`起手 1,000 次：卡手率 ${(r.brick_rate * 100).toFixed(2)}%`)
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) } finally { setBusy(false) }
  }

  const items = data?.items || []
  const pages = data?.pages || 1
  const tone = res ? (res.ok ? (res.warnings.length ? 'warn' : 'ok') : 'brand') : null

  return (
    <div className="grid cols-2">
      <div className="card">
        <h3>编排卡组
          <Pill tone={total === DECK_SIZE ? 'ok' : 'warn'}>{total} / {DECK_SIZE} 张</Pill>
        </h3>
        <label htmlFor="dn">卡组名称</label>
        <input id="dn" value={name} onChange={(e) => setName(e.target.value)} style={{ width: '100%' }} />

        <div className="deck-builder">
          <div className="deck-search">
            <div className="row filter-row">
              <input aria-label="搜索卡牌" value={q} placeholder="搜索卡名（中 / 英 / 日）"
                onChange={(e) => setQ(e.target.value)} style={{ flex: 1, minWidth: 140 }} />
              <select aria-label="超类型" value={supertype} onChange={(e) => setSupertype(e.target.value)}>
                <option value="">全部类型</option>
                <option value="Pokemon">宝可梦</option>
                <option value="Trainer">训练家</option>
                <option value="Energy">能量</option>
              </select>
            </div>
            <label className="row" style={{ marginTop: 6 }}>
              <input type="checkbox" checked={onlyReady} onChange={(e) => setOnlyReady(e.target.checked)} />
              <span className="sm">仅显示可对战卡</span>
              {searchBusy && <Spinner label="搜索中…" />}
            </label>

            <div className="deck-results">
              {items.map((c) => {
                const n = countOf(c)
                const max = isBasicEnergy(c) ? DECK_SIZE : MAX_COPIES
                const disabled = total >= DECK_SIZE || n >= max
                return (
                  <div key={c.id} className="deck-result">
                    <CardArt card={c} className="deck-thumb" />
                    <div className="meta">
                      <b title={c.name}>{c.display}</b>
                      <span className="sm muted">
                        {c.supertype === 'Pokemon' ? '宝可梦' : c.supertype === 'Trainer' ? '训练家' : '能量'}
                        {c.subtype ? ` · ${c.subtype}` : ''}{c.hp ? ` · HP ${c.hp}` : ''}
                        {printingLabel(c) ? ` · ${printingLabel(c)}` : ''}
                      </span>
                    </div>
                    {!c.engine_ready && <Pill tone="warn">未建模</Pill>}
                    <button className="ghost sm" disabled={disabled} onClick={() => addCard(c)}>
                      {n > 0 ? `+1 · ${n}` : '添加'}
                    </button>
                  </div>
                )
              })}
              {!searchBusy && items.length === 0 && <p className="sm muted">没有匹配的卡</p>}
            </div>
            <div className="row" style={{ justifyContent: 'center', marginTop: 8 }}>
              <button className="ghost sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>上一页</button>
              <span className="sm muted mono">{page} / {pages}</span>
              <button className="ghost sm" disabled={page >= pages} onClick={() => setPage((p) => Math.min(pages, p + 1))}>下一页</button>
            </div>
          </div>

          <div className="deck-selected">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <b>已选 {selected.length} 种</b>
              <button className="ghost sm" onClick={() => { setSelected([]); setRes(null) }}>清空</button>
            </div>
            <div className="deck-list">
              {selected.map((e) => (
                <div key={e.uid} className="deck-row">
                  <CardArt card={e} className="deck-thumb" />
                  <div className="meta">
                    <b title={e.name}>{e.display}</b>
                    <span className="sm muted">
                      {e.count} 张{printingLabel(e) ? ` · ${printingLabel(e)}` : ''}
                    </span>
                  </div>
                  <div className="stepper">
                    <button aria-label={`减少 ${e.display}`} onClick={() => changeCount(e.uid, -1)}>−</button>
                    <span className="mono">{e.count}</span>
                    <button aria-label={`增加 ${e.display}`} onClick={() => changeCount(e.uid, 1)}>＋</button>
                  </div>
                </div>
              ))}
              {selected.length === 0 && <p className="sm muted">左侧搜索并添加卡牌</p>}
            </div>
            <div className="deck-summary">
              <Pill tone={basicCount > 0 ? 'ok' : 'warn'}>基础宝可梦 {basicCount}</Pill>
              <Pill tone={total === DECK_SIZE ? 'ok' : 'warn'}>还需 {Math.max(0, DECK_SIZE - total)} 张</Pill>
            </div>
          </div>
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy || total === 0} onClick={saveDeck}>
            {busy ? <Spinner label="保存中…" /> : '保存卡组'}
          </button>
        </div>
        {err && <ErrBar code={err.code} message={err.message} onRetry={saveDeck} />}

        {res && (
          <div className="import-result">
            <div className="row">
              <Pill tone={tone}>
                {res.ok ? (res.warnings.length ? '通过（有告警）' : '校验通过') : '校验未通过'}
              </Pill>
              <Pill tone={res.summary.battle_ready ? 'ok' : 'brand'}>
                可对战：{res.summary.battle_ready ? '是' : '否'}
              </Pill>
              <Pill>总 {res.summary.total} 张 / 唯 {res.summary.unique} 种</Pill>
              <Pill>宝可梦 {res.summary.pokemon} · 训练家 {res.summary.trainer} · 能量 {res.summary.energy}</Pill>
              {res.summary.unknown > 0 && <Pill tone="warn">未收录 {res.summary.unknown}</Pill>}
            </div>
            {res.errors.length > 0 && (
              <ul className="err-list">{res.errors.map((x, i) => <li key={i} className="sm">{x}</li>)}</ul>
            )}
            {res.warnings.length > 0 && (
              <ul className="warn-list">{res.warnings.map((x, i) => <li key={i} className="sm">{x}</li>)}</ul>
            )}
          </div>
        )}

        {openingRes && (<>
          <h3 style={{ marginTop: 14 }}>起手分析（n={openingRes.trials}）</h3>
          <div className="row">
            <Pill tone={openingRes.brick_rate > 0.15 ? 'brand' : 'ok'}>
              卡手率 <b className="mono">{(openingRes.brick_rate * 100).toFixed(2)}%</b></Pill>
            <Pill>平均基础 <b className="mono">{openingRes.avg_basics}</b></Pill>
            <Pill>平均能量 <b className="mono">{openingRes.avg_energy}</b></Pill>
          </div>
          <LogBlock lines={`基础数分布 ${JSON.stringify(openingRes.basics_hist)}\n能量数分布 ${JSON.stringify(openingRes.energy_hist)}`} max={140} />
        </>)}
      </div>

      <div className="card">
        <h3>我的卡组</h3>
        <div className="tbl-scroll">
          <table className="tbl">
            <thead><tr><th scope="col">名称</th><th scope="col">张数</th><th scope="col">校验</th><th scope="col">操作</th></tr></thead>
            <tbody>
              {decks.map((d) => (
                <tr key={d.id}>
                  <td>{d.name}<div className="sm muted mono">{d.id}</div></td>
                  <td className="mono">{d.summary?.total ?? '—'}</td>
                  <td>{d.valid ? <Pill tone="ok">通过</Pill> : <Pill tone="brand">未通过</Pill>}</td>
                  <td>
                    <button className="pill sm" onClick={() => editDeck(d.id)}>编辑</button>
                    <button className="pill sm" style={{ marginLeft: 6 }} onClick={() => doOpening(d.id)}>起手</button>
                    {d.valid && (
                      <button className="pill sm" style={{ marginLeft: 6 }} onClick={onGoBattle}>对战</button>
                    )}
                  </td>
                </tr>
              ))}
              {decks.length === 0 && <tr><td colSpan="4" className="muted sm">{t('empty.decks')}</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
