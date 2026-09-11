// 中文指令输入 UI 端到端验证 —— 真实 Chrome
// 覆盖：① 指令栏渲染 + 候选 chips ② 输入中文卡名/位置执行
//      ③ 点选 chip 执行 ④ 未知指令 → 候选提示 ⑤ 中文招式名出招（多回合后）
// 用法：node cmd_input_e2e.cjs [base] [outdir]
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
    try { await page.screenshot({ path: p }); return p } catch (e) { return 'ERR:' + String(e).slice(0, 60) }
  }

  const cmdState = () => page.evaluate(() => {
    const bar = document.querySelector('.cmdbar')
    if (!bar) return { found: false }
    const chips = [...bar.querySelectorAll('.cmd-chips:not([aria-label="歧义候选"]) .chip-btn')]
      .map((b) => b.textContent.trim())
    const warn = [...bar.querySelectorAll('.chip-btn.warn')].map((b) => b.textContent.trim())
    const hint = (bar.querySelector('#cmd-hint') || {}).textContent || ''
    return { found: true, chips, warn, hint: hint.replace(/\s+/g, ' ').trim(),
      options: [...bar.querySelectorAll('datalist option')].map((o) => o.value) }
  })

  const setCmd = (text) => page.evaluate((v) => {
    const i = document.querySelector('#cmd-in')
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    setter.call(i, v); i.dispatchEvent(new Event('input', { bubbles: true }))
    return i.value
  }, text)

  const lastLog = () => page.evaluate(() => {
    const el = document.querySelector('[role="log"]')
    return el ? el.innerText.replace(/\s+/g, ' ').trim() : ''
  })

  try {
    await page.goto(BASE + '/app/', { waitUntil: 'domcontentloaded', timeout: 30000 })
    await sleep(2000)

    // 注册登录
    const uname = 'cmdui_' + Date.now().toString(36).slice(-6)
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
    log('① 注册/登录', await page.evaluate(() => !!localStorage.getItem('pokelab_token')))

    // 建局（A1 vs A3）
    await clickText('nav.main button', '对战')
    await sleep(1200)
    await page.evaluate(() => {
      const sels = document.querySelectorAll('select')
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set
      const set = (sel, want) => {
        const o = [...sel.options].find((x) => x.value === want) ||
          [...sel.options].find((x) => x.textContent.includes(want))
        if (o) { setter.call(sel, o.value); sel.dispatchEvent(new Event('change', { bubbles: true })) }
      }
      set(sels[0], 'A1'); set(sels[1], 'A3')
    })
    await sleep(600)
    await clickText('button', '开始对局')
    await sleep(3500)

    // 指令栏渲染
    let st = await cmdState()
    log('② 指令栏渲染 + 候选 chips', st.found && st.chips.length > 0,
      { chips: (st.chips || []).slice(0, 6), options: (st.options || []).slice(0, 4) })
    await shot('cmd_1_bar.png')

    // 输入中文指令：附能量到战斗位
    st = await cmdState()
    const attach = (st.chips || []).find((c) => c.includes('附能量'))
    if (attach) {
      await setCmd('附能量到战斗位')
      await sleep(200)
      await clickText('.cmdbar button', '执行')
      await sleep(2500)
      const lg = await lastLog()
      // 守卫：日志中的卡名必须已中文化（不得回退成英文规范名）
      const englishNames = (lg.match(/Basic [A-Z][a-z]+ Energy|\b[A-Z][a-z]+ Ball\b|Professor's Research/g) || [])
      log('③ 输入中文位置指令执行', /指令「附能量到战斗位」/.test(lg) && /附能量/.test(lg)
        && englishNames.length === 0,
        { matched: (await cmdState()).hint, log: lg.slice(-120), englishNames })
    } else {
      log('③ 输入中文位置指令执行', false, { reason: '无附能量候选' })
    }
    await shot('cmd_2_executed.png')

    // 点选 chip 执行
    st = await cmdState()
    const chip = (st.chips || [])[0]
    if (chip) {
      await page.evaluate(() => {
        const b = document.querySelector('.cmd-chips:not([aria-label="歧义候选"]) .chip-btn')
        b && b.click()
      })
      await sleep(2500)
      const lg = await lastLog()
      log('④ 点选候选 chip 执行', lg.includes(chip), { chip, log: lg.slice(-110) })
    }

    // 未知指令 → 候选提示
    await setCmd('这是一句没有意义的话')
    await sleep(200)
    await clickText('.cmdbar button', '执行')
    await sleep(2000)
    st = await cmdState()
    const lg = await lastLog()
    log('⑤ 未知指令 → 拒绝并给出候选', /未执行/.test(lg) && (st.chips || []).length > 0,
      { log: lg.slice(-110), chips: (st.chips || []).length })
    await shot('cmd_3_unknown.png')

    // 多回合推进，寻找「按中文招式名出招」
    let attackDone = null
    const trace = []
    for (let i = 0; i < 60 && !attackDone; i += 1) {
      const s2 = await page.evaluate(() => {
        const bar = document.querySelector('.cmdbar')
        const chips = bar ? [...bar.querySelectorAll(
          '.cmd-chips:not([aria-label="歧义候选"]) .chip-btn')].map((b) => b.textContent.trim()) : []
        const body = document.body.innerText
        const info = [...document.querySelectorAll('.kv tr, table tr')]
          .map((r) => r.innerText.replace(/\s+/g, ' ')).find((x) => /回合/.test(x)) || ''
        return {
          attack: chips.find((c) => c.startsWith('攻击')) || null,
          chips: chips.slice(0, 8),
          finished: /对局已结束|终局/.test(body) || chips.length === 0,
          info,
          disabled: bar ? !![...bar.querySelectorAll('button')].every((b) => b.disabled) : true,
        }
      })
      if (i % 10 === 0) trace.push({ i, ...s2 })
      if (s2.finished && !s2.attack) break
      if (s2.attack) {
        const move = s2.attack.replace(/^攻击[：:]\s*/, '').trim()
        await setCmd(move)
        await sleep(200)
        await clickText('.cmdbar button', '执行')
        await sleep(2500)
        const l2 = await lastLog()
        attackDone = { move, ok: l2.includes(move) && /指令/.test(l2), log: l2.slice(-140) }
        break
      }
      // 否则继续推进（优先附能量/进化，其次结束回合）
      const clicked = await page.evaluate(() => {
        const bar = document.querySelector('.cmdbar')
        const chips = [...bar.querySelectorAll('.cmd-chips:not([aria-label="歧义候选"]) .chip-btn')]
        const pick = chips.find((b) => /附能量|进化/.test(b.textContent)) ||
          chips.find((b) => b.textContent.includes('结束回合')) || chips[0]
        if (!pick || pick.disabled) return false
        pick.click(); return true
      })
      if (!clicked) break
      await sleep(2200)
    }
    log('⑥ 按中文招式名出招', !!(attackDone && attackDone.ok),
      attackDone || { note: '未在轮次内出现可用招式', trace })
    await shot('cmd_4_attack.png')
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 220) })
  }

  const failed = steps.filter((s) => !s.ok).length
  await browser.close()
  const out = { ok: failed === 0, failed, errors: errors.slice(0, 8), steps }
  const file = path.join(OUTDIR, `cmd_${new Date().toTimeString().slice(0, 8).replace(/:/g, '')}.json`)
  try { fs.writeFileSync(file, JSON.stringify(out, null, 1)) } catch (e) { out.writeErr = String(e).slice(0, 80) }
  process.stdout.write(JSON.stringify({ ...out, file }, null, 1) + '\n')
})()
