/**
 * PokéLab AI · 前端路由内核（零依赖 · History API）
 *
 * 为什么自己写而不是装 react-router：
 *   1) 本仓库的 CI 全离线（`npm ci` 只拉 package-lock 里的包），新增依赖要改
 *      lock 文件、在沙箱里还得能联网装 —— 收益（本应用只有 10 条路由、没有
 *      loader/嵌套布局）配不上这个风险；
 *   2) 需要支持**同源部署在 /app 子路径**（FastAPI `app.mount`），`BASE_URL`
 *      剥离逻辑必须与 vite 的 `base` 严格一致，自己写反而更可控。
 *
 * 关键约束：
 *   - **URL 是唯一真相**：页面状态（对局 id / 卡组 id / 卡库筛选）都从路由读，
 *     这样刷新、前进后退、复制链接给别人打开，三者行为一致；
 *   - 深链接能直接打开，靠的是后端 SPA 回退（`apps/api/app/main.py`
 *     `_spa_entry`）：未知路径一律返回 index.html，静态资源照旧按文件返回。
 *     前端这边只负责「把 pathname 解析成路由」，不感知部署形态。
 *   - 不用 hash 路由：hash 无法被后端 404 回退覆盖，且 `?` 查询参数与 hash 混用
 *     时 `location.search` 语义会被打乱（卡库的筛选用 query，不是 hash）。
 */

import React from 'react'

/** vite 注入的 base（build 时 `/app/`，dev 时 `/`）→ 归一化成 '' | '/app' */
const RAW_BASE = (import.meta.env && import.meta.env.BASE_URL) || '/'
export const BASE = RAW_BASE.endsWith('/') ? RAW_BASE.slice(0, -1) : RAW_BASE

const EVENT = 'pokelab:navigate'

/**
 * 路由表（顺序无关，按段数 + 字面量匹配；`*` 必须最后）
 *
 * `title` 参与 `document.title` —— 深链接分享出去时浏览器标签要能看出是哪一页。
 */
export const ROUTES = [
  { name: 'battles', path: '/battles', title: '对战' },
  { name: 'battle', path: '/battles/:id', title: '对局' },
  { name: 'batch', path: '/batch', title: '批量对战' },
  { name: 'batchJob', path: '/batch/:id', title: '批量任务' },
  { name: 'decks', path: '/decks', title: '我的卡组' },
  { name: 'deck', path: '/decks/:id', title: '卡组编排' },
  { name: 'library', path: '/library', title: '卡库' },
  { name: 'train', path: '/train', title: 'RL 训练' },
  { name: 'trainJob', path: '/train/:id', title: '训练任务' },
  { name: 'model', path: '/model', title: '模型配置' },
  { name: 'notFound', path: '*', title: '页面不存在' },
]

function segs(p) {
  return String(p || '').split('/').filter(Boolean)
}

/** path → {name, params}；未命中返回 notFound */
export function matchPath(path) {
  const want = segs(path)
  for (const r of ROUTES) {
    if (r.name === 'notFound') continue
    const have = segs(r.path)
    if (have.length !== want.length) continue
    const params = {}
    let ok = true
    for (let i = 0; i < have.length; i += 1) {
      if (have[i].startsWith(':')) {
        try { params[have[i].slice(1)] = decodeURIComponent(want[i]) }
        catch { params[have[i].slice(1)] = want[i] }
      } else if (have[i] !== want[i]) { ok = false; break }
    }
    if (ok) return { name: r.name, params }
  }
  return { name: 'notFound', params: {} }
}

/** 内部 path → 浏览器真实 href（浏览器里要带 base 前缀） */
export function hrefOf(to) {
  const p = String(to || '/')
  const withSlash = p.startsWith('/') ? p : `/${p}`
  return BASE + withSlash
}

function readLocation() {
  if (typeof window === 'undefined') {
    return { name: 'battles', params: {}, path: '/battles', query: {}, href: '/battles' }
  }
  let p = window.location.pathname || '/'
  if (BASE && (p === BASE || p.startsWith(`${BASE}/`))) p = p.slice(BASE.length)
  if (!p.startsWith('/')) p = `/${p}`
  const query = {}
  new URLSearchParams(window.location.search || '').forEach((v, k) => { query[k] = v })
  const m = matchPath(p)
  return { ...m, path: p, query, href: window.location.href }
}

/** 统一的跳转入口：所有 url 变更都从这里走，保证订阅者收到通知 */
export function navigate(to, { replace = false, scroll = true } = {}) {
  if (typeof window === 'undefined') return
  const url = hrefOf(to)
  const same = url === window.location.pathname + window.location.search
  if (same && !replace) return
  if (replace) window.history.replaceState({}, '', url)
  else window.history.pushState({}, '', url)
  window.dispatchEvent(new Event(EVENT))
  if (scroll && !replace) window.scrollTo({ top: 0 })
}

/**
 * 订阅路由变化
 *
 * `popstate` 覆盖浏览器前进/后退；自定义事件覆盖 `navigate()`。
 * 两者都会让组件拿到新的 location（对象身份变化 → 触发重渲染）。
 */
export function useRoute() {
  const [loc, setLoc] = React.useState(readLocation)
  React.useEffect(() => {
    const onChange = () => setLoc(readLocation())
    window.addEventListener('popstate', onChange)
    window.addEventListener(EVENT, onChange)
    return () => {
      window.removeEventListener('popstate', onChange)
      window.removeEventListener(EVENT, onChange)
    }
  }, [])
  React.useEffect(() => {
    const meta = ROUTES.find((r) => r.name === loc.name)
    const suffix = loc.params && loc.params.id ? ` · ${loc.params.id}` : ''
    document.title = `${(meta && meta.title) || 'PokéLab AI'}${suffix} · PokéLab AI`
  }, [loc])
  return loc
}

/**
 * 站内链接
 *
 * 渲染成真 `<a href>`（不是 button）：可中键新开、可右键复制链接、悬停能看到
 * 目标地址 —— 这是「动态站点」与「一堆按钮拼的静态页」在体感上最直接的差别。
 * 但普通左键点击仍走 `navigate()`，避免整页刷新丢掉内存里的对局。
 */
export function Link({ to, children, onClick, ...rest }) {
  const handle = (e) => {
    if (e.defaultPrevented) return
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
    const a = rest.target || ''
    if (a === '_blank') return
    e.preventDefault()
    if (onClick) onClick(e)
    navigate(to)
  }
  return <a href={hrefOf(to)} onClick={handle} {...rest}>{children}</a>
}

/** 当前路径是否属于某个导航分组（用于顶部导航高亮） */
export const NAV_GROUP = {
  battles: ['battles', 'battle', 'batch', 'batchJob'],
  decks: ['decks', 'deck', 'library'],
  train: ['train', 'trainJob'],
  model: ['model'],
}

export function groupOf(routeName) {
  const hit = Object.entries(NAV_GROUP).find(([, names]) => names.includes(routeName))
  return hit ? hit[0] : null
}
