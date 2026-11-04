/**
 * 卡面美术生成器（原创程序化卡图 · 不使用任何官方卡图）
 *
 * 为什么用程序生成而不是图片文件：
 *  - 卡库 1267 张，逐张外链图片既不合规（C2：不得使用官方卡图）也不现实；
 *  - SVG 由卡牌自身属性（类别 / 属性 / 系列 / 编号）确定性生成，
 *    同一张卡每次得到同一张图，离线可用、零网络请求、可按任意尺寸缩放。
 *
 * 合规：全部为几何图形与色块，不含任何宝可梦形象、官方插画或商标元素。
 */

const PALETTES = {
  fire: ['#3B0A0A', '#991B1B', '#F97316', '#FDE68A'],
  water: ['#0B1E3A', '#1E40AF', '#38BDF8', '#E0F2FE'],
  grass: ['#052E16', '#166534', '#4ADE80', '#ECFDF5'],
  lightning: ['#3B2A05', '#A16207', '#FACC15', '#FEFCE8'],
  psychic: ['#2E1065', '#6D28D9', '#C084FC', '#F5F3FF'],
  fighting: ['#3B1508', '#9A3412', '#FB923C', '#FFF7ED'],
  darkness: ['#0B1120', '#334155', '#818CF8', '#E2E8F0'],
  metal: ['#0F172A', '#475569', '#CBD5E1', '#F8FAFC'],
  dragon: ['#2A1A05', '#92400E', '#F59E0B', '#FEF3C7'],
  colorless: ['#1E293B', '#64748B', '#CBD5E1', '#F8FAFC'],
  any: ['#3B2A05', '#B45309', '#FBBF24', '#FEF3C7'],
  team_rocket: ['#3B0A1E', '#9D174D', '#F472B6', '#FCE7F3'],
  trainer: ['#0B1220', '#0F766E', '#2DD4BF', '#F0FDFA'],
}

/** 属性图腾（纯几何 / 象形，不含官方形象） */
const GLYPHS = {
  fire: '<path d="M150 92c16 30 6 46 26 62 26 22 40 48 24 74-16 26-54 34-78 12-24-22-26-56-8-80 10-14 26-22 30-38 3-12 0-20 6-30z"/>',
  water: '<path d="M150 80c32 48 62 80 62 116 0 36-28 64-62 64s-62-28-62-64c0-36 30-68 62-116z"/>',
  grass: '<path d="M96 268c-14-82 34-156 112-176-4 92-48 156-112 176z"/><path d="M108 252c28-40 56-72 92-96" fill="none"/>',
  lightning: '<path d="M176 70 96 214h44l-24 136 92-172h-48z"/>',
  psychic: '<ellipse cx="150" cy="188" rx="92" ry="52" fill="none"/><circle cx="150" cy="188" r="30"/><path d="M60 188c26-34 58-52 90-52s64 18 90 52" fill="none"/>',
  fighting: '<path d="M150 80 226 126v104l-76 46-76-46V126z" fill="none"/><path d="M150 118v128M112 150l76 64M188 150l-76 64" fill="none"/>',
  darkness: '<path d="M188 96c-52 6-86 44-86 92s34 86 86 92c-16 10-36 16-56 16-60 0-108-48-108-108s48-108 108-108c20 0 40 6 56 16z"/>',
  metal: '<circle cx="150" cy="188" r="56" fill="none"/><circle cx="150" cy="188" r="20"/><path d="M150 108v34M150 234v34M70 188h34M196 188h34M94 132l24 24M182 220l24 24M206 132l-24 24M118 220l-24 24" fill="none"/>',
  dragon: '<path d="M150 86 224 188l-74 102-74-102z" fill="none"/><path d="M150 128v120M116 188h68" fill="none"/>',
  colorless: '<path d="M150 92l20 58 60 2-48 38 16 60-48-36-48 36 16-60-48-38 60-2z" fill="none"/>',
  any: '<path d="M150 92l14 54 54 14-54 14-14 54-14-54-54-14 54-14z"/>',
  team_rocket: '<path d="M150 96c40 0 72 32 72 72 0 26-14 48-36 60v30h-28v-26c-4 2-8 2-12 2s-8 0-12-2v26h-28v-30c-22-12-36-34-36-60 0-40 32-72 72-72z" fill="none"/><circle cx="150" cy="168" r="22"/>',
  trainer: '<rect x="86" y="84" width="128" height="176" rx="12" fill="none"/><path d="M112 130h76M112 160h76M112 190h52" fill="none"/>',
}

function hashStr(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function rng(seed) {
  let s = seed >>> 0
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 4294967296
  }
}

function cardInfo(card) {
  if (!card) return { key: 'empty', type: 'colorless', glyph: 'colorless', holo: false }
  const sup = card.supertype
  const t = card.ptype || card.etype || (card.types || [])[0]
    || (sup === 'Trainer' ? 'trainer' : sup === 'Energy' ? 'energy' : 'colorless')
  const isTrainer = sup === 'Trainer' || (card.type && ['item', 'supporter', 'stadium', 'tool'].includes(card.type))
  const type = isTrainer ? 'trainer' : t
  const glyph = GLYPHS[type] ? type : 'colorless'
  const name = card.display || card.name_zh || card.name || ''
  const key = card.id || `${name}|${card.series || ''}|${card.number || ''}|${type}`
  const holo = !!card.ex || /(^|\s)ex$/i.test(name) || /ex$/i.test(name)
  return { key, type, glyph, holo }
}

const cache = new Map()

/** 生成一张卡的 SVG data URI（确定性；同一张卡复用缓存） */
export function cardArtUri(card) {
  const { key, type, glyph, holo } = cardInfo(card)
  if (cache.has(key)) return cache.get(key)
  const pal = PALETTES[type] || PALETTES.colorless
  const rand = rng(hashStr(key))
  const seed = hashStr(key)
  const angle = (seed % 360)
  const blobs = Array.from({ length: 3 }, () => {
    const cx = 30 + rand() * 240
    const cy = 50 + rand() * 320
    const r = 40 + rand() * 90
    return `<circle cx="${cx.toFixed(0)}" cy="${cy.toFixed(0)}" r="${r.toFixed(0)}" fill="url(#glow)" opacity="${(0.18 + rand() * 0.24).toFixed(2)}"/>`
  }).join('')
  const dots = Array.from({ length: 18 }, () => {
    const cx = 10 + rand() * 280
    const cy = 10 + rand() * 400
    const r = 0.8 + rand() * 2.2
    return `<circle cx="${cx.toFixed(0)}" cy="${cy.toFixed(0)}" r="${r.toFixed(1)}" fill="#fff" opacity="${(0.12 + rand() * 0.35).toFixed(2)}"/>`
  }).join('')
  const rings = Array.from({ length: 3 }, (_, i) => {
    const r = 70 + i * 42
    return `<circle cx="150" cy="196" r="${r}" fill="none" stroke="#fff" stroke-width="1.2" opacity="${(0.20 - i * 0.05).toFixed(2)}" stroke-dasharray="${i === 1 ? '6 10' : ''}"/>`
  }).join('')
  const bands = Array.from({ length: 4 }, (_, i) => {
    const y = 60 + i * 92 + rand() * 20
    return `<polygon points="-40,${y} 340,${y - 60 - rand() * 40} 340,${y + 22} -40,${y + 62}" fill="#fff" opacity="${(0.05 + rand() * 0.05).toFixed(2)}"/>`
  }).join('')
  const holoLines = holo
    ? `<g opacity=".22"><polygon points="0,300 300,120 300,160 0,340" fill="#fff"/><polygon points="0,380 300,200 300,220 0,400" fill="#fff"/></g>`
    : ''
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 420" width="300" height="420">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1" gradientTransform="rotate(${angle} .5 .5)">
<stop offset="0" stop-color="${pal[0]}"/><stop offset=".55" stop-color="${pal[1]}"/><stop offset="1" stop-color="${pal[2]}"/>
</linearGradient>
<radialGradient id="glow"><stop offset="0" stop-color="${pal[3]}" stop-opacity=".9"/><stop offset="1" stop-color="${pal[3]}" stop-opacity="0"/></radialGradient>
<linearGradient id="veil" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".10"/><stop offset=".55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".38"/></linearGradient>
</defs>
<rect width="300" height="420" fill="url(#bg)"/>
${bands}
${blobs}
${rings}
<g transform="translate(150 196) scale(1.02) translate(-150 -196)" fill="${pal[3]}" stroke="#fff" stroke-width="2.5" opacity=".30">
${GLYPHS[glyph]}
</g>
${dots}
${holoLines}
<rect width="300" height="420" fill="url(#veil)"/>
<rect x="10" y="10" width="280" height="400" rx="14" fill="none" stroke="#fff" stroke-width="1.2" opacity=".18"/>
<path d="M26 52V26h26M274 52V26h-26M26 368v26h26M274 368v26h-26" fill="none" stroke="#fff" stroke-width="2" opacity=".35" stroke-linecap="round"/>
</svg>`
  const uri = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
  cache.set(key, uri)
  return uri
}

/** 卡面美术块（可用于列表缩略图 / 详情大图） */
export function CardArt({ card, className = '', style }) {
  return (
    <div className={`card-art ${className}`.trim()} aria-hidden="true"
      style={{ backgroundImage: `url("${cardArtUri(card)}")`, ...style }} />
  )
}
