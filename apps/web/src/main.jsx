import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { api, token, DECKS } from './api.js'
import BattlePage from './pages/BattlePage.jsx'
import BatchPage from './pages/BatchPage.jsx'
import StatsPage from './pages/StatsPage.jsx'

const PAGES = [
  { key: 'battle', label: '对战' },
  { key: 'batch', label: '批量对战' },
  { key: 'stats', label: '统计报告' },
]

function LoginBar({ user, setUser, onMsg }) {
  const [name, setName] = useState('')
  const [pw, setPw] = useState('')
  const [open, setOpen] = useState(false)

  async function doAuth(kind) {
    try {
      const r = kind === 'login' ? await api.login(name, pw) : await api.register(name, pw)
      token.set(r.token)
      setUser({ name: r.name, role: r.role })
      setOpen(false)
      onMsg(`已${kind === 'login' ? '登录' : '注册'}：${r.name}（${r.role}）`, 'ok')
    } catch (e) { onMsg(e.message, 'err') }
  }

  if (user) {
    return (
      <div className="user">
        <span className="pill">{user.name} · {user.role}</span>
        <button className="ghost sm" onClick={() => { token.clear(); setUser(null); onMsg('已退出登录', 'ok') }}>退出</button>
      </div>
    )
  }
  return (
    <div className="user">
      {!open && <button className="primary sm" onClick={() => setOpen(true)}>登录 / 注册</button>}
      {open && (
        <div className="row">
          <input placeholder="用户名" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 110 }} />
          <input placeholder="密码" type="password" value={pw} onChange={(e) => setPw(e.target.value)} style={{ width: 110 }} />
          <button className="primary sm" onClick={() => doAuth('login')}>登录</button>
          <button className="ghost sm" onClick={() => doAuth('register')}>注册</button>
          <button className="ghost sm" onClick={() => setOpen(false)}>取消</button>
        </div>
      )}
    </div>
  )
}

function App() {
  const [page, setPage] = useState('battle')
  const [user, setUser] = useState(null)
  const [msg, setMsg] = useState(null)
  const [batchJobs, setBatchJobs] = useState([])

  useEffect(() => {
    if (token.get()) api.me().then(setUser).catch(() => token.clear())
  }, [])

  function onMsg(text, kind = 'ok') {
    setMsg({ text, kind })
    setTimeout(() => setMsg(null), 4000)
  }

  return (
    <>
      <div className="topbar">
        <span className="brand">PokéLab <b>AI</b> · Web 客户端</span>
        <span className="pill sm">v0.1</span>
        <span className="muted sm">Unofficial community project · Non-commercial</span>
      </div>
      <header className="site">
        <div className="logo"><span className="mark" aria-hidden="true" /> PokéLab AI</div>
        <nav className="main" aria-label="主导航">
          {PAGES.map((p) => (
            <button key={p.key} aria-selected={page === p.key} onClick={() => setPage(p.key)}>{p.label}</button>
          ))}
        </nav>
        <LoginBar user={user} setUser={setUser} onMsg={onMsg} />
      </header>

      {msg && <div className={`msg ${msg.kind}`}>{msg.text}</div>}

      <div className="wrap">
        {page === 'battle' && <BattlePage decks={DECKS} onMsg={onMsg} />}
        {page === 'batch' && <BatchPage decks={DECKS} onMsg={onMsg} jobs={batchJobs} setJobs={setBatchJobs} />}
        {page === 'stats' && <StatsPage decks={DECKS} onMsg={onMsg} jobs={batchJobs} setJobs={setBatchJobs} />}
      </div>

      <footer className="site">
        Pokémon and Pokémon TCG are © Nintendo / Creatures Inc. / GAME FREAK inc. / The Pokémon Company.
        PokéLab AI 为非官方粉丝项目（Non-commercial），与 The Pokémon Company 无隶属或背书关系；仅文字卡 + 官方外链。
      </footer>
    </>
  )
}

createRoot(document.getElementById('root')).render(<App />)
