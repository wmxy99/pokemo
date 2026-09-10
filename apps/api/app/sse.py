# -*- coding: utf-8 -*-
"""PokéLab AI API · SSE（Server-Sent Events）工具

替代前端轮询：服务端每秒推送一次快照，终态发送 `event: end` 后关闭。
事件格式（前端 EventSource 兼容）：
  event: update   data: {...快照...}
  event: end      data: {}
  event: timeout  data: {}
"""
from __future__ import annotations
import asyncio
import json
from typing import Callable

from fastapi.responses import StreamingResponse

FINAL = ("done", "failed", "cancelled")


async def event_stream(snapshot: Callable[[], dict], interval: float = 1.0,
                       timeout_s: float = 3600.0):
    """snapshot() 返回 dict（须含 status）；终态自动收尾"""
    t0 = asyncio.get_event_loop().time()
    while True:
        try:
            snap = snapshot()
        except Exception as e:                       # noqa: BLE001
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            break
        yield "event: update\ndata: " + \
            json.dumps(snap, ensure_ascii=False) + "\n\n"
        if snap.get("status") in FINAL:
            yield "event: end\ndata: {}\n\n"
            break
        if asyncio.get_event_loop().time() - t0 > timeout_s:
            yield "event: timeout\ndata: {}\n\n"
            break
        await asyncio.sleep(interval)


def sse_response(snapshot: Callable[[], dict], interval: float = 1.0):
    return StreamingResponse(
        event_stream(snapshot, interval=interval),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )
