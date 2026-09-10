// PokéLab AI API 客户端（fetch 封装 + JWT localStorage）
// BASE：显式 VITE_API_BASE 优先；同源部署（FastAPI 挂 /app）用 ''；dev 用 '/api'（Vite proxy）
const BASE = import.meta.env.VITE_API_BASE
  ?? (typeof location !== 'undefined' && location.pathname.startsWith('/app') ? '' : '/api')
const TOKEN_KEY = 'pokelab_token'

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

async function req(path, { method = 'GET', body, raw = false } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const tk = token.get()
  if (tk) headers.Authorization = `Bearer ${tk}`
  let res
  try {
    res = await fetch(BASE + path, { method, headers, body: body ? JSON.stringify(body) : undefined })
  } catch (e) {
    const err = new Error('网络不可达（后端未启动？）')
    err.status = 0
    throw err
  }
  if (res.status === 401) token.clear()
  const text = await res.text()
  if (!res.ok) {
    let detail = text
    try { detail = JSON.parse(text).detail ?? text } catch { /* 纯文本 */ }
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    err.status = res.status
    throw err
  }
  if (raw) return text
  try { return JSON.parse(text) } catch { return text }
}

export const api = {
  register: (name, password) => req('/auth/register', { method: 'POST', body: { name, password } }),
  login: (name, password) => req('/auth/login', { method: 'POST', body: { name, password } }),
  me: () => req('/auth/me'),
  // F5 对战
  createBattle: (payload) => req('/battles', { method: 'POST', body: payload }),
  battleState: (id) => req(`/battles/${id}`),
  act: (id, action) => req(`/battles/${id}/actions`, { method: 'POST', body: { action } }),
  // F6 批量
  submitBatch: (payload) => req('/batch/jobs', { method: 'POST', body: payload }),
  listBatch: () => req('/batch/jobs'),
  batchStatus: (id) => req(`/batch/jobs/${id}`),
  cancelBatch: (id) => req(`/batch/jobs/${id}/cancel`, { method: 'POST' }),
  report: (id, format = 'md') => req(`/batch/jobs/${id}/report?format=${format}`, { raw: true }),
  // F3 卡组
  importDeck: (text, name) => req('/decks/import', { method: 'POST', body: { text, name } }),
  myDecks: () => req('/decks'),
  deckPresets: () => req('/decks/presets'),
  opening: (id, trials = 1000) => req(`/decks/${id}/opening?trials=${trials}`, { method: 'POST' }),
  exportDeck: (id) => req(`/decks/${id}/export`),
  // F7 统计
  statsSummary: () => req('/stats/summary'),
  watchdog: () => req('/stats/watchdog'),
  // F7 卡组池矩阵（多对位批量任务）
  submitPool: (payload) => req('/batch/pool-jobs', { method: 'POST', body: payload }),
  // SSE 事件流 URL（EventSource 无法带 header → token 走 query）
  streamUrl: (path, params = {}) => {
    const q = new URLSearchParams(params)
    const tk = token.get()
    if (tk) q.set('token', tk)
    const qs = q.toString()
    return BASE + path + (qs ? (path.includes('?') ? '&' : '?') + qs : '')
  },
  // 训练（M2/M6）
  submitTrain: (payload) => req('/train/jobs', { method: 'POST', body: payload }),
  listTrain: () => req('/train/jobs'),
  trainStatus: (id) => req(`/train/jobs/${id}`),
  trainLog: (id, lines = 60) => req(`/train/jobs/${id}/log?lines=${lines}`),
  cancelTrain: (id) => req(`/train/jobs/${id}/cancel`, { method: 'POST' }),
  // 管理（M5）
  adminUsers: () => req('/admin/users'),
  adminSetRole: (name, role) => req(`/admin/users/${name}?role=${role}`, { method: 'PATCH' }),
}

// demo 卡组字典（引擎内演示卡池；预设 A1–A4 映射见 /decks/presets）
export const DECKS = [
  { id: 'lightning', label: 'Lightning demo' },
  { id: 'fire', label: 'Fire demo' },
  { id: 'psychic', label: 'Psychic demo' },
  { id: 'draco', label: 'Draco line（≈Dragapult 系）' },
  { id: 'ability_demo', label: 'Ability demo' },
  { id: 'grass', label: 'Grass demo' },
  { id: 'status_demo', label: 'Status demo' },
]
