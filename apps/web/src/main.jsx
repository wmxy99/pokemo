import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { api, token } from './api.js'
import { t } from './i18n.js'
import { Toast, useToasts } from './components/ui.jsx'
import BattlePage from './pages/BattlePage.jsx'
import BatchPage from './pages/BatchPage.jsx'
import StatsPage from './pages/StatsPage.jsx'
import DecksPage from './pages/DecksPage.jsx'
import { DashboardPage, TrainingPage, UsersPage } from './pages/admin.jsx'

const FRONT = [
  { key: 'battle', label: t('nav.battle') },
  { key: 'batch', label: t('nav.batch') },
  { key: 'stats', label: t('nav.stats') },
  { key: 'decks', label: t('nav.decks') },
]
const ADMIN = [
  { key: 'dash', label: '仪表盘 M6' },
  { key: 'training', label: 'RL 训练' },
  { key: 'users', label: '用户管理 M5' },
]

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

function App() {
  const [zone, setZone] = useState('front')
  const [page, setPage] = useState('battle')
  const [user, setUser] = useState(null)
  const [toasts, push] = useToasts()
  const onMsg = push

  useEffect(() => {
    if (token.get()) api.me().then(setUser).catch(() => token.clear())
  }, [])

  const nav = zone === 'front' ? FRONT : ADMIN
  return (
    <>
      {/* 顶部区域切换（UI 规范 §2.1 双区结构） */}
      <div className="app-switch" role="tablist" aria-label="区域切换">
        <button role="tab" aria-selected={zone === 'front'}
          onClick={() => { setZone('front'); setPage('battle') }}>{t('zone.front')} · 玩家</button>
        <button role="tab" aria-selected={zone === 'admin'}
          onClick={() => { setZone('admin'); setPage('dash') }}>{t('zone.admin')} · 工程</button>
        <span className="app-switch-note">非商用 · 无官方卡图 · 差异透明（C1/C2/C5）</span>
      </div>

      <header className="site">
        <div className="logo"><span className="mark" aria-hidden="true" /> PokéLab <b>AI</b></div>
        <nav className="main" aria-label="主导航">
          {nav.map((p) => (
            <button key={p.key} aria-selected={page === p.key}
              onClick={() => setPage(p.key)}>{p.label}</button>
          ))}
        </nav>
        <LoginBar user={user} setUser={setUser} onMsg={onMsg} push={push} />
      </header>

      <main className="area">
        {zone === 'front' && (<>
          {page === 'battle' && <BattlePage onMsg={onMsg} onGoDecks={() => setPage('decks')} />}
          {page === 'batch' && <BatchPage onMsg={onMsg} />}
          {page === 'stats' && <StatsPage onMsg={onMsg} onGoBatch={() => setPage('batch')} />}
          {page === 'decks' && <DecksPage onMsg={onMsg} onGoBattle={() => setPage('battle')} />}
        </>)}
        {zone === 'admin' && (<>
          {page === 'dash' && <DashboardPage onMsg={onMsg} />}
          {page === 'training' && <TrainingPage onMsg={onMsg} authed={!!user} />}
          {page === 'users' && <UsersPage />}
        </>)}
      </main>

      {/* C3 三处版权声明之一：全站页脚恒定可见（不得在 view 层隐藏） */}
      <footer className="site">
        <p>
          Unofficial fan project. Pokémon and Pokémon TCG are © Nintendo / Creatures Inc. /
          GAME FREAK inc. / The Pokémon Company. Not affiliated with or endorsed by The Pokémon Company.
        </p>
        <p>
          宝可梦内容域永久免费非商用（C1）· 全站仅文字卡 + 官方外链、无官方卡图（C2）·
          引擎为社区实现，与官方规则可能存在差异（C5）。
        </p>
      </footer>

      <Toast items={toasts} />
    </>
  )
}

createRoot(document.getElementById('root')).render(<App />)
