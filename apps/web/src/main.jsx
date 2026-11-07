import React, { Suspense, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import './cards.css'
import { api, token } from './api.js'
import { t } from './i18n.js'
import { Toast, useToasts, PageHead, Spinner } from './components/ui.jsx'
import { useRoute, Link, navigate, groupOf } from './router.jsx'

/**
 * 页面按路由**懒加载**：首屏只加载当前页面的 chunk，切页时按需拉取。
 * 这样深链接直接进「卡库」时不必先下载对战/训练两套组件。
 */
const BattleCenterPage = React.lazy(() => import('./pages/BattleCenterPage.jsx'))
const CardCenterPage = React.lazy(() => import('./pages/CardCenterPage.jsx'))
const ModelConfigPage = React.lazy(() => import('./pages/ModelConfig.jsx'))
const TrainingPage = React.lazy(() =>
  import('./pages/admin.jsx').then((m) => ({ default: m.TrainingPage })))

/**
 * 顶层错误边界（ErrorBoundary）
 *
 * 为什么必须有：React 里任何一个组件渲染抛错（最常见的是「把对象当子节点
 * 渲染」→ error #31），**没有 ErrorBoundary 时 React 会卸载整棵树** → 整屏黑屏，
 * 用户连报错都看不到（踩过：真实卡组的训练家 tag 未收录 → trainerDesc 返回
 * {zh,en} 对象 → 一进手牌就黑屏）。
 *
 * 这里把错误**就地显示**在页面里，并给出可复制的诊断信息，其余部分照常可用。
 */
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('[UI] 渲染异常已被错误边界捕获：', error, info?.componentStack)
  }
  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="card" style={{ borderColor: 'var(--brand, #c0392b)' }}>
        <h3>该区域渲染出错（已隔离，其余功能仍可用）</h3>
        <p className="sm muted">
          错误不会影响其他页面。请把下面的信息反馈给维护者：
        </p>
        <pre className="log mono sm">{String(error?.message || error)}</pre>
        <button className="ghost sm" onClick={() => this.setState({ error: null })}>
          重试
        </button>
      </div>
    )
  }
}

// 导航 = 路由分组。每项都带 `to`（真实 href），既能点击也能复制/新开。
const FRONT = [
  { key: 'battles', label: t('nav.battle'), to: '/battles' },
  { key: 'decks', label: t('nav.collection'), to: '/decks' },
]
const ADMIN = [
  { key: 'train', label: 'RL 训练', to: '/train' },
  { key: 'model', label: '模型配置', to: '/model' },
]

// 页面头部元信息：标题 + 标签（统一由 main.jsx 渲染，避免各页面各写一套）
const PAGE_META = {
  battles: { title: '对战', tags: ['人机对局', '批量平衡性', '同 seed 可复现'] },
  decks: { title: '卡组与卡库', tags: ['60 张校验', '中 / 英 / 日检索'] },
  train: { title: 'RL 训练', tags: ['数据可视化', 'BC → PPO'] },
  model: { title: '模型配置', tags: ['本地 / 云端'] },
}

/** 当前页链接：动态站点最实用的小工具 —— 复制出去别人能直接落到同一页 */
function ShareBar({ loc }) {
  const [done, setDone] = useState(false)
  async function copy() {
    try { await navigator.clipboard.writeText(window.location.href) } catch { /* 忽略 */ }
    setDone(true)
    setTimeout(() => setDone(false), 1600)
  }
  return (
    <span className="share-bar" title="本页状态已写进 URL：刷新、前进后退、分享都保持同一页">
      <code className="mono">{loc.path}{loc.query && Object.keys(loc.query).length
        ? `?${new URLSearchParams(loc.query)}` : ''}</code>
      <button className="ghost sm" onClick={copy}>{done ? '已复制' : '复制链接'}</button>
    </span>
  )
}

function LoginBar({ user, setUser, onMsg, push }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [pw, setPw] = useState('')
  async function doAuth(kind) {
    try {
      const r = kind === 'login' ? await api.login(name, pw) : await api.register(name, pw)
      token.set(r.token)
      setUser({ name: r.name, role: r.role })
      setOpen(false)
      push(`已${kind === 'login' ? '登录' : '注册'}：${r.name}（${r.role}）`)
    } catch (e) { push(e.message, 'err') }
  }
  if (user) {
    return (
      <div className="user">
        <span className="pill">{user.name} · {user.role}</span>
        <button className="ghost sm" onClick={() => { token.clear(); setUser(null); push('已退出登录') }}>{t('btn.logout')}</button>
      </div>
    )
  }
  return (
    <div className="user">
      {!open && <button className="primary sm" onClick={() => setOpen(true)}>{t('btn.login')}</button>}
      {open && (<div className="row">
        <input aria-label="用户名" placeholder="用户名" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 110 }} />
        <input aria-label="密码" placeholder="密码" type="password" value={pw} onChange={(e) => setPw(e.target.value)} style={{ width: 110 }} />
        <button className="primary sm" onClick={() => doAuth('login')}>登录</button>
        <button className="ghost sm" onClick={() => doAuth('register')}>注册</button>
        <button className="ghost sm" onClick={() => setOpen(false)}>取消</button>
      </div>)}
    </div>
  )
}

/** 404：URL 打错时给出可点的出口，而不是白屏 */
function NotFound({ loc }) {
  return (
    <div className="card">
      <h3>页面不存在：<code className="mono">{loc.path}</code></h3>
      <p className="sm muted">
        这个地址没有对应的页面。可能是链接过期，或者路由改过名。
      </p>
      <div className="row">
        <Link className="primary" to="/battles">去对战</Link>
        <Link className="ghost" to="/decks">去卡组与卡库</Link>
        <Link className="ghost" to="/library">去卡库</Link>
      </div>
    </div>
  )
}

function App() {
  const loc = useRoute()
  const [user, setUser] = useState(null)
  const [toasts, push] = useToasts()
  const onMsg = push

  useEffect(() => {
    if (token.get()) api.me().then(setUser).catch(() => token.clear())
  }, [])

  // 根路径与旧书签（`/`）统一落到对战页；用 replace 避免历史里多一条空记录
  useEffect(() => {
    if (loc.path === '/' || loc.path === '') navigate('/battles', { replace: true })
  }, [loc.path])

  const group = groupOf(loc.name)
  const zone = (group === 'train' || group === 'model') ? 'admin' : 'front'
  const nav = zone === 'front' ? FRONT : ADMIN
  const trainJobId = loc.name === 'trainJob' ? loc.params.id : null

  return (
    <>
      {/* 顶部区域切换（UI 规范 §2.1 双区结构）→ 现在是真链接 */}
      <div className="app-switch" role="tablist" aria-label="区域切换">
        <Link role="tab" aria-selected={zone === 'front'} to="/battles">
          {t('zone.front')} · 玩家
        </Link>
        <Link role="tab" aria-selected={zone === 'admin'} to="/train">
          {t('zone.admin')} · 工程
        </Link>
        <span className="app-switch-note">非商用 · 素材标注权利方 · 差异透明</span>
      </div>

      <header className="site">
        <Link className="logo" to="/battles"><span className="mark" aria-hidden="true" /> PokéLab <b>AI</b></Link>
        <nav className="main" aria-label="主导航">
          {nav.map((p) => (
            <Link key={p.key} to={p.to} aria-selected={group === p.key}>{p.label}</Link>
          ))}
        </nav>
        <ShareBar loc={loc} />
        <LoginBar user={user} setUser={setUser} onMsg={onMsg} push={push} />
      </header>

      <main className="area">
        {group ? <PageHead {...(PAGE_META[group] || {})} /> : null}
        {/* 页面级错误边界：任一页渲染抛错时只隔离该页，导航/页脚仍可用 */}
        <ErrorBoundary>
          <Suspense fallback={<div className="card"><Spinner label="页面加载中…" /></div>}>
            {loc.name === 'notFound' && <NotFound loc={loc} />}
            {(group === 'battles') && (
              <BattleCenterPage onMsg={onMsg} onGoDecks={() => navigate('/decks')} route={loc} />
            )}
            {(group === 'decks') && (
              <CardCenterPage onMsg={onMsg} onGoBattle={() => navigate('/battles')} route={loc} />
            )}
            {(group === 'train') && (
              <TrainingPage onMsg={onMsg} authed={!!user} jobId={trainJobId} />
            )}
            {group === 'model' && <ModelConfigPage onMsg={onMsg} authed={!!user} />}
          </Suspense>
        </ErrorBoundary>
      </main>

      {/* 全站版权与隐私声明：恒定可见，不在 view 层隐藏 */}
      <footer className="site">
        <div className="footer-grid">
          <section>
            <h4>版权与来源</h4>
            <p>
              Pokémon and Pokémon TCG are © Nintendo / Creatures Inc. /
              GAME FREAK inc. / The Pokémon Company.
              本项目为非官方粉丝研究项目，与上述权利方无关联，亦未获其授权或赞助。
            </p>
          </section>
          <section>
            <h4>使用范围</h4>
            <p>
              宝可梦内容仅用于研究与教学演示，不作商业用途。卡牌名称、规则文本与图像的使用均标注权利方；
              对战结果与平衡性报告仅供研究参考。引擎由社区独立实现，与官方规则可能存在差异。
            </p>
          </section>
          <section>
            <h4>AI 与隐私</h4>
            <p>
              AI 生成的效果解析与对战解说均以「AI 生成」徽标显著标识。数据传输支持 HTTPS，
              数据库字段支持加密，可本地化部署；调用外部模型前对敏感信息脱敏，
              不将用户数据用于服务以外用途，也不向第三方提供。
            </p>
          </section>
        </div>
        <div className="footer-bottom">
          <span>仅供学习与研究 · 非官方项目</span>
          <a href="/compliance" target="_blank" rel="noreferrer">机器可读声明</a>
        </div>
      </footer>

      <Toast items={toasts} />
    </>
  )
}

createRoot(document.getElementById('root')).render(<App />)
