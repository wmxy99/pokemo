import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import './cards.css'
import { api, token } from './api.js'
import { t } from './i18n.js'
import { Toast, useToasts, PageHead } from './components/ui.jsx'
import BattleCenterPage from './pages/BattleCenterPage.jsx'
import CardCenterPage from './pages/CardCenterPage.jsx'
import { TrainingPage } from './pages/admin.jsx'
import ModelConfigPage from './pages/ModelConfig.jsx'

const FRONT = [
  { key: 'battle', label: t('nav.battle') },
  { key: 'collection', label: t('nav.collection') },
]
const ADMIN = [
  { key: 'training', label: 'RL 训练' },
  { key: 'model', label: '模型配置' },
]

// 页面头部元信息：标题 + 标签（统一由 main.jsx 渲染，避免各页面各写一套）
const PAGE_META = {
  battle: { title: '对战', tags: ['人机对局', '批量平衡性', '同 seed 可复现'] },
  collection: { title: '卡组与卡库', tags: ['60 张校验', '中 / 英 / 日检索'] },
  training: { title: 'RL 训练', tags: ['数据可视化', 'BC → PPO'] },
  model: { title: '模型配置', tags: ['本地 / 云端'] },
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
          onClick={() => { setZone('admin'); setPage('training') }}>{t('zone.admin')} · 工程</button>
        <span className="app-switch-note">非商用 · 素材标注权利方 · 差异透明</span>
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
        <PageHead {...(PAGE_META[page] || {})} />
        {zone === 'front' && (<>
          {page === 'battle' && <BattleCenterPage onMsg={onMsg} onGoDecks={() => setPage('collection')} />}
          {page === 'collection' && <CardCenterPage onMsg={onMsg} onGoBattle={() => setPage('battle')} />}
        </>)}
        {zone === 'admin' && (<>
          {page === 'training' && <TrainingPage onMsg={onMsg} authed={!!user} />}
          {page === 'model' && <ModelConfigPage onMsg={onMsg} authed={!!user} />}
        </>)}
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
