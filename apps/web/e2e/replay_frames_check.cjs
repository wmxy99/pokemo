// 逐帧回放验证：点「结束回合」后，棋盘应在回放窗口内经过**多个中间态**
// （而不是只在最后一次性套用终局）。
//
// 判据：对「对手区指纹」在回放期间去重计数。若回放是逐帧的，
//       指纹数 ≈ AI 动作数 + 1（初始态）；旧实现（只播报日志）最多 2 个。
//
// 用法：node replay_frames_check.cjs [base] [outDir]   （需后端与前端已启动）
const puppeteer = require('puppeteer-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUTDIR = process.argv[3] || process.env.POKELAB_SHOTS
  || path.join(require('os').tmpdir(), 'pokelab_shots')
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find((p) => fs.existsSync(p))
const OUT = path.join(OUTDIR, `rframes_${Date.now()}.json`)

const steps = []
const errors = []
function flush() {
  try {
    fs.mkdirSync(OUTDIR, { recursive: true })
    fs.writeFileSync(OUT, JSON.stringify({ ok: steps.every((s) => s.ok), errors, steps }, null, 1))
  } catch { /* 忽略 */ }
}
const log = (name, ok, extra = {}) => { steps.push({ name, ok, ...extra }); flush() }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ⚠ 这个前导分号不能删：上一行是箭头函数表达式，缺 `;` 时 ASI 会把 IIFE 解析成
//   「对上一表达式求值的调用」，脚本退出码 0 且零输出（踩过）。
;(async () => {
  let browser = null
  try {
    browser = await puppeteer.launch({
      executablePath: CHROME, headless: 'new',
      args: ['--no-sandbox', '--disable-gpu', '--no-zygote', '--single-process'],
    })
    const page = await browser.newPage()
    await page.setViewport({ width: 1500, height: 1050 })
    page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 160)))

    const clickText = (sel, t) => page.evaluate((s, x) => {
      const el = [...document.querySelectorAll(s)].find((b) => b.textContent.includes(x))
      if (!el) return false; el.click(); return true
    }, sel, t)

    /** 对手区指纹 + 回放条状态 + AI 日志行数 */
    const probe = () => page.evaluate(() => ({
      fp: (document.querySelector('.zone.side-opp')?.innerText || '')
        .replace(/\s+/g, ' ').trim(),
      replay: !!document.querySelector('.replay-bar'),
      ai: (document.querySelector('.log')?.textContent || '')
        .split('\n').filter((x) => x.includes('< AI：')).length,
      endTurn: [...document.querySelectorAll('button')].some((b) => b.textContent.includes('结束回合')),
    }))

    await page.goto(BASE + '/app/', { waitUntil: 'domcontentloaded', timeout: 30000 })
    const uname = 'rf_' + Date.now().toString(36).slice(-6)
    await clickText('button', '登录 / 注册')
    await sleep(400)
    await page.evaluate((u) => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
      const ins = document.querySelectorAll('.user input')
      set.call(ins[0], u); ins[0].dispatchEvent(new Event('input', { bubbles: true }))
      set.call(ins[1], 'pw123456'); ins[1].dispatchEvent(new Event('input', { bubbles: true }))
    }, uname)
    await clickText('button', '注册')
    await sleep(900)

    // 用进攻性演示卡组（雷电 vs 火系）→ AI 会连续攻击，动作数足够多
    await clickText('button', '开始对局')
    await sleep(1800)

    let best = { aiSteps: 0, distinct: 0, sawReplayBar: false, turn: 0 }
    for (let turn = 1; turn <= 8; turn += 1) {
      const p0 = await probe()
      if (!p0.endTurn) { log(`第 ${turn} 轮：对局已结束，停止采样`, true, { turn }); break }
      const frames = new Set([p0.fp])
      let maxAi = p0.ai
      let sawReplayBar = false
      await clickText('button', '结束回合')
      for (let i = 0; i < 70; i += 1) {
        await sleep(120)
        const p = await probe()
        frames.add(p.fp)
        maxAi = Math.max(maxAi, p.ai)
        if (p.replay) sawReplayBar = true
        if (sawReplayBar && !p.replay) break
        if (!sawReplayBar && i > 12) break      // 本轮没有 AI 动作
      }
      const aiSteps = maxAi - p0.ai
      const distinct = frames.size
      console.log(`rframes: turn${turn} aiSteps=${aiSteps} distinctFrames=${distinct} replayBar=${sawReplayBar}`)
      if (distinct > best.distinct) best = { aiSteps, distinct, sawReplayBar, turn }
      // 找到「AI 至少 2 个动作且中间态数 >= 动作数+1」的窗口即可判定
      if (aiSteps >= 2 && distinct >= aiSteps + 1) break
    }

    log('逐帧回放：棋盘经过的中间态数与 AI 动作数匹配',
      best.aiSteps >= 2 && best.distinct >= best.aiSteps + 1,
      best)
    try { await page.screenshot({ path: path.join(OUTDIR, 'rframes_final.png') }) } catch { /* 忽略 */ }
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 200) })
  } finally {
    flush()
    console.log(JSON.stringify({ ok: steps.every((s) => s.ok), errors, steps }, null, 1))
    try { if (browser) await browser.close() } catch { /* 忽略 */ }
  }
})()
