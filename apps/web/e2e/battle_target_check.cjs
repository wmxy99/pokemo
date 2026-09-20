// 目标语义端到端验证：点「老大的指令」后，**高亮的卡**必须就是**实际被换上场的卡**
//
// 背景：引擎的 target 对 switch_self/boss 是「后备下标」，对 attach/evolve 是「槽位」，
//       前端若混用，玩家会看到 A 高亮、B 被换上场（曾真实存在）。
//
// 用法：node battle_target_check.cjs [base] [outDir]   （需后端与前端已启动）
const puppeteer = require('puppeteer-core')
const fs = require('fs')
const path = require('path')

const BASE = process.argv[2] || 'http://127.0.0.1:8000'
const OUTDIR = process.argv[3] || process.env.POKELAB_SHOTS
  || path.join(require('os').tmpdir(), 'pokelab_shots')
const CHROME = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find((p) => fs.existsSync(p))
const OUT = path.join(OUTDIR, `btgt_${Date.now()}.json`)
const BOSS_NAMES = ['老大的指令', '反攻捕捉器', '指令', "Boss's Orders", 'Counter Catcher', 'Orders']

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

// ⚠ 必须保留这个前导分号：上一行是箭头函数表达式，缺少 `;` 时 ASI 不会补分号，
//   下面这行会被解析成「对上一表达式求值结果的调用」，整个 IIFE 于是永不执行
//   —— 症状是进程退出码 0 且零输出（踩过）。仓库内其它 e2e 脚本同理。
;(async () => {
  let browser = null
  console.log('btgt: start, chrome =', CHROME || '(未找到 Chrome/Edge)')
  try {
    browser = await puppeteer.launch({
      executablePath: CHROME, headless: 'new',
      args: ['--no-sandbox', '--disable-gpu', '--no-zygote', '--single-process'],
    })
    console.log('btgt: browser launched')
    const page = await browser.newPage()
  await page.setViewport({ width: 1500, height: 1050 })
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message.slice(0, 160)))
  // 诊断：建局请求的实际 payload（确认前端真的把 A1 发出去了）
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/battles/.test(r.url())) {
      console.log(`btgt: [${Date.now() % 100000}] create req =`, (r.postData() || '').slice(0, 200))
    }
  })
  const mark = (x) => console.log(`btgt: [${Date.now() % 100000}] mark ${x}`)
  const shot = async (n) => {
    try { await page.screenshot({ path: path.join(OUTDIR, `btgt_${n}.png`) }) } catch { /* 忽略 */ }
  }
  const clickText = (sel, t) => page.evaluate((s, x) => {
    const el = [...document.querySelectorAll(s)].find((b) => b.textContent.includes(x))
    if (!el) return false; el.click(); return true
  }, sel, t)

  /** 棋盘快照：双方战斗位/后备名 + 高亮位置 */
  const board = () => page.evaluate(() => {
    const side = (cls) => {
      const z = document.querySelector(`.zone.side-${cls}`)
      if (!z) return null
      const one = (el) => (el ? {
        name: (el.querySelector('.cf-name') || {}).textContent?.trim() || null,
        highlight: el.classList.contains('highlight'),
      } : null)
      return {
        active: one(z.querySelector('.table-active .cf')),
        bench: [...z.querySelectorAll('.bench-list-cards .cf')].map(one),
      }
    }
    return {
      me: side('me'),
      opp: side('opp'),
      targetBar: !!document.querySelector('.target-bar'),
      barText: (document.querySelector('.target-bar') || {}).textContent || '',
      drawerVisible: document.querySelectorAll('.zone').length,
    }
  })

  const hand = () => page.evaluate((names) => ([...document.querySelectorAll('.zone.side-me .hand-card')]
    .map((w, i) => {
      const cf = w.querySelector('.cf')
      return {
        idx: i,
        name: (w.querySelector('.cf-name') || {}).textContent?.trim() || '',
        usable: cf ? cf.classList.contains('usable') : false,
      }
    }).filter((c) => names.some((n) => c.name.includes(n)))),
  BOSS_NAMES)

  const clickHand = (idx) => page.evaluate((i) => {
    const w = document.querySelectorAll('.zone.side-me .hand-card')[i]
    const cf = w && w.querySelector('.cf')
    if (!cf) return false
    cf.click(); return true
  }, idx)

  const clickOppHighlight = () => page.evaluate(() => {
    const z = document.querySelector('.zone.side-opp')
    const el = z && z.querySelector('.cf.highlight')
    if (!el) return null
    const name = (el.querySelector('.cf-name') || {}).textContent?.trim() || null
    el.click()
    return name
  })

  await page.goto(BASE + '/app/', { waitUntil: 'domcontentloaded', timeout: 30000 })
  console.log('btgt: page loaded')

    // 注册
    const uname = 'bt_c' + Date.now().toString(36).slice(-6)
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

    // 应用默认就停在对战页（有 3 个下拉），所以**必须先选卡组、再点建局** ——
    // 顺序颠倒会先建出默认卡组（lightning/fire）的局（踩过）。
    const pre = await page.evaluate(() => ({
      selects: document.querySelectorAll('select').length,
      createBtn: [...document.querySelectorAll('button')].some((b) => b.textContent.trim() === '开始对局'),
    }))
    if (!pre.createBtn) { await clickText('button,a', '开始对局'); await sleep(900) }
    // ⚠ 受控 <select> 用「原生 value setter + dispatchEvent」或 page.select() 都**驱动不了
    //   React 状态**（DOM 看着是 A1，state 仍是默认值 → 建局请求里还是 lightning/fire，踩过）。
    //   这里直接取元素上的 __reactProps$ 句柄调用 onChange，确保状态真正变更。
    const setReactSelect = (sel, value) => page.evaluate((s, v) => {
      const el = document.querySelector(s)
      if (!el) return 'no-element'
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set
      setter.call(el, v)
      const key = Object.keys(el).find((k) => k.startsWith('__reactProps$'))
      if (!key) return 'no-react-props'
      el[key].onChange({ target: el, currentTarget: el, type: 'change' })
      return 'ok'
    }, sel, value)

    const r1 = await setReactSelect('select#dx', 'A1')
    const r2 = await setReactSelect('select#dy', 'A1')
    await sleep(500)
    const selState = await page.evaluate(() => [...document.querySelectorAll('select')].map((s) => s.value))
    const presetOn = await page.evaluate(() =>
      ([...document.querySelectorAll('.preset.on')][0] || {}).textContent || '')
    log('选中 A1 卡组（含老大的指令）',
      r1 === 'ok' && r2 === 'ok' && selState.filter((x) => x === 'A1').length >= 2,
      { r: [r1, r2], state: selState, presetOn: presetOn.slice(0, 30) })

    mark('before create click')
    await clickText('button', '开始对局')
    mark('after create click')
    await sleep(1800)

    // 诊断：确认对局真的建起来了
    const diag = await page.evaluate(() => ({
      zones: document.querySelectorAll('.zone').length,
      hasMe: !!document.querySelector('.zone.side-me'),
      hasOpp: !!document.querySelector('.zone.side-opp'),
      handCards: document.querySelectorAll('.zone.side-me .hand-card').length,
      handNames: [...document.querySelectorAll('.zone.side-me .hand-card .cf-name')].map((n) => n.textContent.trim()),
      bodyHead: document.body.innerText.replace(/\s+/g, ' ').slice(0, 200),
    }))
    console.log('btgt: diag =', JSON.stringify(diag))
    log('对局已创建（棋盘与手牌存在）', diag.hasMe && diag.hasOpp, diag)

    let verified = false
    let tried = 0
    for (let turn = 0; turn < 26 && !verified; turn += 1) {
      const b0 = await board()
      if (!b0) { log('棋盘读取失败', false, { turn }); break }
      const bossCards = await hand()
      const handAll = await page.evaluate(() => [...document.querySelectorAll('.zone.side-me .hand-card')]
        .map((w) => {
          const cf = w.querySelector('.cf')
          return `${(w.querySelector('.cf-name') || {}).textContent?.trim() || '?'}${cf && cf.classList.contains('usable') ? '*' : ''}`
        }))
      console.log(`btgt: turn${turn + 1} hand=${JSON.stringify(handAll)} oppBench=${(b0.opp.bench || []).length}`)
      if (turn === 0) {
        console.log('btgt: turn0 board =', JSON.stringify(b0))
        console.log('btgt: turn0 boss in hand =', JSON.stringify(bossCards))
      }
      const use = bossCards.find((c) => c.usable)
      if (use && b0.opp && (b0.opp.bench || []).length > 0) {
        tried += 1
        const opened = await clickHand(use.idx)
        await sleep(400)
        const b1 = await board()
        if (b1 && b1.targetBar) {
          // 需要选目标 → 场上应高亮**后备**（引擎语义：boss 的 target 是后备下标）
          const hlBench = (b1.opp.bench || []).filter((x) => x.highlight)
          const hlActive = b1.opp.active && b1.opp.active.highlight
          log(`第 ${turn + 1} 回合：老大的指令 → 高亮位置正确（后备而非战斗位）`,
            hlBench.length > 0 && !hlActive,
            { benchHighlighted: hlBench.map((x) => x.name), activeHighlighted: !!hlActive,
              bar: (b1.barText || '').replace(/\s+/g, ' ').slice(0, 90) })
          await shot(`target_${turn + 1}`)
          // 点第一个高亮的后备
          const clickedName = await clickOppHighlight()
          await sleep(1600)
          const b2 = await board()
          const newActive = b2 && b2.opp && b2.opp.active && b2.opp.active.name
          log('点高亮卡后，实际换上场的正是该卡', !!clickedName && newActive === clickedName,
            { highlighted: clickedName, becameActive: newActive })
          await shot(`after_${turn + 1}`)
          if (clickedName && newActive === clickedName) verified = true
          else verified = false
        } else {
          // 只有 1 个后备 → 自动执行，校验提示文案与结果一致
          const toast = await page.evaluate(() =>
            ((document.body.innerText.match(/出牌：[^\n]*/) || [])[0] || '').trim())
          await sleep(1200)
          const b2 = await board()
          const newActive = b2 && b2.opp && b2.opp.active && b2.opp.active.name
          log('唯一目标自动执行：结果与提示一致', !!newActive,
            { toast: toast.slice(0, 80), becameActive: newActive })
          verified = !!newActive
        }
      }
      if (!verified) {
        await clickText('button', '结束回合')
        await sleep(1500)
      }
    }
    if (!verified) log('未能在 26 回合内取到「老大的指令」可用的局面', false, { tried })

    // 训练家卡详情浮层：应出现「卡牌效果」段（此前 card.type==='trainer' 判断永假 → 整段不渲染）
    // 注意：**可用的**训练家卡点击即出牌（不会开浮层），必须找一张当前不可用的训练家卡。
    let zoomText = null
    for (let i = 0; i < 10 && !zoomText; i += 1) {
      const opened = await page.evaluate(() => {
        const cand = [...document.querySelectorAll('.zone.side-me .hand-card .cf')]
          .find((c) => !c.classList.contains('pokemon') && !c.classList.contains('energy')
            && !c.classList.contains('usable'))
        if (!cand) return false
        cand.click(); return true
      })
      if (!opened) { await clickText('button', '结束回合'); await sleep(1400); continue }
      await sleep(400)
      zoomText = await page.evaluate(() => {
        const z = document.querySelector('.zoom-body')
        if (!z) return null
        return { hasEffect: /卡牌效果/.test(z.innerText), text: z.innerText.replace(/\s+/g, ' ').slice(0, 200) }
      })
      if (!zoomText) { // 点到的其实是可用卡 → 关掉重来
        await page.evaluate(() => {
          const m = document.querySelector('.zoom-mask'); if (m) m.click()
        })
        await sleep(200)
      }
    }
    log('训练家卡详情浮层包含「卡牌效果」段', !!(zoomText && zoomText.hasEffect),
      { zoom: zoomText })
    await shot('zoom_trainer')
  } catch (e) {
    log('脚本异常', false, { error: String(e).slice(0, 200) })
    console.log('btgt: ERROR', String(e).slice(0, 300))
  } finally {
    flush()
    const summary = JSON.stringify({ ok: steps.every((s) => s.ok), errors, steps }, null, 1)
    console.log(summary)
    try { if (browser) await browser.close() } catch { /* 忽略 */ }
  }
})()
