import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { Pill, ErrBar, EmptyState, LogBlock, Spinner } from '../components/ui.jsx'

// 中文卡表（推荐）：卡名可用中文，系列/编号可省略；英文规范名同样支持
const SAMPLE_ZH = `4 多龙梅西亚
4 多龙奇
3 多龙巴鲁托ex
2 夜巡灵
1 彷徨夜灵
1 黑夜魔灵
2 愿增猿
1 含羞苞
1 土龙弟弟
1 土龙节节
1 喵喵ex
1 吉雉鸡ex
4 好朋友宝芬
4 高级球
3 破坏之锤
2 老大的指令
2 反攻捕捉器
3 博士的研究
2 奇树
1 夜之担架
2 大地之器
2 神奇糖果
8 基本超能量
5 基本火能量`

// 英文规范名（Limitless 原始格式，与官方/社区文本互通）
const SAMPLE_EN = `4 Dreepy TWM 128
4 Drakloak TWM 129
3 Dragapult ex TWM 130
2 Duskull SIT 68
1 Dusclops SIT 69
1 Dusknoir SIT 70
4 Buddy-Buddy Poffin TEF 144
4 Ultra Ball SVI 196
2 Professor's Research SVI 189
2 Boss's Orders PAL 172
8 Basic Psychic Energy SVE 5
5 Basic Fire Energy SVE 2`

const SAMPLE = SAMPLE_ZH

export default function DecksPage({ onMsg, onGoBattle }) {
  const [text, setText] = useState('')
  const [name, setName] = useState('我的卡组')
  const [res, setRes] = useState(null)
  const [decks, setDecks] = useState([])
  const [presets, setPresets] = useState([])
  const [pd, setPd] = useState(null)              // 预设逐卡详情（M1）
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

  async function loadPreset(id) {
    onMsg(`加载预设 ${id} 逐卡映射…`)
    try {
      setPd(await api.deckPresetDetail(id))
    } catch (e) {
      setErr({ code: e.status || 'ERR', message: e.message })
    }
  }

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
        <h3>导入卡组
          <span className="pill sm">支持中文卡名 · `数量 卡名 [系列 编号]`</span></h3>
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
          <button className="ghost" onClick={() => setText(SAMPLE_ZH)}>填入中文示例</button>
          <button className="ghost" onClick={() => setText(SAMPLE_EN)}>填入英文示例</button>
          <button className="ghost" onClick={() => { setText(''); setRes(null) }}>清空</button>
        </div>
        <p className="sm muted">
          校验规则：60 张上限 · 同名不超过 4 张（基本能量豁免）· 必须包含基础宝可梦；
          未收录的卡会进入灰名单并标记「不可对战」，差异如实展示。
          <br />卡名既可用<b>中文</b>（多龙巴鲁托ex / 高级球 / 基本超能量），也可用<b>英文规范名</b>
          （Dragapult ex / Ultra Ball）；中英混写同一张卡按同一张合并计数。
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
            {res.parsed?.length > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary className="sm">
                  解析明细（{res.parsed.length} 行 · 中文卡名自动归一到英文数据键）
                </summary>
                <table className="tbl">
                  <thead><tr>
                    <th scope="col">张数</th>
                    <th scope="col">卡名（展示）</th>
                    <th scope="col">数据键</th>
                    <th scope="col">输入</th>
                    <th scope="col">状态</th>
                  </tr></thead>
                  <tbody>
                    {res.parsed.map((e, i) => (
                      <tr key={i}>
                        <td className="mono">{e.count}</td>
                        <td>{e.display || e.name}
                          {e.display && e.display !== e.name && (
                            <span className="sm muted"> · {e.name}</span>)}
                        </td>
                        <td className="mono sm">{e.canonical || '—'}</td>
                        <td className="sm">{e.input_lang === 'zh' ? '中文' : 'EN'}</td>
                        <td className="sm">
                          {!e.matched ? <Pill tone="warn">未收录</Pill>
                            : (e.meta?.engine_ready === false
                              ? <Pill tone="warn">占位</Pill>
                              : <Pill tone="ok">可对战</Pill>)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="ghost sm" onClick={() => doOpening(res.id)}>{t('btn.opening')}</button>
              <button className="ghost sm" onClick={() => api.exportDeck(res.id, 'zh').then((r) => { setText(r.text); onMsg('已导出中文卡表（可直接再导入）') })}>导出中文</button>
              <button className="ghost sm" onClick={() => api.exportDeck(res.id, 'en').then((r) => { setText(r.text); onMsg('已导出英文规范名（Limitless 互通）') })}>导出英文</button>
              <button className="ghost sm" onClick={() => api.importDeck(text, name + ' 副本').then((r) => { setRes(r); load(); onMsg(r.ok ? '副本已保存' : '副本存在校验错误') })}>存为副本</button>
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
                  <button className="pill sm" onClick={() => api.exportDeck(d.id, 'zh').then((r) => { setText(r.text); onMsg('已载入中文卡表到编辑区') })}>载入</button>
                  <button className="pill sm" style={{ marginLeft: 6 }} onClick={() => doOpening(d.id)}>起手</button>
                </td>
              </tr>
            ))}
            {decks.length === 0 && <tr><td colSpan="4" className="muted sm">{t('empty.decks')}</td></tr>}
          </tbody>
        </table>

        <h3 style={{ marginTop: 16 }}>预设对手池 <span className="pill sm">真实 60 张卡表</span></h3>
        <div className="preset-row">
          {presets.map((p) => (
            <button key={p.id} className={`preset ${pd && pd.id === p.id ? 'on' : ''}`}
              title={`${p.note}｜点击查看逐卡映射（60 卡真实卡表）`}
              onClick={() => loadPreset(p.id)}>
              <b>{p.id} · {p.label}</b>
              <small className="muted">{p.note}</small>
              <small className="muted mono">
                60 卡 · 映射 {(p.executable_rate * 100).toFixed(1)}%
                {p.replaced.length > 0 && ` · 引擎占位 ${p.replaced.reduce((s, r) => s + r.count, 0)} 张`}
              </small>
            </button>
          ))}
        </div>
        <p className="sm muted">
          {presets.length
            ? '真实卡名 + 引擎映射数值。点开可查看逐卡状态：可映射 / 近似 / 占位，差异如实展示。'
            : '加载中…'}
        </p>

        {pd && (
          <div className="card" style={{ marginTop: 12 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <b>{pd.id} · {pd.label} <span className="pill sm">{pd.cards.length} 种 / {pd.coverage.total} 张</span></b>
              <div className="row">
                <Pill tone={pd.coverage.executable_rate >= 0.9 ? 'ok' : 'warn'}>
                  可执行 {(pd.coverage.executable_rate * 100).toFixed(1)}%
                </Pill>
                <button className="ghost sm" onClick={onGoBattle}>用此卡组对战</button>
                <button className="ghost sm" onClick={() => setPd(null)}>收起</button>
              </div>
            </div>
            <div className="row sm muted" style={{ marginTop: 6 }}>
              <span>定位：{pd.role || '—'}</span><span>·</span><span>{pd.style}</span>
            </div>
            {pd.coverage.replaced.length > 0 && (
              <p className="sm" style={{ color: 'var(--brand-deep)' }}>
                引擎未实现（对战时以基础能量占位，共 {pd.coverage.replaced.reduce((s, r) => s + r.count, 0)} 张）：
                {pd.coverage.replaced.map((r) => `${r.name}×${r.count}`).join('、')}
              </p>)}
            {pd.coverage.approximated.length > 0 && (
              <p className="sm muted">
                近似实现（特性未落地，招式保留）：{pd.coverage.approximated.map((r) => `${r.name}×${r.count}`).join('、')}
              </p>)}
            <table className="tbl" style={{ marginTop: 8 }}>
              <thead><tr>
                <th scope="col">张数</th><th scope="col">卡名</th>
                <th scope="col">类型</th><th scope="col">引擎状态</th>
              </tr></thead>
              <tbody>
                {pd.cards.map((c, i) => (
                  <tr key={i}>
                    <td className="mono">{c.count}</td>
                    <td>{c.display || c.name_zh || c.name}
                      {c.display && c.display !== c.name && (
                        <span className="sm muted"> · {c.name}</span>)}</td>
                    <td className="sm muted">{c.engine || '—'}</td>
                    <td>
                      <Pill tone={c.status === 'mapped' ? 'ok' : c.status === 'approximated' ? 'warn' : 'brand'}>
                        {c.status === 'mapped' ? '可映射' : c.status === 'approximated' ? '近似' : '占位'}
                      </Pill>
                      {c.note && <span className="sm muted"> {c.note.slice(0, 42)}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>)}
      </div>
    </div>
  )
}
