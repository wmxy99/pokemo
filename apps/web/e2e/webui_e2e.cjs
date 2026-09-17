// F3 → F5 UI 端到端验证（真实 Chrome，无落盘依赖）
// 流程：注册登录 → 卡组管理粘贴卡表并校验 → 对战页选「我的卡组」→ 建局 → 出现合法动作按钮
// 用法：node webui_e2e.cjs [base] [deckTextPath]
const puppeteer = require('puppeteer-core')
const fs = require('fs')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const TEXT_PATH = process.argv[3] || 'D:/pokemo/deck_text_e2e.txt'
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find((p) => fs.existsSync(p))

const steps = []
const log = (name, ok, extra = {}) => { steps.push({ name, ok, ...extra }) }

;(async () => {
  const deckText = fs.readFileSync(TEXT_PATH, 'utf-8')
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] })
  const page = await browser.newPage()
  await page.setViewport({ width: 1440, height: 940 })
  const errors = []
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 160)))
  page.on('response', (r) => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`) })

  const click = (sel, text) => page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((b) => b.textContent.includes(t))
    if (!el) return false
    el.click(); return true
  }, sel, text)
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

  await page.goto(BASE + '/app/', { waitUntil: 'networkidle2', timeout: 30000 })

  // 1) 注册登录（注意：必须精确匹配按钮文本，"登录 / 注册" 会误命中"注册"子串）
  const uname = 'ui_' + Date.now().toString(36).slice(-6)
  await click('button', '登录 / 注册')
  await sleep(500)
  await page.evaluate((u) => {
    const set = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype, 'value').set
    const ins = document.querySelectorAll('.user input')
    set.call(ins[0], u); ins[0].dispatchEvent(new Event('input', { bubbles: true }))
    set.call(ins[1], 'pw123456'); ins[1].dispatchEvent(new Event('input', { bubbles: true }))
  }, uname)
  const registered = await page.evaluate(() => {
    const b = [...document.querySelectorAll('.user button')]
      .find((x) => x.textContent.trim() === '注册')
    if (!b) return false
    b.click(); return true
  })
  await sleep(1800)
  const logged = await page.evaluate(() => ({
    pill: (document.querySelector('.user .pill') || {}).textContent || '',
    hasLogout: [...document.querySelectorAll('button')]
      .some((b) => b.textContent.includes('退出')),
    token: !!localStorage.getItem('pokelab_token'),
  }))
  log('注册/登录', registered && logged.token && logged.hasLogout, logged)

  // 2) 卡组管理：粘贴 + 校验
  await click('nav.main button', '卡组管理')
  await sleep(800)
  await page.evaluate((txt) => {
    const ta = document.querySelector('textarea#dt')
    ta.value = txt; ta.dispatchEvent(new Event('input', { bubbles: true }))
  }, deckText)
  await click('button', '解析并校验')
  await sleep(1600)
  const importRes = await page.evaluate(() => {
    const el = document.querySelector('.import-result')
    return el ? el.innerText.replace(/\s+/g, ' ').slice(0, 160) : ''
  })
  log('卡表解析校验', /通过|可对战/.test(importRes), { importRes })

  // 3) 对战页：选「我的」卡组 → 建局
  await click('nav.main button', '对战')
  await sleep(1200)
  const picked = await page.evaluate(() => {
    const sel = document.querySelector('select#dx')
    const opt = [...sel.options].find((o) => o.value.startsWith('deck_'))
    if (!opt) return null
    sel.value = opt.value; sel.dispatchEvent(new Event('change', { bubbles: true }))
    return opt.textContent
  })
  await sleep(300)
  await click('button.primary', '开始对局')
  await sleep(2500)
  const board = await page.evaluate(() => ({
    acts: document.querySelectorAll('.act').length,
    chips: document.querySelectorAll('.chip').length,
    seed: (document.querySelector('.pill.mono') || {}).textContent || '',
    zones: document.querySelectorAll('.zone').length,
    err: (document.querySelector('.alert.err') || {}).textContent || '',
  }))
  log('选中我的卡组', !!picked, { picked })
  log('建局并出现合法动作', board.acts > 0 && board.zones >= 2, board)

  // 4) 点第一个动作（走一步）
  if (board.acts > 0) {
    const clicked = await page.evaluate(() => {
      const b = document.querySelector('.act')
      if (!b) return null
      const label = b.textContent.trim(); b.click(); return label
    })
    await sleep(2200)
    const after = await page.evaluate(() => ({
      acts: document.querySelectorAll('.act').length,
      logLines: (document.querySelector('pre.log') || {}).textContent?.split('\n').length || 0,
      turn: (document.querySelector('.card .pill') || {}).textContent || '',
    }))
    log('提交动作', after.logLines > 0, { clicked, ...after })
  }

  console.log(JSON.stringify({ base: BASE, deck: TEXT_PATH, user: uname,
    steps, errors }, null, 1))
  await browser.close()
})().catch((e) => { console.error('FATAL', e.message); process.exit(1) })
