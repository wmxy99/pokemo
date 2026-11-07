#!/usr/bin/env node
/**
 * 路由一致性守卫（CI 与本地都可跑）
 *
 * 背景：前端从「一堆按钮切 React state」改成**真路由 + 深链接**后，出现一类
 * 新的静默故障：代码里写了 `to="/battlesx"` 或 `navigate('/libary')`，
 * 编译不报错、点击也有反应（页面跳转成功），但落到 404 —— 只有人工点到才发
 * 现。手工自查靠不住，所以把「链接目标必须命中路由表」变成可执行断言。
 *
 * 独立实现原则：本脚本**不 import** `src/router.jsx`（它是 JSX + import.meta，
 * 而且共用实现会让「两边一起写错」变成同时绿）。路由表用正则从源文件里读，
 * 匹配逻辑在脚本里另写一份 —— 与后端 `test_spa_routes.py` 的意图一致。
 *
 * 用法：
 *   node apps/web/e2e/routes_check.cjs               # 校验（退出码 0 通过 / 1 失败）
 *   node apps/web/e2e/routes_check.cjs --self-test   # 负向自测：故意喂错路径，必须被拒
 */
const fs = require('fs')
const path = require('path')

const SRC = path.join(__dirname, '..', 'src')
const ROUTER = path.join(SRC, 'router.jsx')
let failed = false
const fail = (msg) => { console.error(`✗ ${msg}`); failed = true }

/** 从 router.jsx 读路由表（`{ name: 'x', path: '/y' }` 逐条） */
function readRoutes() {
  const s = fs.readFileSync(ROUTER, 'utf8')
  const out = []
  const re = /\{\s*name:\s*'([^']+)',\s*path:\s*'([^']+)'/g
  let m
  while ((m = re.exec(s)) !== null) out.push({ name: m[1], path: m[2] })
  return out
}

const segs = (p) => String(p || '').split(/[?#]/)[0].split('/').filter(Boolean)

/**
 * 目标 ↔ 路由是否相容
 *
 * 模板字符串插值（`/battles/${id}`）在静态扫描里只能看到**前半段**，所以采用
 * 「目标段必须是某条路由段的前缀」：既能容忍动态尾段，也能抓住拼错的字面量。
 */
function compatible(target, pattern) {
  const t = segs(target)
  const p = segs(pattern)
  if (t.length > p.length) return false
  for (let i = 0; i < t.length; i += 1) {
    if (p[i].startsWith(':')) continue
    if (p[i] !== t[i]) return false
  }
  return true
}

/** 全量扫 src 下的导航字面量
 *
 * 三种写法都要覆盖（漏一种就是守卫瞎一只眼）：
 *   ① JSX 属性  `<Link to="/battles">`
 *   ② 对象字段  `{ v: 'batch', label: '…', to: '/batch' }`  ← 导航/标签表这么写
 *   ③ 调用参数  `navigate('/battles/' + id)`
 * ⚠ ① 与 ② 的差别是 `=` 与 `:`，第一版只写了 `=`，注入错链接时守卫**没变红**，
 *   负向自测当场抓出（这正是「守卫必须注入验证」的原因）。
 */
const LITERAL_PATTERNS = [
  /\bto\s*=\s*"([^"]*)"/g,
  /\bto\s*=\s*\{\s*'([^']*)'\s*\}/g,
  /\bto\s*=\s*\{\s*`([^`]*)`\s*\}/g,
  /\bto:\s*'([^']*)'/g,
  /\bto:\s*"([^"]*)"/g,
  /\bnavigate\(\s*'([^']*)'/g,
  /\bnavigate\(\s*`([^`]*)`/g,
]

function walk(dir, acc = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p, acc)
    else if (/\.(js|jsx)$/.test(e.name)) acc.push(p)
  }
  return acc
}

function collect() {
  const hits = []
  for (const file of walk(SRC)) {
    if (path.resolve(file) === path.resolve(ROUTER)) continue   // 路由表自身不算引用
    const text = fs.readFileSync(file, 'utf8')
    const lines = text.split(/\r?\n/)
    lines.forEach((line, i) => {
      LITERAL_PATTERNS.forEach((re) => {
        re.lastIndex = 0
        let m
        while ((m = re.exec(line)) !== null) {
          const raw = m[1]
          if (!raw || !raw.startsWith('/')) continue
          hits.push({ file, line: i + 1, raw })
        }
      })
    })
  }
  return hits
}

/** 模板插值统一抹成空，便于按段比较（`/battles/${id}` → `/battles/`）
 *
 * 嵌套模板（`` `/library${qs ? `?${qs}` : ''}` ``）在逐行正则里只能截到内层
 * 反引号，会留下一个**未闭合**的 `${...` 尾巴 —— 一并抹掉，否则误报。
 */
const normalize = (raw) => raw
  .replace(/\$\{[^}]*\}/g, '')
  .replace(/\$\{[^}]*$/, '')
  .replace(/\/{2,}/g, '/')

function main() {
  const routes = readRoutes()
  if (routes.length < 8) fail(`路由表解析异常：只读到 ${routes.length} 条`)

  const patterns = routes.filter((r) => r.name !== 'notFound').map((r) => r.path)
  // 路由表内部一致性：每条路由都能匹配自己（防「路径写错但没人引用」）
  patterns.forEach((p) => {
    if (!compatible(p, p)) fail(`路由表自相矛盾：${p}`)
  })

  const hits = collect()
  if (hits.length < 8) fail(`只扫到 ${hits.length} 个导航目标，扫描规则可能失效`)

  let checked = 0
  for (const h of hits) {
    const target = normalize(h.raw)
    // 纯锚点 / 外链（/compliance 是后端页面，不走前端路由）单独放行
    if (target.startsWith('/compliance')) continue
    checked += 1
    if (!patterns.some((p) => compatible(target, p))) {
      fail(`${path.relative(process.cwd(), h.file)}:${h.line} 链接目标不在路由表：${h.raw}`)
    }
  }

  if (!failed) console.log(`✓ 路由守卫通过：${routes.length} 条路由 / ${checked} 处导航目标全部命中`)
}

/** 负向自测：这些都必须被拒，否则守卫本身是坏的 */
function selfTest() {
  const patterns = readRoutes().filter((r) => r.name !== 'notFound').map((r) => r.path)
  const bad = ['/battlesx', '/libary', '/decks/1/2', '/train/abc/def', '/Battles']
  const good = ['/battles', '/battles/x1', '/library', '/decks', '/train/t1', '/model']
  let ok = true
  bad.forEach((b) => {
    if (patterns.some((p) => compatible(b, p))) { console.error(`✗ 误放行：${b}`); ok = false }
  })
  good.forEach((g) => {
    if (!patterns.some((p) => compatible(g, p))) { console.error(`✗ 误拒：${g}`); ok = false }
  })
  if (!ok) { console.error('✗ 自测失败：匹配器行为不对'); process.exit(1) }
  console.log(`✓ 自测通过：${bad.length} 个非法目标全部被拒 / ${good.length} 个合法目标全部放行`)
}

if (process.argv.includes('--self-test')) selfTest()
else { main(); if (failed) process.exit(1) }
