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
  // 「弃置对手的特殊能量」（Enhanced Hammer）—— 引擎按 senergy 卡精确区分基本/特殊
  discard_energy: (a) => ({ zh: `弃置对手 1 只宝可梦身上的${argOf(a) === 'all' ? '全部' : ` ${argOf(a)} 个`}能量（special 表示只弃特殊能量）。`, en: `Discard ${argOf(a) === 'all' ? 'all' : argOf(a)} Energy from 1 of your opponent’s Pokémon (special = Special Energy only).` }),
  mill: (a) => ({ zh: `弃掉牌库顶 ${argOf(a)} 张卡。`, en: `Discard the top ${argOf(a)} card(s) of your deck.` }),
  draw: (a, _x, parts) => {
    if ((parts || []).includes('per')) {
      return { zh: `按场上计数抽牌（基数 ${argOf(a)} 张，每满足 1 个计数条件额外抽 1 张）。`, en: `Draw cards by a count on the field (base ${argOf(a)}, plus 1 per condition met).` }
    }
    return { zh: `抽 ${argOf(a)} 张牌。`, en: `Draw ${argOf(a)} card(s).` }
  },
  draw_to: (a) => ({ zh: `抽牌，直到手牌达到 ${argOf(a)} 张。`, en: `Draw cards until you have ${argOf(a)} in hand.` }),
  // 「Draw until N; if <条件>，改为抽到 M」（Ariana）
  draw_to_branch: (a, b, c) => ({ zh: `抽牌直到手牌 ${argOf(a)} 张；若满足条件（${argOf(c) || '全队同阵营'}），改为抽到 ${argOf(b)} 张。`, en: `Draw until you have ${argOf(a)}; if the condition holds, draw until ${argOf(b)} instead.` }),
  draw_cond: (_a, _x, parts) => {
    const ns = numsOf(parts)
    const base = ns[0] || 'n'
    const more = ns[1] || 'n'
    return { zh: `抽 ${base} 张牌；若满足卡面条件，额外抽 ${more} 张。`, en: `Draw ${base} card(s); if the condition is met, draw ${more} more.` }
  },
  shuffle_draw_split: (a) => ({ zh: `双方各自将手牌洗回牌库，然后各抽 ${argOf(a)} 张牌。`, en: `Each player shuffles their hand into their deck and draws ${argOf(a)} card(s).` }),
  shuffle_both_coin_draw: (a) => ({ zh: `双方将手牌洗回牌库，然后抛硬币决定各自抽牌数（你抽 ${argOf(a)} 张）。`, en: `Both players shuffle their hands into their decks, then flip a coin to set the draws (you draw ${argOf(a)}).` }),
  // D3（2026-11-08）：退化 devolve —— 把最高阶进化卡退回手牌，宝可梦本体降一级。
  // 卡面两种写法（对手最高阶 1 张 / 自己任意张）在文案里都要能读出来，
  // 否则 UI 只说「退化」，玩家不知道该退几张、退给谁。
  devolve: (a) => {
    const [scope, amt] = String(argOf(a) || 'self:1').split(':')
    const who = scope === 'opp' ? '对手的' : '自己的'
    const n = amt === 'any' ? '任意数量（由你选择）' : '最高阶 1 张'
    return {
      zh: `让${who} 1 只进化宝可梦退化：${n}进化卡放回手牌。`,
      en: `Devolve 1 of ${scope === 'opp' ? "your opponent's" : 'your'} evolved Pokémon: return ${amt === 'any' ? 'any number of' : 'the highest'} Evolution card(s) to its owner's hand.`,
    }
  },
  // S6a（2026-11-08）：**先手首回合的例外许可**（不是效果，是合法性标记）。
  // 卡面：「If you go first, you may use this card / can use this attack during
  // your first turn.」—— 复合 tag 的第二段会走到这里，必须有人话说明，
  // 否则 UI 上显示「（效果说明未收录）」把许可句藏起来（C5 差异透明不允许）。
  use_any: { zh: '若你先手，这一招／这张卡在你第一个回合也能使用。', en: 'If you go first, you can use this during your first turn.' },
  // S5（2026-11-08）：手牌放**牌库底**（不洗牌）+ 每人**各**抛一枚币分抽。
  // 与上一条只差「放底 vs 洗回」和「一枚 vs 每人一枚」，文案必须点明这两点 ——
  // 写成同一条会让玩家以为双方抽牌数必然相关（实际各自独立抛币）。
  shuffle_both_bottom_coin_draw: (a, b) => ({
    zh: `双方将手牌放到各自牌库底（不洗牌）；只要有任一方放了牌，双方就各自抛一枚硬币：正面抽 ${argOf(a)} 张，反面抽 ${argOf(b)} 张。`,
    en: `Each player puts their hand on the bottom of their deck (no shuffle). If either player did, each player flips a coin: heads draws ${argOf(a)}, tails draws ${argOf(b)}.`,
  }),
  // S5：抛币分岔检索（正面/反面找**不同进化阶段**的同一阵营宝可梦）
  coin_search: (a, b, parts) => {
    const stage = { evolution: ['进化形态', 'Evolution'], basic: ['基础形态', 'Basic'],
      stage1: ['1 阶进化', 'Stage 1'], stage2: ['2 阶进化', 'Stage 2'] }
    const h = (stage[a] || [a, a])[0]
    const t = (stage[b] || [b, b])[0]
    const he = (stage[a] || [a, a])[1]
    const te = (stage[b] || [b, b])[1]
    const ci = (parts || []).indexOf('category')
    const cat = ci >= 0 ? String((parts || [])[ci + 1] || '') : ''
    const catZh = cat ? `${cat} 阵营` : '指定阵营'
    return {
      zh: `抛一枚硬币：正面从牌库检索 1 张${catZh}的${h}宝可梦，反面检索 1 张${catZh}的${t}宝可梦，亮出后加入手牌，然后洗牌。`,
      en: `Flip a coin: heads searches your deck for 1 ${he} ${cat || 'matching'} Pokémon, tails for 1 ${te}, reveals it, and puts it into your hand. Then shuffle your deck.`,
    }
  },
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
  // 第八十五 / 八十九批（2026-11-05）：奖赏卡修正确（少拿 / 本回合多拿）
  ko_prize_minus: (_a) => ({ zh: '该宝可梦被击倒时，对手少拿奖赏卡（具体限定见卡面）。', en: 'When this Pokémon is Knocked Out, your opponent takes fewer Prize cards (conditions per card text).' }),
  // 第九十一 / 九十二批（2026-11-05）：训练家侧补齐的三个 tag 族
  shuffle_top: (a) => ({ zh: `查看牌库顶部 ${argOf(a)} 张，然后按任意顺序放回。`, en: `Look at the top ${argOf(a)} cards of your deck and put them back in any order.` }),
  evolve_from_deck: (_a) => ({ zh: '从牌库检索可进化己方宝可梦的卡，并直接使其进化，然后洗牌。', en: 'Search your deck for a card that evolves from 1 of your Pokémon and put it onto that Pokémon to evolve it, then shuffle.' }),
  discard_search_multi: (_a) => ({ zh: '弃掉全部手牌，然后从牌库按类别各取 1 张加入手牌，随后洗牌。', en: 'Discard your hand, then search your deck for 1 card of each listed kind and put them into your hand, then shuffle.' }),
  prize_bonus: (a) => ({ zh: `本回合内，对手战斗宝可梦被指定的己方宝可梦招式击倒时，多拿 ${argOf(a)} 张奖赏卡。`, en: `During this turn, take ${argOf(a)} more Prize card(s) when the opponent’s Active is Knocked Out by the specified Pokémon’s attack.` }),
  energy_move: (a) => ({ zh: `在己方宝可梦之间移动至多 ${argOf(a)} 个能量。`, en: `Move up to ${argOf(a)} Energy between your Pokémon.` }),
  recon: (a) => ({ zh: `查看牌库顶 ${argOf(a)} 张，取符合条件的牌加入手牌。`, en: `Look at the top ${argOf(a)} cards and take the matching ones into your hand.` }),
  choose: () => ({ zh: '打出后由你选择要弃掉哪些手牌（可随时结束选择），然后抽牌到指定张数。', en: 'Choose any number of cards to discard from your hand (you may stop at any time), then draw up to the stated number.' }),
  recon_bottom: (a) => ({ zh: `查看牌库底 ${argOf(a)} 张，可取其中 1 张宝可梦加入手牌，其余洗回牌库。`, en: `Look at the bottom ${argOf(a)} cards, take a Pokémon into your hand, then shuffle the rest back.` }),
  discard_tool_senergy: () => ({ zh: '弃掉对手 1 只宝可梦身上的 1 张道具与 1 张特殊能量。', en: 'Discard a Pokémon Tool and a Special Energy from 1 of your opponent’s Pokémon.' }),
  // F7（2026-11-08）：Megaton Blower —— 全场道具 + 全部特殊能量（可选带弃场地）。
  // 与上面的 `discard_tool_senergy`（同一只身上各 1 张）**不是**一回事，
  // 文案必须分开，否则玩家会按「只弃 2 张」理解。
  discard_tool_senergy_all: (_a, _b, parts) => {
    const withStadium = (parts || []).includes('stadium')
    return {
      zh: '弃置对手所有宝可梦身上的全部道具与特殊能量' + (withStadium ? '，并弃置场上 1 张竞技场卡。' : '。'),
      en: 'Discard all Pokémon Tools and Special Energy from all of your opponent’s Pokémon' + (withStadium ? ', and discard a Stadium in play.' : '.'),
    }
  },
  // S3（2026-11-08）区域对象化族：奖赏卡重发 / 翻面对手奖赏卡 / 亮出对手牌库顶
  prize: (a, _x, parts) => {
    if ((parts || []).includes('redeal')) {
      return { zh: '数出你的奖赏卡，洗匀后放到牌库底；再从牌库顶取同样张数背面朝下作为新的奖赏卡。', en: 'Count your Prize cards, shuffle them onto the bottom of your deck, then take that many from the top as new face-down Prize cards.' }
    }
    if ((parts || []).includes('flip_opp')) {
      return { zh: '将对手 1 张背面朝上的奖赏卡翻到正面，并随机查看对手 1 张手牌；你可以让对手交换这两张卡（该奖赏位保持正面朝上）。', en: 'Turn 1 of your opponent’s face-down Prize cards face up and reveal a random card from their hand; you may have them switch those cards (that Prize slot stays face up).' }
    }
    return { zh: `奖赏卡操作（${a || '未知'}）。`, en: `Prize-card operation (${a || 'unknown'}).` }
  },
  // S2c（2026-11-08）：**订阅式道具**（Amulet of Hope / Powerglass）——
  // 卡面只写触发条件与效果，这里按事件点给出可读说明（C1/C5 自述转写）。
  on_event: (a, b, parts) => {
    const when = { ko: '所附宝可梦被招式击倒时', turn_end: '你的回合末（在战斗位时）',
                   switch_out: '对手换下战斗位时', switch_in: '对手换上新战斗位时',
                   evolve: '对手进化时' }[a] || a || '触发时'
    // S2d：`move_energy_to_bench` 的张数在 parts[3]（`……:move_energy_to_bench:3:…`）
    const cnt = (parts || [])[3]
    const what = { search_any: `从牌库检索 ${b || 'N'} 张卡加入手牌`, attach_basic: '可从弃牌堆附着 1 个基本能量',
                   counters: `放置 ${b || 'N'} 个伤害指示物`, status: '施加特殊状态',
                   move_energy_to_bench: `把最多 ${cnt || 'N'} 张基本能量从那只搬到你的后备（自由分配）` }[b] || (b || '按卡面结算')
    // `require:` 限定词也要显示 —— 少写就等于把条件藏起来（C5 差异透明不允许）
    const reqs = []
    const rp = parts || []
    for (let i = 0; i < rp.length; i += 1) {
      if (rp[i] === 'require' && rp[i + 1] === 'retreat') reqs.push(`撤退费恰好 ${rp[i + 2]}`)
      if (rp[i] === 'require' && rp[i + 1] === 'was_active') reqs.push('须在战斗位')
    }
    const tail = reqs.length ? `（条件：${reqs.join('、')}）` : ''
    return { zh: `${when}${tail}：${what}。`, en: `When ${a}: ${b}.` }
  },
  flute: (a) => ({ zh: `亮出对手牌库顶 ${argOf(a)} 张，可将其中任意数量的基础宝可梦放到对手的后备；其余洗回对手牌库。`, en: `Reveal the top ${argOf(a)} cards of your opponent’s deck; put any number of Basic Pokémon found there onto their Bench, then shuffle the rest back.` }),
  // S2（2026-11-08）：**延迟生效**（Amarys「At the end of this turn, …」）
  queue: (a, _x, parts) => {
    const when = a === 'turn_end' ? '本回合结束' : (a === 'opp_next_turn_end' ? '对手的下个回合结束' : '稍后')
    return { zh: `${when}时执行后续效果（${(parts || []).slice(2).join(':') || '—'}）。`, en: `Delayed effect, resolved ${when === '本回合结束' ? 'at the end of this turn' : 'later'} (${(parts || []).slice(2).join(':') || '—'}).` }
  },
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
  tool: (kind, num, parts) => {
    if (kind === 'hp_plus') return { zh: `附着的宝可梦获得 ${argOf(num)} 点最大 HP。`, en: `The Pokémon this card is attached to gains ${argOf(num)} max HP.` }
    if (kind === 'survive') return { zh: '附着的宝可梦满 HP 时，可承受一次本应将其击倒的攻击伤害。', en: 'If the attached Pokémon has full HP, it survives damage that would knock it out.' }
    if (kind === 'on_damaged') return { zh: `附着的宝可梦在战斗位受到对手招式伤害时，抽 ${argOf(num)} 张牌。`, en: `When the attached Pokémon is damaged in the Active Spot, draw ${argOf(num)} card(s).` }
    if (kind === 'retreat_less') return { zh: `附着的宝可梦撤退费用减少 ${argOf(num)}。`, en: `The attached Pokémon’s Retreat Cost is ${argOf(num)} less.` }
    // F7（2026-11-08）：道具的两个**静态修正**（可同时存在，`a|b` 复合）
    if (kind === 'cost_less') {
      const holder = (parts || []).find((x) => x === 'holder')
      return {
        zh: `附着的宝可梦使用的招式费用减少 ${argOf(num)}${holder ? '（仅限卡面指定的持有者）' : ''}。`,
        en: `Attacks used by the attached Pokémon cost ${argOf(num)} less${holder ? ' (specified holder only)' : ''}.`,
      }
    }
    if (kind === 'dmg_plus') {
      const holder = (parts || []).includes('holder')
      return {
        zh: `附着的宝可梦使用的招式对对手战斗宝可梦造成的伤害 +${argOf(num)}${holder ? '（仅限卡面指定的持有者）' : ''}（在计算弱点与抗性之前）。`,
        en: `Attacks used by the attached Pokémon do ${argOf(num)} more damage to your opponent’s Active Pokémon${holder ? ' (specified holder only)' : ''} (before applying Weakness and Resistance).`,
      }
    }
    // F7：Technical Machine 类（招式写在道具卡上，只有指定持有者能用）
    if (kind === 'provide_attack') {
      return {
        zh: '附着的指定宝可梦可以使用此卡上的招式（仍需支付该招式的能量费用）。',
        en: 'The specified Pokémon this card is attached to can use the attack on this card (the necessary Energy is still required).',
      }
    }
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
  // S2（2026-11-08）：**复合 tag**（`a|b`，一句两动作）必须两段都说明 ——
  // 只显示第一段 = 静默隐藏后半句（C5「差异透明」不允许）。`coin_flip:` 的
  // `|` 是同一枚币的两面，不拆。
  const _raw = String(tag || '')
  if (_raw.includes('|') && !_raw.startsWith('coin')) {
    return _raw.split('|').filter(Boolean).map(trainerDesc)
      .join(cc({ zh: '；', en: '; ' }))
  }
  const parts = _raw.split(':')
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
    case 'deck_bottom_discard':
      // S3（2026-11-08）：Misty's Psyduck「Flustered Leap」——两段式复合 tag
      return { zh: `每回合 1 次（仅限后备）：弃掉牌库底 1 张卡，然后弃掉本只身上的所有卡并把它放到牌库顶。`, en: `Once per turn (Bench only): discard the bottom card of your deck, then discard all cards from this Pokémon and put it on top of your deck.` }
    case 'choose':
      // S1c：手牌与牌库顶互换（Gumshoos「Evidence Gathering」）
      return { zh: `每回合 1 次：将手牌中的 1 张卡与牌库顶的卡交换（由你选择哪一张）。`, en: `Once per turn: switch a card from your hand with the top card of your deck (you choose which).` }
    case 'evolve_act':
      // S2b：**进化触发**（Archaludon ex「Assemble Alloy」/ Grumpig「Energized Steps」）
      return { zh: `进化时触发一次：${String(tag).split(':').slice(1).join(':') || '按卡面结算'}。`, en: `Triggers once when this Pokémon evolves: ${String(tag).split(':').slice(1).join(':') || 'see card text'}.` }
    case 'on_event': {
      // S2/S2e（2026-11-08）：**事件订阅**。文案要按 tag 的三段实际内容说清，
      // 不能一律套「对手换下」——S2e 的 Latios / Palafin 也是 on_event，
      // 套旧文案就是把卡面说错（C1/C5 差异透明不允许）。
      const ps = String(tag || '').split(':')
      const kind = ps[1] || ''
      const body = ps[2] || ''
      const may = ps.includes('may') ? '（可选）' : ''
      if (kind === 'switch_in' && body === 'pick_energy') {
        const nm = ps.includes('event_name') ? ps[ps.indexOf('event_name') + 1] : '指定宝可梦'
        return { zh: `每回合 1 次：当你的 ${nm} 从后备上场时${may}，可将后备宝可梦身上的任意数量能量移到战斗位。`, en: `Once during your turn, when your ${nm} moves to the Active Spot, you may move any amount of Energy from your Benched Pokémon to your Active Pokémon.` }
      }
      if (kind === 'switch_out' && body === 'replace_self') {
        const nm = ps[3] || '指定卡'
        return { zh: `每回合 1 次：当此宝可梦从战斗位下场时${may}，可从牌库检索 ${nm} 与之交换；附着卡、伤害指示物、特殊状态与所有效果保留在新宝可梦身上，此卡洗回牌库。`, en: `Once during your turn, when this Pokémon moves to the Bench, you may search your deck for a ${nm} and switch it with this Pokémon; all attached cards, damage counters, Special Conditions and other effects remain, then put this card into your deck.` }
      }
      if (kind === 'ko') {
        return { zh: `被动：所附宝可梦被招式击倒时，结算后续效果（${body || '按卡面'}）。`, en: `Passive: when the attached Pokémon is KO'd by an attack, resolve the subscribed effect.` }
      }
      if (kind === 'turn_end') {
        return { zh: `被动：你的回合末结算后续效果（${body || '按卡面'}）。`, en: `Passive: resolves at the end of your turn.` }
      }
      return { zh: `被动：对手的宝可梦在其回合换下战斗位时，结算后续效果（${extra || '换下目标'}）。`, en: `Passive: when your opponent's Active Pokémon moves to the Bench during their turn, resolve the subscribed effect.` }
    }
    case 'fx': {
      // S4（2026-11-08）：`fx:effect_immune:*` 的四种形态要说清范围与来源 ——
      // 一律套「未建模」文案就是把已实现的能力说成不存在（C1 自述不实）。
      const fp = String(tag || '').split(':')
      if (fp[1] === 'effect_immune') {
        const src = fp[2]
        if (src === 'trainer') {
          const kinds = fp[3] === 'supporter' ? '支援者卡' : '道具卡或支援者卡'
          return { zh: `对手从手牌打出${kinds}时，防止该卡对此宝可梦造成的所有效果。`, en: `Whenever your opponent plays ${fp[3] === 'supporter' ? 'a Supporter' : 'an Item or Supporter'} card from their hand, prevent all effects of that card done to this Pokémon.` }
        }
        const team = fp[3] === 'team'
        const srcZh = src === 'ability' ? '特性' : '招式'
        if (team) {
          return { zh: `对手宝可梦的${srcZh}效果不作用于你的基础火箭队宝可梦（伤害不受影响）。`, en: `Prevent all effects of your opponent's Pokémon's ${src === 'ability' ? 'Abilities' : 'attacks'} done to your Basic Team Rocket's Pokémon (damage is not an effect).` }
        }
        return { zh: `对手宝可梦的${srcZh}效果不作用于此宝可梦（伤害不受影响）。`, en: `Prevent all effects of your opponent's Pokémon's ${src === 'ability' ? 'Abilities' : 'attacks'} done to this Pokémon.` }
      }
      return { zh: `静态效果：${String(tag).split(':').slice(1).join(':') || '按卡面结算'}。`, en: `Static effect: ${String(tag).split(':').slice(1).join(':') || 'see card text'}.` }
    }
    case 'passive':
      // S2e：`passive:no_evolve_into`（Palafin ex「Hero's Spirit」）
      if (String(tag).includes('no_evolve_into')) {
        return { zh: '此宝可梦只能由卡面指定的效果放入场（不能从手牌进化上场）。', en: 'Put this Pokémon into play only with the effect named on the card (not by evolving from your hand).' }
      }
      return { zh: `被动：${String(tag).split(':').slice(1).join(':') || '按卡面结算'}。`, en: `Passive: ${String(tag).split(':').slice(1).join(':') || 'see card text'}.` }
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
  // S3（2026-11-08）：完美信息等价类（查看对手奖赏卡 / 展示手牌）
  info: { zh: '查看信息', en: 'Info' },
  prize: { zh: '奖赏卡', en: 'Prize' },
  flute: { zh: '对手牌库', en: 'Opp deck' },
  deck_bottom_discard: { zh: '弃牌库底', en: 'Deck bottom' },
  // S2b（2026-11-08）
  evolve_act: { zh: '进化触发', en: 'On evolve' },
  on_event: { zh: '事件订阅', en: 'Event' },
  shield: { zh: '护盾/限时', en: 'Shield' },
  // S2c（2026-11-08）：击倒订阅 / 回合末被动道具 / 延迟弃置
  ko: { zh: '被击倒时', en: 'On KO' },
  turn_end: { zh: '回合末', en: 'Turn end' },
  attach_basic: { zh: '附基本能量', en: 'Attach Basic' },
  search_any: { zh: '检索任意卡', en: 'Search any' },
  discard_defender: { zh: '弃置战斗位', en: 'Discard Defender' },
  // S2d（2026-11-08）：限时订阅 / 撤退费条件 / 具名能量
  arm: { zh: '限时登记', en: 'Arm' },
  move_energy_to_bench: { zh: '能量搬到后备', en: 'Move energy' },
  attach_named: { zh: '附具名能量', en: 'Attach named' },
  // S2e（2026-11-08）：可选触发 / 原地替换 / 只能由效果上场
  pick_energy: { zh: '搬能量', en: 'Move energy' },
  replace_self: { zh: '原地替换', en: 'Replace self' },
  passive: { zh: '被动', en: 'Passive' },
  fx: { zh: '静态效果', en: 'Static' },
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
