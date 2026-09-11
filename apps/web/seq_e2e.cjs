// 组合指令（多步批量提交）UI 端到端验证 —— 真实 Chrome
// 覆盖：① 组合输入预览（将执行 N 步）② 逐步执行日志 [1/2][2/2]
//      ③ 首步非法 → 拒绝 + 候选 ④ 回合移交 → 停止并报告剩余步骤
// 用法：node seq_e2e.cjs [base] [outdir]
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
  await page.setViewport({ width: 1500, height: 1000 })
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
    try { await page.screenshot({ path: p }); return p } catch { return null }
  }
  const setCmd = (t) => page.evaluate((v) => {
    const i = document.querySelector('#cmd-in')
    const S = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    S.call(i, v); i.dispatchEvent(new Event('input', { bubbles: true }))
    return i.value
  }, t)
  const preview = () => page.evaluate(() => {
    const p = [...document.querySelectorAll('.cmdbar p')]
      .find((x) => /将按顺序执行/.test(x.textContent))
    return p ? p.textContent.replace(/\s+/g, ' ').trim() : null
  })
  const lastLog = () => page.evaluate(() => {
    const el = document.querySelector('[role="log"]')
    return el ? el.innerText.replace(/\s+/g, ' ').trim() : ''
  })
  const firstChip = (pred) => page.evaluate((src) => {
    const re = new RegExp(src)
    const chips = [...document.querySelectorAll('.cmdbar .chip-btn')]
      .map((b) => b.textContent.trim())
    return chips.find((c) => re.test(c)) || null
  }, pred)

  try {
    await page.goto(BASE + '/app/', { waitUntil: 'domcontentloaded', timeout: 30000 })
    await sleep(2000)
    await clickText('button', '登录 / 注册'); await sleep(500)
    await page.evaluate((u) => {
      const ins = document.querySelectorAll('.user input')
      const S = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      ins[0].focus(); S.call(ins[0], u); ins[0].dispatchEvent(new Event('input', { bubbles: true }))
      ins[1].focus(); S.call(ins[1], 'pw123456'); ins[1].dispatchEvent(new Event('input', { bubbles: true }))
    }, 'seq_' + Date.now().toString(36).slice(-6))
    await clickText('button', '注册'); await sleep(1800)
    log('① 注册/登录', await page.evaluate(() => !!localStorage.getItem('pokelab_token')))

    await clickText('nav.main button', '对战'); await sleep(1200)
    await page.evaluate(() => {
      const sels = document.querySelectorAll('select')
      const S = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set
      const set = (sel, want) => {
        const o = [...sel.options].find((x) => x.value === want) ||
          [...sel.options].find((x) => x.textContent.includes(want))
        if (o) { S.call(sel, o.value); sel.dispatchEvent(new Event('change', { bubbles: true })) }
      }
      set(sels[0], 'A1'); set(sels[1], 'A3')
    })
    await sleep(600)
    await clickText('button', '开始对局'); await sleep(3500)

    const attach = await firstChip('^附能量')
    log('② 有附能量候选', !!attach, { attach })

    // ---- 组合预览 ----
    await setCmd(`${attach} 然后 结束回合`)
    await sleep(400)
    const pv = await preview()
    log('③ 组合预览显示步数', !!pv && /将按顺序执行\s*2\s*步/.test(pv) && pv.includes('结束回合'),
      { preview: pv })
    await shot('seq_1_preview.png')

    // ---- 执行组合 ----
    await clickText('.cmdbar button', '执行'); await sleep(2600)
    let lg = await lastLog()
    log('④ 逐步执行日志 [1/2][2/2]', /\[1\/2\]/.test(lg) && /\[2\/2\]/.test(lg)
      && lg.includes('结束回合'), { log: lg.slice(-260) })
    await shot('seq_2_executed.png')

    // ---- 首步非法 ----
    await setCmd('这不是一个动作 然后 结束回合')
    await sleep(300)
    await clickText('.cmdbar button', '执行'); await sleep(2000)
    lg = await lastLog()
    // 拒绝执行 + 给出可点选的候选（输入被清空，因此不再断言步数预览）
    const cand = await page.evaluate(() => {
      const bar = document.querySelector('.cmdbar')
      return {
        warn: [...(bar ? bar.querySelectorAll('.chip-btn.warn') : [])]
          .map((b) => b.textContent.trim()),
        hasHint: /请指明|候选/.test((bar ? bar.innerText : '')),
      }
    })
    log('⑤ 首步非法 → 拒绝并提示候选',
      /未执行/.test(lg) && (cand.warn.length > 0 || cand.hasHint),
      { log: lg.slice(-120), warnChips: cand.warn.slice(0, 3), hasHint: cand.hasHint })
    await shot('seq_3_invalid_first.png')

    // ---- 回合移交 → 停止并报告剩余 ----
    await setCmd('结束回合 然后 附能量到战斗位')
    await sleep(300)
    await clickText('.cmdbar button', '执行'); await sleep(2600)
    lg = await lastLog()
    log('⑥ 回合移交 → 停止并报告剩余步骤',
      /剩余/.test(lg) && /回合/.test(lg), { log: lg.slice(-220) })
    await shot('seq_4_remaining.png')
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 220) })
  }

  const failed = steps.filter((s) => !s.ok).length
  await browser.close()
  const out = { ok: failed === 0, failed, errors: errors.slice(0, 8), steps }
  const file = path.join(OUTDIR, `seq_${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}.json`)
  try { fs.writeFileSync(file, JSON.stringify(out, null, 1)) } catch (e) { out.writeErr = String(e).slice(0, 80) }
  process.stdout.write(JSON.stringify({ ...out, file }, null, 1) + '\n')
})()
