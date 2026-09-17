// 渲染自检：真实 Chrome 打开 /app/，收集 console/网络/运行时错误并截图
// 用法：node apps/web/check_render.cjs [url] [outPng]
const puppeteer = require('puppeteer-core')

const URL = process.argv[2] || 'http://127.0.0.1:8000/app/'
const OUT = process.argv[3] || 'D:/pokemo/pokelab-ai/apps/web/dist/preview.png'
const CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
]

;(async () => {
  const fs = require('fs')
  const exe = CANDIDATES.find((p) => fs.existsSync(p))
  const browser = await puppeteer.launch({
    executablePath: exe,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1440, height: 900 })
  const errors = []
  const failed = []
  const logs = []
  page.on('pageerror', (e) => errors.push(String(e.message).slice(0, 300)))
  page.on('console', (m) => {
    if (['error', 'warning'].includes(m.type())) logs.push(`[${m.type()}] ${m.text().slice(0, 200)}`)
  })
  page.on('requestfailed', (r) => failed.push(`REQFAIL ${r.url()} :: ${r.failure() && r.failure().errorText}`))
  page.on('response', (r) => { if (r.status() >= 400) failed.push(`HTTP ${r.status()} ${r.url()}`) })

  try {
    await page.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 })
  } catch (e) {
    errors.push('GOTO: ' + e.message)
  }
  await new Promise((r) => setTimeout(r, 1800))

  const info = await page.evaluate(() => {
    const root = document.getElementById('root')
    return {
      title: document.title,
      rootChildren: root ? root.children.length : -1,
      rootText: root ? root.innerText.replace(/\s+/g, ' ').slice(0, 260) : '',
      nav: [...document.querySelectorAll('nav.main button')].map((b) => b.textContent),
      zoneSwitch: [...document.querySelectorAll('.app-switch button')].map((b) => b.textContent),
      hasFooter: !!document.querySelector('footer.site'),
      cards: document.querySelectorAll('.card').length,
      chips: document.querySelectorAll('.chip').length,
      presetButtons: document.querySelectorAll('.preset').length,
      bodyBg: getComputedStyle(document.body).backgroundColor,
      height: document.body.scrollHeight,
    }
  })
  try { await page.screenshot({ path: OUT, fullPage: false }) } catch (e) { info.screenshotError = e.message }
  console.log(JSON.stringify({ url: URL, chrome: exe, info, errors, failed, logs }, null, 1))
  await browser.close()
})().catch((e) => { console.error('FATAL', e); process.exit(1) })
