import React from 'react'
import { typeColor, typeLabel } from './cardtext.js'

/**
 * 卡面文本渲染（对齐官方卡牌的信息密度与符号习惯，但**全部自绘** —— C1/C2）
 *
 * 为什么需要：
 *   卡面原文里带符号标记 —— `{G}`（草能量）、`{C}`（无色）、`{ex}` / `{V}`（规则框
 *   标记）。引擎与卡库都按原文存，前端此前直接当普通字符串渲染，于是玩家看到的是
 *   「Attach a Basic {G} Energy card」这种**带花括号的原始标记**；官方卡面那里是
 *   一个彩色能量图标。175 条卡面条目受影响。
 *
 * 做法：把 `{...}` 切出来渲染成**自绘徽章**（不用任何官方图标素材）：
 *   · 能量符号 `{G}` → 该属性主色的圆形徽章（字母 + 属性色，hover 显示中文属性名）
 *   · 规则框标记 `{ex}` / `{V}` / `{VSTAR}` → 圆角矩形标记
 *   · 不认识的 `{...}` → 原样显示内容（去掉花括号，避免看起来像程序错误）
 *
 * 另外这里统一了**排版**：`white-space: pre-line` 保留原文的分段（卡面规则文本
 * 常用空行分隔两段效果），行高与字号由 CSS 变量控制，卡面/详情/卡库三处一致。
 */

const MARK = /\{([^{}]{1,18})\}/g

// 能量符号 → 引擎属性键（与 cardtext.TYPE_LABEL 同源）
const ENERGY = {
  G: 'grass', R: 'fire', W: 'water', L: 'lightning', P: 'psychic',
  F: 'fighting', D: 'darkness', M: 'metal', N: 'dragon', C: 'colorless',
  Y: 'psychic', // 老卡（黄色系）偶尔出现的别名
}

// 规则框 / 特殊标记（不是能量）
const SPECIAL = {
  ex: 'ex', EX: 'ex', V: 'V', v: 'V', VSTAR: 'VSTAR', VMAX: 'VMAX',
  'prism star': '◇', Prism: '◇', Tera: 'Tera', tera: 'Tera',
}

function EnergyChip({ code }) {
  const t = ENERGY[code] || ENERGY[code.toUpperCase()]
  if (!t) return null
  const [fg] = typeColor(t)
  return (
    <span className="en-chip" style={{ background: fg }}
      title={`${typeLabel(t)}能量`} aria-label={`${typeLabel(t)}能量`}>
      {code.toUpperCase()}
    </span>
  )
}

/** 单个 `{...}` 标记 */
function Mark({ body }) {
  if (ENERGY[body] || ENERGY[body.toUpperCase()]) return <EnergyChip code={body} />
  const sp = SPECIAL[body] || SPECIAL[body.toLowerCase()]
  if (sp) {
    return (
      <span className="en-chip rule" title={`规则框标记 ${sp}`}>{sp}</span>
    )
  }
  // 未知标记：去掉花括号照显示（不吞掉信息，也不让它看起来像未渲染的模板）
  return <span className="en-chip plain">{body}</span>
}

/**
 * 卡面文本：符号徽章 + 保留段落。
 * @param {string} text 已由 `abilityText` / `attackText` / `trainerText` 取到的说明
 * @param {string} className 额外类（如 'muted'）
 */
export function CardText({ text, className = '' }) {
  if (!text) return null
  const src = String(text)
  const out = []
  let last = 0
  let m
  MARK.lastIndex = 0
  while ((m = MARK.exec(src)) !== null) {
    if (m.index > last) out.push(src.slice(last, m.index))
    out.push(<Mark key={`m${m.index}`} body={m[1]} />)
    last = m.index + m[0].length
  }
  if (last < src.length) out.push(src.slice(last))
  return <span className={`card-text ${className}`.trim()}>{out}</span>
}
