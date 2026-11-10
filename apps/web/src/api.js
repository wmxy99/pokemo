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
    err.detail = detail          // 结构化 detail（如指令歧义时含 candidates/ambiguous）
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
  // 自由文本指令（中文招式名/卡名/位置），如「幻影俯冲」「附能量到后备1」
  actCommand: (id, command, verb) => req(`/battles/${id}/actions`,
    { method: 'POST', body: verb ? { command, verb } : { command } }),
  // 组合指令：显式步骤数组（等价于用「然后」串联的 command）
  actCommands: (id, commands) => req(`/battles/${id}/actions`,
    { method: 'POST', body: { commands } }),
  battleCommands: (id) => req(`/battles/${id}/commands`),
  // F6 批量
  submitBatch: (payload) => req('/batch/jobs', { method: 'POST', body: payload }),
  listBatch: () => req('/batch/jobs'),
  batchStatus: (id) => req(`/batch/jobs/${id}`),
  cancelBatch: (id) => req(`/batch/jobs/${id}/cancel`, { method: 'POST' }),
  report: (id, format = 'md') => req(`/batch/jobs/${id}/report?format=${format}`, { raw: true }),
  // F3 卡组
  importDeck: (text, name) => req('/decks/import', { method: 'POST', body: { text, name } }),
  myDecks: () => req('/decks'),
  getDeck: (id) => req(`/decks/${id}`),
  deckPresets: () => req('/decks/presets'),
  deckPresetDetail: (id) => req(`/decks/presets/${id}`),
  opening: (id, trials = 1000) => req(`/decks/${id}/opening?trials=${trials}`, { method: 'POST' }),
  exportDeck: (id, lang = 'zh') => req(`/decks/${id}/export?lang=${lang}`),
  // F7 统计
  statsSummary: () => req('/stats/summary'),
  watchdog: () => req('/stats/watchdog'),
  // F7 卡组池矩阵（多对位批量任务）
  submitPool: (payload) => req('/batch/pool-jobs', { method: 'POST', body: payload }),
  // F2 卡库（M1 清洗入库的 1267 张卡）
  libraryCards: (params = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') q.set(k, v)
    })
    const qs = q.toString()
    return req('/cards' + (qs ? `?${qs}` : ''))
  },
  libraryStats: () => req('/cards/stats'),
  cardDetail: (id) => req(`/cards/${id}`),
  // F9 合规声明（6.1 内容合规 / 6.2 数据隐私 / 红线 C1–C5）
  compliance: () => req('/compliance'),
  complianceAudit: (limit = 100) => req(`/compliance/audit?limit=${limit}`),
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
  // 后台「云端模型」配置（admin）：OpenAI 兼容端点 + 密钥加密存储 + 连通性测试
  llmConfig: () => req('/admin/llm/config'),
  llmSave: (payload) => req('/admin/llm/config', { method: 'PUT', body: payload }),
  llmClear: () => req('/admin/llm/config', { method: 'DELETE' }),
  llmTest: (prompt) => req('/admin/llm/test', { method: 'POST', body: { prompt } }),
  // 对战解说（F5 内）：纯展示可降级；返回值带 C4 AI 标识
  commentary: (id, payload = {}) => req(`/battles/${id}/commentary`, { method: 'POST', body: payload }),
}

// 卡组字典（中文展示；id 为数据键，保持英文）
//
// **只有真实卡池组建的 10 套**（A1–A4 Meta 锚点 + B1–B5 五属性实例 + C1 钢系特殊能量）。
// 演示卡组（draco / lightning / fire / psychic / ability / grass / status）已从下拉移除：
// 它们是 `decklib` 的**合成卡**（自造卡名 + 数值，如 Seedling「幼芽」/ Leaf Cutter「叶刃」），
// 卡面看起来像真卡、实际在卡库里查不到 —— 玩家点开会以为数据错误。
// 引擎侧仍保留这些函数（K1 训练/验收脚本用 `make_deck("lightning")` 做固定基准，
// 删掉会让历史曲线无法复现），只是不再出现在对战下拉里。
export const DECKS = [
  { id: 'A1', label: 'A1 · 多龙巴鲁托ex / 黑夜魔灵 · 遗产能量' },
  { id: 'A2', label: 'A2 · 多龙巴鲁托ex / 火焰鸡ex · 点火能量 + 遗产能量' },
  { id: 'A3', label: 'A3 · N的索罗亚克ex · 遗产能量 + 尖刺能量' },
  { id: 'A4', label: 'A4 · 基础盒 · 厄诡椪ex / 皮皮ex · 棱镜能量 + 生长草能量' },
  // 实例卡组（B1–B5）：全部由 engine_ready 卡池组建，60 张可对战，各带 2–3 种特殊能量
  { id: 'B1', label: 'B1 · 火：炽焰咆哮虎ex / Mega 喷火龙Yex · 新上能量 + 点火能量' },
  { id: 'B2', label: 'B2 · 水：Mega 大力鳄ex / 狂欢浪舞鸭 · 新上能量 + 点火能量' },
  { id: 'B3', label: 'B3 · 雷：皮卡丘ex / 自爆磁怪 · 新上能量 + 棱镜能量' },
  { id: 'B4', label: 'B4 · 恶：三首恶龙ex / Mega 耿鬼ex · 遗产能量 + 尖刺能量' },
  { id: 'B5', label: 'B5 · 超：Mega 沙奈朵ex / 黑夜魔灵 · 心灵感应能量 + 新上能量' },
  // C1：钢系 + 三件套特殊能量（新上能量 / 棱镜能量 / 薄雾能量）
  { id: 'C1', label: 'C1 · 钢：大吾的巨金怪ex / 超级盔甲鸟ex · 新上能量 + 薄雾能量' },
]

export function shortDeck(id) {
  const m = {
    A1: '多龙巴鲁托ex/黑夜魔灵', A2: '多龙巴鲁托ex/火焰鸡ex',
    A3: 'N的索罗亚克ex', A4: '基础盒',
    B1: '火·炽焰咆哮虎ex', B2: '水·大力鳄ex', B3: '雷·皮卡丘ex',
    B4: '恶·三首恶龙ex', B5: '超·沙奈朵ex',
    C1: '钢·巨金怪ex',
  }
  return m[id] || id
}
