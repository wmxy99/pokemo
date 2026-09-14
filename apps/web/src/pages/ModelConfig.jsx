import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { Pill, Spinner, ErrBar } from '../components/ui.jsx'

/**
 * 后台 · 云端模型配置（admin）
 *
 * 能力：可视化配置 OpenAI 兼容端点（base/model/key/timeout）→ 保存即生效
 * （后台配置优先于环境变量，无需重启）；提供连通性测试与合规状态展示。
 *
 * 合规要点（与 /compliance 6.2 同源，页面上必须如实呈现）：
 *  · **本地化部署**（POKELAB_LOCAL_ONLY=1）→ 禁止保存外部端点，仅允许本机/内网；
 *  · API Key 无条件加密入库，页面只回显尾 4 位；
 *  · 所有外发经脱敏并写审计（不含原文）。
 */

const SOURCE_TONE = {
  runtime: { text: '后台配置', tone: 'ok' },
  env: { text: '环境变量', tone: 'warn' },
  none: { text: '未配置', tone: 'muted' },
}

export function ModelConfigPage({ onMsg, authed }) {
  const [cfg, setCfg] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({
    base: '', model: '', key: '', timeout: 60, preset: '',
  })
  const [test, setTest] = useState(null)
  const [load, setLoad] = useState(true)

  async function refresh() {
    setLoad(true)
    try {
      const d = await api.llmConfig()
      setCfg(d)
      setErr(null)
      // 表单初值：优先后台已保存值，其次环境变量
      const s = d.saved || {}
      setForm((f) => ({
        ...f,
        base: s.base || d.env?.base || '',
        model: s.model || d.env?.model || '',
        timeout: Number(s.timeout || d.effective?.timeout || 60),
        key: '',
      }))
    } catch (e) {
      setErr({ code: e.status || 'ERR', message: e.message })
    } finally {
      setLoad(false)
    }
  }
  useEffect(() => { refresh() }, [])

  const localOnly = !!cfg?.compliance?.local_only
  const presets = cfg?.presets || []
  const externalNow = useMemo(() => {
    const b = (form.base || '').toLowerCase()
    if (!b) return false
    return !(/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\]|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(b))
  }, [form.base])
  const blocked = localOnly && externalNow

  function applyPreset(id) {
    const p = presets.find((x) => x.id === id)
    if (!p) return
    setForm((f) => ({ ...f, preset: id, base: p.base, model: p.model }))
  }

  async function save() {
    if (blocked) {
      onMsg?.('本地化部署已启用，禁止保存外部端点', 'err')
      return
    }
    setBusy(true)
    try {
      const r = await api.llmSave({
        base: form.base.trim(), model: form.model.trim(),
        key: form.key, timeout: Number(form.timeout) || 60,
      })
      onMsg?.(`已保存并即时生效（来源：${r.source}）`)
      setForm((f) => ({ ...f, key: '' }))
      await refresh()
    } catch (e) {
      onMsg?.(e.message, 'err')
    } finally { setBusy(false) }
  }

  async function clearCfg() {
    setBusy(true)
    try {
      const r = await api.llmClear()
      onMsg?.(`已清除后台配置（当前来源：${r.source}）`)
      await refresh()
    } catch (e) { onMsg?.(e.message, 'err') } finally { setBusy(false) }
  }

  async function runTest() {
    setBusy(true); setTest(null)
    try {
      const r = await api.llmTest('请只回复四个字：连通正常')
      setTest(r)
      onMsg?.(r.ok ? `连通正常（${r.elapsed_ms}ms）` : '连通失败，见下方提示',
        r.ok ? 'ok' : 'err')
    } catch (e) {
      setTest({ ok: false, error: e.message, hint: '请求被拒绝或后端不可用' })
      onMsg?.(e.message, 'err')
    } finally { setBusy(false) }
  }

  if (!authed) {
    return (
      <section className="card">
        <h3>后台 · 云端模型配置</h3>
        <p className="sm muted">需要管理员登录后使用（配置含凭据，仅 admin 可读写）。</p>
      </section>
    )
  }

  return (
    <div className="llm-admin">
      <section className="card" style={{ marginBottom: 14 }}>
        <h3>
          后台 · 云端模型配置
          {cfg && <Pill tone={SOURCE_TONE[cfg.source]?.tone || 'muted'}>
            当前来源：{SOURCE_TONE[cfg.source]?.text || cfg.source}
          </Pill>}
          {cfg?.effective?.external === false && <Pill tone="ok">本机/内网端点</Pill>}
          {cfg?.effective?.external === true && <Pill tone="warn">外部端点（数据将外发）</Pill>}
        </h3>
        <p className="sm muted">
          OpenAI 兼容端点（DeepSeek / 通义 / Kimi / OpenAI / 自建 vLLM / Ollama 均可）。
          保存后**立即生效，无需重启**；未配置后台时回退环境变量。
          生效范围：<b>K2 卡牌效果解析</b> 与 <b>对战解说</b>。
        </p>
        {load && <Spinner label="读取配置…" />}
        {err && <ErrBar code={err.code} message={err.message} onRetry={refresh} />}
      </section>

      {localOnly && (
        <div className="alert warn" role="status" style={{ marginBottom: 14 }}>
          <b>已启用本地化部署（POKELAB_LOCAL_ONLY=1）</b>：数据不出本地，
          <b>禁止保存外部云端端点</b>；仅可配置本机/内网端点（自建 vLLM / Ollama 等）。
          如需使用云端模型，请先关闭该开关。
        </div>
      )}

      <section className="card" style={{ marginBottom: 14 }}>
        <h3>接入配置</h3>
        <div className="row" style={{ gap: 8, marginBottom: 10 }}>
          <span className="sm muted">预设：</span>
          <select value={form.preset} onChange={(e) => applyPreset(e.target.value)}
            aria-label="预设供应商">
            <option value="">（选择预设一键填充）</option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          {form.preset && (
            <span className="sm muted">
              {presets.find((p) => p.id === form.preset)?.note}
            </span>
          )}
        </div>

        <div className="kv-form">
          <label htmlFor="llm-base">端点 base_url</label>
          <input id="llm-base" value={form.base} placeholder="https://api.deepseek.com/v1"
            onChange={(e) => setForm({ ...form, base: e.target.value })} />
          <label htmlFor="llm-model">模型名</label>
          <input id="llm-model" value={form.model} placeholder="deepseek-chat"
            onChange={(e) => setForm({ ...form, model: e.target.value })} />
          <label htmlFor="llm-key">API Key</label>
          <input id="llm-key" type="password" value={form.key}
            placeholder={cfg?.saved?.key_hint
              ? `已保存（${cfg.saved.key_hint}）· 留空表示不修改`
              : '自建服务可留空'}
            onChange={(e) => setForm({ ...form, key: e.target.value })} />
          <label htmlFor="llm-timeout">超时（秒）</label>
          <input id="llm-timeout" type="number" min="5" max="600" value={form.timeout}
            onChange={(e) => setForm({ ...form, timeout: e.target.value })} />
        </div>

        {blocked && (
          <div className="alert err" role="alert" style={{ marginTop: 10 }}>
            <code className="mono">403</code>
            <span>本地化部署已启用：当前端点为外部地址，保存会被拒绝。</span>
          </div>
        )}

        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" onClick={save} disabled={busy || blocked}>
            {busy ? '处理中…' : '保存并生效'}
          </button>
          <button className="ghost" onClick={runTest} disabled={busy}>连通性测试</button>
          <button className="ghost" onClick={clearCfg} disabled={busy}>
            清除后台配置（回退环境变量）
          </button>
          <button className="ghost sm" onClick={refresh} disabled={busy}>刷新</button>
        </div>

        {test && (
          <div className={`alert ${test.ok ? 'ok' : 'err'}`} role="status"
            style={{ marginTop: 12 }}>
            {test.ok ? (
              <span>
                连通正常：模型 <code className="mono">{test.model}</code> ·
                端点 <code className="mono">{test.endpoint_host}</code> ·
                {test.external ? '外部（已脱敏外发并审计）' : '本机/内网'} ·
                {test.elapsed_ms}ms · 脱敏命中 {test.desensitized} 处
                {test.reply_preview && <> · 回复：{test.reply_preview}</>}
              </span>
            ) : (
              <span>
                <b>连通失败：</b>{test.error}　→　{test.hint}
              </span>
            )}
          </div>
        )}
      </section>

      <section className="card">
        <h3>合规状态（6.2 · 与 /compliance 同源）</h3>
        <table className="tbl">
          <tbody>
            <tr>
              <th scope="row">本地化部署</th>
              <td>
                <Pill tone={localOnly ? 'ok' : 'muted'}>
                  {localOnly ? '已启用（禁止外部调用）' : '未启用'}
                </Pill>
                <div className="sm muted">开关：<code className="mono">
                  {cfg?.compliance?.local_only_env || 'POKELAB_LOCAL_ONLY=1'}</code></div>
              </td>
            </tr>
            <tr>
              <th scope="row">密钥存储</th>
              <td>
                <Pill tone="ok">Fernet 加密入库</Pill>
                <div className="sm muted">
                  算法 {cfg?.compliance?.encryption?.algorithm || 'Fernet'} ·
                  页面仅回显尾 4 位 · 加密不可用时**拒绝落库**（不写明文）
                </div>
              </td>
            </tr>
            <tr>
              <th scope="row">外发脱敏 + 审计</th>
              <td>
                <Pill tone={cfg?.compliance?.audit_enabled ? 'ok' : 'warn'}>
                  {cfg?.compliance?.audit_enabled ? '审计已开启' : '审计已关闭'}
                </Pill>
                <div className="sm muted">
                  审计文件：<code className="mono">{cfg?.compliance?.audit_log}</code> ·
                  仅记录端点/模型/字符数/脱敏计数，**不含原文**
                </div>
              </td>
            </tr>
            <tr>
              <th scope="row">生效范围</th>
              <td className="sm">
                K2 卡牌效果解析（<code className="mono">/cards/{'{id}'}</code> 的
                effects_parsed）· 对战解说（<code className="mono">
                POST /battles/{'{id}'}/commentary</code>，带 AI 生成标识 C4）
              </td>
            </tr>
          </tbody>
        </table>
        <p className="sm muted" style={{ marginBottom: 0 }}>
          {cfg?.compliance?.note}
        </p>
      </section>
    </div>
  )
}

export default ModelConfigPage
