// 卡库页（F2）端到端验证：真实 Chrome
// 覆盖：导航 → 统计条 → 搜索（EN/中文/归一）→ 筛选 → 翻页 → 详情（招式/能量/伤害）
// 用法：node library_e2e.cjs [base] [outdir]
const puppeteer = require('puppeteer-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUTDIR = process.argv[3] || '.'
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe']
  .find((p) => fs.existsSync(p))

const steps = []
const log = (name, ok, extra = {}) => steps.push({ name, ok, ...extra })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
      '--single-process', '--no-zygote'],
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1500, height: 1000 })
  const errors = []
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 180)))
  page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text().slice(0, 180)) })

  const shot = async (name) => {
    try { await page.screenshot({ path: path.join(OUTDIR, name) }) } catch { /* 忽略 */ }
  }
  const clickNav = (label) => page.evaluate((lb) => {
    const b = [...document.querySelectorAll('nav.main button')]
      .find((x) => x.textContent.trim() === lb)
    if (!b) return false
    b.click(); return true
  }, label)
  /** 等表格行数稳定 */
  const waitRows = async (min = 1, tries = 20) => {
    for (let i = 0; i < tries; i += 1) {
      const n = await page.evaluate(() => document.querySelectorAll('table.tbl tbody tr').length)
      if (n >= min) return n
      await sleep(400)
    }
    return 0
  }
  const readStats = () => page.evaluate(() => {
    const cards = [...document.querySelectorAll('.stat-row .stat, .stat-row > div')]
    return cards.map((c) => c.innerText.replace(/\s+/g, ' ').trim())
  })

  try {
    // ---- 1) 打开并进入卡库 ----
    await page.goto(`${BASE}/app/`, { waitUntil: 'domcontentloaded', timeout: 25000 })
    await sleep(1200)
    const navOk = await clickNav('卡库')
    await sleep(1800)
    const rows0 = await waitRows(20)
    log('① 导航到「卡库」并加载列表', navOk && rows0 >= 20, { navOk, rows: rows0 })
    await shot('lib_1_list.png')

    // ---- 2) 统计条（总量 1267）----
    const statsTxt = (await readStats()).join(' | ')
    const total1267 = /1267/.test(statsTxt)
    log('② 统计条显示卡库总量 1267', total1267, { stats: statsTxt.slice(0, 260) })

    // ---- 3) 搜索 EN ----
    await page.evaluate(() => {
      const i = document.querySelector('#lib-q')
      const S = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      S.call(i, 'dragapult'); i.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await sleep(1400)
    await waitRows(1)
    const en = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('table.tbl tbody tr')]
      return { n: rows.length, first: rows[0] ? rows[0].innerText.replace(/\s+/g, ' ').trim() : null }
    })
    log('③ 搜索 dragapult → 命中 1 行且为 Dragapult ex',
      en.n === 1 && /Dragapult ex/.test(en.first || ''), en)

    // ---- 4) 搜索中文 ----
    await page.evaluate(() => {
      const i = document.querySelector('#lib-q')
      const S = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      S.call(i, '多龙'); i.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await sleep(1400)
    const zh = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('table.tbl tbody tr')]
      return { n: rows.length, names: rows.map((r) => r.querySelector('td').innerText.replace(/\s+/g, ' ').trim()) }
    })
    log('④ 搜索中文「多龙」→ 命中多张（含中文展示名）',
      zh.n >= 3 && zh.names.some((x) => /多龙/.test(x)), zh)
    await shot('lib_2_search_zh.png')

    // ---- 5) 清空搜索 + 筛选训练家 ----
    await page.evaluate(() => {
      const i = document.querySelector('#lib-q')
      const S = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      S.call(i, ''); i.dispatchEvent(new Event('input', { bubbles: true }))
      const sel = document.querySelector('#lib-st')
      sel.value = 'Trainer'; sel.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await sleep(1500)
    const tr = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('table.tbl tbody tr')]
      const kinds = rows.map((r) => r.children[2].innerText)
      return { n: rows.length, trainerOnly: kinds.every((k) => /训练家/.test(k)), sample: kinds.slice(0, 3) }
    })
    log('⑤ 筛选「训练家」→ 全为训练家', tr.n > 0 && tr.trainerOnly, tr)

    // ---- 6) 筛选引擎可对战 ----
    await page.select('#lib-er', 'true')
    await sleep(1500)
    const er = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('table.tbl tbody tr')]
      return { n: rows.length, allReady: rows.every((r) => /可对战/.test(r.innerText)) }
    })
    log('⑥ 筛选「仅可对战」→ 全部可对战且在 36 张内', er.allReady && er.n > 0 && er.n <= 30, er)

    // ---- 7) 翻页 ----
    await page.select('#lib-st', ''); await page.select('#lib-er', '')
    await sleep(1500)
    const p1 = await page.evaluate(() => document.querySelector('table.tbl tbody tr td').innerText)
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((x) => x.textContent.includes('下一页'))
      if (b) b.click()
    })
    await sleep(1500)
    const p2 = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('table.tbl tbody tr')]
      return { first: rows[0] ? rows[0].querySelector('td').innerText : null, n: rows.length }
    })
    log('⑦ 翻页 → 第二页内容不同', !!p2.first && p2.first !== p1 && p2.n > 0, { p1, p2 })

    // ---- 8) 详情面板（招式/能量/伤害）----
    await page.evaluate(() => {
      const i = document.querySelector('#lib-q')
      const S = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      S.call(i, 'dragapult'); i.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await sleep(1500)
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('table.tbl tbody tr button')].find((x) => x.textContent.includes('详情'))
      if (b) b.click()
    })
    await sleep(1200)
    const det = await page.evaluate(() => {
      const blocks = [...document.querySelectorAll('.move-block')]
      const chips = [...document.querySelectorAll('.kv-grid .echip')].map((c) => c.textContent)
      return {
        moves: blocks.map((b) => b.innerText.replace(/\s+/g, ' ').trim()),
        chips,
        hasHp: /320/.test(document.body.innerText),
        kv: (document.querySelector('.kv-grid') || {}).innerText || '',
      }
    })
    const movesOk = det.moves.some((m) => /Jet Headbutt/.test(m) && /70/.test(m))
      && det.moves.some((m) => /Phantom Dive/.test(m) && /200/.test(m))
    log('⑧ 详情面板：招式名 + 伤害 + 能量筹码 + HP',
      movesOk && det.hasHp && det.chips.length > 0,
      { moves: det.moves, chips: det.chips, hp320: det.hasHp })
    await shot('lib_3_detail.png')

    // ---- 9) 无运行时错误 ----
    log('⑨ 零页面错误', errors.length === 0, { errors: errors.slice(0, 5) })
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 220) })
  }

  const failed = steps.filter((s) => !s.ok).length
  const out = {
    ok: failed === 0, failed, errors: errors.slice(0, 8),
    url: `${BASE}/app/`, steps,
  }
  const dst = path.join(OUTDIR, `lib_${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}.json`)
  try { fs.writeFileSync(dst, JSON.stringify(out, null, 1)) } catch { /* 忽略 */ }
  await browser.close()
  process.stdout.write(JSON.stringify(out, null, 1) + '\n')
})()
