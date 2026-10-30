# -*- coding: utf-8 -*-
"""卡库中文本地化：加载 i18n 语料并写入卡表字段。

字段约定（与 ``cards_challenge.json`` / SQLite / 扩展卡池一致）：

    card.name_zh
    card.attacks[].name_zh / effect_zh
    card.abilities[].name_zh / effect_zh
    card.other_text_zh
    card.rule_zh

设计要点
--------
· **英文原文是数据键**：所有查找以英文原文为准，中文只做展示/检索辅助；
· **不做机器硬译**：缺译文就留空，由展示层回退英文，绝不把英文塞进中文位；
· **幂等**：重复运行结果一致；已有中文会被语料覆盖（便于术语统一），
  但覆盖差异会写入报告，方便审计；
· **可校验**：效果/规则文本会检查中文是否存在、英文数字是否全部保留。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional

PKG = Path(__file__).resolve().parents[1]          # packages/card-db
I18N_FILE = PKG / "data" / "i18n" / "zh_card_translations.json"

CATS = ("card_name", "attack_name", "ability_name", "effect", "rule")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_NUM_RE = re.compile(r"\d+")
_SYM_RE = re.compile(r"\{[^}]+\}")


def norm_text(value) -> str:
    """与生成器一致的匹配归一：NBSP → 空格，去首尾空白。"""
    if value is None:
        return ""
    return str(value).replace("\xa0", " ").strip()


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def load(path: Path = I18N_FILE) -> dict:
    """加载 i18n 语料 → ``{meta, maps, by_id, path}``"""
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"缺少中文语料：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    maps: dict[str, dict[str, str]] = {c: {} for c in CATS}
    by_id: dict[str, dict] = {}
    duplicates = Counter()
    for item in payload.get("items") or []:
        cat = item.get("cat")
        en = norm_text(item.get("en"))
        zh = item.get("zh")
        if cat not in maps or not en or not isinstance(zh, str) or not zh.strip():
            continue
        zh = zh.strip()
        if en in maps[cat] and maps[cat][en] != zh:
            duplicates[cat] += 1
        maps[cat][en] = zh
        by_id[str(item.get("id") or "")] = {**item, "zh": zh}
    return {
        "meta": payload.get("meta") or {},
        "maps": maps,
        "by_id": by_id,
        "path": str(path),
        "duplicates": dict(duplicates),
    }


def apply_to_cards(cards: list[dict], i18n: dict) -> dict:
    """把中文写入卡表字段（原地修改）→ 统计与冲突清单"""
    maps = i18n["maps"]
    st = Counter()
    conflicts: list[dict] = []
    missing: list[dict] = []

    def _effect_zh(en_text: str, current: str = "") -> tuple[str, bool]:
        key = norm_text(en_text)
        if not key:
            return "", True
        zh = maps["effect"].get(key)
        if zh:
            return zh, True
        return (current or ""), False

    for c in cards:
        name = norm_text(c.get("name"))
        zh = maps["card_name"].get(name)
        if zh:
            old = (c.get("name_zh") or "").strip()
            if old and old != zh:
                conflicts.append({"field": "card.name_zh", "card": name,
                                  "old": old, "new": zh,
                                  "set": c.get("series"), "number": c.get("number")})
            c["name_zh"] = zh
            st["card_name"] += 1
        else:
            st["card_name_missing"] += 1
            missing.append({"field": "card.name_zh", "card": name,
                            "en": name, "set": c.get("series"),
                            "number": c.get("number")})

        for a in c.get("attacks") or []:
            an = norm_text(a.get("name"))
            azh = maps["attack_name"].get(an)
            if azh:
                a["name_zh"] = azh
                st["attack_name"] += 1
            else:
                a.setdefault("name_zh", "")
                if an:
                    st["attack_name_missing"] += 1
                    missing.append({"field": "attack.name_zh", "card": name,
                                    "en": an})
            if a.get("effect"):
                azh, ok = _effect_zh(a["effect"], a.get("effect_zh", ""))
                a["effect_zh"] = azh
                if ok:
                    st["attack_effect"] += 1
                else:
                    st["attack_effect_missing"] += 1
                    missing.append({"field": "attack.effect_zh", "card": name,
                                    "en": a["effect"][:80]})
            else:
                a["effect_zh"] = ""

        for ab in c.get("abilities") or []:
            an = norm_text(ab.get("name"))
            azh = maps["ability_name"].get(an)
            if azh:
                ab["name_zh"] = azh
                st["ability_name"] += 1
            else:
                ab.setdefault("name_zh", "")
                if an:
                    st["ability_name_missing"] += 1
                    missing.append({"field": "ability.name_zh", "card": name,
                                    "en": an})
            if ab.get("effect"):
                azh, ok = _effect_zh(ab["effect"], ab.get("effect_zh", ""))
                ab["effect_zh"] = azh
                if ok:
                    st["ability_effect"] += 1
                else:
                    st["ability_effect_missing"] += 1
                    missing.append({"field": "ability.effect_zh", "card": name,
                                    "en": ab["effect"][:80]})
            else:
                ab["effect_zh"] = ""

        if c.get("other_text"):
            ozh, ok = _effect_zh(c["other_text"], c.get("other_text_zh", ""))
            c["other_text_zh"] = ozh
            if ok:
                st["other_text"] += 1
            else:
                st["other_text_missing"] += 1
                missing.append({"field": "card.other_text_zh", "card": name,
                                "en": c["other_text"][:80]})
        else:
            c["other_text_zh"] = ""

        if c.get("rule"):
            rzh = maps["rule"].get(norm_text(c["rule"]))
            if rzh:
                c["rule_zh"] = rzh
                st["rule"] += 1
            else:
                c.setdefault("rule_zh", "")
                st["rule_missing"] += 1
                missing.append({"field": "card.rule_zh", "card": name,
                                "en": c["rule"][:80]})
        else:
            c["rule_zh"] = ""

    return {"stats": dict(st), "conflicts": conflicts,
            "missing": missing, "duplicates": i18n.get("duplicates") or {}}


def validate_cards(cards: list[dict]) -> dict:
    """校验已写入的中文字段（不修正，只如实报告）"""
    st = Counter()
    problems: list[dict] = []

    def _check(field: str, card: str, en: str, zh: str,
               *, require_numbers: bool = False):
        st["checked"] += 1
        if not zh:
            st["empty"] += 1
            problems.append({"kind": "empty", "field": field, "card": card,
                             "en": en[:80]})
            return
        if not has_cjk(zh):
            st["no_cjk"] += 1
            problems.append({"kind": "no_cjk", "field": field, "card": card,
                             "zh": zh[:80]})
        if require_numbers:
            en_nums = Counter(_NUM_RE.findall(en or ""))
            zh_nums = Counter(_NUM_RE.findall(zh))
            lost = {k: v - zh_nums.get(k, 0) for k, v in en_nums.items()
                    if v > zh_nums.get(k, 0)}
            if lost:
                st["lost_numbers"] += 1
                problems.append({"kind": "lost_numbers", "field": field,
                                 "card": card, "lost": lost,
                                 "en": en[:100], "zh": zh[:100]})
            if Counter(_SYM_RE.findall(en or "")) != Counter(_SYM_RE.findall(zh)):
                st["symbol_mismatch"] += 1
                problems.append({"kind": "symbol_mismatch", "field": field,
                                 "card": card,
                                 "en": _SYM_RE.findall(en or ""),
                                 "zh": _SYM_RE.findall(zh)})

    for c in cards:
        name = c.get("name") or ""
        _check("card.name_zh", name, name, c.get("name_zh") or "")
        for a in c.get("attacks") or []:
            _check("attack.name_zh", name, a.get("name") or "",
                   a.get("name_zh") or "")
            if a.get("effect"):
                _check("attack.effect_zh", name, a["effect"],
                       a.get("effect_zh") or "", require_numbers=True)
        for ab in c.get("abilities") or []:
            _check("ability.name_zh", name, ab.get("name") or "",
                   ab.get("name_zh") or "")
            if ab.get("effect"):
                _check("ability.effect_zh", name, ab["effect"],
                       ab.get("effect_zh") or "", require_numbers=True)
        if c.get("other_text"):
            _check("card.other_text_zh", name, c["other_text"],
                   c.get("other_text_zh") or "", require_numbers=True)
        if c.get("rule"):
            _check("card.rule_zh", name, c["rule"],
                   c.get("rule_zh") or "", require_numbers=True)
    return {"stats": dict(st), "problems": problems}


def apply_and_report(cards: list[dict], i18n_path: Optional[Path] = None) -> dict:
    """便捷入口：加载 → 写入 → 校验（供 ETL 脚本复用）"""
    i18n = load(i18n_path or I18N_FILE)
    applied = apply_to_cards(cards, i18n)
    checked = validate_cards(cards)
    return {"i18n": i18n, "applied": applied, "validation": checked}
