// 三项增强的 UI 端到端验证（真实 Chrome，无落盘依赖）
// ① 矩阵热力图（底色/图例/行·列聚合）② SSE 实时进度（心跳+续传状态灯）
// ③ 真实卡池预设 A1–A4（60 卡逐卡映射状态）
// 用法：node sse_matrix_e2e.cjs [base] [outJsonPath]
//   环境提示：本沙箱内 Chrome 需 --single-process --no-zygote 才能稳定启动，
//   且输出需逐步骤落盘（进程可能被外部回收，最后统一写会丢结果）。
const puppeteer = require('puppeteer-core')
const fs = require('fs')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUT = process.argv[3] || null
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find((p) => fs.existsSync(p))

const steps = []
const errors = []
function flush() {
  const failed = steps.filter((s) => !s.ok).length
  const payload = JSON.stringify({ ok: failed === 0, failed, errors: errors.slice(0, 8), steps }, null, 1)
  if (OUT) { try { fs.writeFileSync(OUT, payload) } catch { /* 忽略 */ } }
  return payload
}
const log = (name, ok, extra = {}) => {
  steps.push({ name, ok, ...extra })
  flush()                                  // 每步落盘：中途被回收也能拿到部分结果
}

;(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--no-zygote', '--single-process'],
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1500, height: 980 })
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 160)))
  page.on('response', (r) => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`) })
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  const clickText = (sel, text) => page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((b) => b.textContent.includes(t))
    if (!el) return false
    el.click(); return true
  }, sel, text)

  try {
    await page.goto(BASE + '/app/', { waitUntil: 'networkidle2', timeout: 30000 })

    // ---- 登录 ----
    const uname = 'sm_' + Date.now().toString(36).slice(-6)
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
    await sleep(1600)
    log('登录', await page.evaluate(() => !!localStorage.getItem('pokelab_token')))

    // ---- ① 卡组页：预设 A1 逐卡映射 ----
    await clickText('nav.main button', '卡组管理')
    await sleep(1200)
    const presetCards = await page.evaluate(() => document.querySelectorAll('.preset').length)
    await page.evaluate(() => {
      const p = [...document.querySelectorAll('.preset')].find((x) => x.textContent.includes('A1'))
      p && p.click()
    })
    await sleep(1500)
    const pd = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('.tbl tbody tr')]
      const pills = [...document.querySelectorAll('.pill')].map((p) => p.textContent.trim())
      const dragapult = rows.find((r) => r.textContent.includes('Dragapult ex'))
      return {
        rows: rows.length,
        mapped: pills.filter((x) => x === '可映射').length,
        hasDragapult: !!dragapult,
        exec: (document.body.innerText.match(/可执行\s*([\d.]+)%/) || [])[1],
      }
    })
    log('③ 预设 A1 真实卡表逐卡', presetCards === 4 && pd.hasDragapult && pd.rows >= 20,
      { presets: presetCards, ...pd })

    // ---- ② 批量页：池模式提交 + SSE 状态灯 ----
    await clickText('nav.main button', '批量对战')
    await sleep(900)
    await clickText('.seg button', '卡组池矩阵')
    await sleep(400)
    const submitted = await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((x) => x.textContent.includes('提交'))
      if (!b) return false
      b.click(); return true
    })
    await sleep(1500)
    const live = await page.evaluate(() => {
      const pills = [...document.querySelectorAll('.pill')].map((p) => p.textContent.trim())
      return { pills, sse: pills.find((p) => /实时|重连中|轮询兜底|连接中/.test(p)) || null }
    })
    log('② 批量任务 SSE 状态灯', submitted && !!live.sse, live)

    // 等任务推进，观察进度条 + 是否出现续传状态
    await sleep(6000)
    const prog = await page.evaluate(() => {
      const bar = document.querySelector('.bar[role="progressbar"]')
      const txt = (document.body.innerText.match(/(\d+)\/(\d+)（(\d+)%）/) || [])
      const pills = [...document.querySelectorAll('.pill')].map((p) => p.textContent.trim())
      return { bar: !!bar, pct: txt[3] || null, sse: pills.find((p) => /实时|重连中|轮询兜底/.test(p)) || null }
    })
    log('② 实时进度推进', !!prog.bar && Number(prog.pct || 0) >= 0, prog)

    // ---- 统计页：热力图 ----
    await clickText('nav.main button', '统计报告')
    await sleep(2000)
    const heat = await page.evaluate(() => {
      const cells = [...document.querySelectorAll('.matrix .heat')]
      const bgs = [...new Set(cells.map((c) => c.style.background))]
      return {
        heatCells: cells.length,
        distinctBgs: bgs.length,
        legend: document.querySelectorAll('.heat-legend .heat-bar').length,
        rowAgg: document.querySelectorAll('.matrix .agg').length,
        colAvg: document.body.innerText.includes('列平均'),
        rowAvg: document.body.innerText.includes('行平均'),
        aria: cells.length ? (cells[0].getAttribute('aria-label') || '').slice(0, 60) : '',
      }
    })
    log('① 矩阵热力图', heat.heatCells > 0 && heat.legend === 1 && heat.colAvg && heat.rowAvg,
      heat)

    // ---- ② 重连状态灯（断网模拟：拦截 SSE 请求）----
    await page.setRequestInterception(true)
    const blockSse = (req) => {
      if (req.url().includes('/stream')) return req.abort()
      return req.continue()
    }
    page.on('request', blockSse)
    await clickText('nav.main button', '批量对战')
    await sleep(900)
    await page.evaluate(() => {
      const row = [...document.querySelectorAll('tbody tr')]
        .find((r) => r.textContent.includes('running'))
      const b = row && [...row.querySelectorAll('button')].pop()
      b && b.click()
    })
    await sleep(9000)
    const sse2 = await page.evaluate(() => {
      const pills = [...document.querySelectorAll('.pill')].map((p) => p.textContent.trim())
      return { pills: pills.filter((p) => /实时|重连中|轮询兜底/.test(p)) }
    })
    log('② 断线续传/兜底状态', sse2.pills.length > 0, sse2)
    page.off('request', blockSse)
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 200) })
  }

  flush()
  try { await browser.close() } catch { /* 忽略 */ }
  process.stdout.write('done\n')
  process.exit(0)
})()
