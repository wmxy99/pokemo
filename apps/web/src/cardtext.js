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
  if (typeof entry === 'function') return entry(lang)
  if (!entry) return ''
  return entry[lang] ?? entry.zh ?? ''
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
}

/**
 * 训练师效果文案：先按「族名」（首个冒号前）查，再回退到完整 tag；带参数族代入参数。
 * @param {string} tag 引擎的 TrainerDef.tag，如 'boss' / 'recover:1:any'
 */
export function trainerDesc(tag) {
  const parts = String(tag || '').split(':')
  const [head, arg, extra] = parts
  const entry = TRAINER_TAG_DESC[head] || TRAINER_TAG_DESC[tag]
  if (!entry) return { zh: '（该效果暂无说明）', en: '(no description yet)' }
  return cc(typeof entry === 'function' ? entry(arg, extra) : entry)
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
