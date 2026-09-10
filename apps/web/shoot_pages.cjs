// 逐页截图 + 错误采集（前台 4 页 / 后台 3 页）
// 用法：node apps/web/shoot_pages.cjs [baseUrl] [outDir]
const puppeteer = require('puppeteer-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUT = process.argv[3] || 'D:/pokemo/pokelab-ai/apps/web/dist/shots'
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe']
  .find((p) => fs.existsSync(p))
fs.mkdirSync(OUT, { recursive: true })

const clickByText = async (page, sel, text) => page.evaluate((s, t) => {
  const el = [...document.querySelectorAll(s)].find((b) => b.textContent.includes(t))
  if (el) { el.click(); return true }
  return false
}, sel, text)

;(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'] })
  const page = await browser.newPage()
  await page.setViewport({ width: 1440, height: 940 })
  const problems = []
  page.on('pageerror', (e) => problems.push('PAGEERROR ' + e.message.slice(0, 200)))
  page.on('response', (r) => { if (r.status() >= 400) problems.push(`HTTP ${r.status()} ${r.url()}`) })

  const shots = []
  const take = async (name) => {
    await new Promise((r) => setTimeout(r, 900))
    const f = path.join(OUT, `${name}.jpg`)
    let fileErr = null
    try {
      await page.screenshot({ path: f, type: 'jpeg', quality: 62 })
    } catch (e) { fileErr = String(e.message).slice(0, 120) }
    const summary = await page.evaluate(() => ({
      cards: document.querySelectorAll('.card').length,
      chips: document.querySelectorAll('.chip').length,
      tables: document.querySelectorAll('table.tbl').length,
      text: (document.querySelector('main.area') || document.body).innerText.replace(/\s+/g, ' ').slice(0, 120),
    }))
    shots.push({ name, file: f, fileErr, ...summary })
  }

  await page.goto(BASE + '/app/', { waitUntil: 'networkidle2', timeout: 30000 })
  for (const [key, label, file] of [
    ['battle', '对战', '01_front_battle'],
    ['batch', '批量对战', '02_front_batch'],
    ['stats', '统计报告', '03_front_stats'],
    ['decks', '卡组管理', '04_front_decks'],
  ]) {
    await clickByText(page, 'nav.main button', label)
    await take(file)
  }
  await clickByText(page, '.app-switch button', '后台')
  for (const [label, file] of [['仪表盘', '05_admin_dashboard'], ['RL 训练', '06_admin_training'], ['用户管理', '07_admin_users']]) {
    await clickByText(page, 'nav.main button', label)
    await take(file)
  }
  console.log(JSON.stringify({ base: BASE, chrome: CHROME, shots, problems }, null, 1))
  await browser.close()
})().catch((e) => { console.error('FATAL', e); process.exit(1) })
