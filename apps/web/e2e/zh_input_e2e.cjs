// 中文卡名（输入 + 展示 + 导出）UI 端到端验证 —— 真实 Chrome
// 覆盖：① 中文卡表导入（粘贴中文卡名）② 解析明细「中文展示 / 英文数据键」
//      ③ 中文字段落库后可对战 ④ 导出中文/英文 ⑤ 对战场内中文卡名
// 用法：node zh_input_e2e.cjs [base] [outdir]
const puppeteer = require('puppeteer-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUTDIR = process.argv[3] || path.join(process.env.USERPROFILE || '.', 'pokelab_shots')
const CHROME = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
].find((p) => fs.existsSync(p))

const steps = []
const errors = []
const log = (name, ok, extra = {}) => steps.push({ name, ok, ...extra })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1500,1000'],
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1500, height: 980 })
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + String(e.message).slice(0, 160)))
  page.on('console', (m) => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text().slice(0, 160)) })
  page.on('response', (r) => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`) })

  const clickText = (sel, text) => page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((b) => b.textContent.trim() === t ||
      b.textContent.includes(t))
    if (!el) return false
    el.click(); return true
  }, sel, text)

  const shot = async (name) => {
    const p = path.join(OUTDIR, name)
    try { await page.screenshot({ path: p }); return p } catch (e) { return 'ERR:' + String(e).slice(0, 60) }
  }

  try {
    await page.goto(BASE + '/app/', { waitUntil: 'domcontentloaded', timeout: 30000 })
    await sleep(2000)

    // ---- 0) 注册登录 ----
    const uname = 'zhui_' + Date.now().toString(36).slice(-6)
    await clickText('button', '登录 / 注册')
    await sleep(500)
    await page.evaluate((u) => {
      const ins = document.querySelectorAll('.user input')
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      ins[0].focus(); setter.call(ins[0], u); ins[0].dispatchEvent(new Event('input', { bubbles: true }))
      ins[1].focus(); setter.call(ins[1], 'pw123456'); ins[1].dispatchEvent(new Event('input', { bubbles: true }))
    }, uname)
    await clickText('button', '注册')
    await sleep(1800)
    const tok = await page.evaluate(() => !!localStorage.getItem('pokelab_token'))
    log('① 注册/登录', tok, { uname })

    // ---- 1) 卡组管理：填入中文示例 → 导入 ----
    await clickText('nav.main button', '卡组管理')
    await sleep(1200)
    await clickText('button', '填入中文示例')
    await sleep(600)
    const sample = await page.evaluate(() => document.querySelector('#dt').value)
    const sampleZh = /多龙巴鲁托ex/.test(sample) && /高级球/.test(sample)
    log('② 中文示例卡表已填入', sampleZh, { head: sample.split('\n').slice(0, 3).join(' / ') })

    await clickText('button', '解析并校验')
    await sleep(2500)

    const result = await page.evaluate(() => {
      const card = [...document.querySelectorAll('.card')].find((c) => c.querySelector('.import-result'))
      const box = card ? card.querySelector('.import-result') : null
      const pills = box ? [...box.querySelectorAll('.pill')].map((p) => p.textContent.trim()) : []
      const summary = box ? box.innerText : ''
      const errList = box ? [...box.querySelectorAll('.err-list li')].map((li) => li.textContent.trim()) : []
      return {
        pills,
        // 「✓ 校验通过」或「⚠ 通过（有告警）」都算通过（告警含 M1 灰名单占位，属 C5 预期）
        passed: /校验通过|通过（有告警）/.test(summary),
        ready: /可对战：\s*是/.test(summary),
        errors: errList,
        text: summary.replace(/\s+/g, ' ').slice(0, 220),
      }
    })
    log('③ 中文卡表校验通过且可对战',
      !!result.passed && !!result.ready && result.errors.length === 0, result)
    const s1 = await shot('zhin_1_import_zh.png')

    // ---- 2) 解析明细：中文展示 + 英文数据键 ----
    const detail = await page.evaluate(() => {
      const d = [...document.querySelectorAll('details')].find((x) => /解析明细/.test(x.textContent))
      if (!d) return { found: false }
      d.open = true
      const rows = [...d.querySelectorAll('tbody tr')].map((r) =>
        [...r.querySelectorAll('td')].map((td) => td.textContent.trim()))
      const drag = rows.find((r) => r[1].includes('多龙巴鲁托ex')) || null
      const langZh = rows.filter((r) => r[3] === '中文').length
      return { found: true, total: rows.length, drag, langZh,
        head: [...d.querySelectorAll('thead th')].map((t) => t.textContent.trim()) }
    })
    log('④ 解析明细：中文展示 + 英文数据键', !!detail.found && !!detail.drag &&
      detail.drag[2] === 'Dragapult ex' && detail.drag[3] === '中文',
      { total: detail.total, langZh: detail.langZh, dragRow: detail.drag, head: detail.head })
    const s2 = await shot('zhin_2_parse_detail.png')

    // ---- 3) 导出中文 / 英文 ----
    await clickText('button', '导出中文')
    await sleep(1200)
    const zhText = await page.evaluate(() => document.querySelector('#dt').value)
    await clickText('button', '导出英文')
    await sleep(1500)
    const enText = await page.evaluate(() => document.querySelector('#dt').value)
    log('⑤ 导出中文=中文卡名 / 导出英文=规范名',
      /多龙巴鲁托ex/.test(zhText) && /^4 Dreepy/m.test(enText),
      { zhHead: zhText.split('\n').slice(0, 3).join(' / '),
        enHead: enText.split('\n').slice(0, 2).join(' / ') })

    // ---- 4) 去对战：中文字段落库后可作为卡组出战 ----
    await clickText('button', '去对战')
    await sleep(1500)
    const battleSetup = await page.evaluate(() => {
      const sel = [...document.querySelectorAll('select')]
      const opts = sel[0] ? [...sel[0].options].map((o) => o.textContent) : []
      const mine = opts.find((o) => /我的：/.test(o)) || null
      return { opts: opts.slice(0, 6), mine }
    })
    log('⑥ 我的卡组（中文卡表）出现在对战下拉', !!battleSetup.mine, battleSetup)

    if (battleSetup.mine) {
      await page.evaluate(() => {
        const sel = document.querySelectorAll('select')[0]
        const opt = [...sel.options].find((o) => /我的：/.test(o.textContent))
        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set
        setter.call(sel, opt.value); sel.dispatchEvent(new Event('change', { bubbles: true }))
      })
      await sleep(600)
    }
    await clickText('button', '开始对局')
    await sleep(3000)
    const board = await page.evaluate(() => {
      const chips = [...document.querySelectorAll('.cf-name')].map((c) => c.textContent.trim())
      const acts = [...document.querySelectorAll('.acts button')].map((b) => b.textContent.trim())
      return { chips, acts: acts.slice(0, 6), turn: (document.querySelector('.zt') || {}).textContent || '' }
    })
    const zhChips = board.chips.filter((c) => c && !/^[\x00-\x7F]*$/.test(c))
    log('⑦ 对战场内卡名中文', zhChips.length > 0,
      { chips: board.chips.slice(0, 6), zhCount: zhChips.length })
    log('⑧ 动作按钮中文化', board.acts.some((a) => !/^[\x00-\x7F]*$/.test(a)),
      { acts: board.acts })
    const s3 = await shot('zhin_3_battle_zh.png')

    // 提交一个动作，确认日志中文
    if (board.acts.length) {
      await page.evaluate(() => {
        const b = document.querySelector('.acts button'); b && b.click()
      })
      await sleep(2500)
      const lg = await page.evaluate(() => {
        const el = document.querySelector('[role="log"]')
        return el ? el.innerText.replace(/\s+/g, ' ').slice(0, 200) : ''
      })
      log('⑨ 回合日志中文动作', /我方|攻击|附能量|训练家|进化/.test(lg), { log: lg })
      await shot('zhin_4_turnlog_zh.png')
    }
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 220) })
  }

  const failed = steps.filter((s) => !s.ok).length
  await browser.close()
  const out = { ok: failed === 0, failed, errors: errors.slice(0, 8), steps }
  const file = path.join(OUTDIR, `zhin_${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}.json`)
  try { fs.writeFileSync(file, JSON.stringify(out, null, 1)) } catch (e) { out.writeErr = String(e).slice(0, 80) }
  process.stdout.write(JSON.stringify({ ...out, file }, null, 1) + '\n')
})()
