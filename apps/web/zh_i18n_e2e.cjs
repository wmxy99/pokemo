// 中文展示端到端验证：对战棋盘/手牌/动作按钮 · 预设逐卡 · 报告与矩阵
// 用法：node zh_i18n_e2e.cjs <base> <outDir>
// 环境提示：Chrome 需 --single-process --no-zygote；每步 flush 落盘；截图名带时间戳。
const puppeteer = require('puppeteer-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUTDIR = process.argv[3] || 'C:/Users/无名/pokelab_shots'
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find((p) => fs.existsSync(p))
const OUT = path.join(OUTDIR, `zh_${Date.now()}.json`)
const steps = []
const errors = []
const CJK = /[\u4e00-\u9fa5]/
const LATIN_ONLY = /^[A-Za-z0-9\s'’.\-#/]+$/

function flush() {
  try {
    fs.mkdirSync(OUTDIR, { recursive: true })
    fs.writeFileSync(OUT, JSON.stringify({
      ok: steps.every((s) => s.ok), errors: errors.slice(0, 8), steps,
    }, null, 1))
  } catch { /* 忽略 */ }
}
const log = (name, ok, extra = {}) => { steps.push({ name, ok, ...extra }); flush() }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--no-zygote', '--single-process'],
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1500, height: 1000 })
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 160)))
  page.on('response', (r) => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`) })
  const RUN = new Date().toISOString().slice(11, 19).replace(/:/g, '')
  const shot = async (n) => {
    const p = path.join(OUTDIR, `zh${RUN}_${n}`)
    try { await page.screenshot({ path: p }); return p } catch (e) { return 'ERR:' + String(e).slice(0, 70) }
  }
  const clickText = (sel, t) => page.evaluate((s, x) => {
    const el = [...document.querySelectorAll(s)].find((b) => b.textContent.includes(x))
    if (!el) return false; el.click(); return true
  }, sel, t)

  try {
    await page.goto(BASE + '/app/', { waitUntil: 'domcontentloaded', timeout: 30000 })
    // 登录
    const uname = 'zh_' + Date.now().toString(36).slice(-6)
    await clickText('button', '登录 / 注册')
    await sleep(400)
    await page.evaluate((u) => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
      const ins = document.querySelectorAll('.user input')
      set.call(ins[0], u); ins[0].dispatchEvent(new Event('input', { bubbles: true }))
      set.call(ins[1], 'pw123456'); ins[1].dispatchEvent(new Event('input', { bubbles: true }))
    }, uname)
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('.user button')].find((x) => x.textContent.trim() === '注册')
      b && b.click()
    })
    await sleep(1500)
    log('登录', await page.evaluate(() => !!localStorage.getItem('pokelab_token')))

    // ---- ① 对战页：卡组下拉 + 棋盘 + 手牌 + 动作按钮全中文 ----
    await clickText('nav.main button', '对战')
    await sleep(900)
    const sel = await page.evaluate(() => [...document.querySelectorAll('select option')]
      .map((o) => o.textContent.trim()))
    log('① 卡组下拉为中文', sel.some((x) => /多龙巴鲁托ex/.test(x)) &&
      sel.some((x) => /演示卡组/.test(x)), { options: sel.slice(0, 4) })

    // 起局（默认我方 A1 / 对手 A3 已在 DECKS 首位）
    await clickText('button', '开始对局')
    await sleep(3500)
    const board = await page.evaluate(() => {
      const chips = [...document.querySelectorAll('.chip-name')].map((c) => c.textContent.trim())
      const hand = (document.querySelector('.hand') || {}).textContent || ''
      const acts = [...document.querySelectorAll('.acts .act')].map((b) => b.textContent.trim())
      return { chips, hand: hand.replace(/^手牌：/, '').slice(0, 80), acts: acts.slice(0, 6) }
    })
    const chipsZh = board.chips.filter((c) => c && c !== '—')
    const handZh = CJK.test(board.hand)
    const actsZh = board.acts.length > 0 && board.acts.every((a) => CJK.test(a))
    const s1 = await shot('battle_zh.png')
    log('① 棋盘/手牌/动作按钮中文', chipsZh.length > 0 && chipsZh.every((c) => CJK.test(c)) &&
      handZh && actsZh, { ...board, shot: s1 })

    // ---- ② 卡组页：预设逐卡中文 ----
    await clickText('nav.main button', '卡组管理')
    await sleep(1200)
    await page.evaluate(() => {
      const p = [...document.querySelectorAll('.preset')].find((x) => x.textContent.includes('A1'))
      p && p.click()
    })
    await sleep(1500)
    const preset = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('.tbl tbody tr')].map((r) => r.textContent.replace(/\s+/g, ' ').trim())
      const names = rows.slice(0, 6).map((r) => r.split(' ').slice(1, 3).join(' '))
      return { count: rows.length, rows: rows.slice(0, 3), names }
    })
    const s2 = await shot('preset_zh.png')
    log('② 预设逐卡中文', preset.count >= 20 &&
      /多龙梅西亚|多龙奇|多龙巴鲁托ex/.test(preset.rows.join(' ')), { ...preset, shot: s2 })

    // ---- ③ 池任务（A1 vs A3）→ cells 中文 + 报告 md 中文 ----
    await clickText('nav.main button', '批量对战')
    await sleep(800)
    const sub = await page.evaluate(async () => {
      const tk = localStorage.getItem('pokelab_token')
      const r = await fetch('/batch/pool-jobs', { method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tk },
        body: JSON.stringify({ decks_a: ['A1'], decks_b: ['A3'], games_each: 20, seed_start: 5 }) })
      return { status: r.status, text: (await r.text()).slice(0, 110) }
    })
    log('③ 池任务提交', [200, 202].includes(sub.status), sub)
    // 详情面板里才有对位 cells 表：先等任务行出现，点「查看」，再读表
    let cellsZh = null
    for (let i = 0; i < 20; i += 1) {
      await sleep(1500)
      const ready = await page.evaluate(() => [...document.querySelectorAll('tbody tr')]
        .some((r) => /batch_/.test(r.textContent) && r.querySelector('button.pill')))
      if (ready) {
        await page.evaluate(() => {
          const r = [...document.querySelectorAll('tbody tr')]
            .find((x) => /batch_/.test(x.textContent) && x.querySelector('button.pill'))
          const b = r && [...r.querySelectorAll('button')].find((x) => x.textContent.includes('查看'))
          b && b.click(); return true
        })
        await sleep(1800)
      }
      cellsZh = await page.evaluate(() => {
        const rows = [...document.querySelectorAll('.card tbody tr')]
          .map((r) => r.textContent.replace(/\s+/g, ' ').trim())
        return rows.find((r) => /%/.test(r) && /多龙|索罗亚克/.test(r)) || null
      })
      if (cellsZh) break
    }
    const s3 = await shot('batch_cells_zh.png')
    log('③ 详情对位行中文', !!cellsZh && /多龙巴鲁托ex|N的索罗亚克ex/.test(cellsZh), { cellsZh, shot: s3 })

    // 报告 md 预览
    const jid = sub.text.match(/"(batch_[^"]+)"/)?.[1]
    if (jid) {
      const rep = await page.evaluate(async (id) => {
        const tk = localStorage.getItem('pokelab_token')
        const r = await fetch(`/batch/jobs/${id}/report?format=md`,
          { headers: { Authorization: 'Bearer ' + tk } })
        return (await r.text()).slice(0, 400)
      }, jid)
      log('③ 报告 md 中文卡组名', /多龙巴鲁托ex/.test(rep) && /N的索罗亚克ex/.test(rep),
        { head: rep.split('\n').slice(0, 6) })
    }

    // ---- ④ 统计页：矩阵表头中文 ----
    await clickText('nav.main button', '统计报告')
    await sleep(2500)
    const matrix = await page.evaluate(() => ({
      heads: [...document.querySelectorAll('.matrix thead th')].map((t) => t.textContent.trim()),
      rowHeads: [...document.querySelectorAll('.matrix tbody th')].map((t) => t.textContent.trim()).slice(0, 5),
      aria: (document.querySelector('.matrix .heat') || {}).getAttribute
        ? (document.querySelector('.matrix .heat') || {}).getAttribute('aria-label') : null,
    }))
    const s4 = await shot('stats_matrix_zh.png')
    log('④ 矩阵表头中文', matrix.heads.some((h) => CJK.test(h)) &&
      matrix.rowHeads.some((h) => CJK.test(h)), { ...matrix, shot: s4 })
    log('④ 矩阵 aria 中文', matrix.aria ? CJK.test(matrix.aria) : false, { aria: matrix.aria })
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 220) })
  }

  flush()
  try { await browser.close() } catch { /* 忽略 */ }
  process.stdout.write('done ' + OUT + '\n')
  process.exit(0)
})()
