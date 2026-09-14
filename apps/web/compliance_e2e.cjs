// 合规 6.1 / 6.2 前端验证（真实 Chrome）
// 覆盖：页脚声明（6.1/6.2 原文要点 + 权利方 + 合规链接）→ 卡库合规提示
//       → 卡详情「AI 生成」标识 + 权利方标注 + 解析结果 → 零页面错误
// 用法：node compliance_e2e.cjs [base] [outdir]
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
  const setInput = (sel, val) => page.evaluate((s, v) => {
    const i = document.querySelector(s)
    if (!i) return false
    const S = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    S.call(i, v); i.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  }, sel, val)
  const clickNav = (label) => page.evaluate((lb) => {
    const b = [...document.querySelectorAll('nav.main button')]
      .find((x) => x.textContent.trim() === lb)
    if (!b) return false
    b.click(); return true
  }, label)

  try {
    // ---- 1) 页脚合规声明 ----
    await page.goto(`${BASE}/app/`, { waitUntil: 'domcontentloaded', timeout: 25000 })
    await sleep(1500)
    const foot = await page.evaluate(() => {
      const f = document.querySelector('footer.site')
      return { text: f ? f.innerText : '', href: f ? !!f.querySelector('a[href="/compliance"]') : false }
    })
    const hasHeading = /6\.1 内容合规/.test(foot.text) && /6\.2 数据隐私/.test(foot.text)
    const hasContent = /教学研究与算法演示/.test(foot.text) && /责任由使用者承担/.test(foot.text)
    const hasPrivacy = /HTTPS/.test(foot.text) && /脱敏/.test(foot.text) && /第三方/.test(foot.text)
    const hasAi = /AI 生成/.test(foot.text)
    const hasRights = /Nintendo/.test(foot.text) && /The Pokémon Company/.test(foot.text)
    log('① 页脚 6.1 声明（研究用途/责任/权利方/AI 标识）',
      hasHeading && hasContent && hasAi && hasRights, { hasHeading, hasContent, hasAi, hasRights })
    log('② 页脚 6.2 声明（HTTPS/脱敏/第三方）+ 合规链接',
      hasPrivacy && foot.href, { hasPrivacy, link: foot.href })
    await shot('comp_1_footer.png')

    // ---- 2) /compliance 机器可读声明可访问 ----
    const api = await page.evaluate(async () => {
      const r = await fetch('/compliance')
      const j = await r.json()
      return { status: r.status, has61: !!j.content_compliance, has62: !!j.data_privacy,
        redLines: (j.red_lines || []).map((x) => x.id), doc: j.doc }
    })
    log('③ /compliance 返回 6.1/6.2 与红线（C1–C5）',
      api.status === 200 && api.has61 && api.has62 && api.redLines.length >= 5,
      { status: api.status, redLines: api.redLines, doc: api.doc })

    // ---- 3) 卡库页合规提示 ----
    await clickNav('卡库')
    await sleep(2200)
    const libNote = await page.evaluate(() => {
      const t = document.body.innerText
      return {
        attribution: /Nintendo/.test(t) && /The Pokémon Company/.test(t),
        aiHint: /AI 解析/.test(t) || /AI 生成/.test(t),
        nonOfficial: /非官方/.test(t),
      }
    })
    log('④ 卡库页合规提示（权利方 + AI 标识说明 + 非官方）',
      libNote.attribution && libNote.aiHint && libNote.nonOfficial, libNote)
    await shot('comp_2_library.png')

    // ---- 4) 卡详情：AI 生成标识 + 权利方标注 + 解析结果 ----
    await setInput('#lib-q', 'dragapult')
    await sleep(1600)
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('table.tbl tbody tr button')]
        .find((x) => x.textContent.includes('详情'))
      if (b) b.click()
    })
    await sleep(1400)
    const det = await page.evaluate(() => {
      const t = document.body.innerText
      const pre = document.querySelector('.spec-pre')
      return {
        aiBadge: /AI 生成/.test(t),
        backend: /后端 (rule|llm)/.test(t),
        notice: /仅供研究参考/.test(t),
        attribution: /Nintendo/.test(t) && /The Pokémon Company/.test(t),
        specs: pre ? pre.innerText.slice(0, 160) : null,
        engineWarn: /引擎未建模动作/.test(t),
      }
    })
    log('⑤ 卡详情「AI 生成」标识（含后端与免责）',
      det.aiBadge && det.backend && det.notice, det)
    log('⑥ 卡详情权利方标注 + 解析 spec 展示',
      det.attribution && !!det.specs, { attribution: det.attribution, specs: det.specs })
    await shot('comp_3_detail.png')

    // ---- 5) 零运行时错误 ----
    const realErrors = errors.filter((e) => !/favicon/.test(e))
    log('⑦ 零页面错误', realErrors.length === 0, { errors: realErrors.slice(0, 5) })
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 220) })
  }

  const failed = steps.filter((s) => !s.ok).length
  const out = { ok: failed === 0, failed, errors: errors.slice(0, 8), url: `${BASE}/app/`, steps }
  try {
    fs.writeFileSync(path.join(OUTDIR, `comp_${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}.json`),
      JSON.stringify(out, null, 1))
  } catch { /* 忽略 */ }
  await browser.close()
  process.stdout.write(JSON.stringify(out, null, 1) + '\n')
})()
