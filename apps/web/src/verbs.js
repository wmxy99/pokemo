// verbs.json（与引擎 legal_actions 同源；UI 规范 §5.2 / §8.2）
// 严禁 emoji 作图标 —— 使用 SVG path（Lucide 风格 24×24 线框）
export const VERBS = {
  play: { zh: '上场', en: 'Play', icon: 'M12 5v14M5 12h14' },
  attach: { zh: '附能量', en: 'Attach', icon: 'M13 2 4.09 12.11a2 2 0 0 0 .13 2.7l5 4.5A2 2 0 0 0 20 17.9V4a2 2 0 0 0-2-2h-5Z' },
  attack: { zh: '攻击', en: 'Attack', icon: 'M14.5 17.5 3 6V3h3l11.5 11.5M13 19l6-6M16 16l4 4M19 21l2-2' },
  retreat: { zh: '撤退', en: 'Retreat', icon: 'M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3' },
  evolve: { zh: '进化', en: 'Evolve', icon: 'M12 19V5M5 12l7-7 7 7' },
  ability: { zh: '使用特性', en: 'Ability', icon: 'm12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z' },
  trainer: { zh: '训练家', en: 'Trainer', icon: 'M3 7h18v12H3zM7 7V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v2' },
  pass: { zh: '结束回合', en: 'End turn', icon: 'M5 4v16M19 4l-11 8 11 8V4Z' },
  // 决策阶段（`phase="choice"`）的两个动词：引擎在挂起时**只**接受这两个
  // （B4 任意数量 / 退化数量 / mulligan 补抽 / 开局上场 …）。缺这两项时按钮会
  // 直接显示英文原词（`choose`/`stop`），玩家看不懂该点哪个。
  choose: { zh: '选择', en: 'Choose', icon: 'M20 6 9 17l-5-5' },
  stop: { zh: '结束选择', en: 'Stop', icon: 'M6 6h12v12H6z' },
}

/** 动词按钮文案（中文）。ctx 可选：{hand[], field[], attacks[], energy} 展示名数组
 *  —— 有 ctx 时显示「中文卡名 / 招式名」，无 ctx 时回退到卡位序号形式。 */
export function verbLabel(a, ctx) {
  const v = VERBS[a.verb] || { zh: a.verb, en: a.verb }
  const zh = v.zh
  const c = ctx || {}
  const at = (arr, i) => (arr && i != null && arr[i] != null ? arr[i] : null)
  const slot = (i) => (i === 0 ? '战斗位' : `后备 ${i}`)
  if (a.verb === 'attack') {
    const nm = at(c.attacks, a.attack)
    return nm ? `${zh}：${nm}` : `${zh}：招式 #${a.attack ?? 0}`
  }
  if (a.verb === 'attach') {
    const where = slot(a.target ?? 0)
    return c.energy ? `${zh}：${c.energy} → ${where}` : `${zh} → 目标 #${a.target ?? 0}`
  }
  if (a.verb === 'evolve') {
    const card = at(c.hand, a.hand)          // 手牌中的进化卡
    const onto = at(c.field, a.target)       // 场上被进化的宝可梦
    // 读作「场上宝可梦 → 进化后」（更符合玩家心智模型）
    return card && onto ? `${zh}：${onto} → ${card}`
      : `${zh} → 卡位 #${a.hand ?? 0}`
  }
  if (a.verb === 'trainer') {
    const nm = at(c.hand, a.hand ?? a.index)
    return nm ? `${zh}：${nm}` : `${zh}：卡位 #${a.index ?? a.hand ?? 0}`
  }
  if (a.verb === 'play') {
    const nm = at(c.hand, a.index)
    return nm ? `${zh}：${nm}` : `${zh} #${a.index ?? 0}`
  }
  if (a.verb === 'retreat') {
    const to = at(c.field, (a.bench ?? 0) + 1)
    return to ? `${zh} → ${to}` : zh
  }
  if (a.verb === 'ability') return `${zh}：${a.name_zh || a.name || ''}`
  // 选择项：引擎已经给了**可读文案**（「抽 2 张」/「把 3 点能量洗回牌库，对 B1
  // 造成 120 伤害」/「保持常规铺场（Bunny 在战斗位）」）→ 直接用它；
  // 否则回退到「选择 #k」，绝不让玩家对着 "choose" 猜。
  if (a.verb === 'choose') {
    return a.display || `${zh} #${(a.pick ?? a.index ?? 0) + 1}`
  }
  if (a.verb === 'stop') return a.display || zh
  return zh
}
