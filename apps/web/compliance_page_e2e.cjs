// F9 合规详情页前端验证（真实 Chrome）
// 覆盖：导航入口 → 6.1 内容合规（权利方/使用政策/口径澄清/标注模板/AI 标识契约）
//       → 6.2 数据隐私（HTTPS/口令哈希/字段加密/本地模式/脱敏/审计/不二次使用）
//       → 红线 C1–C5（含 C2 v1.1 修订标注）→ 审计区权限边界 → 页脚互链 → 零页面错误
// 用法：node compliance_page_e2e.cjs [base] [outdir]
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
  // 已知且**预期**的错误：审计区故意探测非管理员边界 → /compliance/audit 返回 403，
  // 浏览器会把该失败请求记进 console（文本里不含 URL，需从 location 取）。
  // 断言「零页面错误」时必须排除这一条，否则会把预期行为误报为缺陷。
  const isExpected = (m) => {
    const url = (m.location && m.location().url) || ''
    return /\/compliance\/audit/.test(url) || /403/.test(m.text())
  }
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 180)))
  page.on('console', (m) => {
    if (m.type() === 'error' && !isExpected(m)) {
      errors.push('CONSOLE: ' + m.text().slice(0, 180))
    }
  })
  const shot = async (name) => {
    try { await page.screenshot({ path: path.join(OUTDIR, name), fullPage: false }) }
    catch { /* 忽略 */ }
  }
  const clickNav = (label) => page.evaluate((lb) => {
    const b = [...document.querySelectorAll('nav.main button')]
      .find((x) => x.textContent.trim() === lb)
    if (!b) return false
    b.click(); return true
  }, label)
  const txt = () => page.evaluate(() => document.querySelector('main.area').innerText)

  try {
    await page.goto(`${BASE}/app/`, { waitUntil: 'domcontentloaded', timeout: 25000 })
    await sleep(1500)

    // ---- 1) 导航入口 ----
    const hasNav = await page.evaluate(() => [...document.querySelectorAll('nav.main button')]
      .some((b) => b.textContent.trim() === '合规声明'))
    log('① 主导航含「合规声明」入口', hasNav === true)

    const clicked = await clickNav('合规声明')
    await sleep(1800)
    await shot('compliance_1_page.png')
    const t1 = await txt()
    log('② 进入合规页（标题 + 版本戳）',
      clicked && /合规声明与自查/.test(t1) && /版本\s*1\.1/.test(t1),
      { head: t1.slice(0, 120) })

    // ---- 2) 6.1 内容合规 ----
    log('③ 6.1 声明原文（研究用途 / 责任 / 权利方）',
      /6\.1\s*·\s*内容合规/.test(t1) &&
      /教学研究与算法演示/.test(t1) &&
      /责任由使用者承担/.test(t1) &&
      /Nintendo/.test(t1) && /The Pokémon Company/.test(t1))
    const policy = await page.evaluate(() => {
      const tb = document.querySelector('#c-61 table.tbl')
      const rows = [...(tb ? tb.querySelectorAll('tbody tr') : [])]
      return rows.map((r) => r.innerText.replace(/\s+/g, ' ').trim())
    })
    const policyOk = ['卡牌名称与规则文本', '卡牌图像', '卡表文本（卡组清单）',
      '官方美术素材打包'].every((k) => policy.some((r) => r.includes(k)))
    log('④ 使用政策四行（可用/须标注 + 不打包）',
      policyOk &&
      policy.filter((r) => /可用（须标注权利方）/.test(r)).length >= 3 &&
      policy.some((r) => /不打包 \/ 不传播/.test(r)),
      { rows: policy.length, first: policy[0] || '' })
    log('④b 口径澄清（6.1 原文 vs C2 v1.1 关系）',
      /不打包、不传播与本研究无关的官方美术素材/.test(t1) &&
      /按红线 C2\(v1\.1\) 解释为/.test(t1))
    log('⑤ 标注模板 + 无关联/未获背书',
      /与权利方无关联/.test(t1) && /未获背书/.test(t1))
    log('⑥ AI 标识契约（C4）：三字段 + 适用位置 + 前端要求',
      /ai_generated/.test(t1) && /generated_by/.test(t1) && /ai_notice/.test(t1) &&
      /卡牌效果解析结果/.test(t1) && /徽标/.test(t1) && /已落地/.test(t1))

    // ---- 3) 6.2 数据隐私 ----
    log('⑦ 6.2 声明原文',
      /6\.2\s*·\s*数据隐私保护/.test(t1) && /HTTPS加密传输/.test(t1) &&
      /脱敏/.test(t1) && /向第三方提供/.test(t1))
    log('⑧ 6.2 落地状态（传输/口令/字段加密/本地模式）',
      /支持 HTTPS/.test(t1) && /pbkdf2_sha256/.test(t1) &&
      /Fernet/.test(t1) && /POKELAB_LOCAL_ONLY=1/.test(t1) &&
      /支持完全离线/.test(t1))
    log('⑨ 6.2 大模型调用（脱敏规则 + 审计不含原文）',
      /外发前脱敏/.test(t1) && /脱敏规则\s*\d+\s*条/.test(t1) &&
      /不含原文/.test(t1))
    log('⑩ 不二次使用声明与执行手段',
      /不用于其它用途|不用于服务以外用途|不向第三方提供/.test(t1) &&
      /本地模式可完全离线/.test(t1) && /外发前脱敏/.test(t1))

    // ---- 4) 红线 C1–C5 ----
    const red = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('#c-red tbody tr')]
      return rows.map((r) => r.innerText.replace(/\s+/g, ' ').trim())
    })
    const ids = red.map((r) => (r.match(/^(C\d)/) || [])[1]).filter(Boolean)
    log('⑪ 红线表 C1–C5 完整',
      ids.join(',') === 'C1,C2,C3,C4,C5', { ids })
    log('⑫ C2 标出 v1.1 修订来源',
      red.some((r) => r.startsWith('C2') && /v1\.0/.test(r)),
      { c2: (red.find((r) => r.startsWith('C2')) || '').slice(0, 160) })
    log('⑬ C4 AI 产出标识在列',
      red.some((r) => r.startsWith('C4') && /AI/.test(r)))

    // ---- 5) 审计区（游客 → 403 权限边界） ----
    const auditExists = await page.evaluate(() => !!document.querySelector('#c-audit'))
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('#c-audit button')][0]
      if (b) b.click()
    })
    await sleep(1200)
    await shot('compliance_2_audit.png')
    const t2 = await txt()
    const auditBoundary = /403|非管理员/.test(t2) || /共\s*\d+\s*条/.test(t2)
    log('⑭ 审计区存在且权限边界可见（403 或结果）',
      auditExists && auditBoundary,
      { snippet: (t2.match(/(403|非管理员)[^\n]{0,40}/) || [''])[0] })

    // ---- 6) 页脚互链（站内按钮 + 原始 JSON） ----
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await sleep(400)
    const foot = await page.evaluate(() => {
      const f = document.querySelector('footer.site')
      return {
        text: f ? f.innerText : '',
        json: f ? !!f.querySelector('a[href="/compliance"]') : false,
        btn: f ? [...f.querySelectorAll('button')]
          .some((b) => /站内查看完整合规声明/.test(b.textContent)) : false,
      }
    })
    log('⑮ 页脚互链（站内入口 + 原始 JSON 链接）',
      foot.btn && foot.json, { btn: foot.btn, json: foot.json })

    // 点页脚按钮应回到合规页
    await page.evaluate(() => {
      const b = [...document.querySelectorAll('footer.site button')]
        .find((x) => /站内查看完整合规声明/.test(x.textContent))
      if (b) b.click()
    })
    await sleep(1200)
    const t3 = await txt()
    log('⑯ 页脚按钮可跳回合规页',
      /合规声明与自查/.test(t3) && /6\.1\s*·\s*内容合规/.test(t3))

    // ---- 7) 零页面错误 ----
    log('⑰ 零页面错误（pageerror + console.error）', errors.length === 0,
      { errors: errors.slice(0, 4) })
    await shot('compliance_3_final.png')
  } catch (e) {
    log('EXCEPTION', false, { error: String(e).slice(0, 200) })
  } finally {
    await browser.close()
    const pass = steps.filter((s) => s.ok).length
    console.log('\n=== 合规详情页 E2E ===')
    for (const s of steps) {
      const extra = Object.keys(s).filter((k) => !['name', 'ok'].includes(k))
      const info = extra.length
        ? '  ' + extra.map((k) => `${k}=${JSON.stringify(s[k])}`).join(' ')
        : ''
      console.log(`${s.ok ? '[PASS]' : '[FAIL]'} ${s.name}${info}`)
    }
    console.log(`\n${pass}/${steps.length} PASS`)
    process.exit(pass === steps.length ? 0 : 1)
  }
})()
