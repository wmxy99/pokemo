#!/usr/bin/env node
/**
 * i18n 键对齐守卫（CI 与本地都可跑）
 *
 * 背景：`src/i18n.js` 头部自称「中文为主 + EN 键同步维护（UI 规范 §8.2）」，
 * 但实测 EN 一度缺 5 个键（empty.decks / empty.tasks / lbl.ai_iters /
 * state.loading / state.thinking）→ EN 模式因 `t()` 兜底 `DICT.zh` 而**显示中文**，
 * 不报错、难察觉。本脚本把「键必须两边一致」变成可执行断言。
 *
 * 用法：node apps/web/e2e/check_i18n.cjs   （退出码 0 = 一致 / 1 = 不一致）
 */
const fs = require('fs')
const path = require('path')

const src = path.join(__dirname, '..', 'src', 'i18n.js')
const text = fs.readFileSync(src, 'utf8')

function block(lang) {
  const start = text.search(new RegExp(lang + "\\s*:\\s*\\{"))
  if (start < 0) throw new Error(`未找到 ${lang} 字典块`)
  let i = text.indexOf('{', start)
  let depth = 0
  const from = i
  for (; i < text.length; i++) {
    if (text[i] === '{') depth++
    else if (text[i] === '}') { depth--; if (depth === 0) break }
  }
  return text.slice(from, i)
}

const keys = (body) => new Set([...body.matchAll(/'([\w.]+)'\s*:/g)].map((m) => m[1]))

const zh = keys(block('zh'))
const en = keys(block('en'))
const onlyZh = [...zh].filter((k) => !en.has(k)).sort()
const onlyEn = [...en].filter((k) => !zh.has(k)).sort()

console.log(`i18n: zh ${zh.size} 键 / en ${en.size} 键`)
if (onlyZh.length) console.log(`  仅 zh 有：${onlyZh.join(' · ')}`)
if (onlyEn.length) console.log(`  仅 en 有：${onlyEn.join(' · ')}`)
if (onlyZh.length || onlyEn.length) {
  console.error('\n❌ i18n 键不一致：英文模式会因 t() 兜底 DICT.zh 而显示中文（不报错、难察觉）')
  process.exit(1)
}
console.log('✅ i18n 键一致')
