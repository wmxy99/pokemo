# -*- coding: utf-8 -*-
"""PokéLab Data · 规则知识库工具

流程：规则条款化（按"章节/裁定条目"切分为原子 chunk，≤~512 token）→ rule_chunks 入库
（source_ref 溯源）→ 检索（MVP：关键字 top-k；向量化：bge-m3 预留，embedding_key 占位，
待 embedding 环境就绪后启用 FAISS/annoy 本地索引）。
"""
from __future__ import annotations
import re
from .schema import RuleChunk

# 原子化分块：按编号条目/换行段拆分（避免粗暴固定长度切碎语义）
_ITEM_RE = re.compile(r"(?m)^\s*(?:(\d+(?:\.\d+)*)[.、\s]+|[-*]\s+)(?=\S)")


def chunk_rules_text(text: str, source_ref: str, max_len: int = 900) -> list[RuleChunk]:
    """把一段规则文本按编号/列表条目切为 RuleChunk 列表"""
    if not text.strip():
        return []
    matches = list(_ITEM_RE.finditer(text))
    if len(matches) <= 1:
        segs = [text.strip()]
    else:
        segs = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            segs.append(text[start:end].strip())
    out = []
    for i, seg in enumerate(segs):
        if not seg:
            continue
        if len(seg) > max_len:  # 超长兜底二次切（按句）
            sub = [s.strip() for s in re.split(r"(?<=[。.;;])", seg) if s.strip()]
            for j, s in enumerate(sub):
                if s:
                    out.append(RuleChunk(source_ref=f"{source_ref}#{i}-{j}", chunk_text=s))
        else:
            out.append(RuleChunk(source_ref=f"{source_ref}#{i}", chunk_text=seg))
    return out


def search_keyword(con, keyword: str, top_k: int = 5) -> list:
    """MVP 关键字检索（向量检索接入点：命中后可用 embedding_key 查 FAISS）"""
    cur = con.execute(
        """SELECT id, source_ref, chunk_text FROM rule_chunks
           WHERE chunk_text LIKE ? AND status='active'
           ORDER BY id LIMIT ?""",
        (f"%{keyword}%", top_k),
    )
    return [dict(r) for r in cur.fetchall()]
