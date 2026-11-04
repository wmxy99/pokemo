import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { Pill, Spinner, ErrBar } from '../components/ui.jsx'

/**
 * 后台 · 云端模型配置（admin）
 *
 * 能力：配置 DeepSeek（base 固定、model/key/timeout 可设）→ 保存即生效
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

// 当前阶段仅接 DeepSeek；外发主机白名单在后端 privacy.ALLOWED_EXTERNAL_HOSTS 同步
const DEEPSEEK = { base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' }

export function ModelConfigPage({ onMsg, authed }) {
  const [cfg, setCfg] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [consent, setConsent] = useState(false)
  const [form, setForm] = useState({
    base: DEEPSEEK.base, model: DEEPSEEK.model, key: '', timeout: 60,
  })
  const [test, setTest] = useState(null)
  const [load, setLoad] = useState(true)

  async function refresh() {
    setLoad(true)
    try {
      const d = await api.llmConfig()
      setCfg(d)
      setErr(null)
      // 当前阶段端点固定为 DeepSeek；模型名可沿用已保存值
      const s = d.saved || {}
      setForm((f) => ({
        ...f,
        base: DEEPSEEK.base,
        model: s.model || DEEPSEEK.model,
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
  const externalNow = useMemo(() => {
    const b = (form.base || '').toLowerCase()
    if (!b) return false
    return !(/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\]|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(b))
  }, [form.base])
  const blocked = localOnly && externalNow

  async function save() {
    if (!consent) {
      onMsg?.('请先勾选外发合规确认', 'err')
      return
    }
    if (blocked) {
      onMsg?.('本地化部署已启用，禁止保存外部端点', 'err')
      return
    }
    setBusy(true)
    try {
      const r = await api.llmSave({
        base: DEEPSEEK.base, model: form.model.trim() || DEEPSEEK.model,
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
        <h3>需要管理员权限</h3>
        <p className="sm muted">模型配置包含访问凭据，登录管理员账号后才能读取与修改。</p>
      </section>
    )
  }

  return (
    <div className="llm-admin">
      <section className="card" style={{ marginBottom: 14 }}>
        <h3>
          当前来源与连通状态
          {cfg && <Pill tone={SOURCE_TONE[cfg.source]?.tone || 'muted'}>
            当前来源：{SOURCE_TONE[cfg.source]?.text || cfg.source}
          </Pill>}
          {cfg?.effective?.external === false && <Pill tone="ok">本机/内网端点</Pill>}
          {cfg?.effective?.external === true && <Pill tone="warn">外部端点（数据将外发）</Pill>}
        </h3>
        {load && <Spinner label="读取配置…" />}
        {err && <ErrBar code={err.code} message={err.message} onRetry={refresh} />}
      </section>

      {localOnly && (
        <div className="alert warn" role="status" style={{ marginBottom: 14 }}>
          <b>已启用本地化部署（POKELAB_LOCAL_ONLY=1）</b>：数据不出本地，
          <b>禁止保存外部云端端点</b>；仅可配置本机/内网端点（自建 vLLM / Ollama 等）。
        </div>
      )}

      <section className="card" style={{ marginBottom: 14 }}>
        <h3>接入配置 <Pill tone="brand">DeepSeek</Pill></h3>
        <div className="kv-form">
          <label htmlFor="llm-base">端点 base_url</label>
          <input id="llm-base" value={DEEPSEEK.base} readOnly />
          <label htmlFor="llm-model">模型名</label>
          <input id="llm-model" value={form.model} placeholder={DEEPSEEK.model}
            onChange={(e) => setForm({ ...form, model: e.target.value })} />
          <label htmlFor="llm-key">API Key</label>
          <input id="llm-key" type="password" value={form.key}
            placeholder={cfg?.saved?.key_hint
              ? `已保存（${cfg.saved.key_hint}）· 留空表示不修改`
              : 'sk-…'}
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

        <div className="alert ok" role="status" style={{ marginTop: 12 }}>
          <b>数据外发合规</b>
          <ul className="sm" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            <li>只允许发往 <code className="mono">api.deepseek.com</code>，且强制 HTTPS；其他外部主机一律拒绝。</li>
            <li>外发前对邮箱、手机号、证件号、卡号、密钥、本地路径与账号等做脱敏，只发送占位符。</li>
            <li>外部调用强制写审计（端点 / 模型 / 字符数 / 脱敏计数），不记录原文，不可关闭。</li>
            <li>API Key 加密入库，页面仅回显尾 4 位；不发送用户账号与登录信息。</li>
          </ul>
        </div>
        <label className="row" style={{ marginTop: 10, alignItems: 'flex-start' }}>
          <input type="checkbox" checked={consent}
            onChange={(e) => setConsent(e.target.checked)} style={{ marginTop: 2 }} />
          <span className="sm">我确认：外发内容仅含脱敏后的卡牌与对局文本，且已阅读上述合规说明。</span>
        </label>

        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" onClick={save}
            disabled={busy || blocked || !consent}>
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
        <h3>合规状态 <span className="pill sm">与机器可读声明同源</span></h3>
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
              <th scope="row">外发范围</th>
              <td>
                <Pill tone="ok">仅 DeepSeek · 强制 HTTPS</Pill>
                <div className="sm muted">
                  白名单：<code className="mono">
                    {(cfg?.compliance?.allowed_external_hosts || ['api.deepseek.com']).join(' · ')}
                  </code> · 其他外部主机在保存与实际调用处均被拒绝
                </div>
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
                <Pill tone="ok">
                  审计强制开启（外部外发）
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
                卡牌效果解析（<code className="mono">/cards/{'{id}'}</code> 的
                effects_parsed）· 对战解说（<code className="mono">
                POST /battles/{'{id}'}/commentary</code>，带 AI 生成标识）
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
