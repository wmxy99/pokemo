// PokéLab AI API 客户端（fetch 封装 + JWT localStorage）
// BASE 规则：显式 VITE_API_BASE 优先；同源部署（由 FastAPI 挂载 /app）用 ''；
// 开发（Vite dev server :5173）用 '/api' 经 proxy 转发到 127.0.0.1:8000
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
  const t = token.get()
  if (t) headers.Authorization = `Bearer ${t}`
  const res = await fetch(BASE + path, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
  })
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
  // 对战
  createBattle: (payload) => req('/battles', { method: 'POST', body: payload }),
  battleState: (id) => req(`/battles/${id}`),
  act: (id, action) => req(`/battles/${id}/actions`, { method: 'POST', body: { action } }),
  // 批量
  submitBatch: (payload) => req('/batch/jobs', { method: 'POST', body: payload }),
  listBatch: () => req('/batch/jobs'),
  batchStatus: (id) => req(`/batch/jobs/${id}`),
  cancelBatch: (id) => req(`/batch/jobs/${id}/cancel`, { method: 'POST' }),
  report: (id, format = 'md') => req(`/batch/jobs/${id}/report?format=${format}`, { raw: true }),
}

export const DECKS = ['lightning', 'fire', 'psychic', 'darkness', 'ability_demo']
