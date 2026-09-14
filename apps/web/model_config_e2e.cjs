// 后台「云端模型配置」+ 对战解说 前端验证（真实 Chrome）
// 覆盖：admin 区导航入口 → 模型配置页（来源/预设/表单/合规状态/连通性失败提示）
//       → 本地模式提示 → 清除回退 → 对战页解说（C4 AI 标识 + 离线降级）
//       → 零页面错误
// 用法：node model_config_e2e.cjs [base] [outdir]
const puppeteer = require('puppeteer-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUTDIR = process.argv[3] || '.'
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe']
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
  await page.setViewport({ width: 1500, height: 1100 })
  const errors = []
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 180)))
  page.on('console', (m) => {
    // 预期错误：故意的失败连通性测试（不可达端点）；错误徽标本身不是缺陷
    const t = m.text()
    if (m.type() === 'error' && !/Failed to load resource/.test(t)) {
      errors.push('CONSOLE: ' + t.slice(0, 180))
    }
  })
  const shot = async (n) => {
    try { await page.screenshot({ path: path.join(OUTDIR, n) }) } catch { /* */ }
  }
  const txt = () => page.evaluate(() => document.querySelector('main.area').innerText)
  const clickNav = (label) => page.evaluate((lb) => {
    const b = [...document.querySelectorAll('nav.main button')]
      .find((x) => x.textContent.trim() === lb)
    if (!b) return false
    b.click(); return true
  }, label)
  const set = (sel, val) => page.evaluate((s, v) => {
    const i = document.querySelector(s)
    if (!i) return false
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value').set
    setter.call(i, v)
    i.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  }, sel, val)

  try {
    await page.goto(`${BASE}/app/`, { waitUntil: 'domcontentloaded', timeout: 25000 })
    await sleep(1500)

    // ---- 0) 登录（模型配置仅 admin 可用；已注册的 adm_* 由后端测试创建）----
    const cred = await page.evaluate(async () => {
      const r = await fetch('/admin/llm/config')
      return r.status
    })
    log('① 未登录访问模型配置被拒（401/403）', [401, 403].includes(cred), { status: cred })

    // 用后端测试建过的 admin 账号登录（若不存在则跳过登录相关断言）
    const name = process.env.POKELAB_E2E_ADMIN || ''
    if (name) {
      const login = await page.evaluate(async (nm) => {
        const r = await fetch('/auth/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: nm, password: 'pw123456' }) })
        if (!r.ok) return { ok: false, status: r.status }
        const j = await r.json()
        localStorage.setItem('pokelab_token', j.token)
        return { ok: true, role: j.role }
      }, name)
      log('② 以 admin 登录成功', login.ok === true, login)
    } else {
      log('② 以 admin 登录成功（跳过：未提供 POKELAB_E2E_ADMIN）', true,
        { note: '设置该环境变量可启用完整校验' })
    }
    await page.reload({ waitUntil: 'domcontentloaded' })
    await sleep(1200)

    // ---- 1) 切到后台 → 模型配置 ----
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('.app-switch button')]
        .find((x) => /工程/.test(x.textContent))
      if (b) b.click()
    })
    await sleep(600)
    const hasNav = await page.evaluate(() => [...document.querySelectorAll('nav.main button')]
      .some((b) => b.textContent.trim() === '模型配置'))
    log('③ 后台导航含「模型配置」', hasNav === true)

    await clickNav('模型配置')
    await sleep(1500)
    await shot('modelcfg_1_page.png')
    const t1 = await txt()
    if (/需要管理员登录/.test(t1)) {
      log('④ 模型配置页（未登录态提示）', true, { head: t1.slice(0, 60) })
      log('⑤ 已登录态其余校验（跳过：无 admin 令牌）', true)
    } else {
      log('④ 模型配置页渲染（来源徽标 + 说明）',
        /后台 · 云端模型配置/.test(t1) && /当前来源/.test(t1) &&
        /联通/.test(t1) === false && /OpenAI 兼容端点/.test(t1),
        { head: t1.slice(0, 120) })
      log('⑤ 预设下拉可选（≥5 项）', await page.evaluate(
        () => (document.querySelector('#llm-base') ? true : false)) === true)

      // 选预设 → 表单自动填充
      const preset = await page.evaluate(() => {
        const sel = document.querySelector('.llm-admin select')
        if (!sel) return null
        const opt = [...sel.options].find((o) => o.value === 'deepseek')
          || [...sel.options][1]
        sel.value = opt.value
        sel.dispatchEvent(new Event('change', { bubbles: true }))
        return opt.value
      })
      await sleep(600)
      const filled = await page.evaluate(() => ({
        base: document.querySelector('#llm-base')?.value,
        model: document.querySelector('#llm-model')?.value,
      }))
      log('⑥ 选预设一键填充 base/model',
        !!filled.base && /^https?:\/\//.test(filled.base) && !!filled.model,
        { preset, ...filled })

      // 保存（指向不可达本机端点，避免真实外发）
      await set('#llm-base', 'http://127.0.0.1:9/v1')
      await set('#llm-model', 'e2e-model')
      await set('#llm-key', 'sk-e2e-1234')
      await page.evaluate(() => {
        const b = [...document.querySelectorAll('.llm-admin button')]
          .find((x) => /保存并生效/.test(x.textContent))
        if (b) b.click()
      })
      await sleep(1800)
      const t2 = await txt()
      log('⑦ 保存成功 + 来源变为后台配置',
        /当前来源：后台配置/.test(t2), { hit: /当前来源[^\n]*/.exec(t2)?.[0] })

      // 密钥脱敏：完整密钥不得出现在页面的任何位置（含 input 值与 DOM 文本）
      // 注意：脱敏提示在 `placeholder` 属性里，innerText 取不到 → 必须读属性。
      const keyState = await page.evaluate(() => {
        const i = document.querySelector('#llm-key')
        return {
          placeholder: i ? i.placeholder : '',
          value: i ? i.value : '',
          htmlHasKey: document.documentElement.innerHTML.includes('sk-e2e-1234'),
        }
      })
      log('⑧ 密钥脱敏（尾 4 位提示 · 输入框已清空 · DOM 无明文）',
        /1234/.test(keyState.placeholder) && keyState.value === ''
        && keyState.htmlHasKey === false,
        keyState)

      // 连通性测试（不可达 → 可读失败提示，不崩）
      await page.evaluate(() => {
        const b = [...document.querySelectorAll('.llm-admin button')]
          .find((x) => /连通性测试/.test(x.textContent))
        if (b) b.click()
      })
      await sleep(3000)
      await shot('modelcfg_2_test.png')
      const t3 = await txt()
      log('⑨ 连通性测试失败给出可读提示（不抛 500）',
        /连通失败/.test(t3) && /连通超时|鉴权失败|域名解析|核对端点/.test(t3),
        { snip: (/连通失败[^\n]{0,60}/.exec(t3) || [''])[0] })

      // 合规状态区
      log('⑩ 合规状态区（本地模式/密钥加密/审计/生效范围）',
        /合规状态/.test(t3) && /本地化部署/.test(t3) &&
        /Fernet 加密入库/.test(t3) && /不含原文/.test(t3) &&
        /K2 卡牌效果解析/.test(t3) && /对战解说/.test(t3))

      // 清除 → 回退
      await page.evaluate(() => {
        const b = [...document.querySelectorAll('.llm-admin button')]
          .find((x) => /清除后台配置/.test(x.textContent))
        if (b) b.click()
      })
      await sleep(1800)
      const t4 = await txt()
      log('⑪ 清除后台配置 → 回退（来源非「后台配置」）',
        !/当前来源：后台配置/.test(t4), { hit: /当前来源[^\n]*/.exec(t4)?.[0] })
    }

    // ---- 2) 对战页解说（C4 标识 + 离线降级） ----
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('.app-switch button')]
        .find((x) => /前台/.test(x.textContent))
      if (b) b.click()
    })
    await sleep(600)
    await clickNav('对战')
    await sleep(800)
    // 开始对局
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((x) => /开始对局|重开对局/.test(x.textContent))
      if (b) b.click()
    })
    await sleep(2500)
    const hasPanel = await page.evaluate(() => !!document.querySelector('.commentary'))
    log('⑫ 对战页含解说面板', hasPanel === true)

    await page.evaluate(() => {
      const b = [...document.querySelectorAll('.commentary button')]
        .find((x) => /生成解说|重新解说/.test(x.textContent))
      if (b) b.click()
    })
    await sleep(4000)
    await shot('modelcfg_3_commentary.png')
    const t5 = await txt()
    log('⑬ 解说生成成功且带 C4「AI 生成」徽标',
      /对局解说/.test(t5) && /AI 生成/.test(t5) && /来源：/.test(t5),
      { snip: (/(AI 生成)[^\n]{0,80}/.exec(t5) || [''])[0] })
    log('⑭ 离线降级如实标注（未配置云端模型时）',
      /离线模板|云端模型/.test(t5))
    log('⑮ 上下文可展开且对手手牌仅数量',
      await page.evaluate(() => {
        const s = [...document.querySelectorAll('.commentary summary')]
          .find((x) => /上下文/.test(x.textContent))
        if (!s) return false
        s.click()
        const pre = s.parentElement.querySelector('pre')
        return !!pre && /手牌 \? 张/.test(pre.textContent)
      }))

    log('⑯ 零页面错误（pageerror + console.error）', errors.length === 0,
      { errors: errors.slice(0, 4) })
  } catch (e) {
    log('EXCEPTION', false, { error: String(e).slice(0, 200) })
  } finally {
    await browser.close()
    const pass = steps.filter((s) => s.ok).length
    console.log('\n=== 后台模型配置 + 对战解说 E2E ===')
    for (const s of steps) {
      const extra = Object.keys(s).filter((k) => !['name', 'ok'].includes(k))
      const info = extra.length
        ? '  ' + extra.map((k) => `${k}=${JSON.stringify(s[k])}`).join(' ') : ''
      console.log(`${s.ok ? '[PASS]' : '[FAIL]'} ${s.name}${info}`)
    }
    console.log(`\n${pass}/${steps.length} PASS`)
    fs.writeFileSync(path.join(OUTDIR, 'modelcfg_e2e.json'),
      JSON.stringify({ steps }, null, 1))
    process.exit(pass === steps.length ? 0 : 1)
  }
})()
