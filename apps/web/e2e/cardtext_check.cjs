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
  const { trainerDesc, TRAINER_TAG_DESC, DESC_FALLBACK_MARK } =
    await import(pathToFileURL(CARDTEXT).href)

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
  // ⚠ 兜底标记必须取自 `cardtext.DESC_FALLBACK_MARK`：写死旧词（如「未收录」）
  //   在兜底文案改版后会静默失效 —— 断言照样绿，缺失的族再也抓不到。
  const FALLBACK = DESC_FALLBACK_MARK
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

  // ---------- 性质 3：统一取值函数对**卡库全量**都产出说明 ----------
  // 背景（2026-11-09 用户截图）：卡库详情里「暗影隐匿」显示「（该特性暂无说明）」。
  // 根因是详情用 `abilityDesc(a.tag)`，而卡库接口**没有 tag**、只有 effect/effect_zh；
  // 招式同理（读 desc/desc_zh，卡库给 effect）→ 说明整段不渲染；训练家靠 card.tag
  // → 兜底；8 张基本能量本来就没有卡面文本 → 整段消失。
  // 现在统一走 abilityText/attackText/trainerText：**原文优先**，任何分支都非空。
  const CARDS = path.join(REPO, 'packages', 'card-db', 'data', 'cleaned',
    'cards_challenge.json')
  const { abilityText, attackText, trainerText } =
    await import(pathToFileURL(CARDTEXT).href)
  const BAD = ['暂无说明', '无说明', 'no description']
  const badText = (s) => typeof s !== 'string' || !s.trim()
    || BAD.some((b) => s.includes(b))

  if (!fs.existsSync(CARDS)) {
    fail(`找不到卡库文件：${CARDS}`)
  } else {
    const cards = (JSON.parse(fs.readFileSync(CARDS, 'utf8')).cards) || []
    const empty = []
    const fallback = []
    const mismatch = []
    // 「说明」必须是**卡面原文**，不能是通用替身文案。
    // 只断言「非空」会假绿：曾经把 `abilityText` 的原文分支删掉后，特性落到
    // 「被动特性：在本局中持续生效」——非空、不含兜底标记，检查照样通过。
    const head = (s) => String(s || '').replace(/\s+/g, ' ').trim().slice(0, 20)
    let nAb = 0
    let nAt = 0
    let nTr = 0
    for (const c of cards) {
      for (const a of c.abilities || []) {
        nAb++
        const t = abilityText(a)
        const src = a.effect_zh || a.effect
        if (badText(t)) empty.push(`${c.name} · 特性 ${a.name || '?'}`)
        else if (t.includes(DESC_FALLBACK_MARK)) fallback.push(`${c.name} · 特性 ${a.name || '?'}`)
        else if (src && head(t) !== head(src)) {
          mismatch.push(`${c.name} · 特性 ${a.name || '?'}（显示「${head(t)}…」，原文「${head(src)}…」）`)
        }
      }
      for (const a of c.attacks || []) {
        nAt++
        const t = attackText(a)
        const src = a.effect_zh || a.effect
        if (badText(t)) empty.push(`${c.name} · 招式 ${a.name || '?'}`)
        else if (src && head(t) !== head(src)) {
          mismatch.push(`${c.name} · 招式 ${a.name || '?'}（显示「${head(t)}…」，原文「${head(src)}…」）`)
        }
      }
      const isMon = String(c.supertype || '').toLowerCase() === 'pokemon'
      if (!isMon) {
        nTr++
        const src = c.rules_text_zh || c.library?.rules_text_zh
          || c.other_text_zh || c.other_text || c.rules_text
        const t = trainerText({ ...c, rules_text_zh: c.rules_text_zh || c.library?.rules_text_zh })
        if (badText(t)) empty.push(`${c.name} · 卡牌效果`)
        else if (t.includes(DESC_FALLBACK_MARK)) fallback.push(`${c.name} · 卡牌效果`)
        else if (src && head(t) !== head(src)) {
          mismatch.push(`${c.name} · 卡牌效果（显示「${head(t)}…」，原文「${head(src)}…」）`)
        }
      }
    }
    if (mismatch.length) {
      fail(`卡库里有 ${mismatch.length} 处说明**与卡面原文不一致**（落到替身文案）：`
        + mismatch.slice(0, 6).join(' / '))
    }
    if (empty.length) {
      fail(`卡库 ${cards.length} 张里有 ${empty.length} 处**没有说明**：`
        + empty.slice(0, 12).join(' / ') + (empty.length > 12 ? ' …' : ''))
    } else {
      console.log(`✓ 卡库 ${cards.length} 张：特性 ${nAb} / 招式 ${nAt} / 训练家·能量 ${nTr} 全部有说明`)
    }
    // 卡库侧**全都**有卡面原文 → 不应有任何一条停在模板兜底
    if (fallback.length) {
      fail(`卡库里有 ${fallback.length} 处只拿到模板兜底（原文没被取到）：`
        + fallback.slice(0, 8).join(' / '))
    }
  }

  // ---------- 性质 4：引擎池全量（只有 tag/desc，没有卡面原文） ----------
  if (fs.existsSync(POOL)) {
    const pool = JSON.parse(fs.readFileSync(POOL, 'utf8'))
    const empty = []
    let nAb = 0
    let nAt = 0
    for (const c of pool.cards || []) {
      for (const a of c.abilities || []) {
        nAb++
        if (badText(abilityText(a))) empty.push(`${c.name} · 特性 ${a.name || '?'}`)
      }
      for (const a of c.attacks || []) {
        nAt++
        if (badText(attackText(a))) empty.push(`${c.name} · 招式 ${a.name || '?'}`)
      }
    }
    if (empty.length) {
      fail(`引擎池 ${(pool.cards || []).length} 张里有 ${empty.length} 处没有说明：`
        + empty.slice(0, 12).join(' / '))
    } else {
      console.log(`✓ 引擎池：特性 ${nAb} / 招式 ${nAt} 全部有说明`)
    }
  }

  // ---------- 性质 5：源码里不得再出现「暂无说明」这类占位 ----------
  // 只看**代码**，跳过注释：注释里记录「曾经显示过这条占位」是必要的背景说明，
  // 若连注释一起禁，下次有人想写清历史就得删掉线索（更糟）。
  const SRC = ['src/cardtext.js', 'src/components/card.jsx', 'src/pages/LibraryPage.jsx',
    'src/names.js']

  /** 去掉 // 行注释、/* *\/ 块注释与字符串外的干扰（保留字符串内容） */
  function stripComments(src) {
    let out = ''
    let i = 0
    let inBlock = false
    let inLine = false
    let quote = ''
    while (i < src.length) {
      const c = src[i]
      const n = src[i + 1]
      if (inLine) {
        if (c === '\n') { inLine = false; out += c }
        i++
        continue
      }
      if (inBlock) {
        if (c === '*' && n === '/') { inBlock = false; i += 2 } else i++
        continue
      }
      if (quote) {
        out += c
        if (c === '\\') { out += src[i + 1] || ''; i += 2; continue }
        if (c === quote) quote = ''
        i++
        continue
      }
      if (c === '"' || c === "'" || c === '`') { quote = c; out += c; i++; continue }
      if (c === '/' && n === '/') { inLine = true; i += 2; continue }
      if (c === '/' && n === '*') { inBlock = true; i += 2; continue }
      out += c
      i++
    }
    return out
  }

  const hits = []
  for (const rel of SRC) {
    const p = path.join(__dirname, '..', rel)
    if (!fs.existsSync(p)) continue
    const code = stripComments(fs.readFileSync(p, 'utf8'))
    for (const b of BAD) {
      if (code.includes(b)) hits.push(`${rel} 含「${b}」`)
    }
  }
  if (hits.length) {
    fail(`这些文件仍含无说明占位：${hits.join(' / ')}`)
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
