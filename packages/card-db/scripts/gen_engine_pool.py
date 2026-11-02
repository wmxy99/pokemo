# -*- coding: utf-8 -*-
"""从清洗后的卡牌数据集生成**引擎扩展卡池**（卡池扩容）

背景
----
引擎原生卡池（`engine/data/cards_real.json`，40 张）是**人工逐卡校对**的，保真度高但
覆盖极小（全库 1267 张的 2.8%）。而清洗后的数据集已经带有：
  · 卡面数值（HP / 属性 / 弱点 / 抗性 / 撤退 / 阶段 / 进化前 / 招式费用与伤害）
  · K2 解析链产出的**引擎可执行 tag**（`parse.items[].engine_tags`）
因此可以把「解析链已能完整执行」的卡**批量转成引擎卡池条目**，把可对战卡池从 40 张
扩到数百张——这是「按未建模动作清单分批建模」的自然延伸。

严格准入（**只收无未建模项**的卡）
----------------------------------
1. 该卡若有 `parse` 产物，**所有** item 的 `unsupported` 均为空
   （任何一处未建模 → 整卡不收；避免「部分效果静默丢失」）；
   无 `parse` 的卡仅允许**无效果文本的宝可梦**（纯伤害招式）与基本能量；
2. 至少有一个招式/特性/卡级效果可执行（不做空壳）；
3. 印刷（set+number）未与人工卡池重复（人工条目**永远优先**；同名不同印刷可共存）；
4. 宝可梦必须有 HP 与属性；训练家必须有受支持的训练家 tag。

诚实标注
--------
生成的条目带
  · `provenance: "dataset-derived"` + `source`

  · `fidelity: "rule-parser-derived"`（区别于人工卡池的 `engine-mapped`）
装载侧（`engine.reallib`）把扩展池卡标记为 `engine_status="approximated"`，
**不冒充**人工校对卡。

用法
----
    python scripts/gen_engine_pool.py            # 生成并写盘
    python scripts/gen_engine_pool.py --dry-run  # 只看统计
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[3]
CLEANED = REPO / "packages" / "card-db" / "data" / "cleaned" / "cards_challenge.json"
POOL_MAIN = REPO / "packages" / "engine" / "engine" / "data" / "cards_real.json"
POOL_EXT = REPO / "packages" / "engine" / "engine" / "data" / "cards_pool_ext.json"

# 属性代码 → 英文名（基本能量统一命名，与人工卡池/卡表一致）
TYPE_EN = {
    "grass": "Grass", "fire": "Fire", "water": "Water", "lightning": "Lightning",
    "psychic": "Psychic", "fighting": "Fighting", "darkness": "Darkness",
    "metal": "Metal", "dragon": "Dragon", "colorless": "Colorless",
}

# 引擎支持的训练家 tag 前缀（与 reallib._SUPPORTED_TRAINER_TAGS 对齐）
TRAINER_TAGS = ("poffin", "ultra_ball", "hammer", "boss", "research",
                "shuffle_draw", "switch_self", "recover",
                "discard_search_energy", "search_energy", "candy",
                "search_pokemon", "search_trainer", "search_any",
                # 2026-10-27：检索**铺场**（去向是后备，不是入手）
                "search_bench",
                # S2 道具（2026-10-26）：附着型训练家
                "tool:",
                # S3 场地（2026-10-27）：全场唯一
                "stadium:",
                # 2026-10-27：训练家/整卡级「非检索类」效果（引擎机能早已存在，
                # 只是派发链没接）。前缀互不包含：`draw_to:` vs `draw:`、
                # `discard_search_energy:` vs `discard:` 的第 5/8 位都是 `_`。
                "draw:", "draw_to:", "heal:", "attach", "gust", "discard:",
                "mill:",
                # B3（2026-10-28）：抽牌完整性 / 对手洗回手牌
                "draw_both:", "opp_shuffle_draw:",
                # S6（2026-10-28）：弃道具 / 弃场地 / 回手族 / 能量移动 / S5 手牌
                "discard_tool:", "discard_stadium", "bounce:", "energy_move:",
                "discard_opp_random:", "shuffle_opp_hand:", "discard_opp_to:",
                # 化石卡（2026-10-28）：当作 N-HP 基础宝可梦上场
                "fossil:",
                # 检视牌库顶（2026-10-30 第二十批）：筛选 + 去向 + 打出前置条件
                "recon:",
                # 对手手牌操作（2026-10-30 第三十二批）：**我方选**口径
                # ⚠ 与上面的 `discard_opp_random:` / `discard_opp_to:` /
                # `shuffle_opp_hand:` 相反：那三个是「对手自选 ⇒ 引擎随机」，
                # 本族卡面写的是 «you discard a card you find there»（使用者选）。
                "opp_hand:")

# 引擎支持的招式效果 tag 前缀（与 battle._apply_attack_effects 对齐）
ATTACK_TAG_PREFIXES = (
    "poison", "burn", "sleep", "paralyze", "confuse", "status_self:", "spread_free:", "spread:",
    "snipe:", "heal:", "self:", "draw:", "draw_to:", "discard:",
    "discard_energy:", "switch_self", "gust", "attach", "move_damage:",
    "recover:", "dmg_plus:", "dmg_set:", "dmg_minus:", "dmg_zero",
    "ignore_weakness",
    "ignore_resistance",
    "coin:", "coin_each:", "coin_all:", "coin_until_tails:", "shield:", "evolve_from_deck", "mill:",
    # 跨回合锁定（2026-10-27）：攻击封锁 / 撤退封锁 / 手牌禁打
    "attack_lock:", "retreat_lock:", "play_lock:",
    # 招式侧全牌库检索（2026-10-27）——语义与特性侧一致（检索入手）。
    # ⚠ 不等于「检索后铺场/附着」。
    "search_pokemon:", "search_trainer:", "search_any:", "search_energy:",
    # 第二十五批之二（2026-11-02）：招式侧「检视牌库顶」（Lapras ex SCR 32 的
    # `recon:20:all:energy:dest:attach_spread`）。**只放通扩展口径**：旧口径
    # `recon:<N>` 是「随机取 1 入手」，招式侧不适用 —— 解析侧已有守卫
    # （非特性侧的 `look_top` 未精确识别一律 unsupported），不会漏进来。
    "recon:",
    # 检索**铺场**（2026-10-27）
    "search_bench:",
    # B3（2026-10-28）：双方同抽 / 对手洗回手牌后抽 / 带类型手牌弃能（含失败门）
    "draw_both:", "opp_shuffle_draw:", "discard_hand_energy:",
    # S6（2026-10-28）：弃道具 / 弃场地 / 回手族 / 能量移动 / S5 手牌
    "discard_tool:", "discard_stadium", "bounce:", "energy_move:",
    "discard_opp_random:", "shuffle_opp_hand:", "discard_opp_to:",
    # 攻击复用 / 下回合限制 / 无视效果（2026-10-28）
    "borrow_attack:", "attack_denial:", "attack_cost_up:",
    "retreat_cost_up:", "ignore_effects",
    # 第九批（2026-10-28）：招式击倒 / 检视牌库顶重排
    "knockout:", "shuffle_top:",
    # 第十二批（2026-10-28）：逐只后备各进化一次（Reuniclus）
    "evolve_each_bench",
    # 第十三批（2026-10-28）：Checkup 伤害改写（Nidoking ex）
    "checkup_set:",
    # 第十四批（2026-10-28）：伤害指示物直放（不吃弱抗，Haunter / Alakazam）
    "counters:",
    # 第十七批（2026-10-29）：**补白** —— 第七批就落地了 `nullify_if:<cond>`
    # （「If …, this attack does nothing.」失败门），但准入白名单一直漏了它：
    # 池内 `nullify_if:` tag 数量为 **0**，等于该机制从未真正可用（诚实但不达标）。
    "nullify_if:",
    # 第十六批（2026-10-29）：跨回合伤害修正（弱抗前/后两种口径）
    "dmg_taken_plus_next:", "atk_minus_next:", "self_attack_plus_next:",
    # 第二十五批（2026-10-29）：**伤害前阶段**（「Before doing damage, …」）
    # 部分招式要求弃道具/特殊能量发生在伤害结算**之前**（守方道具此时还生效），
    # 放到伤害后执行属静默少做。只放通 `pre:` 前缀，具体形态由引擎抛错校验。
    "pre:",
    # 第二十八批（2026-10-29）：弃牌堆基本能量洗回牌库
    # （「Then, shuffle those Energy cards into your deck」，Kyogre / Sinistcha ex）
    "energy_to_deck:",
    # 第二十九批（2026-10-29）：下回合「受击反制」（Bouffalant / Zamazenta）
    "retaliate_next:",
    # 第三十二批（2026-10-30）：对手手牌操作（**我方选**）
    # Rotom / Luxray ex / N's Purrloin / Energy Swatter / Eri
    "opp_hand:",
    # 第三十三批（2026-10-30）：检索能量并**自由分配**附着
    # ⚠ 必须显式列出：虽然 `attach` 前缀也能匹配到它，但引擎侧两分支语义不同
    # （单目标 vs 多目标轮转），白名单写清楚才不会被后人误删。
    "attach_spread:"
)

# 引擎支持的被动/field 特性 tag 前缀（与 battle 派发对齐）
ABILITY_TAG_PREFIXES = (
    "recon:", "cursed:", "draw:", "move_damage:", "attach_energy:",
    # 特性侧自由分配附着（2026-10-30 第三十三批；2026-11-02 起解析侧开始产出）：
    # 引擎在 `_do_ability` 里排在 `attach_energy:` 之前。当前来源是 Magneton SSP 59
    # （场上主动特性 + `:selfko`）；进化触发形态仍需 `evolve_act:` 包装，未产出。
    "attach_spread:",
    "ko_draw:", "evolve_attach:", "weak_change:", "passive:", "status_self:",
    # 特性侧**全牌库检索**（2026-09-22）：与 recon（检视牌库顶 N 取 1）语义不同
    "search_pokemon:", "search_trainer:", "search_any:", "search_energy:",
    # 特性回复（2026-10-27）：范围语义见 battle._apply_heal
    "heal:",
    # 第十批（2026-10-28）：进化触发 / 被击倒触发
    "evolve_act:", "on_ko:", "ko_prize_plus:",
    # 特性检索铺场（2026-10-27）
    "search_bench:",
    # 受击反应特性（2026-10-27）：`on_damaged:<kind>:<arg>`
    # ⚠ 与道具侧 `tool:on_damaged:*` 是两套独立实现（字段不同：tool / abilities）
    "on_damaged:",
    # fx 效果族（2026-10-27）：特性侧「no Abilities」静态封锁等可插拔机制
    "fx:",
    # B3（2026-10-28）：特性侧双方同抽 / 对手洗回手牌后抽 / 后备自换位
    "draw_both:", "opp_shuffle_draw:", "switch_self",
    # S6（2026-10-28）：弃道具 / 弃场地 / 回手族 / 能量移动 / S5 手牌
    "discard_tool:", "discard_stadium", "bounce:", "energy_move:",
    "discard_opp_random:", "shuffle_opp_hand:", "discard_opp_to:",
    # 第二十批（2026-10-29）：特性直放伤害指示物（Dusclops / Dusknoir「诅咒爆裂」）
    "counters:"
)

# 特性侧**精确匹配**的 tag（不带冒号，用前缀匹配会误收 `poison_extra` 一类）
ABILITY_EXACT_TAGS = ("poison", "burn", "sleep", "paralyze", "confuse")


def _supported_attack_tags(tags) -> list:
    out = []
    for t in tags or []:
        if any(str(t).startswith(p) for p in ATTACK_TAG_PREFIXES):
            out.append(t)
        else:
            return []          # 出现不支持 → 整条不收
    return out


def _supported_ability_tags(tags, location: str) -> list:
    out = []
    for t in tags or []:
        if any(str(t).startswith(p) for p in ABILITY_TAG_PREFIXES) \
                or str(t) in ABILITY_EXACT_TAGS:
            out.append(t)
        else:
            return []
    return out


def _cost_of(cost_list) -> dict:
    out: dict = {}
    for c in cost_list or []:
        out[c] = out.get(c, 0) + 1
    return out


def _items_by_name(parse: dict) -> dict:
    out = {}
    for it in (parse or {}).get("items") or []:
        out.setdefault((it.get("target"), it.get("name")), it)
    return out


def _unsupported_any(parse: dict) -> list:
    bad = []
    for it in (parse or {}).get("items") or []:
        for u in it.get("unsupported") or []:
            bad.append(u)
    return bad


def build_pokemon(card: dict, items: dict, name_key: str) -> Optional[dict]:
    hp = card.get("hp")
    types = card.get("types") or []
    if hp is None or not types:
        return None
    # 无解析产物的宝可梦：只允许「纯伤害招式、无特性、无其他文本」的空壳卡。
    # 有卡级文本（其他文本）而解析链没覆盖时，宁可整卡不收，避免静默丢规则。
    if not card.get("parse") and card.get("other_text"):
        return None
    attacks = []
    for a in card.get("attacks") or []:
        it = items.get(("attack", a.get("name")))
        tags = _supported_attack_tags((it or {}).get("engine_tags"))
        if (it or {}).get("unsupported"):
            return None
        effect = a.get("effect") or ""
        if effect and not tags:
            return None                     # 有效果文本但无可执行 tag → 不收
        attacks.append({
            "name": a.get("name"),
            "cost": _cost_of(a.get("cost")),
            "damage": int(a.get("damage") or 0),
            "effects": tags,
            "desc": effect,
            "name_zh": a.get("name_zh") or "",
            # 中文效果文本来自卡库 i18n；缺译文留空，展示层回退 desc
            "desc_zh": a.get("effect_zh") or "",
        })
    abilities = []
    for ab in card.get("abilities") or []:
        it = items.get(("ability", ab.get("name")))
        loc = "hand" if str(ab.get("effect") or "").lower().find("discard this") >= 0 \
            else "field"
        tags = _supported_ability_tags((it or {}).get("engine_tags"), loc)
        if (it or {}).get("unsupported"):
            return None
        if not tags:
            return None                     # 特性无法执行 → 不收（保真优先）
        abilities.append({
            "name": ab.get("name"),
            "name_zh": ab.get("name_zh") or "",
            "tag": tags[0],
            "location": loc,
            "desc": ab.get("effect") or "",
            "desc_zh": ab.get("effect_zh") or "",
        })
    if not attacks and not abilities:
        return None
    rule = str(card.get("rule") or "").lower()
    # 2026-11-02：Mega ex 的「被击倒取 3 张」已由引擎建模（GameConfig.mega_prizes），
    # 此处不再整族拒收。`is_ex` 对 Mega 仍为 True（它确实是 ex），靠 `is_mega` 区分取卡数。
    is_ex = "ex" in rule or str(card.get("name") or "").rstrip().endswith(" ex")
    return {
        "name": card["name"],
        "name_zh": card.get("name_zh") or "",
        "supertype": "Pokemon",
        "stage": int(card.get("stage") or 0),
        "hp": int(hp),
        "types": types,
        "weakness": card.get("weakness"),
        "resistance": card.get("resistance"),
        "retreat": int(card.get("retreat") or 1),
        "is_ex": bool(is_ex),
        # B3（2026-10-28）：条件/计数源的分类字段（Mega / Ancient / Future / …）
        "is_mega": "mega" in rule,
        "category": card.get("category") or "",
        "evolves_from": card.get("evolves_from"),
        "attacks": attacks,
        "abilities": abilities,
        "real": {"set": card.get("series"), "number": card.get("number")},
        "provenance": "dataset-derived",
        "fidelity": "rule-parser-derived",
        "source_card_id": card.get("id"),
        "name_key": name_key,
    }


def build_trainer(card: dict, items: dict) -> Optional[dict]:
    it = items.get(("card", card["name"]))
    if (it or {}).get("unsupported"):
        return None
    tags = (it or {}).get("engine_tags") or []
    subtype = str(card.get("subtype") or "")
    is_tool = "tool" in subtype.lower()
    is_stadium = "stadium" in subtype.lower()
    tag = next((t for t in tags
                if any(str(t).startswith(p) for p in TRAINER_TAGS)), None)
    if not tag:
        return None
    is_fossil = str(tag).startswith("fossil:")
    fossil = None
    if is_fossil:
        # 化石卡：卡级 tag 必须**唯一**（化石文本整段就是这一条机制），且卡面
        # 特性必须**全部可执行** —— 化石上场后是宝可梦，特性由引擎的静态钩子
        # 执行；任何一条未建模都整卡不收（绝不静默丢特性）。
        if len(tags) != 1:
            return None
        fossil = _build_fossil(card, items, tag)
        if fossil is None:
            return None
    elif card.get("abilities"):
        # 其它训练家卡带特性：引擎没有承载处（训练家卡不会作为宝可梦上场）→
        # 整卡不收，避免卡面规则被静默丢弃。
        return None
    if is_stadium:
        # 场地同理：必须**唯一 tag 且是 stadium:***（卡面常夹带触发式副效果）
        st = [t for t in tags if str(t).startswith("stadium:")]
        if len(st) != 1 or len(tags) != 1:
            return None
    if is_tool:
        # ⚠ 道具必须**只认 tool: tag**：道具卡面里常夹带「draw / search」等
        #   触发式子效果，若按普通训练家 tag 准入，就会变成**无条件生效**
        #   （静默加强）。这里要求：唯一的可执行 tag 必须是 tool:*。
        tool_tags = [t for t in tags if str(t).startswith("tool:")]
        if len(tool_tags) != 1 or len(tags) != 1:
            return None
    # 多 tag 训练家（2026-10-28 第十二批）：引擎的 `TrainerDef.tag` 只有**一个**
    # tag，派发链也只执行一条。若解析产出多条可执行 tag（Carmine 的
    # 「discard:all + draw:5」、Surfer 的「switch_self + draw_to:5」等），
    # 只收第一条会**静默少做**后半段效果 → 整卡不收（宁可未建模）。
    # 需要多动作的卡必须在解析侧合并成**一条** tag（如 Wally's Compassion
    # 的 `heal:all:mega:energy`）。
    supported_tags = [t for t in tags
                      if any(str(t).startswith(p) for p in TRAINER_TAGS)]
    if len(supported_tags) != len(tags):
        return None
    if len(supported_tags) > 1:
        return None
    kind = ("tool" if is_tool
            else "stadium" if is_stadium
            else "supporter" if "supporter" in subtype.lower() else "item")
    entry = {
        "name": card["name"],
        "name_zh": card.get("name_zh") or "",
        "supertype": "Trainer",
        "kind": kind,
        "tag": tag,
        "desc": card.get("other_text") or "",
        "desc_zh": card.get("other_text_zh") or "",
        "real": {"set": card.get("series"), "number": card.get("number")},
        "provenance": "dataset-derived",
        "fidelity": "rule-parser-derived",
        "source_card_id": card.get("id"),
    }
    if fossil is not None:
        entry["fossil"] = fossil
    # 打出代价（2026-11-02 第三十六批）：「You can use this card only if you
    # discard N other cards from your hand.」→ `TrainerDef.play_cost`。
    # ⚠ 必须在这里**显式**搬运：它不是 tag，`TRAINER_TAGS` 白名单管不到它；
    # 漏搬的后果是「要付账的卡变成免费」（静默加强），且对局里完全看不出来。
    _pc = str((it or {}).get("play_cost") or "")
    if _pc:
        entry["play_cost"] = _pc
    return entry


def _build_fossil(card: dict, items: dict, tag: str) -> Optional[dict]:
    """化石卡的「宝可梦形态」定义：HP / 属性 / 卡面特性（全部可执行才收）

    tag 形如 ``fossil:60:colorless``。特性按**特性侧** tag 白名单准入，
    与 `build_pokemon` 同口径（未建模 / 空 → 整卡不收）。
    """
    parts = str(tag).split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    hp = int(parts[1])
    if hp <= 0:
        return None
    abilities = []
    for ab in card.get("abilities") or []:
        it = items.get(("ability", ab.get("name")))
        tags = _supported_ability_tags((it or {}).get("engine_tags"), "field")
        if (it or {}).get("unsupported") or not tags:
            return None
        abilities.append({
            "name": ab.get("name"),
            "name_zh": ab.get("name_zh") or "",
            "tag": tags[0],
            "location": "field",
            "desc": ab.get("effect") or "",
            "desc_zh": ab.get("effect_zh") or "",
        })
    return {"hp": hp, "types": [parts[2]], "abilities": abilities}


def build_energy(card: dict, items: dict) -> Optional[dict]:
    """能量卡准入：**基本能量**（统一命名）+ **特殊能量**（整段可执行的 senergy tag）

    基本能量：英文名统一为 ``Basic <Type> Energy``（与人工卡池/卡表一致），
    避免同一张草能量以 ``Basic {G} Energy`` 另立条目。

    特殊能量（2026-10-28）：卡级效果必须**整段**被解析成**唯一**一条
    ``senergy:<kind>``（`engine/special_energy.py` 认得该 kind），且没有
    unsupported —— 若只剩「提供能量」一段而附加规则丢了，整卡不收。
    """
    subtype = str(card.get("subtype") or "")
    if "special energy" in subtype.lower():
        it = items.get(("card", card["name"]))
        if (it or {}).get("unsupported"):
            return None
        tags = (it or {}).get("engine_tags") or []
        if len(tags) != 1 or not str(tags[0]).startswith("senergy:"):
            return None
        return {
            "name": card["name"],
            "name_zh": card.get("name_zh") or "",
            "supertype": "Energy",
            "kind": "special",
            "tag": tags[0],
            "type": (card.get("types") or ["colorless"])[0],
            "desc": card.get("other_text") or "",
            "desc_zh": card.get("other_text_zh") or "",
            "real": {"set": card.get("series"), "number": card.get("number")},
            "provenance": "dataset-derived",
            "fidelity": "rule-parser-derived",
            "source_card_id": card.get("id"),
        }
    if "basic energy" not in subtype.lower():
        return None
    etype = None
    types = card.get("types") or []
    if types:
        etype = types[0]
    else:
        import re as _re
        m = _re.search(r"basic\s+([a-z]+)\s+energy", card["name"], _re.I)
        if m:
            etype = m.group(1).lower()
    if not etype:
        return None
    en_name = f"Basic {TYPE_EN.get(etype, etype.title())} Energy"
    return {
        "name": en_name,
        "name_zh": card.get("name_zh") or "",
        "supertype": "Energy",
        "type": etype,
        "desc": "",
        "real": {"set": card.get("series"), "number": card.get("number")},
        "provenance": "dataset-derived",
        "fidelity": "rule-parser-derived",
        "source_card_id": card.get("id"),
    }


def _entry_rank(entry: dict) -> tuple:
    """同一印刷重复候选的保留优先级：效果标签数 > 招式数 > HP。

    注意：**不再按卡名去重**（2026-10-29 第二十一批）。同一卡名的不同印刷
    （SIT 69 vs SFA 19）可以共存放行，由 `uid` 区分；引擎按 uid 寻址，
    缺省解析取「数据集最新印刷」（见 `engine.reallib.catalog`）。
    """
    effects = sum(len(a.get("effects") or []) for a in entry.get("attacks") or [])
    effects += len(entry.get("abilities") or [])
    return (effects, len(entry.get("attacks") or []), int(entry.get("hp") or 0))


def _card_uid(name: str, real: Optional[dict]) -> str:
    """印刷身份键：``<卡名>@<SET> <number>``（无印刷信息 → ``<卡名>@-``）

    卡名仍是引擎/卡组/卡面规则的键（`PokemonDef.name` 不变，进化链与
    「同名」类规则不受影响）；`uid` 只用于**跨印刷寻址**。
    """
    real = real or {}
    s = str(real.get("set") or "").strip().upper()
    n = str(real.get("number") or "").strip()
    return f"{name}@{s} {n}" if s and n else f"{name}@-"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成引擎扩展卡池（卡池扩容）")
    ap.add_argument("--cleaned", type=Path, default=CLEANED)
    ap.add_argument("--pool", type=Path, default=POOL_MAIN, help="人工卡池（去重基准）")
    ap.add_argument("--out", type=Path, default=POOL_EXT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.cleaned.exists():
        raise SystemExit(f"缺少清洗产物：{args.cleaned}")
    data = json.loads(args.cleaned.read_text(encoding="utf-8"))
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    # 人工优先的判据是**印刷**（set+number），不是卡名：同名不同印刷可共存
    manual_uids = {_card_uid(c["name"], c.get("real"))
                   for c in pool.get("cards") or [] if c.get("name")}
    # 基本能量没有印刷差异（同名即同卡）→ 仍按卡名判重，避免无意义的重复条目
    manual_energy = {c["name"] for c in pool.get("cards") or []
                     if c.get("supertype") == "Energy"}

    kept, skipped = [], Counter()
    candidates: list[dict] = []
    by_kind = Counter()
    for card in data.get("cards") or []:
        name = card.get("name")
        uid = _card_uid(name, {"set": card.get("series"),
                               "number": card.get("number")})
        if uid in manual_uids or (
                card.get("supertype") == "Energy" and name in manual_energy):
            skipped["人工卡池已有同一印刷（人工优先）"] += 1
            continue
        parse = card.get("parse")
        if parse:
            bad = _unsupported_any(parse)
            if bad:
                skipped["存在未建模项（整卡不收）"] += 1
                continue
        # 无 parse 的卡不再一律拒绝：
        #   · 无效果文本的宝可梦（纯伤害招式）→ build_pokemon 可安全接收；
        #   · 基本能量 → build_energy 本来就不依赖解析；
        #   · 训练家 / 带效果文本的宝可梦仍会因无 tag 被 build_* 拒绝。
        items = _items_by_name(parse) if parse else {}
        st = card.get("supertype")
        entry = None
        if st == "Pokemon":
            entry = build_pokemon(card, items, card.get("name_key") or "")
        elif st == "Trainer":
            entry = build_trainer(card, items)
        elif st == "Energy":
            entry = build_energy(card, items)
        if entry is None:
            skipped[f"{st} 不可执行"] += 1
            continue
        entry["uid"] = uid
        candidates.append(entry)

    # 印刷级去重：同一 uid 只保留**效果更完整**的一张（同一印刷的重复行）。
    # 卡名相同但印刷不同 → **全部保留**（引擎按 uid 寻址，缺省名解析取最新印刷）。
    best: dict[str, dict] = {}
    for entry in candidates:
        key = entry["uid"]
        old = best.get(key)
        if old is None:
            best[key] = entry
            continue
        skipped["同一印刷重复（保留效果更完整者）"] += 1
        if _entry_rank(entry) > _entry_rank(old):
            best[key] = entry
    kept = list(best.values())
    for entry in kept:
        by_kind[entry["supertype"]] += 1

    payload = {
        "meta": {
            "version": "1.0",
            "date": "2026-09-14",
            "milestone": "卡池扩容：由清洗数据集 + K2 解析链生成扩展卡池",
            "source": "packages/card-db/data/cleaned/cards_challenge.json",
            "generator": "packages/card-db/scripts/gen_engine_pool.py",
            "provenance": "dataset-derived",
            "fidelity": "rule-parser-derived",
            "admission": ("严格准入：仅收录「所有解析条目均已映射到引擎可执行 tag」的卡；"
                          "任一处未建模即整卡不收（避免部分效果静默丢失）。"
                          "无解析产物的卡仅收纯伤害宝可梦与基本能量。"),
            "precedence": ("人工卡池 cards_real.json 优先（**按印刷 set+number**："
                           "同名不同印刷可以共存放行）；条目带 `uid`"
                           "（`<卡名>@<SET> <number>`）用于跨印刷寻址"),
            "honesty": ("本池条目为**规则解析链推导**（非人工逐卡校对），装载侧标记为 "
                        "engine_status=approximated；卡面数值来自清洗数据集，"
                        "效果保真度取决于解析链召回率，差异见未建模动作清单"),
            "counts": {"kept": len(kept), **{f"by_{k}": v for k, v in by_kind.items()},
                       "skipped": dict(skipped)},
        },
        "cards": kept,
    }
    print(f"[pool] 收录 {len(kept)} 张"
          f"（{'宝可梦' if by_kind['Pokemon'] else ''} "
          f"{by_kind['Pokemon']} / 训练家 {by_kind['Trainer']} / "
          f"能量 {by_kind['Energy']}）")
    for k, v in skipped.most_common():
        print(f"[pool]   跳过 {k}：{v}")
    if args.dry_run:
        print("[pool] dry-run：未写盘")
        return 0
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"[out]  {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
