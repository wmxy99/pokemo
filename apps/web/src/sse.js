/**
 * PokéLab AI · SSE 客户端封装（心跳探测 + 断线续传 + 轮询兜底）
 *
 * 后端事件格式（apps/api/app/sse.py）：
 *   retry: 3000
 *   event: update    data: {...快照...}          ← 每 1s 快照（不带 id）
 *   id: 7
 *   event: progress  data: {...业务事件...}      ← 带 id，推进续传游标
 *   event: replay    data: {...回放...}          ← 重连时补发（带 id）
 *   : ping <ts>                                  ← 心跳注释（本封装忽略，仅计时）
 *   event: end       data: {}
 *
 * 行为：
 *   - 浏览器 EventSource 原生支持自动重连并回传 Last-Event-ID；本封装额外
 *     把游标持久化到 sessionStorage，页面刷新后也能从断点续传；
 *   - 连续失败超过 maxRetry 次 → 调用方给的轮询兜底（onState('polling')）；
 *   - 心跳超时检测（heartbeatTimeout）：静默连接判定为断开，主动重建。
 */

const CURSOR_KEY = (jobId) => `pokelab_sse_cursor_${jobId}`

function readCursor(jobId) {
  try {
    return Number(sessionStorage.getItem(CURSOR_KEY(jobId)) || 0) || 0
  } catch {
    return 0
  }
}

function writeCursor(jobId, id) {
  try {
    if (id) sessionStorage.setItem(CURSOR_KEY(jobId), String(id))
  } catch { /* 私密模式忽略 */ }
}

export function clearCursor(jobId) {
  try {
    sessionStorage.removeItem(CURSOR_KEY(jobId))
  } catch { /* 忽略 */ }
}

/**
 * 打开任务事件流。
 * @param {object} o
 * @param {string} o.jobId               任务 id（用于续传游标持久化）
 * @param {string} o.url                 事件流 URL（含 token）
 * @param {Function} o.onUpdate          快照回调 (snap)
 * @param {Function} [o.onProgress]      业务事件回调 (data, kind)
 * @param {Function} [o.onEnd]           终态回调 (snap)
 * @param {Function} [o.onState]         连接状态回调 ('live'|'reconnecting'|'polling')
 * @param {Function} [o.onFallback]      轮询兜底启动器
 * @param {number} [o.maxRetry=4]        重连失败上限
 * @param {number} [o.heartbeatTimeout=45000] 心跳静默上限（ms）
 * @returns {{close: Function, state: Function}}
 */
export function openJobStream({
  jobId, url, onUpdate, onProgress, onEnd, onState, onFallback,
  maxRetry = 4, heartbeatTimeout = 45000,
}) {
  let es = null
  let closed = false
  let failures = 0
  let lastBeat = Date.now()
  let curState = 'connecting'
  let watchdog = null

  const setState = (s) => {
    if (curState === s) return
    curState = s
    if (onState) onState(s)
  }

  const startFallback = (reason) => {
    setState('polling')
    if (onFallback) onFallback(reason)
  }

  const attach = () => {
    const cursor = readCursor(jobId)
    const full = cursor ? `${url}${url.includes('?') ? '&' : '?'}last_event_id=${cursor}` : url
    es = new EventSource(full)

    es.onopen = () => {
      failures = 0
      lastBeat = Date.now()
      setState('live')
    }

    es.onmessage = (e) => handle('update', e)

    es.addEventListener('update', (e) => handle('update', e))
    es.addEventListener('progress', (e) => handle('progress', e))
    es.addEventListener('replay', (e) => handle('replay', e))
    es.addEventListener('timeout', () => { /* 服务端超时：走原生重连 */ })
    es.addEventListener('error', () => { /* 服务端 error 事件：交给 onerror 计数 */ })

    es.addEventListener('end', (e) => {
      let snap = {}
      try { snap = JSON.parse(e.data || '{}') } catch { /* 忽略 */ }
      closed = true
      try { es.close() } catch { /* 忽略 */ }
      if (watchdog) clearInterval(watchdog)
      // 终态：清掉续传游标（任务已结束，无需再回放）
      clearCursor(jobId)
      if (onEnd) onEnd(snap)
    })

    es.onerror = () => {
      if (closed) return
      failures += 1
      if (failures > maxRetry) {
        try { es.close() } catch { /* 忽略 */ }
        startFallback(`SSE 重连 ${failures} 次失败`)
        return
      }
      setState('reconnecting')
      // EventSource 会自动重连；此处仅记录状态（游标由浏览器 + sessionStorage 维护）
    }
  }

  const handle = (kind, e) => {
    lastBeat = Date.now()
    if (e && e.lastEventId) writeCursor(jobId, e.lastEventId)
    let data = {}
    try { data = JSON.parse(e.data || '{}') } catch { /* 忽略 */ }
    if (kind === 'update') {
      if (onUpdate) onUpdate(data)
      if (data.status && ['done', 'failed', 'cancelled'].includes(data.status)
          && onEnd) {
        // 快照已到终态（end 事件可能因网络丢失）：兜底收尾
        clearCursor(jobId)
        onEnd(data)
      }
    } else if (onProgress) {
      onProgress(data, kind)
    }
  }

  attach()
  // 心跳看门狗：连接静默过久（网络半开）→ 主动重建
  watchdog = setInterval(() => {
    if (closed || !es) return
    if (Date.now() - lastBeat > heartbeatTimeout) {
      try { es.close() } catch { /* 忽略 */ }
      failures += 1
      if (failures > maxRetry) return startFallback('心跳静默超时')
      setState('reconnecting')
      attach()
    }
  }, 10000)

  return {
    close: () => {
      closed = true
      if (watchdog) clearInterval(watchdog)
      try { es && es.close() } catch { /* 忽略 */ }
    },
    state: () => curState,
  }
}

/** 连接状态 → 展示文案/tone（供 UI 指示器使用） */
export const SSE_STATE = {
  connecting: { label: '连接中…', tone: 'warn' },
  live: { label: '实时', tone: 'ok' },
  reconnecting: { label: '重连中（自动续传）', tone: 'warn' },
  polling: { label: '轮询兜底', tone: 'warn' },
}
