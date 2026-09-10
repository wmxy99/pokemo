/**
 * PokéLab AI · 展示名工具（i18n 中文优先）
 *
 * 约定：**英文名是数据键**（引擎 view 的 `name`、API 字段、Limitless 文本匹配都用英文），
 * 展示层一律取 `display`（中文，引擎已下发）→ `name_zh` → `name` 回退。
 */

/** 单个对象（宝可梦/手牌/招式/特性）的展示名 */
export function disp(o, fallback = '?') {
  if (!o) return fallback
  return o.display || o.name_zh || o.name || o.card_id || fallback
}

/** 卡组名展示：优先中文（API 已把 label 本地化，这里只做兜底） */
export function deckDisp(oOrName, fallback = '?') {
  if (!oOrName) return fallback
  if (typeof oOrName === 'string') return oOrName
  return oOrName.label || oOrName.label_zh || oOrName.name || fallback
}

/** 矩阵格子用短标签：A1 · 多龙巴鲁托ex / 黑夜魔灵 → 多龙巴鲁托ex/黑夜魔灵 */
export function shortDeckLabel(zh, en) {
  const base = (zh || en || '').replace(/^A\d\s*·\s*/, '')
  const inParen = base.match(/^(.+?)（/)?.[1]
  const core = (inParen || base).replace(/\s+/g, '')
  return core.length > 18 ? core.slice(0, 17) + '…' : core
}

/** 从 view 构建动作名解析上下文（供动词按钮显示中文招式/卡名） */
export function actionContext(view) {
  const me = view?.me_state || {}
  const hand = (me.hand || []).map((c) => disp(c))
  const field = [me.active, ...(me.bench || [])]
    .filter(Boolean).map((p) => disp(p))
  const attacks = (me.active?.attacks || []).map((a) => disp(a))
  const energy = (me.hand || []).find((c) => c.type === 'energy')
  return { hand, field, attacks, energy: energy ? disp(energy) : '能量' }
}
