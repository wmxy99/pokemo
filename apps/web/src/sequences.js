/**
 * 组合指令的前端**预览**切分（与引擎 `engine.commands.split_sequence` 同规则）
 *
 * ⚠️ 仅用于 UI 提示「将执行 N 步」；**真实解析以引擎为准**
 * （引擎还会按最新 legal_actions 逐步重解析，前端无法也不应模拟那部分）。
 */

// 强分隔词（与 engine/commands.py 的 SEQ_SEPARATORS 对齐）
const STRONG = ['然后', '接著', '接着', '之后', '之後', '而后', '再到', 'then', ';', '；', '\n']

// 动词别名前缀（用于判断箭头是否切分，与引擎 VERB_ALIASES 对齐）
const VERB_PREFIXES = [
  '结束回合', '结束', '过', 'pass', 'endturn',
  '撤退', '换人', '后撤', 'retreat',
  '附能量', '贴能量', '上能量', '充能', '附能', 'attach',
  '攻击', '出招', '打', 'attack', '招式',
  '训练家', '道具', '支援者', '使用卡', 'trainer', 'item',
  '特性', '能力', 'ability',
  '上场', '放置', '打出', 'play',
  '进化', 'evolve',
]

const norm = (s) => String(s ?? '').toLowerCase().replace(/[\s：:，,。.、\-_/\\（）()\[\]「」【】'"]+/g, '')

function looksLikeChain(parts) {
  const segs = parts.filter(Boolean)
  if (segs.length < 2) return false
  return segs.slice(1).every((seg) => {
    const q = norm(seg)
    return q && VERB_PREFIXES.some((v) => q.startsWith(norm(v)))
  })
}

/** 文本 → 步骤数组（预览用） */
export function splitSequence(text) {
  if (Array.isArray(text)) return text.map((x) => String(x).trim()).filter(Boolean)
  const s = String(text ?? '').trim()
  if (!s) return []
  let parts = [s]
  for (const sep of STRONG) {
    const next = []
    for (const p of parts) next.push(...p.split(sep))
    parts = next
  }
  parts = parts.map((p) => p.trim()).filter(Boolean)

  if (parts.length === 1 && /→|->/.test(s)) {
    const cand = s.replace(/->/g, '→').split('→').map((p) => p.trim())
    if (looksLikeChain(cand)) parts = cand.filter(Boolean)
  }

  const out = []
  for (const p of parts) {
    if (p.length > 1 && p.startsWith('再') && out.length) {
      const rest = p.slice(1).trim()
      if (rest) { out.push(rest); continue }
    }
    out.push(p)
  }
  return out.filter(Boolean)
}

/** 是否是组合指令（多步） */
export function isSequence(text) {
  return splitSequence(text).length > 1
}
