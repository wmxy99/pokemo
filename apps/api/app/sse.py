# -*- coding: utf-8 -*-
"""PokéLab AI API · SSE（Server-Sent Events）工具

替代前端轮询：服务端按 interval 推送任务快照，终态发送 `event: end` 后关闭。

v1.1（本次）：**心跳 + 断线续传**
  - 每条事件带 `id: <seq>`，客户端（EventSource）断线重连时浏览器自动回传
    `Last-Event-ID` 请求头 → 服务端重放 `seq > last` 的历史事件（不丢进度）；
  - 空闲时每 `heartbeat_s` 发送注释行 `: ping <ts>`（SSE 注释，客户端忽略，
    但可穿过反代/网关的空闲超时，避免连接被静默掐断）；
  - 首行 `retry: 3000` 指定重连间隔；客户端断开（CancelledError）静默收尾。

事件格式：
  retry: 3000
  id: 7
  event: update    data: {...快照...}
  : ping 1699...            （心跳注释，客户端忽略）
  id: 8
  event: end       data: {}
事件类型：`update`（快照/进度）· `replay`（续传重放的历史事件）·
         `end`（终态）· `timeout`（服务端超时）· `error`
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

FINAL = ("done", "failed", "cancelled")


def _fmt(event: str, data: dict, seq: Optional[int] = None) -> str:
    lines = []
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, ensure_ascii=False))
    return "\n".join(lines) + "\n\n"


def parse_last_event_id(request: Optional[Request],
                        query_value: Optional[str] = None) -> int:
    """续传游标：优先 `Last-Event-ID` 请求头（EventSource 自动发送），
    其次 `?last_event_id=`（部分客户端/手工重连场景）"""
    raw = None
    if request is not None:
        raw = request.headers.get("last-event-id")
    if raw is None:
        raw = query_value
    try:
        return max(0, int(str(raw).strip())) if raw not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


async def event_stream(snapshot: Callable[[], dict],
                       interval: float = 1.0,
                       timeout_s: float = 3600.0,
                       heartbeat_s: float = 15.0,
                       history: Optional[Callable[[int], list]] = None,
                       last_event_id: int = 0,
                       request: Optional[Request] = None):
    """snapshot() 返回 dict（须含 status）；终态自动收尾

    事件编号约定（断线续传的锚点）：
      - 历史/新增的**业务事件**（history() 提供）带 `id: <seq>` —— 客户端
        EventSource 会自动把最后收到的 id 通过 `Last-Event-ID` 回传；
      - 每轮的状态快照 `event: update` **不带 id**（只是 UI 快照，不推进游标）。
    history(last_seq) → [{seq, kind, data}]：需回放的事件（seq 单调递增）。
    """
    loop = asyncio.get_event_loop()
    yield "retry: 3000\n\n"

    cursor = int(last_event_id or 0)

    # 1) 断线续传：先重放 cursor 之后的历史事件
    if history is not None and cursor > 0:
        try:
            for ev in history(cursor):
                yield _fmt("replay", {**ev.get("data", {}),
                                      "kind": ev.get("kind")},
                           seq=ev.get("seq"))
                cursor = max(cursor, int(ev.get("seq") or 0))
        except Exception as e:                        # noqa: BLE001
            yield _fmt("error", {"error": f"replay failed: {e}"})

    t0 = loop.time()
    last_ping = loop.time()
    while True:
        try:
            snap = snapshot()
        except Exception as e:                        # noqa: BLE001
            yield _fmt("error", {"error": str(e)})
            break
        yield _fmt("update", snap)                    # 快照（不带 id）
        # 2) 增量业务事件（带 id，推进续传游标）
        if history is not None:
            try:
                for ev in history(cursor):
                    cursor = max(cursor, int(ev.get("seq") or 0))
                    yield _fmt("progress", {**ev.get("data", {}),
                                            "kind": ev.get("kind")},
                               seq=ev.get("seq"))
            except Exception as e:                    # noqa: BLE001
                yield _fmt("error", {"error": f"history failed: {e}"})
                break
        if snap.get("status") in FINAL:
            yield _fmt("end", {}, seq=None)
            break
        if loop.time() - t0 > timeout_s:
            yield _fmt("timeout", {"timeout_s": timeout_s})
            break
        # 3) 心跳：把等待切分为 interval，空闲超时前发注释行保活
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:                # 客户端断开
            return
        if loop.time() - last_ping >= max(heartbeat_s, interval):
            last_ping = loop.time()
            yield f": ping {int(loop.time())}\n\n"


def sse_response(snapshot: Callable[[], dict], *,
                 interval: float = 1.0, timeout_s: float = 3600.0,
                 heartbeat_s: float = 15.0,
                 history: Optional[Callable[[int], list]] = None,
                 last_event_id: int = 0,
                 request: Optional[Request] = None) -> StreamingResponse:
    return StreamingResponse(
        event_stream(snapshot, interval=interval, timeout_s=timeout_s,
                     heartbeat_s=heartbeat_s, history=history,
                     last_event_id=last_event_id, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )
