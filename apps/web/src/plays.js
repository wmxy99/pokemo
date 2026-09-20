/**
 * 「点牌出牌」：把手牌 / 场上的卡映射成玩家视角的可执行操作
 *
 * 引擎的 legal_actions 是一张**扁平动作表**（9 种 verb），而玩家心智是
 * 「我点这张牌 → 告诉我能干什么」。本模块负责这层翻译，并把结果分成三类：
 *
 *   instant  无歧义，点了直接执行（如「上基础宝可梦到后备」）
 *   target   需要再选一个目标（进化对象 / 附能量目标 / 拉对手后备 / 弃牌堆回收）
 *   none     这张牌当前不可用（给出原因）
 *
 * ⚠ 引擎的 `target` 有**两种语义**，混用会导致「高亮错卡」（踩过，实测 4 处错配）：
 *
 *   attach / evolve            target = **槽位**：0 = 战斗位，1.. = 我方后备
 *   trainer  switch_self       target = **我方后备下标**（0 起）
 *   trainer  boss              target = **对手后备下标**（0 起）
 *   trainer  recover:N:...     target = 弃牌堆下标（0 起）
 *   ability  cursed:N（手牌）   target = **对手槽位**：0 = 对手战斗位，1.. = 对手后备
 *
 * 因此本模块统一把目标归一化成 `slot`（**槽位语义**：0 = 战斗位，i+1 = 后备 i），
 * 高亮 / 点选匹配 / 文案一律用 `slot`；原始 `target` 原样挂在 action 上，
 * 提交给引擎时**必须用它**（引擎只认自己的语义）。
 */

// 训练家卡判定与卡面渲染共用同一事实源（引擎给的 type 是 tdef.kind，不是 'trainer'）
import { isTrainerCard } from './cardtext.js'

const isEnergy = (card) => card && card.type === 'energy'

const FIELD_WHERE = ['my-active', 'my-bench', 'opp-active', 'opp-bench']

/** 手牌第 idx 张可发起的动作（attach 不带手牌索引 —— 任何能量卡都可用于附着） */
export function handActions(idx, card, legal) {
  return (legal || []).filter((a) => {
    if (a.verb === 'play') return a.index === idx
    if (a.verb === 'evolve') return a.hand === idx
    if (a.verb === 'trainer') return (a.hand ?? a.index) === idx
    // 手牌发动特性（location='hand'，如 SPECTOR 的「诅咒爆破」）带 hand 索引
    if (a.verb === 'ability') return a.hand === idx
    if (a.verb === 'attach') return isEnergy(card)
    return false
  })
}

/** 训练家动作的目标落在哪个区域（region 名，非槽位） */
function trainerTarget(tag) {
  const t = String(tag || '')
  if (t === 'boss') return 'opp-bench'
  if (t.startsWith('recover')) return 'discard'
  if (t.startsWith('search') || t.startsWith('discard_search')) return 'deck'
  // switch_self 及未列举者按「我方后备」处理（引擎侧的 target 就是这个含义）
  return 'my-bench'
}

/** 目标区域的可读名 */
export const WHERE_LABEL = {
  'my-active': '我方战斗位',
  'my-bench': '我方后备',
  'opp-active': '对手战斗位',
  'opp-bench': '对手后备',
  discard: '弃牌堆',
  deck: '牌库',
}

/**
 * 把一条需要目标的动作翻译成「可选目标」描述
 *
 * @returns {{where:string, slot:number, target:number, label:string}}
 *   where  区域（my-active / my-bench / opp-active / opp-bench / discard / deck）
 *   slot   **槽位**（0 = 战斗位，i+1 = 后备 i）—— 高亮与点选匹配用它
 *   target 引擎原始 target —— 提交动作时用它
 *   label  给玩家看的中文目标文案
 */
export function describeActionTarget(a, view) {
  const raw = a.target ?? 0

  // 手牌特性（cursed:N）：作用对象是**对手场上**，且已是槽位语义
  if (a.verb === 'ability') {
    const where = raw === 0 ? 'opp-active' : 'opp-bench'
    return { where, slot: raw, target: raw, label: whereLabelOf(where, raw, view) }
  }
  // 附能量 / 进化：已经是槽位语义（0 = 战斗位）
  if (a.verb === 'attach' || a.verb === 'evolve') {
    const where = raw === 0 ? 'my-active' : 'my-bench'
    return { where, slot: raw, target: raw, label: whereLabelOf(where, raw, view) }
  }
  const region = trainerTarget(a.tag)
  // boss / switch_self 给的是**后备下标**（0 起）→ 换算成槽位
  if (region === 'my-bench' || region === 'opp-bench') {
    const slot = raw + 1
    return { where: region, slot, target: raw, label: whereLabelOf(region, slot, view) }
  }
  return { where: region, slot: raw, target: raw, label: whereLabelOf(region, raw, view) }
}

function whereLabelOf(where, slot, view) {
  const me = view?.me_state || {}
  const opp = view?.opp_state || {}
  if (where === 'my-active') return `我方战斗位 · ${nameOf(me.active)}`
  if (where === 'my-bench') return `我方${slotName(slot)} · ${nameOf((me.bench || [])[slot - 1])}`
  if (where === 'opp-active') return `对手战斗位 · ${nameOf(opp.active)}`
  if (where === 'opp-bench') return `对手${slotName(slot)} · ${nameOf((opp.bench || [])[slot - 1])}`
  if (where === 'discard') return `弃牌堆第 ${slot + 1} 张`
  return WHERE_LABEL[where] || where
}

function slotName(slot) {
  return slot === 0 ? '战斗位' : `后备 ${slot}`
}

function nameOf(p) {
  if (!p) return '（空）'
  return p.display || p.name_zh || p.name || '—'
}

/**
 * 给手牌第 idx 张生成执行计划
 * @returns {{kind:'instant'|'target'|'none', action?, autoTarget?, targets?, reason?}}
 */
export function planForHand(idx, card, legal, view) {
  const acts = handActions(idx, card, legal)
  if (acts.length === 0) {
    return { kind: 'none', reason: reasonUnavailable(card, view) }
  }
  // 无需目标的动作优先直接执行（上后备 / 一次性道具 / 抽牌支援者）
  const plain = acts.find((a) => a.target === undefined)
  if (plain) return { kind: 'instant', action: plain }

  // 需要目标：全部候选（同手牌位）
  const withTarget = acts.filter((a) => a.target !== undefined)
  if (withTarget.length === 1) {
    // 唯一目标时不必再点一次（例如只有战斗位可附能量）
    const only = withTarget[0]
    return { kind: 'instant', action: only, autoTarget: describeActionTarget(only, view) }
  }
  return {
    kind: 'target',
    verb: withTarget[0].verb,
    targets: withTarget.map((a) => ({ action: a, ...describeActionTarget(a, view) })),
  }
}

/** 该动作的目标是否落在场上（需要高亮 / 点选） */
export function isFieldTarget(where) {
  return FIELD_WHERE.includes(where)
}

/** 目标属于哪一方（'me' | 'opp'） */
export function targetSide(where) {
  return String(where || '').startsWith('opp') ? 'opp' : 'me'
}

function reasonUnavailable(card, view) {
  if (!card) return '这张牌当前不可用'
  const me = (view || {}).me_state || {}
  if (card.type === 'energy') {
    if ((me.attached_energy_this_turn ?? 0) > 0) return '本回合已经附过能量了'
    return '没有可附能量的宝可梦'
  }
  if (isTrainerCard(card)) {
    if (card.type === 'supporter' && me.supporter_used) return '本回合已经用过支援者卡'
    return '当前局面不满足这张卡的使用条件'
  }
  if (card.type === 'pokemon') {
    if ((card.stage ?? 0) > 0) return '场上没有可进化的目标（需先有对应的进化前宝可梦）'
    return '后备已满，无法再上宝可梦'
  }
  return '当前不可用'
}

/** 某手牌索引是否「现在就能用」（用于给卡面加可用标记） */
export function handUsable(idx, card, legal) {
  return handActions(idx, card, legal).length > 0
}

/** 结束回合动作 */
export function passAction(legal) {
  return (legal || []).find((a) => a.verb === 'pass')
}
