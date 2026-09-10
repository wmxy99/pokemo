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
}

export function verbLabel(a) {
  const v = VERBS[a.verb] || { zh: a.verb, en: a.verb }
  const zh = v.zh
  if (a.verb === 'attack') return `${zh}：招式${a.attack != null ? ' #' + a.attack : ''}`
  if (a.verb === 'attach') return `${zh} → 目标${a.target != null ? ' #' + a.target : ''}`
  if (a.verb === 'evolve') return `${zh} → 卡位${a.index != null ? ' #' + a.index : ''}`
  if (a.verb === 'trainer') return `${zh}：卡位${a.index != null ? ' #' + a.index : ''}`
  if (a.verb === 'play') return `${zh} #${a.index ?? 0}`
  return zh
}
