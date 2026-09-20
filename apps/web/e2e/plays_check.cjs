#!/usr/bin/env node
/**
 * plays.js 目标语义回归检查（纯逻辑，不需要后端 / 浏览器）
 *
 * 背景：引擎的 `target` 有两种语义，前端混用会造成「点了高亮的卡，却是另一只被换下」
 * 这种误导（曾真实存在，实测 4 处错配）：
 *
 *   attach / evolve            target = 槽位：0 = 战斗位，1.. = 我方后备
 *   trainer  switch_self       target = 我方后备下标（0 起）
 *   trainer  boss              target = 对手后备下标（0 起）
 *   trainer  recover:N:...     target = 弃牌堆下标（0 起）
 *   ability  cursed:N（手牌）   target = 对手槽位：0 = 对手战斗位，1.. = 对手后备
 *
 * 本脚本把「引擎真实作用对象」与「UI 的标签 / 槽位」逐条对账。
 * 夹具（fixture）抄自引擎真实产出：packages/engine 下构造同形状对局后 dump
 * `battle.view(pi)["legal"]`，因此**引擎若改语义，这里需要同步重新生成夹具**。
 *
 * 用法：node e2e/plays_check.cjs
 */
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const ME_ACTIVE = { name: 'MyActive', display: '我方战斗位' }
const ME_BENCH = [{ name: 'MyBench0', display: '后备甲' }, { name: 'MyBench1', display: '后备乙' }]
const OPP_ACTIVE = { name: 'OppActive', display: '对手战斗位' }
const OPP_BENCH = [{ name: 'OppBench0', display: '对手后备甲' }, { name: 'OppBench1', display: '对手后备乙' }]

const view = {
  me_state: {
    active: ME_ACTIVE, bench: ME_BENCH, attached_energy_this_turn: 0, supporter_used: 0,
  },
  opp_state: { active: OPP_ACTIVE, bench: OPP_BENCH },
}
const viewSupporterUsed = {
  ...view,
  me_state: { ...view.me_state, supporter_used: 1 },
}

/** Zone 组件的槽位 → 该位置实际是哪只宝可梦（0 = 战斗位，i+1 = 后备 i） */
function slotOwner(where, slot) {
  const me = where.startsWith('my')
  const active = me ? ME_ACTIVE : OPP_ACTIVE
  const bench = me ? ME_BENCH : OPP_BENCH
  const mon = slot === 0 ? active : bench[slot - 1]
  return mon ? mon.display : '（越界/空）'
}

const CASES = [
  {
    title: '换位（switch_self）：target = 我方后备下标',
    handIdx: 1,
    card: { type: 'item', tag: 'switch_self' },
    legal: [
      { verb: 'trainer', hand: 1, tag: 'switch_self', kind: 'item', target: 0 },
      { verb: 'trainer', hand: 1, tag: 'switch_self', kind: 'item', target: 1 },
    ],
    // 引擎实际作用对象（下标 → 后备名）
    engineAffects: [ME_BENCH[0].display, ME_BENCH[1].display],
  },
  {
    title: '老大的指令（boss）：target = 对手后备下标',
    handIdx: 0,
    card: { type: 'supporter', tag: 'boss' },
    legal: [
      { verb: 'trainer', hand: 0, tag: 'boss', kind: 'supporter', target: 0 },
      { verb: 'trainer', hand: 0, tag: 'boss', kind: 'supporter', target: 1 },
    ],
    engineAffects: [OPP_BENCH[0].display, OPP_BENCH[1].display],
  },
  {
    title: '附能量（attach）：target = 槽位（0 = 战斗位）',
    handIdx: 2,
    card: { type: 'energy', tag: null },
    legal: [
      { verb: 'attach', target: 0 },
      { verb: 'attach', target: 1 },
      { verb: 'attach', target: 2 },
    ],
    engineAffects: [ME_ACTIVE.display, ME_BENCH[0].display, ME_BENCH[1].display],
  },
  {
    title: '夜之担架（recover）：target = 弃牌堆下标',
    handIdx: 3,
    card: { type: 'item', tag: 'recover:1:any' },
    legal: [
      { verb: 'trainer', hand: 3, tag: 'recover:1:any', kind: 'item', target: 0 },
      { verb: 'trainer', hand: 3, tag: 'recover:1:any', kind: 'item', target: 2 },
    ],
    // 弃牌堆里的卡不进 view，只校验「不是场上目标」+ 文案点出第几张
    engineAffects: null,
    expectLabels: ['弃牌堆第 1 张', '弃牌堆第 3 张'],
  },
  {
    title: '诅咒爆破（手牌特性 cursed）：target = 对手槽位（0 = 对手战斗位）',
    handIdx: 4,
    card: { type: 'pokemon', stage: 0 },
    legal: [
      { verb: 'ability', hand: 4, tag: 'cursed:130', target: 0 },
      { verb: 'ability', hand: 4, tag: 'cursed:130', target: 1 },
    ],
    engineAffects: [OPP_ACTIVE.display, OPP_BENCH[0].display],
  },
]

let failed = 0
function fail(msg) { failed += 1; console.log(`   ❌ ${msg}`) }
function ok(msg) { console.log(`   ✅ ${msg}`) }

(async () => {
  // src/*.js 是 ESM（package.json type=module）→ CJS 里用动态 import + file:// URL
  const mod = await import(pathToFileURL(path.resolve(__dirname, '../src/plays.js')).href)
  const { planForHand, isFieldTarget, targetSide, handUsable } = mod

  console.log('plays.js 目标语义回归检查\n')

  for (const c of CASES) {
    console.log(`── ${c.title}`)
    const plan = planForHand(c.handIdx, c.card, c.legal, view)
    if (plan.kind !== 'target') {
      fail(`期望 kind=target，实际 ${plan.kind}`)
      continue
    }
    if (plan.targets.length !== c.legal.length) {
      fail(`目标数不符：期望 ${c.legal.length}，实际 ${plan.targets.length}`)
      continue
    }
    plan.targets.forEach((t, i) => {
      const label = t.label
      if (c.engineAffects) {
        const expect = c.engineAffects[i]
        const uiOwner = slotOwner(t.where, t.slot)
        if (uiOwner !== expect) fail(`目标 ${i}：UI 槽位指向「${uiOwner}」，引擎实际作用「${expect}」`)
        else ok(`目标 ${i}：UI「${label}」→ 槽位命中 ${expect}`)
        if (!label.includes(expect)) fail(`目标 ${i}：文案「${label}」未点出实际作用对象「${expect}」`)
        // 场上目标必须被判定为需要高亮
        if (!isFieldTarget(t.where)) fail(`目标 ${i}：${t.where} 应判定为场上目标（需高亮）`)
      } else {
        const want = c.expectLabels[i]
        if (label !== want) fail(`目标 ${i}：文案「${label}」≠ 期望「${want}」`)
        else ok(`目标 ${i}：文案「${label}」`)
        if (isFieldTarget(t.where)) fail(`目标 ${i}：${t.where} 不该被当成场上目标`)
      }
      // 原始 target 必须原样带给引擎
      if (t.action.target !== c.legal[i].target) {
        fail(`目标 ${i}：提交给引擎的 target 被篡改（${t.action.target} ≠ ${c.legal[i].target}）`)
      }
    })
    console.log('')
  }

  // 对手侧 / 我方侧判定
  console.log('── 区域归属判定')
  ;[['my-active', 'me'], ['my-bench', 'me'], ['opp-active', 'opp'], ['opp-bench', 'opp']]
    .forEach(([where, side]) => {
      if (targetSide(where) !== side) fail(`${where} 应归属 ${side}`)
      else ok(`${where} → ${side}`)
    })
  console.log('')

  // 训练家卡不可用原因（引擎手牌里 type = tdef.kind，不是 'trainer'）
  console.log('── 不可用原因文案')
  const bossCard = { type: 'supporter', tag: 'boss' }
  if (handUsable(0, bossCard, [])) fail('supporter_used=1 时不该判定为可用')
  const p1 = planForHand(0, bossCard, [], viewSupporterUsed)
  const want1 = '本回合已经用过支援者卡'
  if (p1.kind !== 'none' || p1.reason !== want1) fail(`期望「${want1}」，实际「${p1.reason}」`)
  else ok(`支援者已用过 → ${p1.reason}`)

  const energyCard = { type: 'energy' }
  const vEnergy = { ...view, me_state: { ...view.me_state, attached_energy_this_turn: 1 } }
  const p2 = planForHand(0, energyCard, [], vEnergy)
  if (p2.reason !== '本回合已经附过能量了') fail(`能量卡原因异常：「${p2.reason}」`)
  else ok(`能量已附过 → ${p2.reason}`)

  const stage1 = { type: 'pokemon', stage: 1 }
  const p3 = planForHand(0, stage1, [], view)
  if (!/进化/.test(p3.reason || '')) fail(`进化卡原因异常：「${p3.reason}」`)
  else ok(`无进化目标 → ${p3.reason}`)

  // 训练家 tag 文案覆盖：带参数的 tag 必须也查得到文案（卡池 49 张里 38 张是带参数的）
  console.log('── 训练家 tag 文案覆盖（含参数形态）')
  const { trainerDesc, abilityDesc } = await import(
    pathToFileURL(path.resolve(__dirname, '../src/cardtext.js')).href)
  // 以下 tag 清单由 packages/engine/engine/data/cards*.json 去重生成（引擎改卡池时重新生成）
  const TRAINER_TAGS = ['boss', 'candy', 'discard_search_energy:2', 'hammer', 'poffin',
    'recover:1:any', 'recover:2:energy', 'research:7', 'search_any:1', 'search_any:2',
    'search_any:5', 'search_energy:1', 'search_energy:2', 'search_energy:7',
    'search_pokemon:1', 'search_pokemon:2', 'search_pokemon:3', 'search_pokemon:4',
    'search_pokemon:5', 'search_trainer:1', 'shuffle_draw:5', 'switch_self', 'ultra_ball']
  TRAINER_TAGS.forEach((tag) => {
    // 注意：trainerDesc 返回的是**字符串**（cc() 已按当前语言取值），不是 {zh,en} 对象
    const d = trainerDesc(tag)
    if (!d || typeof d !== 'string' || d.includes('暂无说明')) fail(`trainerDesc('${tag}') 无文案`)
    else {
      const arg = String(tag).split(':')[1]
      const ok2 = /^\d+$/.test(arg || '') ? d.includes(arg) : true
      if (!ok2) fail(`trainerDesc('${tag}') 文案未代入参数：${d}`)
      else ok(`${tag} → ${d}`)
    }
  })
  // 特性文案（同样有带参数形态）
  ;['recon:2', 'cursed:130', 'draw:1', 'move_damage:3', 'attach_energy:1:fire'].forEach((tag) => {
    const d = abilityDesc(tag)
    if (!d || !d.zh || d.zh.includes('暂无说明')) fail(`abilityDesc('${tag}') 无文案`)
    else ok(`${tag} → ${d.zh.slice(0, 40)}`)
  })
  console.log('')

  if (failed > 0) {
    console.log(`结果：❌ ${failed} 项不一致`)
    process.exit(1)
  }
  console.log('结果：✅ 全部一致')
})()
