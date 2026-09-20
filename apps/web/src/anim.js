/**
 * 对战演出：命令式 DOM 动画工具
 *
 * 为什么不用 React 状态驱动动画：
 *   这些效果（卡片飞行 / 伤害飘字）**跨越了 React 的重渲染边界** —— 一次动作后
 *   手牌、场上、日志都会重渲染，若把动画挂在组件状态上，很容易被下一次渲染打断或
 *   重复触发。这里改为直接把克隆节点/浮层挂到 document.body 上，动画结束自行移除，
 *   与 React 渲染完全解耦。
 *
 * 另：所有动画都尊重 `prefers-reduced-motion`（全局 CSS 已把 transition/animation 关掉，
 * 此时元素会瞬间到位，随后仍会按时清理，不残留）。
 */

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** 元素相对视口的矩形（不存在则为 null） */
export function rectOf(el) {
  if (!el || !el.getBoundingClientRect) return null
  const r = el.getBoundingClientRect()
  return r.width && r.height ? r : null
}

/**
 * 卡片飞行：把 fromEl 的**外观**克隆一份，从它的位置飞到 toEl 的位置
 * 用于「出牌 / 附着能量 / 进化」—— 让玩家看到卡真的从手里进到场上了。
 * @returns {Promise<void>} 动画结束（或立即失败）即 resolve，不阻塞业务
 */
export function flyCard(fromEl, toEl, ms = 430) {
  const a = rectOf(fromEl)
  const b = rectOf(toEl)
  if (!a || !b) return Promise.resolve()
  const clone = fromEl.cloneNode(true)
  clone.classList.add('fly-card')
  // 飞行中不该带上「可出 / 待选目标 / 变暗」这些局面标记
  clone.classList.remove('usable', 'highlight', 'picked', 'dim')
  // 用固定定位覆盖原布局位置；初始 transform 归零以便后续平移
  Object.assign(clone.style, {
    position: 'fixed',
    left: `${a.left}px`,
    top: `${a.top}px`,
    width: `${a.width}px`,
    height: `${a.height}px`,
    margin: '0',
    transform: 'none',
    transition: `transform ${ms}ms cubic-bezier(.22,.85,.3,1), opacity ${ms}ms ease`,
  })
  document.body.appendChild(clone)
  const dx = b.left - a.left
  const dy = b.top - a.top
  const scale = Math.max(0.35, Math.min(1.4, b.width / a.width))
  // 下一帧再改变换，确保 transition 生效
  requestAnimationFrame(() => {
    clone.style.transform = `translate(${dx}px, ${dy}px) scale(${scale})`
    clone.style.opacity = '0.25'
  })
  return sleep(ms + 60).then(() => clone.remove())
}

/**
 * 伤害/回复飘字
 * @param el    载体元素（宝可梦卡）
 * @param text  文本，如 "-30" / "+20"
 * @param kind  'dmg' | 'heal' | 'ko' | 'info'
 */
export function floatText(el, text, kind = 'dmg', ms = 1050) {
  const r = rectOf(el)
  if (!r) return
  const d = document.createElement('div')
  d.className = `float-text ${kind}`
  d.textContent = text
  Object.assign(d.style, {
    position: 'fixed',
    left: `${r.left + r.width / 2}px`,
    top: `${r.top + r.height * 0.32}px`,
  })
  document.body.appendChild(d)
  setTimeout(() => d.remove(), ms)
}

/** 在任意坐标飘字（如「奖赏 +1」） */
export function floatAt(x, y, text, kind = 'info', ms = 1050) {
  const d = document.createElement('div')
  d.className = `float-text ${kind}`
  d.textContent = text
  Object.assign(d.style, { position: 'fixed', left: `${x}px`, top: `${y}px` })
  document.body.appendChild(d)
  setTimeout(() => d.remove(), ms)
}

/** 找场上的位置元素（side: 'me'|'opp'，slot: 0=战斗位，1..=后备 i）
 *
 * 用**槽位**而不是卡名/英文名匹配：卡面显示的是中文展示名，引擎给的是英文数据键，
 * 两者对不上（曾因此飘字完全不出现）；而且同名宝可梦会有多张（踩过）。
 */
export function fieldSlotEl(side, slot) {
  const zone = document.querySelector(`.zone.side-${side}`)
  if (!zone) return null
  if (slot === 0) return zone.querySelector('.table-active .cf')
  const items = zone.querySelectorAll('.bench-list-cards .cf')
  return items[slot - 1] || null
}

/** 找手牌第 idx 张的 DOM */
export function handCardEl(idx) {
  const items = document.querySelectorAll('.hand-fan .hand-card')
  return items[idx] || null
}
