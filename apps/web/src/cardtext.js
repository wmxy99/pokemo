// 卡面文案映射（**自述转写，非官方文案** · 合规 C1/C5）
//
// 为什么需要这个文件：
//   引擎的 view() 给出的是**数据键**（如训练师 tag = "ultra_ball"、能量 type = "lightning"），
//   而卡面上要显示的是**可读的效果说明**。引擎侧只保留了 tag 语义（见 cards.py 的
//   TrainerDef / AbilityDef 注释），文字层由前端负责——这样文案改动不必动引擎与测试。
//
// 约束：所有说明均为「用自己的话描述机制」，不复制任何官方卡牌文本。
// 取值方式：cc(entry, lang) —— entry 可以是 {zh, en} 或函数 (tag) => {zh, en}

import { lang as uiLang } from './i18n.js'

export const cc = (entry, lang = uiLang) => {
  // ⚠ 本函数是**所有** {zh,en} 文案的唯一解包口 —— 返回值**必须是字符串**。
  //   漏解包（把对象直接 return 出去）时，React 会把它当子节点渲染 →
  //   error #31「Objects are not valid as a React child」→ 整棵树卸载 → **整屏黑屏**。
  //   （踩过：trainerDesc 的兜底分支漏了 cc()，真实卡组的训练家 tag 一进手牌就黑屏。）
  if (typeof entry === 'function') return cc(entry(lang), lang)
  if (!entry) return ''
  if (typeof entry !== 'object') return String(entry)
  return entry[lang] ?? entry.zh ?? entry.en ?? ''
}

// ---------------- 属性 / 能量 ----------------

export const TYPE_LABEL = {
  grass: { zh: '草', en: 'Grass' },
  fire: { zh: '火', en: 'Fire' },
  water: { zh: '水', en: 'Water' },
  lightning: { zh: '雷', en: 'Lightning' },
  psychic: { zh: '超', en: 'Psychic' },
  fighting: { zh: '斗', en: 'Fighting' },
  darkness: { zh: '恶', en: 'Darkness' },
  metal: { zh: '钢', en: 'Metal' },
  dragon: { zh: '龙', en: 'Dragon' },
  colorless: { zh: '无色', en: 'Colorless' },
  any: { zh: '任意', en: 'Any' },
  team_rocket: { zh: '火箭队', en: 'Team Rocket' },
}

// 卡面配色（主干色 / 浅底）：用于卡头色带与能量圆点
export const TYPE_COLOR = {
  grass: ['#15803D', '#DCFCE7'],
  fire: ['#B91C1C', '#FEE2E2'],
  water: ['#1D4ED8', '#DBEAFE'],
  lightning: ['#A16207', '#FEF9C3'],
  psychic: ['#6D28D9', '#EDE9FE'],
  fighting: ['#C2410C', '#FFEDD5'],
  darkness: ['#334155', '#E2E8F0'],
  metal: ['#475569', '#E2E8F0'],
  dragon: ['#92400E', '#FEF3C7'],
  colorless: ['#64748B', '#F1F5F9'],
  any: ['#B45309', '#FEF3C7'],
  team_rocket: ['#9D174D', '#FCE7F3'],
}
export const typeColor = (t) => TYPE_COLOR[t] || TYPE_COLOR.colorless

export const typeLabel = (t) => cc(TYPE_LABEL[t] || { zh: t || '—', en: t || '—' })

// ---------------- 阶段 / 训练师类别 ----------------

export const STAGE_LABEL = {
  0: { zh: '基础', en: 'Basic' },
  1: { zh: '一阶', en: 'Stage 1' },
  2: { zh: '二阶', en: 'Stage 2' },
}
export const stageLabel = (s) => cc(STAGE_LABEL[s] || { zh: '', en: '' })

export const TRAINER_KIND = {
  item: { zh: '物品', en: 'Item' },
  supporter: { zh: '支援者', en: 'Supporter' },
  stadium: { zh: '竞技场', en: 'Stadium' },
  tool: { zh: '宝可梦道具', en: 'Tool' },
}
export const trainerKindLabel = (k) => cc(TRAINER_KIND[k] || { zh: k || '训练家', en: k || 'Trainer' })

// ⚠ 引擎手牌序列化给训练家卡的 `type` 就是 `tdef.kind`（item / supporter / stadium / tool），
//   **不是**字符串 'trainer'。曾用 `card.type === 'trainer'` 判断，分支永不命中
//   （症状：不可用原因退化成通用文案、详情浮层的「卡牌效果」整段不渲染）。
export const TRAINER_KINDS = Object.keys(TRAINER_KIND)
export const isTrainerCard = (c) => !!c && TRAINER_KINDS.includes(c.type)

/** 卡牌大类：'pokemon' | 'energy' | 'trainer'（含缺 type 时的兜底识别） */
export const cardKind = (c) => {
  if (!c) return 'trainer'
  if (c.type === 'pokemon' || c.type === 'energy') return c.type
  if (isTrainerCard(c)) return 'trainer'
  // 缺 type（历史数据或其它数据源）：有招式/阶段/HP 字段则视作宝可梦
  if (c.attacks !== undefined || c.stage !== undefined || c.hp !== undefined) return 'pokemon'
  return 'trainer'
}

// ---------------- 训练师效果说明 ----------------
// 与 packages/engine/engine/cards.py 的 TrainerDef.tag 全集一一对应。
//
// ⚠ tag 有**两种形态**：不带参数的（boss / switch_self / candy …）与带参数的
//   （research:7 / recover:1:any / search_pokemon:3 …）。卡池里 49 张训练师卡中有 38 张
//   是带参数的 —— 若只按完整 tag 做字典查找，这些卡的卡面与详情都会显示
//   「（该效果暂无说明）」（踩过）。因此带参数的族用**函数**，把参数代入文案。
const argOf = (a) => (/^\d+$/.test(String(a || '')) ? String(a) : 'n')
const RECOVER_KIND = { any: '任意卡', energy: '能量卡', pokemon: '宝可梦', trainer: '训练家卡' }

// tag 参数里的属性/类别 → 可读词（自带 zh/en，供文案函数代入）
const TY = {
  fire: ['火', 'Fire'], water: ['水', 'Water'], grass: ['草', 'Grass'],
  lightning: ['雷', 'Lightning'], psychic: ['超', 'Psychic'],
  fighting: ['斗', 'Fighting'], darkness: ['恶', 'Darkness'],
  metal: ['钢', 'Metal'], dragon: ['龙', 'Dragon'],
  colorless: ['无色', 'Colorless'], any: ['任意', 'any type'],
}
const ty = (k) => TY[k] || [k || '—', k || '—']
const numsOf = (parts) => (parts || []).filter((x) => /^\d+$/.test(x))

export const TRAINER_TAG_DESC = {
  poffin: { zh: '从牌库中检索 2 张基础宝可梦，放置到后备。', en: 'Search your deck for 2 Basic Pokémon and put them on the Bench.' },
  ultra_ball: { zh: '弃置 2 张手牌，从牌库检索 1 张宝可梦加入手牌。', en: 'Discard 2 cards, then search your deck for a Pokémon.' },
  hammer: { zh: '抛硬币；若为正面，弃置对手战斗宝可梦身上的 1 个能量。', en: 'Flip a coin. If heads, discard an Energy from the opponent’s Active Pokémon.' },
  boss: { zh: '将对手的 1 只后备宝可梦换到战斗位（需指定目标）。', en: 'Switch in 1 of your opponent’s Benched Pokémon (requires a target).' },
  switch_self: { zh: '将己方战斗宝可梦与 1 只后备交换（不消耗撤退能量；需指定目标）。', en: 'Switch your Active Pokémon with 1 Benched Pokémon (no Retreat cost; needs a target).' },
  candy: { zh: '跳过一阶：直接由基础宝可梦进化为二阶形态。', en: 'Skip Stage 1: evolve a Basic directly into a Stage 2.' },
  unsupported: { zh: '该卡效果尚未在引擎中建模（以占位方式参与对局，如实标注）。', en: 'Effect not yet modeled by the engine (participates as a placeholder).' },
  // ---- 带参数族（arg 为卡面参数，缺省显示 n）----
  research: (a) => ({ zh: `弃置全部手牌，然后抽 ${argOf(a)} 张牌。`, en: `Discard your hand and draw ${argOf(a)} cards.` }),
  shuffle_draw: (a) => ({ zh: `将手牌洗回牌库，然后抽 ${argOf(a)} 张牌。`, en: `Shuffle your hand into your deck and draw ${argOf(a)} cards.` }),
  recover: (a, kind) => ({
    zh: `从弃牌堆回收 ${argOf(a)} 张${RECOVER_KIND[kind] || '卡'}加入手牌。`,
    en: `Put ${argOf(a)} card(s) from your discard pile into your hand.`,
  }),
  discard_search_energy: (a) => ({ zh: `弃置 1 张手牌，从牌库检索 ${argOf(a)} 个基本能量加入手牌。`, en: `Discard 1 card, then search your deck for ${argOf(a)} Basic Energy.` }),
  search_energy: (a) => ({ zh: `从牌库检索 ${argOf(a)} 个基本能量加入手牌。`, en: `Search your deck for ${argOf(a)} Basic Energy cards.` }),
  search_pokemon: (a) => ({ zh: `从牌库检索 ${argOf(a)} 张宝可梦加入手牌。`, en: `Search your deck for ${argOf(a)} Pokémon.` }),
  search_trainer: (a) => ({ zh: `从牌库检索 ${argOf(a)} 张训练家卡加入手牌。`, en: `Search your deck for ${argOf(a)} Trainer cards.` }),
  search_any: (a) => ({ zh: `从牌库检索 ${argOf(a)} 张任意卡加入手牌。`, en: `Search your deck for ${argOf(a)} cards.` }),
  // 第七十九批（2026-11-05）：逐类别各取 1 张 / 检索后放回牌库顶
  search_multi: (_a) => ({ zh: '从牌库中逐类别各取 1 张卡加入手牌。', en: 'Search your deck for 1 card of each listed category and put them into your hand.' }),
  search_top: (a) => ({ zh: `从牌库检索 ${argOf(a)} 张卡，洗牌后放回牌库顶。`, en: `Search your deck for ${argOf(a)} cards, shuffle, then put them on top of your deck.` }),
  // ---- 真实卡池的 39 类 tag 族（2026-11-04 补齐）----
  // 背景：真实卡组（A1–A4 / B1–B5）一进手牌就整屏黑屏 —— trainerDesc 对
  // 未收录的族走了「返回 {zh,en} 对象」的兜底分支，被 React 当子节点渲染。
  // 修好兜底只是止血，这里把池子里实际出现的族**逐族补齐说明**（自述转写，C1/C5）。
  gust: { zh: '将对手的 1 只后备宝可梦换到战斗位。', en: 'Switch in 1 of your opponent’s Benched Pokémon to the Active Spot.' },
  gust_confuse: { zh: '将对手的 1 只后备基础宝可梦换到战斗位，并使其陷入混乱。', en: 'Switch in 1 of your opponent’s Benched Basic Pokémon to the Active Spot and Confuse it.' },
  heal: (a) => ({ zh: `为 1 只己方宝可梦回复 ${argOf(a)} 点伤害。`, en: `Heal ${argOf(a)} damage from 1 of your Pokémon.` }),
  mill: (a) => ({ zh: `弃掉牌库顶 ${argOf(a)} 张卡。`, en: `Discard the top ${argOf(a)} card(s) of your deck.` }),
  draw: (a, _x, parts) => {
    if ((parts || []).includes('per')) {
      return { zh: `按场上计数抽牌（基数 ${argOf(a)} 张，每满足 1 个计数条件额外抽 1 张）。`, en: `Draw cards by a count on the field (base ${argOf(a)}, plus 1 per condition met).` }
    }
    return { zh: `抽 ${argOf(a)} 张牌。`, en: `Draw ${argOf(a)} card(s).` }
  },
  draw_to: (a) => ({ zh: `抽牌，直到手牌达到 ${argOf(a)} 张。`, en: `Draw cards until you have ${argOf(a)} in hand.` }),
  draw_cond: (_a, _x, parts) => {
    const ns = numsOf(parts)
    const base = ns[0] || 'n'
    const more = ns[1] || 'n'
    return { zh: `抽 ${base} 张牌；若满足卡面条件，额外抽 ${more} 张。`, en: `Draw ${base} card(s); if the condition is met, draw ${more} more.` }
  },
  shuffle_draw_split: (a) => ({ zh: `双方各自将手牌洗回牌库，然后各抽 ${argOf(a)} 张牌。`, en: `Each player shuffles their hand into their deck and draws ${argOf(a)} card(s).` }),
  shuffle_both_coin_draw: (a) => ({ zh: `双方将手牌洗回牌库，然后抛硬币决定各自抽牌数（你抽 ${argOf(a)} 张）。`, en: `Both players shuffle their hands into their decks, then flip a coin to set the draws (you draw ${argOf(a)}).` }),
  opp_shuffle_draw: { zh: '对手把手牌洗回并置于其牌库底。', en: 'Your opponent shuffles their hand and puts it on the bottom of their deck.' },
  opp_hand: (act) => ({
    zh: `查看对手手牌，并从中${act === 'discard' ? '弃置' : '置于其牌库底'}符合条件的卡。`,
    en: `Reveal your opponent’s hand and ${act === 'discard' ? 'discard' : 'put on the bottom of their deck'} matching cards.`,
  }),
  discard_opp_to: (a) => ({ zh: `对手弃牌，直到手牌剩 ${argOf(a)} 张。`, en: `Your opponent discards cards until they have ${argOf(a)} in hand.` }),
  discard_to_both: (a) => ({ zh: `双方各自弃牌，直到手牌剩 ${argOf(a)} 张（对手先弃）。`, en: `Each player discards until they have ${argOf(a)} cards in hand (opponent discards first).` }),
  discard_tool: (a) => ({ zh: `弃置至多 ${argOf(a)} 张双方宝可梦身上的道具。`, en: `Discard up to ${argOf(a)} Pokémon Tools attached to Pokémon (either side).` }),
  discard_tool_or_senergy: (a) => ({ zh: `弃置对手的 ${argOf(a)} 张道具或特殊能量。`, en: `Your opponent discards ${argOf(a)} Pokémon Tool or Special Energy.` }),
  energy_to_deck: (a) => ({ zh: `从弃牌堆把至多 ${argOf(a)} 张基本能量洗回牌库。`, en: `Shuffle up to ${argOf(a)} Basic Energy from your discard pile into your deck.` }),
  // 第八十二/八十三批（2026-11-05）：弃牌堆宝可梦洗回 / 检索后弃置
  pokemon_to_deck: (a) => ({ zh: `从弃牌堆把至多 ${argOf(a)} 张宝可梦洗回牌库。`, en: `Shuffle up to ${argOf(a)} Pokémon from your discard pile into your deck.` }),
  search_discard: (a) => ({ zh: `从牌库检索至多 ${argOf(a)} 张卡并直接弃置，然后洗牌。`, en: `Search your deck for up to ${argOf(a)} cards, discard them, then shuffle.` }),
  energy_move: (a) => ({ zh: `在己方宝可梦之间移动至多 ${argOf(a)} 个能量。`, en: `Move up to ${argOf(a)} Energy between your Pokémon.` }),
  recon: (a) => ({ zh: `查看牌库顶 ${argOf(a)} 张，取符合条件的牌加入手牌。`, en: `Look at the top ${argOf(a)} cards and take the matching ones into your hand.` }),
  search_bench: (a) => ({ zh: `从牌库检索至多 ${argOf(a)} 张基础宝可梦放到后备。`, en: `Search your deck for up to ${argOf(a)} Basic Pokémon and put them on the Bench.` }),
  attach: (a, _x, parts) => {
    const tv = (parts || []).find((x) => TY[x] && x !== 'any')
    const w = tv ? ty(tv) : ['基本', 'Basic']
    return { zh: `从弃牌堆附着 ${argOf(a)} 个${w[0]}能量。`, en: `Attach ${argOf(a)} ${w[1]} Energy from your discard pile.` }
  },
  attach_each_pick: (a) => ({ zh: `选择至多 ${argOf(a)} 只宝可梦，各为其检索并附着 1 个基本能量。`, en: `Choose up to ${argOf(a)} Pokémon; for each, search your deck for a Basic Energy and attach it.` }),
  bounce: { zh: '将 1 只宝可梦及其附属卡收回手牌。', en: 'Put 1 Pokémon and all attached cards back into your hand.' },
  switch_draw_to: (a) => ({ zh: `将己方战斗位与 1 只后备交换，然后抽牌直到手牌达到 ${argOf(a)} 张。`, en: `Switch your Active Pokémon with 1 Benched Pokémon, then draw until you have ${argOf(a)} cards.` }),
  coin_flip: { zh: '抛一次硬币，按正反面执行不同效果。', en: 'Flip a coin and resolve the matching branch.' },
  coin_gate_attach: (a) => ({ zh: `抛 2 枚硬币；若全为正面，从牌库检索并附着 ${argOf(a)} 个基本能量。`, en: `Flip 2 coins; if both are heads, search your deck for ${argOf(a)} Basic Energy and attach it.` }),
  status_multi: { zh: '使对手的战斗位宝可梦同时陷入多个特殊状态。', en: 'Give your opponent’s Active Pokémon multiple Special Conditions at once.' },
  fossil: (a) => ({ zh: `化石卡：可当作 ${argOf(a)} HP 的基础无色宝可梦上场。`, en: `Fossil: may be played as a ${argOf(a)}-HP Basic Colorless Pokémon.` }),
  team_boost: (a) => ({ zh: `本回合己方宝可梦的攻击额外造成 ${argOf(a)} 点伤害。`, en: `During this turn, your Pokémon’s attacks do ${argOf(a)} more damage.` }),
  team_shield: (a) => ({ zh: `对手的下一个回合，己方宝可梦受到的攻击伤害减少 ${argOf(a)} 点。`, en: `During your opponent’s next turn, your Pokémon take ${argOf(a)} less damage from attacks.` }),
  shield_choose: { zh: '选择 1 只己方宝可梦，使其本回合不受对手宝可梦招式效果影响。', en: 'Choose 1 of your Pokémon; it is unaffected by your opponent’s attack effects this turn.' },
  tool: (kind, num) => {
    if (kind === 'hp_plus') return { zh: `附着的宝可梦获得 ${argOf(num)} 点最大 HP。`, en: `The Pokémon this card is attached to gains ${argOf(num)} max HP.` }
    if (kind === 'survive') return { zh: '附着的宝可梦满 HP 时，可承受一次本应将其击倒的攻击伤害。', en: 'If the attached Pokémon has full HP, it survives damage that would knock it out.' }
    if (kind === 'on_damaged') return { zh: `附着的宝可梦在战斗位受到对手招式伤害时，抽 ${argOf(num)} 张牌。`, en: `When the attached Pokémon is damaged in the Active Spot, draw ${argOf(num)} card(s).` }
    if (kind === 'retreat_less') return { zh: `附着的宝可梦撤退费用减少 ${argOf(num)}。`, en: `The attached Pokémon’s Retreat Cost is ${argOf(num)} less.` }
    return { zh: '宝可梦道具：附着于己方宝可梦，持续生效。', en: 'Pokémon Tool: attach to your Pokémon for a lasting effect.' }
  },
  stadium: (_a, _mode, parts) => {
    const ps = parts || []
    const ns = numsOf(ps)
    const tv = ps.find((x) => TY[x])
    const w = tv ? ty(tv) : null
    if (ps.includes('heal_all')) {
      const n = ns[ns.length - 1] || 'n'
      return { zh: `竞技场：满足卡面条件时，回复己方全部宝可梦 ${n} 点伤害。`, en: `Stadium: when its condition is met, heal ${n} damage from all your Pokémon.` }
    }
    if (ps.includes('poison_extra')) {
      const n = ns[0] || 'n'
      return { zh: `竞技场：宝可梦检查时，中毒宝可梦额外放置 ${n} 个伤害指示物${w ? `（${w[0]}属性）` : ''}。`, en: `Stadium: during Pokémon Checkup, Poisoned Pokémon take ${n} extra damage counters${w ? ` (${w[1]})` : ''}.` }
    }
    if (ps.includes('dmg_reduce')) {
      const n = ns[0] || 'n'
      return { zh: `竞技场：${w ? w[0] : '特定'}属性宝可梦受到的攻击伤害减少 ${n} 点。`, en: `Stadium: ${w ? w[1] : 'certain'} Pokémon take ${n} less damage from attacks.` }
    }
    return { zh: '竞技场：持续生效的场地效果。', en: 'Stadium: a persistent field effect.' }
  },
}

/**
 * 训练师效果文案：先按「族名」（首个冒号前）查，再回退到完整 tag；带参数族代入参数。
 * @param {string} tag 引擎的 TrainerDef.tag，如 'boss' / 'recover:1:any'
 */
export function trainerDesc(tag) {
  const parts = String(tag || '').split(':')
  const [head, arg, extra] = parts
  const entry = TRAINER_TAG_DESC[head] || TRAINER_TAG_DESC[tag]
  if (!entry) {
    // ⚠ 兜底**必须经 cc() 解包成字符串**：直接返回 {zh,en} 对象会被 React 当作
    //   子节点渲染（error #31）→ 整棵树卸载 → 整屏黑屏（真实卡组必踩）。
    //   未收录的族如实标注 tag（C5 差异透明），不假装有说明。
    return cc({
      zh: `（效果说明未收录：${tag || '无 tag'}）`,
      en: `(description not catalogued: ${tag || 'no tag'})`,
    })
  }
  return cc(typeof entry === 'function' ? entry(arg, extra, parts) : entry)
}

// ---------------- 特性效果说明 ----------------
// 与 AbilityDef.tag 全集一一对应（前缀参数形如 recon:2 / cursed:13）
export function abilityDesc(tag) {
  const [head, arg, extra] = String(tag || '').split(':')
  const n = arg || 'n'
  switch (head) {
    case 'recon':
      return { zh: `查看牌库顶 ${n} 张，取 1 张加入手牌，其余放回牌库底。`, en: `Look at the top ${n} cards, take 1, put the rest on the bottom.` }
    case 'cursed':
      return { zh: `弃置自身，在对手 1 只宝可梦身上放置 ${n} 个伤害指示物。`, en: `Discard this Pokémon; place ${n} damage counters on 1 of the opponent's Pokémon.` }
    case 'draw':
      return { zh: `每回合 1 次：抽 ${n} 张牌。`, en: `Once per turn: draw ${n} cards.` }
    case 'move_damage':
      return { zh: `每回合 1 次：移动至多 ${n} 个伤害指示物。`, en: `Once per turn: move up to ${n} damage counters.` }
    case 'attach_energy':
      return { zh: `每回合 1 次：从弃牌堆或牌库附着 ${n} 个${extra ? typeLabel(extra) : ''}能量。`, en: `Once per turn: attach ${n} Energy from discard or deck.` }
    case 'ko_draw':
      return { zh: `被动：己方宝可梦被击倒时，抽 ${n} 张牌。`, en: `Passive: when your Pokémon is KO'd, draw ${n} cards.` }
    case 'weak_change':
      return { zh: `被动：在场时改写对手的弱点${extra ? `（限定 ${typeLabel(arg)} / ${extra}）` : ''}。`, en: `Passive: while in play, alters the opponent's Weakness.` }
    case 'unsupported':
      return { zh: '该特性尚未在引擎中建模。', en: 'Ability not yet modeled by the engine.' }
    default:
      return { zh: '（该特性暂无说明）', en: '(no description yet)' }
  }
}

// 效果标签（招式 tags / 特性 tag 的人类可读短名）
export const EFFECT_TAG_LABEL = {
  recon: { zh: '检索', en: 'Search' },
  cursed: { zh: '放伤害', en: 'Damage' },
  draw: { zh: '抽牌', en: 'Draw' },
  move_damage: { zh: '移伤害', en: 'Move dmg' },
  attach_energy: { zh: '附能量', en: 'Attach' },
  ko_draw: { zh: '被击倒抽牌', en: 'KO draw' },
  weak_change: { zh: '改弱点', en: 'Weakness' },
  unsupported: { zh: '未建模', en: 'N/A' },
  coin: { zh: '抛硬币', en: 'Coin flip' },
  heal: { zh: '回复', en: 'Heal' },
  status: { zh: '特殊状态', en: 'Status' },
  discard: { zh: '弃牌', en: 'Discard' },
  snipe: { zh: '狙击后备', en: 'Snipe' },
  bench_damage: { zh: '打后备', en: 'Bench dmg' },
}
export const effectTagLabel = (tag) => cc(EFFECT_TAG_LABEL[String(tag).split(':')[0]]
  || { zh: tag, en: tag })

// 特殊状态中文名
export const STATUS_LABEL = {
  poisoned: { zh: '中毒', en: 'Poisoned' },
  burned: { zh: '灼伤', en: 'Burned' },
  asleep: { zh: '睡眠', en: 'Asleep' },
  paralyzed: { zh: '麻痹', en: 'Paralyzed' },
  confused: { zh: '混乱', en: 'Confused' },
}
export const statusLabel = (s) => cc(STATUS_LABEL[s] || { zh: s, en: s })
