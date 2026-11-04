#!/usr/bin/env node
/**
 * 卡面文案守卫（CI 与本地都可跑）
 *
 * 背景：真实卡组（A1–A4 / B1–B5）一进手牌就**整屏黑屏** ——
 * React error #31「Objects are not valid as a React child (found: object with
 * keys {zh, en})」。根因是 `src/cardtext.js` 的 `trainerDesc()` 对**未收录**的
 * 训练家 tag 族走了「返回 `{zh, en}` 对象」的兜底分支；该对象被直接当 React
 * 子节点渲染 → React 抛错 → **没有 ErrorBoundary** → 整棵树卸载 → 全屏黑。
 *
 * 为什么此前没有任何测试能拦住：
 *   - 前端 JS 不参与 Python 单测；
 *   - 后端不读前端的文案表；
 *   - 演示卡组的 tag 全在旧文案表里 → 只有**真实卡池**的 39 类族会踩。
 *
 * 本脚本把两条关键性质变成可执行断言：
 *   1. `trainerDesc(tag)` 对**任何**输入都必须返回**字符串**（不得返回对象）；
 *   2. 卡池里实际出现的每个 tag 族都必须在 `TRAINER_TAG_DESC` 里有说明
 *      （否则用户看到的是「未收录」，不是「说明」）。
 *
 * 用法：node apps/web/e2e/cardtext_check.cjs   （退出码 0 = 通过 / 1 = 失败）
 */
const fs = require('fs')
const path = require('path')
const { pathToFileURL } = require('url')

const REPO = path.join(__dirname, '..', '..', '..')
const POOL = path.join(REPO, 'packages', 'engine', 'engine', 'data', 'cards_pool_ext.json')
const CARDTEXT = path.join(__dirname, '..', 'src', 'cardtext.js')

function fail(msg) {
  console.error('✗ ' + msg)
  process.exitCode = 1
}

;(async () => {
  const { trainerDesc, TRAINER_TAG_DESC } = await import(pathToFileURL(CARDTEXT).href)

  // ---------- 性质 1：trainerDesc 恒返回字符串 ----------
  const probe = ['boss', 'recover:1:any', '', null, undefined, '完全不存在的族:xyz',
    'shuffle_draw_split:4:4', 'gust', 'heal:30:choose', 'tool:hp_plus:70']
  for (const t of probe) {
    const v = trainerDesc(t)
    if (typeof v !== 'string') {
      fail(`trainerDesc(${JSON.stringify(t)}) 返回了 ${typeof v}（${JSON.stringify(v)}）` +
        ` —— 必须是字符串，否则 React 会把它当子节点渲染 → 整屏黑屏`)
    }
  }

  // ---------- 性质 2：卡池里每个 tag 族都有说明 ----------
  if (!fs.existsSync(POOL)) {
    fail(`找不到卡池文件：${POOL}`)
    return
  }
  const pool = JSON.parse(fs.readFileSync(POOL, 'utf8'))
  const heads = new Set()
  let cards = 0
  for (const c of pool.cards || []) {
    if (c.supertype !== 'Trainer') continue
    const tag = c.tag
    if (!tag) continue
    cards++
    heads.add(String(tag).split(':')[0])
  }
  const missing = [...heads].filter((h) => !(h in TRAINER_TAG_DESC)).sort()
  if (missing.length) {
    fail(`卡池 ${cards} 张训练家卡涉及的 ${heads.size} 个 tag 族中，` +
      `${missing.length} 个没有文案说明：${missing.join(', ')}`)
  } else {
    console.log(`✓ 卡池 ${cards} 张训练家卡的 ${heads.size} 个 tag 族全部有说明`)
  }

  // ---------- 逐族实跑：必须返回非空字符串且不是兜底文案 ----------
  const FALLBACK = '未收录'
  const notRendered = []
  for (const h of [...heads].sort()) {
    const v = trainerDesc(h)
    if (typeof v !== 'string' || !v.trim()) notRendered.push(h)
    else if (v.includes(FALLBACK)) notRendered.push(h)
  }
  if (notRendered.length) {
    fail(`这些 tag 族仍落到兜底文案（说明未补齐）：${notRendered.join(', ')}`)
  } else if (!process.exitCode) {
    console.log(`✓ ${heads.size} 个 tag 族的文案全部可渲染`)
  }

  if (process.exitCode) {
    console.error('卡面文案守卫失败')
  } else {
    console.log('OK: 卡面文案守卫通过')
  }
})().catch((e) => {
  console.error('卡面文案守卫执行失败：' + (e && e.stack ? e.stack : e))
  process.exitCode = 1
})
