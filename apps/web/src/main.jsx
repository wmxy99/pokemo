import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import './cards.css'
import { api, token } from './api.js'
import { t } from './i18n.js'
import { Toast, useToasts, PageHead } from './components/ui.jsx'
import BattlePage from './pages/BattlePage.jsx'
import BatchPage from './pages/BatchPage.jsx'
import StatsPage from './pages/StatsPage.jsx'
import DecksPage from './pages/DecksPage.jsx'
import LibraryPage from './pages/LibraryPage.jsx'
import CompliancePage from './pages/CompliancePage.jsx'
import { DashboardPage, TrainingPage, UsersPage } from './pages/admin.jsx'
import ModelConfigPage from './pages/ModelConfig.jsx'

const FRONT = [
  { key: 'battle', label: t('nav.battle') },
  { key: 'batch', label: t('nav.batch') },
  { key: 'stats', label: t('nav.stats') },
  { key: 'decks', label: t('nav.decks') },
  { key: 'library', label: t('nav.library') },
  { key: 'compliance', label: t('nav.compliance') },
]
const ADMIN = [
  { key: 'dash', label: '仪表盘' },
  { key: 'training', label: 'RL 训练' },
  { key: 'model', label: '模型配置' },
  { key: 'users', label: '用户管理' },
]

// 页面头部元信息：标题 + 一句话说明（统一由 main.jsx 渲染，避免各页面各写一套）
// 说明文案面向使用者而非内部读者：不出现「F2 / M6 / PRD §7」这类编号。
const PAGE_META = {
  battle: { title: '对战', tags: ['人机对局', '点牌出牌', '同 seed 可复现'],
    desc: '与规则基线或 MCTS AI 对局。左侧选择双方卡组与 AI 强度；中间是牌桌 —— 能出的牌带绿色角标，单击即出；需要指定对象的会提示你点选场上高亮的目标（高亮的正是实际会作用到的那只宝可梦）。点不可用的牌或场上的牌可看完整卡面。指令输入与全部动作表收在下方折叠区，作为备选入口。' },
  batch: { title: '批量对战', tags: ['平衡性统计'],
    desc: '提交成批对局用于平衡性分析：统计先后手胜率与平均回合数，并导出报告。任务可随时中断，已完成的对局会保留。' },
  stats: { title: '统计报告', tags: ['胜率矩阵', '三格式导出'],
    desc: '汇总批量任务的对位胜率矩阵，并对异常对局给出看门狗提示；报告可导出为 Markdown / CSV / JSON。' },
  decks: { title: '卡组管理', tags: ['60 张校验'],
    desc: '导入并校验 60 张卡组（Limitless 文本格式），查看起手模拟结果与预设对手池的可执行率（未建模的卡会明确标注为占位）。' },
  library: { title: '卡库', tags: ['中 / 英 / 日检索'],
    desc: '检索收录卡牌的名称与招式效果，并按「引擎是否可对战」筛选，便于判断某张卡在模拟中的可用程度。' },
  compliance: { title: '合规声明', tags: ['非商用', '权利方标注'],
    desc: '本项目的非商用范围、权利方标注要求、数据隐私与 AI 生成内容的标识说明（含实现状态自述）。' },
  dash: { title: '仪表盘', tags: ['近 7 日'],
    desc: '近 7 日批量对局与训练任务汇总，以及关键指标看门狗（达标情况如实标注）。' },
  training: { title: 'RL 训练', tags: ['BC → PPO'],
    desc: '启动与管理强化学习训练任务：BC 预训练 → 对基线 PPO → 自对弈与蒸馏；可实时查看日志与收益曲线。' },
  model: { title: '模型配置', tags: ['本地 / 云端'],
    desc: '配置用于卡牌效果解析与对战解说的模型端点。密钥加密存储，调用前对敏感信息脱敏。' },
  users: { title: '用户管理', tags: ['管理员'],
    desc: '查看注册用户、角色与状态；首个注册账号自动成为管理员。' },
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
          onClick={() => { setZone('admin'); setPage('dash') }}>{t('zone.admin')} · 工程</button>
        <span className="app-switch-note">非商用 · 素材标注权利方 · 差异透明（C1/C2 v1.1/C5）</span>
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
          {page === 'battle' && <BattlePage onMsg={onMsg} onGoDecks={() => setPage('decks')} />}
          {page === 'batch' && <BatchPage onMsg={onMsg} />}
          {page === 'stats' && <StatsPage onMsg={onMsg} onGoBatch={() => setPage('batch')} />}
          {page === 'decks' && <DecksPage onMsg={onMsg} onGoBattle={() => setPage('battle')} />}
          {page === 'library' && <LibraryPage onMsg={onMsg} />}
          {page === 'compliance' && <CompliancePage />}
        </>)}
        {zone === 'admin' && (<>
          {page === 'dash' && <DashboardPage onMsg={onMsg} />}
          {page === 'training' && <TrainingPage onMsg={onMsg} authed={!!user} />}
          {page === 'model' && <ModelConfigPage onMsg={onMsg} authed={!!user} />}
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
          宝可梦内容域永久免费非商用（C1）· 卡名/规则文本/图像依合规 6.1 使用并标注权利方（C2 v1.1）·
          引擎为社区实现，与官方规则可能存在差异（C5）。
        </p>
        <p>
          <b>6.1 内容合规</b>：卡牌名称与规则仅用于教学研究与算法演示，版权与商标归属其权利方；
          对战结果与平衡性报告仅供研究参考；<b>AI 生成的效果解析与对战解说均已显著标识</b>；
          因不当使用产生的责任由使用者承担。
        </p>
        <p>
          <b>6.2 数据隐私</b>：支持 HTTPS 加密传输与数据库存储加密、支持本地化部署；
          调用大模型 API 前对敏感信息脱敏，不将用户数据用于服务以外用途或向第三方提供。
          {'  '}
          <button className="linklike" onClick={() => { setZone('front'); setPage('compliance') }}>
            站内查看完整合规声明（6.1 / 6.2 / C1–C5）
          </button>
          {' · '}
          <a href="/compliance" target="_blank" rel="noreferrer">原始 JSON /compliance</a>
        </p>
      </footer>

      <Toast items={toasts} />
    </>
  )
}

createRoot(document.getElementById('root')).render(<App />)
