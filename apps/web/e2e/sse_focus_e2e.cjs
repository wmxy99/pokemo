// 聚焦验证：SSE 状态灯（实时 → 断线重连/兜底）+ 矩阵热力图 + 预设逐卡映射
// 用法：node sse_focus_e2e.cjs <base> <outDir>
// 环境提示：本沙箱内 Chrome 需 --single-process --no-zygote；结果逐步骤落盘。
const puppeteer = require('puppeteer-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUTDIR = process.argv[3] || process.env.POKELAB_SHOTS
  || path.join(require('os').tmpdir(), 'pokelab_shots')
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find((p) => fs.existsSync(p))

const OUT = path.join(OUTDIR, `focus_${Date.now()}.json`)
const steps = []
const errors = []
function flush() {
  try {
    fs.mkdirSync(OUTDIR, { recursive: true })
    fs.writeFileSync(OUT, JSON.stringify({
      ok: steps.every((s) => s.ok), errors: errors.slice(0, 8), steps,
    }, null, 1))
  } catch { /* 忽略 */ }
}
const log = (name, ok, extra = {}) => { steps.push({ name, ok, ...extra }); flush() }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--no-zygote', '--single-process'],
  })
  const page = await browser.newPage()
  await page.setViewport({ width: 1500, height: 1000 })
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 160)))
  page.on('response', (r) => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`) })
  const clickText = (sel, text) => page.evaluate((s, t) => {
    const el = [...document.querySelectorAll(s)].find((b) => b.textContent.includes(t))
    if (!el) return false
    el.click(); return true
  }, sel, text)
  const RUN = new Date().toISOString().slice(11, 19).replace(/:/g, '')
  const shot = async (name) => {
    const p = path.join(OUTDIR, `${RUN}_${name}`)   // 带时间戳：避免覆盖已存在文件（受限环境 EPERM）
    try { await page.screenshot({ path: p }); return p } catch (e) { return 'ERR:' + String(e).slice(0, 80) }
  }
  const readSse = () => page.evaluate(() => {
    const pill = document.querySelector('.sse-pill')
    const detail = [...document.querySelectorAll('.card')].find((c) => c.querySelector('.zt .mono'))
    const prog = (detail ? detail.innerText : '').match(/(\d+)\/(\d+)（(\d+)%）/)
    return {
      pillState: pill ? pill.getAttribute('data-sse-state') : null,
      pillText: pill ? pill.textContent.trim() : null,
      detailOpened: !!detail,
      detailHead: detail ? detail.querySelector('.zt').textContent.replace(/\s+/g, ' ').slice(0, 40) : null,
      prog: prog ? prog[0] : null,
    }
  })

  try {
    await page.goto(BASE + '/app/', { waitUntil: 'domcontentloaded', timeout: 30000 })

    // 登录
    const uname = 'fx_' + Date.now().toString(36).slice(-6)
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
    await sleep(1500)
    log('登录', await page.evaluate(() => !!localStorage.getItem('pokelab_token')))

    // ---- ③ 预设 A1 真实卡表逐卡映射 ----
    await clickText('nav.main button', '卡组管理')
    await sleep(1000)
    await page.evaluate(() => {
      const p = [...document.querySelectorAll('.preset')].find((x) => x.textContent.includes('A1'))
      p && p.click()
    })
    await sleep(1200)
    const p1 = await shot('10_preset_A1_realcardlist.png')
    const pd = await page.evaluate(() => ({
      rows: document.querySelectorAll('.tbl tbody tr').length,
      mapped: [...document.querySelectorAll('.pill')].filter((x) => x.textContent.trim() === '可映射').length,
      approx: [...document.querySelectorAll('.pill')].filter((x) => x.textContent.trim() === '近似').length,
      repl: [...document.querySelectorAll('.pill')].filter((x) => x.textContent.trim() === '占位').length,
      exec: (document.body.innerText.match(/可执行\s*([\d.]+)%/) || [])[1],
    }))
    log('③ 预设 A1 逐卡映射', pd.rows >= 20 && Number(pd.exec) >= 90, { ...pd, shot: p1 })

    // ---- ② 提交大池任务（16 对位 × 80 局 ≈ 20s+） ----
    await clickText('nav.main button', '批量对战')
    await sleep(800)
    await clickText('.seg button', '卡组池矩阵')
    await sleep(300)
    const sub = await page.evaluate(async () => {
      const tk = localStorage.getItem('pokelab_token')
      const r = await fetch('/batch/pool-jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tk },
        body: JSON.stringify({ decks_a: ['A1', 'A2', 'A3', 'A4'],
          decks_b: ['A1', 'A2', 'A3', 'A4'], games_each: 125, seed_start: 5 }),
      })
      return { status: r.status, body: (await r.text()).slice(0, 120) }
    })
    log('② 大池任务提交', [200, 202].includes(sub.status), sub)
    // 等列表轮询刷新出该任务行（含「查看」按钮），最多 20s
    let rowReady = false
    for (let i = 0; i < 20; i += 1) {
      await sleep(1200)
      rowReady = await page.evaluate(() => [...document.querySelectorAll('tbody tr')]
        .some((r) => r.querySelector('button.pill') &&
          /batch_/.test(r.textContent)))
      if (rowReady) break
    }
    log('② 任务列表出现可查看行', rowReady)

    // 打开该任务详情（含 SSE 状态灯）
    const click = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('tbody tr')]
      const r = rows.find((x) => /batch_/.test(x.textContent) && x.querySelector('button.pill'))
      if (!r) return { clicked: false, reason: 'no row with view button', rows: rows.length }
      const view = [...r.querySelectorAll('button')].find((b) => b.textContent.includes('查看'))
      if (!view) return { clicked: false, reason: 'no view button' }
      view.click()
      return { clicked: true, row: r.textContent.replace(/\s+/g, ' ').slice(0, 70) }
    })
    await sleep(3000)
    const live = await readSse()
    const s1 = await shot('11_batch_sse_live.png')
    log('② SSE 状态灯 = 实时', live.pillState === 'live', { click, ...live, shot: s1 })

    // ---- 断网模拟：真实离线（setOfflineMode）会**掐断已建立的 SSE 连接**，
    //      仅拦截新请求是不够的（已开的流不会断）。改用训练任务：子进程运行数分钟，
    //      足以覆盖「重连 → 轮询兜底」的完整状态机。
    const trainSub = await page.evaluate(async () => {
      const tk = localStorage.getItem('pokelab_token')
      const r = await fetch('/train/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tk },
        body: JSON.stringify({ stage1_episodes: 5000, bc_episodes: 40,
          stage2_episodes: 0, stage3_episodes: 0, features: 'legacy' }),
      })
      return { status: r.status, text: (await r.text()).slice(0, 140) }
    })
    log('② 训练任务提交（长时运行，用于断线验证）',
      [200, 202].includes(trainSub.status), trainSub)

    // 切到后台「RL 训练」页并打开该任务（列表 8s 轮询一次 → 需轮询等待行出现）
    await clickText('.app-switch button', '工程')
    await sleep(1000)
    await clickText('nav.main button', '训练')
    await sleep(2000)
    let tReady = false
    for (let i = 0; i < 20; i += 1) {
      await sleep(1500)
      tReady = await page.evaluate(() => [...document.querySelectorAll('tbody tr')]
        .some((r) => r.querySelector('button') && /train_/.test(r.textContent)))
      if (tReady) break
    }
    const tClick = await page.evaluate(() => {
      const r = [...document.querySelectorAll('tbody tr')]
        .find((x) => /train_/.test(x.textContent) && x.querySelector('button'))
      if (!r) return { clicked: false }
      const b = [...r.querySelectorAll('button')].find((x) => x.textContent.includes('查看'))
      if (!b) return { clicked: false }
      b.click(); return { clicked: true, row: r.textContent.replace(/\s+/g, ' ').slice(0, 60) }
    })
    // 任务可能处于「排队」→ 轮询直到 status=running、状态灯变 live（最多 45s）
    let trainLive = await readSse()
    for (let i = 0; i < 30 && trainLive.pillState !== 'live'; i += 1) {
      await sleep(1500)
      await page.evaluate(() => {
        const r = [...document.querySelectorAll('tbody tr')]
          .find((x) => /train_/.test(x.textContent) && x.querySelector('button'))
        if (!r) return false
        const b = [...r.querySelectorAll('button')].find((x) => x.textContent.includes('查看'))
        b && b.click(); return true
      })
      trainLive = await readSse()
    }
    const s1b = await shot('11b_train_sse_live.png')
    log('② 训练页 SSE 状态灯 = 实时', trainLive.pillState === 'live',
      { tReady, tClick, ...trainLive, shot: s1b })

    // ---- 断线兜底（确定性）：拦截 /stream + **重新加载页面**（必须重建流：
    //      SSE 仅在 detail.id/status 变化时重开，重复点「查看」不会新建连接）
    const openTrainDetail = async () => {
      await clickText('.app-switch button', '工程')
      await sleep(1200)
      await clickText('nav.main button', '训练')
      await sleep(1800)
      for (let i = 0; i < 20; i += 1) {
        const ready = await page.evaluate(() => [...document.querySelectorAll('tbody tr')]
          .some((r) => /train_/.test(r.textContent) && r.querySelector('button')))
        if (ready) break
        await sleep(1500)
      }
      return page.evaluate(() => {
        const r = [...document.querySelectorAll('tbody tr')]
          .find((x) => /train_/.test(x.textContent) && x.querySelector('button'))
        if (!r) return { clicked: false }
        const b = [...r.querySelectorAll('button')].find((x) => x.textContent.includes('查看'))
        if (!b) return { clicked: false }
        b.click(); return { clicked: true, row: r.textContent.replace(/\s+/g, ' ').slice(0, 50) }
      })
    }

    await page.setRequestInterception(true)
    const blockStream = (req) => (req.url().includes('/stream') ? req.abort() : req.continue())
    page.on('request', blockStream)
    await page.reload({ waitUntil: 'domcontentloaded' })
    await sleep(1500)
    const tClick2 = await openTrainDetail()
    await sleep(19000)                    // 等 maxRetry(4)×3s 重试耗尽 → 转轮询兜底
    const degraded = await readSse()
    const s2 = await shot('12_sse_degraded_fallback.png')
    log('② 断线 → 重连/轮询兜底',
      ['reconnecting', 'polling'].includes(degraded.pillState || ''),
      { tClick2, ...degraded, shot: s2 })

    // 恢复：解除拦截 + 新提交一个**池任务**（batch 流程已验证稳定）+ 重新加载
    // → 重新建流 → 状态灯应回到「实时」
    page.off('request', blockStream)
    await page.setRequestInterception(false)
    const rebatch = await page.evaluate(async () => {
      const tk = localStorage.getItem('pokelab_token')
      const r = await fetch('/batch/pool-jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tk },
        body: JSON.stringify({ decks_a: ['A1', 'A2', 'A3', 'A4'],
          decks_b: ['A1', 'A2', 'A3', 'A4'], games_each: 125, seed_start: 77 }),
      })
      return { status: r.status, text: (await r.text()).slice(0, 100) }
    })
    await page.reload({ waitUntil: 'domcontentloaded' })
    await sleep(1500)
    const openBatchDetail = async () => {
      await clickText('nav.main button', '批量对战')
      await sleep(1200)
      for (let i = 0; i < 20; i += 1) {
        const ready = await page.evaluate(() => [...document.querySelectorAll('tbody tr')]
          .some((r) => /batch_/.test(r.textContent) && r.querySelector('button.pill')))
        if (ready) break
        await sleep(1500)
      }
      return page.evaluate(() => {
        const r = [...document.querySelectorAll('tbody tr')]
          .find((x) => /batch_/.test(x.textContent) && x.querySelector('button.pill'))
        if (!r) return { clicked: false, rows: document.querySelectorAll('tbody tr').length }
        const b = [...r.querySelectorAll('button')].find((x) => x.textContent.includes('查看'))
        if (!b) return { clicked: false, reason: 'no view' }
        b.click(); return { clicked: true, row: r.textContent.replace(/\s+/g, ' ').slice(0, 50) }
      })
    }
    let tClick3 = await openBatchDetail()
    let recovered = await readSse()
    for (let i = 0; i < 15 && recovered.pillState !== 'live'; i += 1) {
      await sleep(1500)
      tClick3 = await openBatchDetail()
      recovered = await readSse()
    }
    const s2b = await shot('12b_sse_recovered.png')
    log('② 恢复连接 → 状态灯回到实时', recovered.pillState === 'live',
      { rebatch, tClick3, ...recovered, shot: s2b })

    // 收尾：取消长时训练任务（避免占住并发槽 / 留下长时间子进程）
    await page.evaluate(async () => {
      const tk = localStorage.getItem('pokelab_token')
      const r = await fetch('/train/jobs?limit=5', {
        headers: { Authorization: 'Bearer ' + tk } })
      const js = await r.json()
      for (const j of (js.jobs || [])) {
        if (j.status === 'running' || j.status === 'queued') {
          await fetch(`/train/jobs/${j.id}/cancel`, { method: 'POST',
            headers: { Authorization: 'Bearer ' + tk } })
        }
      }
      return true
    })

    // 回到前台并等池任务出数据
    await clickText('.app-switch button', '玩家')
    await sleep(1000)
    await clickText('nav.main button', '批量对战')
    // ---- ① 矩阵热力图（等池任务出数据） ----
    for (let i = 0; i < 15; i += 1) {
      await sleep(3000)
      const running = await page.evaluate(() => {
        const rows = [...document.querySelectorAll('tbody tr')]
        return rows.some((x) => x.textContent.includes('running'))
      })
      if (!running) break
    }
    await clickText('nav.main button', '统计报告')
    await sleep(2500)
    const heat = await page.evaluate(() => {
      const cells = [...document.querySelectorAll('.matrix .heat')]
      return {
        heatCells: cells.length,
        distinctBgs: [...new Set(cells.map((c) => c.style.background))].length,
        legend: document.querySelectorAll('.heat-legend .heat-bar').length,
        rowAvg: document.body.innerText.includes('行平均'),
        colAvg: document.body.innerText.includes('列平均'),
        aggCells: document.querySelectorAll('.matrix .agg').length,
        samples: cells.slice(0, 3).map((c) => c.getAttribute('aria-label')),
        focusable: cells.every((c) => c.tabIndex === 0),
      }
    })
    const s3 = await shot('13_stats_heatmap.png')
    log('① 矩阵热力图（色阶/图例/行·列聚合/键盘可达）',
      heat.heatCells > 0 && heat.legend === 1 && heat.rowAvg && heat.colAvg
      && heat.distinctBgs >= 2 && heat.focusable,
      { ...heat, shot: s3 })
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 220) })
  }

  flush()
  try { await browser.close() } catch { /* 忽略 */ }
  process.stdout.write('done ' + OUT + '\n')
  process.exit(0)
})()
