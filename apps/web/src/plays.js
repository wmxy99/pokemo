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
 * 目标语义按 verb + tag 区分（与 engine/battle.py 的构造一一对应）：
 *   attach            target 0 = 我方战斗位，1.. = 我方后备
 *   evolve            target 0 = 我方战斗位，1.. = 我方后备
 *   trainer + boss    target = **对手**后备索引
 *   trainer + switch_self / recover 等 → 见 _trainerTarget
 */

const isEnergy = (card) => card && card.type === 'energy'

/** 手牌第 idx 张可发起的动作（attach 不带手牌索引 —— 任何能量卡都可用于附着） */
export function handActions(idx, card, legal) {
  return (legal || []).filter((a) => {
    if (a.verb === 'play') return a.index === idx
    if (a.verb === 'evolve') return a.hand === idx
    if (a.verb === 'trainer') return (a.hand ?? a.index) === idx
    if (a.verb === 'attach') return isEnergy(card)
    return false
  })
}

/** 训练家动作的目标落在哪个区域 */
function trainerTarget(tag, view) {
  const t = String(tag || '')
  if (t === 'boss') return 'opp-bench'
  if (t.startsWith('recover')) return 'discard'
  if (t.startsWith('search') || t.startsWith('discard_search')) return 'deck'
  // switch_self 及未列举者按「我方后备」处理（引擎侧的 target 就是这个含义）
  return 'my-bench'
}

/** 目标区域的可读名 */
export const WHERE_LABEL = {
  'my-bench': '我方后备',
  'opp-bench': '对手后备',
  discard: '弃牌堆',
  deck: '牌库',
}

/** 把一条需要目标动作翻译成「可选目标」描述 */
function describeTarget(a, view) {
  const where = a.verb === 'attach' || a.verb === 'evolve'
    ? (a.target === 0 ? 'my-active' : 'my-bench')
    : trainerTarget(a.tag, view)
  const pos = a.target ?? 0
  return { where, pos, label: whereLabelOf(where, pos, view) }
}

function whereLabelOf(where, pos, view) {
  const me = view?.me_state || {}
  const opp = view?.opp_state || {}
  if (where === 'my-active') return `我方战斗位 · ${nameOf(me.active)}`
  if (where === 'my-bench') return `我方${slotName(pos)} · ${nameOf((me.bench || [])[pos - 1])}`
  if (where === 'opp-bench') return `对手${slotName(pos)} · ${nameOf((opp.bench || [])[pos])}`
  if (where === 'discard') return `弃牌堆第 ${pos + 1} 张`
  return WHERE_LABEL[where] || where
}

function slotName(pos) {
  return pos === 0 ? '战斗位' : `后备 ${pos}`
}

function nameOf(p) {
  if (!p) return '（空）'
  return p.display || p.name_zh || p.name || '—'
}

/**
 * 给手牌第 idx 张生成执行计划
 * @returns {{kind:'instant'|'target'|'none', action?, targets?:[], reason?}}
 */
export function planForHand(idx, card, legal, view) {
  const acts = handActions(idx, card, legal)
  if (acts.length === 0) {
    return { kind: 'none', reason: reasonUnavailable(card, view) }
  }
  // 无需目标的动作优先直接执行
  const plain = acts.find((a) => a.target === undefined && !needsTarget(a))
  if (plain) return { kind: 'instant', action: plain }

  // 需要目标：全部候选（同 verb + 同手牌位）
  const withTarget = acts.filter((a) => a.target !== undefined || needsTarget(a))
  if (withTarget.length === 1) {
    const only = withTarget[0]
    // 唯一目标时也不必再点一次：直接执行（例如只有战斗位可附能量）
    return { kind: 'instant', action: only, autoTarget: describeTarget(only, view) }
  }
  return {
    kind: 'target',
    verb: withTarget[0].verb,
    targets: withTarget.map((a) => ({ action: a, ...describeTarget(a, view) })),
  }
}

function needsTarget(a) {
  // 训练家里有些 tag 天然需要目标（bos/switch_self/recover）；由引擎决定是否有 target 字段
  return a.target !== undefined
}

function reasonUnavailable(card, view) {
  if (!card) return '这张牌当前不可用'
  const v = view || {}
  const me = v.me_state || {}
  if (card.type === 'energy') {
    if ((me.attached_energy_this_turn ?? 0) > 0) return '本回合已经附过能量了'
    return '没有可附能量的宝可梦'
  }
  if (card.type === 'trainer') {
    if ((me.supporter_used) && card.type === 'supporter') return '本回合已经用过支援者卡'
    return '当前局面不满足这张卡的使用条件'
  }
  if (card.type === 'pokemon') {
    if (card.stage > 0) return '需要先有可进化的宝可梦在场'
    return '后备已满，无法再上宝可梦'
  }
  return '当前不可用'
}

/** 场上宝可梦（战斗位）可执行的动作：招式 / 撤退 / 特性 */
export function activeActions(legal) {
  return (legal || []).filter((a) => ['attack', 'retreat', 'ability'].includes(a.verb))
}

/** 场上宝可梦（任意位置）可执行的特性动作 */
export function abilityActionsAt(legal, pos) {
  return (legal || []).filter((a) => a.verb === 'ability' && (a.pos ?? 0) === pos)
}

/** 某手牌索引是否「现在就能用」（用于给卡面加可用标记） */
export function handUsable(idx, card, legal) {
  return handActions(idx, card, legal).length > 0
}

/** 结束回合动作 */
export function passAction(legal) {
  return (legal || []).find((a) => a.verb === 'pass')
}
