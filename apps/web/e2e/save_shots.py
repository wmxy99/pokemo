# -*- coding: utf-8 -*-
"""接收 shoot_pages.cjs 的 stdout(JSON)，解码 base64 截图落盘并打印摘要。

用法：node shoot_pages.cjs <base> <outDir> | python save_shots.py [outDir]
"""
import base64
import json
import os
import sys
import time

raw = sys.stdin.read()
i = raw.find("{")
if i < 0:
    print("[error] 未收到 JSON 输出，原文前 300 字符：")
    print(raw[:300])
    sys.exit(2)
data = json.loads(raw[i:])
out = sys.argv[1] if len(sys.argv) > 1 else r"D:/pokemo/pokelab-ai/apps/web/dist2/shots"
os.makedirs(out, exist_ok=True)
print("chrome :", data.get("chrome"))
print("problems:", data.get("problems") or "（无）")
for s in data.get("shots", []):
    b64 = s.pop("b64", None)
    f = os.path.join(out, s["name"] + ".jpg")
    ok = bool(b64) and not str(b64).startswith("ERR:")
    if ok:
        with open(f, "wb") as fh:
            fh.write(base64.b64decode(b64))
    size = os.path.getsize(f) // 1024 if ok else -1
    print(f"  {s['name']:22s} cards={s['cards']:>2} chips={s['chips']:>2} "
          f"tables={s['tables']} jpg={size}KB | {s['text'][:48]}")
print("saved to:", out, "at", time.strftime("%H:%M:%S"))
