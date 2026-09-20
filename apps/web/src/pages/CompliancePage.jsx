import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Pill, Spinner, ErrBar, LogBlock } from '../components/ui.jsx'

/**
 * 合规详情页（F9）· 内嵌展示 `GET /compliance` 的机器可读声明
 *
 * 为什么要有这一页：合规声明此前只能通过用户可读文档（docs/COMPLIANCE.md）
 * 与后端 JSON 端点（/compliance）访问，站内没有任何入口——政策要求「显著标识」，
 * 页脚一段话不足以承载 6.1/6.2 的全部条款与**当前实现状态**。
 *
 * 本页只做**如实呈现**（C3 数据来源透明）：
 *   · 文本取自后端同源常量，前端不复制、不改写条款（避免文案漂移）
 *   · 展示「落地状态」而非口号（哪些措施已生效、如何验证）
 *   · 后端不可用时降级为静态摘要 + 原始 JSON / 文档链接（不空白、不谎报）
 */

const USAGE_POLICY_LABEL = {
  permitted_with_attribution: { text: '可用（须标注权利方）', tone: 'ok' },
  permitted: { text: '可用', tone: 'ok' },
  not_included: { text: '不打包 / 不传播', tone: 'muted' },
  prohibited: { text: '禁止', tone: 'brand' },
}
const POLICY_KEY_LABEL = {
  card_names_and_rules: '卡牌名称与规则文本',
  card_images: '卡牌图像',
  decklist_text: '卡表文本（卡组清单）',
  official_artwork_bundling: '官方美术素材打包',
}
const STATUS_TONE = {
  enforced: { text: '已落地', tone: 'ok' },
  partial: { text: '部分落地', tone: 'warn' },
  planned: { text: '计划中', tone: 'muted' },
}

/**
 * 去掉接口文本里的 Markdown 强调标记（`**x**`）
 *
 * 合规声明以**纯文本**在页面呈现；后端常量同时被 Markdown 报告复用，可能带
 * `**` 强调。这里统一剥离，避免页面上出现字面星号（单一来源不变，只做渲染净化）。
 */
function stripMd(v) {
  if (typeof v === 'string') return v.replace(/\*\*/g, '')
  if (Array.isArray(v)) return v.map(stripMd)
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, stripMd(x)]))
  }
  return v
}

function Row({ k, children }) {
  return (
    <tr>
      <th scope="row" style={{ width: 190, textTransform: 'none', letterSpacing: 0 }}>
        {k}
      </th>
      <td>{children}</td>
    </tr>
  )
}

function Section({ id, title, extra, children }) {
  return (
    <section className="card" id={id} style={{ marginBottom: 14 }}>
      <h3>
        {title}
        {extra}
      </h3>
      {children}
    </section>
  )
}

function Content61({ c }) {
  const up = c.usage_policy || {}
  const ai = c.ai_labeling || {}
  const st = c.ai_status || {}
  return (
    <Section id="c-61" title="6.1 · 内容合规"
      extra={<span className="pill pill-ok">{c.id}</span>}>
      <p className="sm" style={{ lineHeight: 1.7 }}>{c.statement}</p>

      <h4 className="sm muted" style={{ margin: '14px 0 6px' }}>权利方</h4>
      <div className="row">
        {c.rights_holders?.map((h) => <span key={h} className="pill pill-muted">{h}</span>)}
        {c.no_endorsement && <Pill tone="brand">与权利方无关联 / 未获背书</Pill>}
        {c.research_only && <Pill tone="warn">仅研究参考</Pill>}
      </div>

      <h4 className="sm muted" style={{ margin: '14px 0 6px' }}>使用政策</h4>
      {c.usage_policy_note && <p className="sm usage-note">{c.usage_policy_note}</p>}
      <table className="tbl">
        <tbody>
          {Object.entries(up).map(([k, v]) => {
            const m = USAGE_POLICY_LABEL[v] || { text: v, tone: 'muted' }
            return (
              <Row key={k} k={POLICY_KEY_LABEL[k] || k}>
                <Pill tone={m.tone}>{m.text}</Pill>
                <code className="mono sm" style={{ marginLeft: 8 }}>{v}</code>
              </Row>
            )
          })}
        </tbody>
      </table>

      {c.attribution_required && (
        <>
          <h4 className="sm muted" style={{ margin: '14px 0 6px' }}>标注要求（必须显著展示）</h4>
          <p className="sm attr-quote">{c.attribution_template}</p>
        </>
      )}

      <h4 className="sm muted" style={{ margin: '14px 0 6px' }}>
        AI / 机器生成内容标识（C4）
        <Pill tone={st.enforced ? 'ok' : 'warn'}>{st.enforced ? '已落地' : '待落地'}</Pill>
      </h4>
      <p className="sm">{ai.policy}</p>
      <table className="tbl">
        <tbody>
          <Row k="必需字段">
            {(ai.required_fields || []).map((f) => (
              <code key={f} className="mono sm" style={{ marginRight: 8 }}>{f}</code>
            ))}
          </Row>
          <Row k="适用位置">
            <ul className="tight">{(ai.where || []).map((w) => <li key={w} className="sm">{w}</li>)}</ul>
          </Row>
          <Row k="前端要求">{ai.ui_requirement}</Row>
          <Row k="实现校验">
            <ul className="tight">
              {Object.entries(st.examples || {}).map(([k, v]) => (
                <li key={k} className="sm"><code className="mono">{k}</code>：{v}</li>
              ))}
            </ul>
          </Row>
        </tbody>
      </table>

      <h4 className="sm muted" style={{ margin: '14px 0 6px' }}>责任</h4>
      <p className="sm">{c.liability}</p>
    </Section>
  )
}

function Privacy62({ d }) {
  const t0 = d.transport || {}
  const s = d.storage || {}
  const live = s.live || {}
  const llm = d.llm_call || {}
  const ns = d.no_secondary_use || {}
  const loc = d.local_deployment || {}
  return (
    <Section id="c-62" title="6.2 · 数据隐私保护"
      extra={<span className="pill pill-ok">{d.id}</span>}>
      <p className="sm" style={{ lineHeight: 1.7 }}>{d.statement}</p>

      <table className="tbl">
        <tbody>
          <Row k="传输加密">
            <Pill tone={t0.https_supported ? 'ok' : 'muted'}>
              {t0.https_supported ? '支持 HTTPS' : '未支持'}
            </Pill>
            <div className="sm muted" style={{ marginTop: 4 }}>{t0.how}</div>
            <div className="sm muted">本地自签工具：<code className="mono">{t0.self_signed_helper}</code></div>
          </Row>
          <Row k="口令存储">
            <code className="mono sm">{s.password_hashing}</code>
            <div className="sm muted" style={{ marginTop: 4 }}>不含明文；不可逆哈希 + 随机盐</div>
          </Row>
          <Row k="字段级加密">
            <Pill tone={live.enabled ? 'ok' : 'muted'}>
              {live.enabled ? '已启用' : '可选 / 未启用'}
            </Pill>
            <div className="sm muted" style={{ marginTop: 4 }}>{s.field_encryption}</div>
            <div className="sm muted">启用方式：<code className="mono">{s.encryption_env}</code></div>
            {live.available === false && (
              <div className="sm muted">检测：{live.error || '组件不可用'}</div>
            )}
          </Row>
          <Row k="本地化部署">
            <Pill tone={loc.supported ? 'ok' : 'muted'}>
              {loc.supported ? '支持完全离线' : '不支持'}
            </Pill>
            <div className="sm muted" style={{ marginTop: 4 }}>
              <code className="mono">{loc.local_only_env}</code> —— {loc.effect}
            </div>
          </Row>
          <Row k="大模型调用">
            <Pill tone={llm.desensitize_before_send ? 'ok' : 'brand'}>
              {llm.desensitize_before_send ? '外发前脱敏' : '未脱敏'}
            </Pill>
            <div className="sm muted" style={{ marginTop: 4 }}>
              脱敏规则 {(llm.rules || []).length} 条
              {llm.rules?.length ? `：${llm.rules.join('、')}` : ''}
            </div>
            <div className="sm muted">
              审计：{llm.audit_enabled ? '已开启' : '关闭'}
              {llm.audit_log ? `（${llm.audit_log}）` : ''} ｜ {llm.audit_content}
            </div>
          </Row>
          <Row k="不二次使用">
            <div className="sm">{ns.statement}</div>
            <div className="row" style={{ marginTop: 6 }}>
              {(ns.enforcement || []).map((x) => <span key={x} className="pill pill-muted">{x}</span>)}
            </div>
          </Row>
        </tbody>
      </table>
      <p className="sm muted" style={{ marginTop: 10 }}>
        以上「实时状态」由后端读取 `agent.privacy` 与 `app.crypto` 的当前配置生成，
        非静态承诺（C3）。
      </p>
    </Section>
  )
}

function RedLines({ lines }) {
  return (
    <Section id="c-red" title="红线（C1–C5）" extra={<Pill tone="muted">v1.1</Pill>}>
      <table className="tbl">
        <thead>
          <tr><th>编号</th><th>标题</th><th>条款</th><th>状态</th><th>修订</th></tr>
        </thead>
        <tbody>
          {lines.map((r) => {
            const m = STATUS_TONE[r.status] || { text: r.status, tone: 'muted' }
            return (
              <tr key={r.id}>
                <td><code className="mono">{r.id}</code></td>
                <td>{r.title}</td>
                <td className="sm" style={{ maxWidth: 460 }}>{r.statement}</td>
                <td><Pill tone={m.tone}>{m.text}</Pill></td>
                <td className="sm muted">{r.revised_from || '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </Section>
  )
}

function AuditSection() {
  const [state, setState] = useState({ loading: false, data: null, err: null })
  async function load() {
    setState({ loading: true, data: null, err: null })
    try {
      const d = await api.complianceAudit(100)
      setState({ loading: false, data: d, err: null })
    } catch (e) {
      setState({ loading: false, data: null, err: e })
    }
  }
  return (
    <Section id="c-audit" title="外部调用审计（管理员）"
      extra={<Pill tone="warn">仅 admin</Pill>}>
      <p className="sm muted">
        审计记录仅含端点 / 模型 / 字符数 / 脱敏计数，<b>不含原文</b>。
        非管理员访问返回 403，可据此确认权限边界生效。
      </p>
      <div className="row">
        <button className="ghost sm" onClick={load} disabled={state.loading}>
          {state.loading ? '查询中…' : '查询最近 100 条'}
        </button>
      </div>
      {state.err && (
        <div className="alert err" role="alert" style={{ marginTop: 10 }}>
          <code className="mono">{state.err.status || 'ERR'}</code>
          <span>{state.err.message}{state.err.status === 403 ? '（当前账号非管理员）' : ''}</span>
        </div>
      )}
      {state.data && (
        <>
          <div className="row" style={{ marginTop: 10 }}>
            <span className="pill pill-muted">共 {state.data.total ?? 0} 条</span>
          </div>
          <LogBlock label="审计记录" max={220}
            lines={(state.data.entries || []).slice(-40).map(
              (e) => JSON.stringify(e))} />
        </>
      )}
    </Section>
  )
}

/** 后端不可用时的降级摘要（静态、不谎报：明确标注「未取到实时状态」） */
function Fallback() {
  return (
    <Section id="c-fallback" title="降级摘要（未取到实时状态）"
      extra={<Pill tone="warn">离线</Pill>}>
      <p className="sm">
        后端未就绪，无法读取 <code className="mono">GET /compliance</code> 的实时声明。
        以下为静态要点，**实时实现状态请以后端返回为准**：
      </p>
      <ul>
        <li className="sm"><b>6.1 内容合规</b>：宝可梦卡牌名称与规则文本仅用于教学研究与算法演示；
          卡牌图像与卡表原文可用，但须**标注权利方**（Nintendo · Creatures Inc. ·
          GAME FREAK inc. · The Pokémon Company），且不得声称关联或获其背书；
          AI/机器生成的效果解析与对战解说**必须显著标识**，不得作为官方规则呈现。</li>
        <li className="sm"><b>6.2 数据隐私</b>：支持 HTTPS 传输加密与字段级存储加密、
          支持完全本地化部署；调用大模型 API 前对文本脱敏并留审计；不将用户数据用于
          服务以外用途或向第三方提供。</li>
        <li className="sm"><b>红线</b>：C1 非官方非商业 · C2 素材使用与标注（v1.1 修订）·
          C3 数据来源透明 · C4 AI 产出标识 · C5 差异透明（引擎覆盖如实标注）。</li>
      </ul>
      <div className="row">
        <a className="ghost sm" href="/compliance" target="_blank" rel="noreferrer">
          直接查看 /compliance（JSON）
        </a>
      </div>
    </Section>
  )
}

export default function CompliancePage() {
  const [state, setState] = useState({ loading: true, data: null, err: null })
  async function load() {
    setState({ loading: true, data: null, err: null })
    try {
      const d = await api.compliance()
      setState({ loading: false, data: stripMd(d), err: null })
    } catch (e) {
      setState({ loading: false, data: null, err: e })
    }
  }
  useEffect(() => { load() }, [])

  const d = state.data
  return (
    <div className="compliance-wrap">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <p className="sm muted" style={{ margin: 0 }}>
            本页展示与后端接口同源的声明与<b>当前实现状态</b>，并与页脚、卡库、报告导出保持一致，
            避免多处文案漂移。
          </p>
        </div>
        <div className="row">
          {d && <span className="pill pill-muted">版本 {d.version}</span>}
          {d && <span className="pill pill-muted">更新 {d.updated}</span>}
          <button className="ghost sm" onClick={load} disabled={state.loading}>
            {state.loading ? '刷新中…' : '刷新'}
          </button>
        </div>
      </div>

      {state.loading && <div style={{ padding: 20 }}><Spinner label="读取合规声明…" /></div>}

      {state.err && (
        <>
          <ErrBar code={state.err.status || 'ERR'} message={`合规声明读取失败：${state.err.message}`}
            onRetry={load} retryLabel="重试" />
          <Fallback />
        </>
      )}

      {d && (
        <>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <b>{d.declaration}</b>
              <span className="row">
                <span className="pill pill-muted">机器可读端点 /compliance</span>
                <a className="ghost sm" href="/compliance" target="_blank" rel="noreferrer">
                  原始 JSON
                </a>
              </span>
            </div>
            <p className="sm muted" style={{ marginBottom: 0 }}>
              人读版本：<code className="mono">{d.doc}</code>
              {d.doc_exists === false && '（当前部署未随包提供，请以本页为准）'}
            </p>
          </div>

          <Content61 c={d.content_compliance} />
          <Privacy62 d={d.data_privacy} />
          <RedLines lines={d.red_lines || []} />
          <AuditSection />
        </>
      )}

      {!state.loading && (
        <p className="sm muted">
          本页内容为社区非商业项目的自查披露，不构成法律意见；如与官方条款冲突，
          以官方条款为准（C1 · C3）。
        </p>
      )}
    </div>
  )
}
