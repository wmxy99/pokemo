import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { Pill, ErrBar, EmptyState, LogBlock, Spinner } from '../components/ui.jsx'

const SAMPLE = `4 Dreepy TWM 128
3 Drakloak TWM 129
3 Dragapult ex TWM 130
2 Dusknoir TWM 141
4 Buddy-Buddy Poffin TEF 144
4 Ultra Ball SVI 196
2 Professor's Research SVI 189
2 Boss's Orders PAL 172
10 Lightning Energy SVE 4`

export default function DecksPage({ onMsg, onGoBattle }) {
  const [text, setText] = useState('')
  const [name, setName] = useState('我的卡组')
  const [res, setRes] = useState(null)
  const [decks, setDecks] = useState([])
  const [presets, setPresets] = useState([])
  const [openingRes, setOpeningRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  async function load() {
    try {
      setDecks((await api.myDecks()).decks || [])
      setPresets((await api.deckPresets()).presets || [])
    } catch { /* 忽略 */ }
  }
  useEffect(() => { load() }, [])

  async function doImport() {
    setBusy(true); setErr(null); setOpeningRes(null)
    try {
      const r = await api.importDeck(text, name)
      setRes(r)
      onMsg(r.ok ? '解析通过 ✅' : '校验未通过（见错误清单）', r.ok ? 'ok' : 'err')
      load()
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) } finally { setBusy(false) }
  }

  async function doOpening(id) {
    setBusy(true)
    try {
      const r = await api.opening(id, 1000)
      setOpeningRes(r)
      onMsg(`起手 1,000 次：卡手率 ${(r.brick_rate * 100).toFixed(2)}%`)
    } catch (e) { setErr({ code: e.status || 'ERR', message: e.message }) } finally { setBusy(false) }
  }

  const tone = res ? (res.ok ? (res.warnings.length ? 'warn' : 'ok') : 'brand') : null
  return (
    <div className="grid cols-2">
      <div className="card">
        <h3>导入卡组 <span className="pill sm">Limitless 格式：`数量 卡名 系列 编号`</span></h3>
        <label htmlFor="dn">卡组名称</label>
        <input id="dn" value={name} onChange={(e) => setName(e.target.value)} style={{ width: '100%' }} />
        <label htmlFor="dt">卡表（粘贴；mono 字体）</label>
        <textarea id="dt" className="mono" rows={10} value={text}
          placeholder={SAMPLE} onChange={(e) => setText(e.target.value)}
          style={{ width: '100%', border: '1px solid var(--line)', borderRadius: 10, padding: 10 }} />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="primary" disabled={busy || !text.trim()} onClick={doImport}>
            {busy ? <Spinner label="校验中…" /> : t('btn.parse')}
          </button>
          <button className="ghost" onClick={() => setText(SAMPLE)}>填入示例卡表</button>
          <button className="ghost" onClick={() => { setText(''); setRes(null) }}>清空</button>
        </div>
        <p className="sm muted">
          校验规则（PRD F3）：60 张上限 · 同名 ≤4（基本能量豁免）· 必须含基础宝可梦；
          未收录卡进灰名单（C5 差异透明）并标记「不可对战」。
        </p>
        {err && <ErrBar code={err.code} message={err.message} onRetry={doImport} />}

        {res && (
          <div className="import-result">
            <div className="row">
              <Pill tone={tone}>
                {res.ok ? (res.warnings.length ? '⚠ 通过（有告警）' : '✓ 校验通过') : '✗ 校验未通过'}
              </Pill>
              <Pill tone={res.summary.battle_ready ? 'ok' : 'brand'}>
                可对战：{res.summary.battle_ready ? '是' : '否'}
              </Pill>
              <Pill>总 {res.summary.total} 张 / 唯 {res.summary.unique} 种</Pill>
              <Pill>宝可梦 {res.summary.pokemon} · 训练家 {res.summary.trainer} · 能量 {res.summary.energy}</Pill>
              {res.summary.unknown > 0 && <Pill tone="warn">未收录 {res.summary.unknown}</Pill>}
            </div>
            {res.errors.length > 0 && (
              <ul className="err-list">{res.errors.map((x, i) => <li key={i} className="sm">✗ {x}</li>)}</ul>
            )}
            {res.warnings.length > 0 && (
              <ul className="warn-list">{res.warnings.map((x, i) => <li key={i} className="sm">⚠ {x}</li>)}</ul>
            )}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="ghost sm" onClick={() => doOpening(res.id)}>{t('btn.opening')}</button>
              <button className="ghost sm" onClick={() => api.exportDeck(res.id).then((r) => { setText(r.text); onMsg('已导出为文本（往返一致）') })}>导出文本</button>
              {res.ok && <button className="primary sm" onClick={onGoBattle}>去对战</button>}
            </div>
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
        <table className="tbl">
          <thead><tr><th scope="col">名称</th><th scope="col">张数</th><th scope="col">校验</th><th scope="col">操作</th></tr></thead>
          <tbody>
            {decks.map((d) => (
              <tr key={d.id}>
                <td>{d.name}<div className="sm muted mono">{d.id}</div></td>
                <td className="mono">{d.summary?.total ?? '—'}</td>
                <td>{d.valid ? <Pill tone="ok">通过</Pill> : <Pill tone="brand">未通过</Pill>}</td>
                <td>
                  <button className="pill sm" onClick={() => api.exportDeck(d.id).then((r) => { setText(r.text); onMsg('已载入到编辑区') })}>载入</button>
                  <button className="pill sm" style={{ marginLeft: 6 }} onClick={() => doOpening(d.id)}>起手</button>
                </td>
              </tr>
            ))}
            {decks.length === 0 && <tr><td colSpan="4" className="muted sm">{t('empty.decks')}</td></tr>}
          </tbody>
        </table>

        <h3 style={{ marginTop: 16 }}>预设 Meta 对手池 <span className="pill sm">PRD §10 · Worlds 2026-08</span></h3>
        <div className="preset-row">
          {presets.map((p) => (
            <button key={p.id} className="preset" title={`${p.note}（映射：${p.demo_deck}）`}
              onClick={onGoBattle}>
              <b>{p.id} · {p.label}</b>
              <small className="muted">{p.note}</small>
            </button>
          ))}
        </div>
        <p className="sm muted">{presets.length ? '卡池以 demo 卡组代表（M1 卡池补齐后替换）；差异见 C5 声明。' : '加载中…'}</p>
      </div>
    </div>
  )
}
