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
                # 第五十九批（2026-11-04）：双方洗回后**各抽不同张数**
                # （Unfair Stamp）。⚠ 与 `draw_both:`（双方各抽同数）**不可互换**。
                "shuffle_draw_split:",
                # 第六十批（2026-11-04）：全队**本回合**增伤（Premium Power Pro /
                # Black Belt's Training）。与 `dmg_plus:`（这一招自己）不同量纲 ——
                # 白名单是过滤，漏写即静默丢弃、卡照样能打。
                "team_boost:",
                "discard_search_energy", "search_energy", "candy",
                # 第九十二批（2026-11-05）：从牌库检索并当场进化（Salvatore）。
                # 该前缀此前只在 ATTACK_TAG_PREFIXES，训练家侧与 `_do_trainer`
                # 都缺 → 卡三处全缺、必然在池外。`:no_ability` 变体由
                # `startswith` 一并覆盖（卡面限定「has no Abilities」）。
                "evolve_from_deck",
                # 第九十二批（2026-11-05）：先弃全部手牌、再逐类别各取 1 张
                # （Larry's Skill）。两条动作合成一条复合 tag —— 训练家只认
                # 一个卡级 tag，拆成 `discard:all` + `search_multi:` 会被整卡拒。
                "discard_search_multi:",
                "search_pokemon", "search_trainer", "search_any",
                # 2026-10-27：检索**铺场**（去向是后备，不是入手）
                "search_bench",
                # 第七十九批（2026-11-05）：检索后**放回牌库顶** /
                # **逐类别各取 1 张**（Ciphermaniac's Codebreaking / Secret Box）
                "search_top:", "search_multi:",
                # S2 道具（2026-10-26）：附着型训练家
                "tool:",
                # S3 场地（2026-10-27）：全场唯一
                "stadium:",
                # 2026-10-27：训练家/整卡级「非检索类」效果（引擎机能早已存在，
                # 只是派发链没接）。前缀互不包含：`draw_to:` vs `draw:`、
                # `discard_search_energy:` vs `discard:` 的第 5/8 位都是 `_`。
                # 第五十八批（2026-11-04）：条件抽牌（`draw_cond:` 第 5 位是 `_`，
                # 不会被 `draw:` 覆盖 —— 白名单是**过滤**，漏写即静默丢弃）。
                "draw_cond:",
                # 第九十八批（2026-11-06）：分支替换的「抽到 N 张」
                # （Ariana「…draw until 5. If all your Pokémon are Team
                # Rocket's, draw until 8 instead」）。⚠ 与 `draw_to:` 互不覆盖：
                # 前缀是 `draw_to_branch:`，若漏写这一行会被白名单**静默丢弃**。
                "draw_to_branch:",
                # 第九十八批（2026-11-06）：弃对手**特殊能量**（Enhanced Hammer）。
                # ⚠ 该前缀原本只出现在 ATTACK_TAG_PREFIXES（招式侧），训练家侧
                # 从来没有 → 卡在池外凭空消失（`build_trainer` 找不到可用 tag）。
                "discard_energy:",
                "draw:", "draw_to:", "heal:", "attach", "gust", "discard:",
                "mill:",
                # 第九十一批（2026-11-05）：检视牌库顶后重排（Deduction Kit）。
                # 引擎 `_apply_attack_effects` 早有该 tag，但 `_do_trainer` 当时
                # 没有派发分支；且该前缀此前只出现在 ATTACK_TAG_PREFIXES，
                # 训练家侧缺它 → 卡在池外凭空消失（对账也看不出）。
                "shuffle_top:",
                # B3（2026-10-28）：抽牌完整性 / 对手洗回手牌
                "draw_both:", "opp_shuffle_draw:",
                # S6（2026-10-28）：弃道具 / 弃场地 / 回手族 / 能量移动 / S5 手牌
                "discard_tool:", "discard_stadium", "bounce:", "energy_move:",
                # 第七十批（2026-11-04）：弃牌堆基本能量洗回牌库（Energy Recycler）
                # —— 复用招式侧 Kyogre「Riptide」的同一 helper `_energy_to_deck`。
                "energy_to_deck:",
                # 第八十二批（2026-11-05）：弃牌堆**宝可梦**洗回牌库（Sacred Ash）。
                # 与 energy_to_deck 来源不同，前缀不重叠，必须显式放行。
                "pokemon_to_deck:",
                # 第八十三批（2026-11-05）：检索后**弃置**（Brilliant Blender）。
                # 与 search_top / search_multi 同族但去向不同，须独立放行。
                "search_discard:",
                "discard_opp_random:", "shuffle_opp_hand:", "discard_opp_to:",
                # 第五十四批：**双方**各弃手牌到剩 N（Hand Trimmer）。
                # 前缀不被 `discard:` 覆盖（第 8 位是 `_` 不是 `:`），必须显式列出。
                "discard_to_both:",
                # 第八十五批（2026-11-05）：道具所附宝可梦被击倒时对手少拿 1 张奖赏
                # （Lillie's Pearl）。引擎 `_ko_prize_reduction` 按 `fp.tool` 前缀
                # 读取，tag 本身以 `ko_prize_minus:` 开头（非 `tool:`），须独立放行。
                "ko_prize_minus:",
                # 第八十九批（2026-11-05）：训练家「本回合多拿奖赏」（Briar）。
                # 卡级 tag，引擎按回合旗标在 `_resolve_ko` 结算时消费。
                "prize_bonus:",
                # 化石卡（2026-10-28）：当作 N-HP 基础宝可梦上场
                "fossil:",
                # 检视牌库顶（2026-10-30 第二十批）：筛选 + 去向 + 打出前置条件
                "recon:",
                # 对手手牌操作（2026-10-30 第三十二批）：**我方选**口径
                # ⚠ 与上面的 `discard_opp_random:` / `discard_opp_to:` /
                # `shuffle_opp_hand:` 相反：那三个是「对手自选 ⇒ 引擎随机」，
                # 本族卡面写的是 «you discard a card you find there»（使用者选）。
                "opp_hand:",
                # 2026-11-02 第三十八批：弃对手道具/特殊能量 **或** 弃竞技场
                # （Blowtorch）。前缀**不**被 `discard_tool:` 覆盖（第 13 位
                # 是 `_` 不是 `:`），必须显式列出。
                "discard_tool_or_senergy:",
                # 2026-11-02 第三十九批：选 N 只各附（Janine's Secret Art）
                # 与训练家抛币门（Energy Coin）
                "attach_each_pick:", "coin_gate_attach:",
                # 第五十批：道具形态的奖赏减少（Lillie's Pearl）
                "ko_prize_minus:",
                # 第五十一批：训练家复合句（一条 tag 承载两个动作）——
                # `status_multi:` 多状态 / `switch_draw_to:` 换位+抽 /
                # `gust_confuse` 拉后备+混乱。训练家只认一个卡级 tag，
                # 拆成两条会被整卡拒收（此前 9 张训练家因此静默消失）。
                "status_multi:", "switch_draw_to:", "gust_confuse",
                # 2026-11-03 第四十四批：同一枚币的双分支（Venture Bomb）
                # `coin_flip:heads:<a>|tails:<b>` —— 训练家侧抛一次后派发
                "coin_flip:",
                # 2026-11-03 第四十五批：双方洗回手牌 + 抛币分抽（Harlequin）
                "shuffle_both_coin_draw:",
                # 2026-11-03 第四十六批：全队限时减伤（Iron Defender）/
                # 单体限时护盾 + 奖赏数打出前置条件（Acerola's Mischief）
                "team_shield:", "shield_choose:",
                # 第一百三十四批（2026-11-08）：看**牌库底** N 张取 1 张
                # （Dusk Ball）与「同一只身上的道具 + 特殊能量一起弃」（Ruffian）。
                # ⚠ `recon_bottom:` **不**被 `recon:` 覆盖（第 6 位是 `_`），
                #   漏写就是静默丢弃（卡凭空消失，对账也看不出来）。
                "recon_bottom:", "discard_tool_senergy:",
                # 第一百三十五批（S1a）：**决策分解**族 —— 「弃任意数量手牌」
                # （Naveen）。引擎挂起 `phase="choice"`，由牌手逐张选；
                # 白名单漏写 = 卡在池外凭空消失。
                "choose:")

# 引擎支持的招式效果 tag 前缀（与 battle._apply_attack_effects 对齐）
ATTACK_TAG_PREFIXES = (
    "poison", "burn", "sleep", "paralyze", "confuse", "status_self:", "spread_free:", "spread:",
    "snipe:", "heal:", "self:", "draw:", "draw_to:", "discard:",
    # 第八十一批（2026-11-05）：清除出招者自身全部特殊状态（Arboliva ex）
    "cure:",
    "discard_energy:", "switch_self", "gust", "attach", "move_damage:",
    "recover:", "dmg_plus:", "dmg_set:", "dmg_minus:", "dmg_zero",
    # 第一百零一批（2026-11-06）：按「上回合受到的招式伤害量」加伤
    # （Mega Heracross ex「Juggernaut Horn」）。无冒号形态，故按整串匹配。
    "dmg_plus_prev_taken",
    # 第一百零七/一百零八批（2026-11-06）：招式**静态使用条件**
    # （Terapagos ex「If you go second, you can't use this attack during your
    # first turn」/ Scream Tail ex「only if you go second, and only during your
    # first turn」）。引擎 `_attack_usable` 在合法动作生成时剔除，不进结算。
    "use_only:", "use_not:",
    # 第一百一十二批（2026-11-08）：**招式自身**里的费用改写
    # （Conkeldurr「ignore all Energy in this attack's cost」/
    #  Crawdaunt「this attack can be used for {D}」）。该前缀此前只在特性白名单，
    #  招式侧缺失 → 整条招式被拒。
    "fx:",
    # 第一百一十九批（2026-11-08）：下回合「基础伤害**改为** N」
    # （Watchog「Focus Energy」）。与 `self_attack_plus_next:` 分开：
    # 落成加伤会让 240 变成「印刷 80 + 240」。
    "self_attack_set_next:",
    # 第一百三十四批（2026-11-08）：四类整句规则 —— 纯信息类展示手牌 /
    # 指示物翻倍 / 能量数禁攻 / 奖赏条件胜利。
    # ⚠ `info:` 是**完美信息等价**（引擎里显式记 no-op 日志），不写进这里
    #   就会让「整句展示手牌」的卡永远在池外（Hoothoot / Espurr / Pidove）。
    "info:", "counters_double:", "atk_energy_block:", "win_if:",
    # 第一百三十五批（S1a/S1b）：决策分解 —— 招式侧「可选能量代价」
    # （Mega Eelektross ex）。训练家侧同名前缀另见 TRAINER_TAGS。
    "choose:",
    # 第一百零七批（2026-11-06）：招式侧弃场地（Chi-Yu「Scorching Earth」）。
    # 该 tag 此前只在特性/训练家侧存在。
    "discard_stadium",
    "ignore_weakness",
    "ignore_resistance",
    "coin:", "coin_each:", "coin_all:", "coin_until_tails:", "shield:", "evolve_from_deck", "mill:",
    # 第五十二批（2026-11-04）：**补白** —— 解析早已产出、引擎也早已支持，
    # 但白名单漏了前缀，等于这些机制从未可用：
    #   · `research:`  招式侧「弃全部手牌再抽 N」（Raging Bolt ex「Burst Roar」）
    "research:",
    # 第五十一批：招式侧多状态（「is now Burned and Confused」）——
    # 此前只落第一个状态，第二个被**静默丢掉**（5 个招式受影响）。
    "status_multi:",
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
    # 第七十九批（2026-11-05）：检索后**放回牌库顶**（Dialga「Time Manipulation」/
    # Ciphermaniac's Codebreaking 是训练家侧，招式侧同机制）。
    # ⚠ 与 `search_pokemon:` 不同：那条是「入手」，这条不增加手牌。
    "search_top:",
    # B3（2026-10-28）：双方同抽 / 对手洗回手牌后抽 / 带类型手牌弃能（含失败门）
    "draw_both:", "opp_shuffle_draw:", "discard_hand_energy:",
    # S6（2026-10-28）：弃道具 / 弃场地 / 回手族 / 能量移动 / S5 手牌
    "discard_tool:", "discard_stadium", "bounce:", "energy_move:",
    "discard_opp_random:", "shuffle_opp_hand:", "discard_opp_to:",
    # 第五十四批：双方各弃手牌到剩 N（招式侧同样已实现派发）
    "discard_to_both:",
    # 攻击复用 / 下回合限制 / 无视效果（2026-10-28）
    "borrow_attack:", "attack_denial:", "attack_cost_up:",
    "retreat_cost_up:", "ignore_effects",
    # 第九批（2026-10-28）：招式击倒 / 检视牌库顶重排
    "knockout:", "shuffle_top:",
    # 第十二批（2026-10-28）：逐只后备各进化一次（Reuniclus）
    "evolve_each_bench",
    # 第四十批（2026-11-02）：**选**至多 N 只各进化一次
    # （Team Rocket's Nidorina）。⚠ 与上面那条是两种目标集合，不能互相顶替：
    # 用 each_bench 代替会把「至多 2 只」变成「全部后备」（静默加强）。
    "evolve_each_pick:",
    # 第四十一批（2026-11-03）：自身附着能量洗回牌库（Mega Skarmory ex 的强制
    # 代价）。⚠ 与 `energy_to_deck:`（弃牌堆基本能量）是两种来源，不能互代。
    "energy_self_to_deck:",
    # 第十三批（2026-10-28）：Checkup 伤害改写（Nidoking ex）
    "checkup_set:",
    # 第十四批（2026-10-28）：伤害指示物直放（不吃弱抗，Haunter / Alakazam）
    "counters:",
    # 第八十四批（2026-11-05）：逐只放到「剩余 HP = N」（PalossandEX /
    # Scolipede）。与 `counters:<个数>` 口径不同，前缀不重叠，须显式放行。
    "counters_to_hp:",
    # 第四十五批（2026-11-03）：逐目标各抛一枚（Mega Zygarde ex）
    # 与抛币选状态（Cradily）——两者都是招式侧 tag
    "coin_per_target:", "status_choose:",
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
    "attach_spread:",
    # 第三十八批（2026-11-02）：自由分配的两个变体
    #   · `attach_spread_flip:` 抛 N 币 × per 张（Mega Audino ex）
    #   · `attach_each_bench:` 每只后备**各** per 张、绑定给那一只（Mega Gardevoir ex）
    "attach_spread_flip:", "attach_each_bench:",
    # 第三十九批（2026-11-02）：双色 or-1-of-each / 选 N 只各附 / 抛到反面为止
    # （Steven's Metagross ex · Janine's Secret Art · Snorlax）
    "attach_spread_multi:", "attach_each_pick:", "attach_flip_until:"
)

# 引擎支持的被动/field 特性 tag 前缀（与 battle 派发对齐）
ABILITY_TAG_PREFIXES = (
    "recon:", "cursed:", "draw:", "move_damage:", "attach_energy:",
    # 第四十九批（2026-11-04）：**补白** —— 特性侧「draw cards until you have N」
    # （Quaquaval「Up-Tempo」/ Iono's Kilowattrel「Flashing Draw」）。引擎早在
    # `_do_ability` 里有该派发，但白名单只写了 `draw:`，`draw_to:` 一直被**静默丢弃**
    # （`startswith("draw:")` 不匹配 `draw_to:`，第 5 位是 `_` 不是 `:`）。
    "draw_to:",
    # 第五十二批：特性侧磨牌（Durant ex）与拉后备（Meowstic「Beckoning Tail」）
    "mill:", "gust",
    # 特性侧自由分配附着（2026-10-30 第三十三批；2026-11-02 起解析侧开始产出）：
    # 引擎在 `_do_ability` 里排在 `attach_energy:` 之前。当前来源是 Magneton SSP 59
    # （场上主动特性 + `:selfko`）；进化触发形态仍需 `evolve_act:` 包装，未产出。
    "attach_spread:",
    # 第三十八批（2026-11-02）：自由分配的两个变体 —— Magneton 时代只放了
    # `attach_spread:`，这两个前缀更长（第 13 位是 `_`），**不会**被它覆盖。
    "attach_spread_flip:", "attach_each_bench:",
    # 第三十九批：Steven's Metagross ex 的「X-Boot」是 field 特性
    "attach_spread_multi:",
    "ko_draw:", "evolve_attach:", "weak_change:", "passive:", "status_self:",
    # 第五十批（2026-11-04）：被击倒时**对手少拿**奖赏卡（Shedinja / Mega Gengar /
    # Munkidori）。与 `ko_prize_plus:`（多拿、挂在取奖赏方）方向相反，不可混用。
    "ko_prize_minus:",
    # 特性侧**全牌库检索**（2026-09-22）：与 recon（检视牌库顶 N 取 1）语义不同
    "search_pokemon:", "search_trainer:", "search_any:", "search_energy:",
    # 特性回复（2026-10-27）：范围语义见 battle._apply_heal
    "heal:",
    # 第十批（2026-10-28）：进化触发 / 被击倒触发
    "evolve_act:", "on_ko:", "ko_prize_plus:",
    # 第一百批（2026-11-06）：上场触发（Drilbur「Dig Dig Dig」）——
    # 「When you play this Pokémon from your hand onto your Bench …」。
    # 不包这一层就是普通 field 特性 = **每回合**都能再发动一次。
    "on_bench_act:",
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
    # 第五十四批：双方各弃手牌到剩 N（Hand Trimmer）
    "discard_to_both:",
    # 第二十批（2026-10-29）：特性直放伤害指示物（Dusclops / Dusknoir「诅咒爆裂」）
    "counters:",
    # 第四十四批（2026-11-03）：特性侧抛币（Dustox「Boisterous Wind」）——
    # `coin:heads:bounce:energy:opp_active:1`，由 `_do_ability` 抛一次后派发
    "coin:",
    # 第一百一十一批（2026-11-06）：换位并指定属性/例外，换入后施加状态
    # （Pecharunt ex「Subjugating Chains」）。引擎侧 `_ability_legals` 会按
    # 后备位置逐条列动作，不写前缀 → 整卡在池外**静默消失**。
    "switch_choose:",
    # 第一百三十五批（S1c）：决策分解 —— 手牌与牌库顶互换（Gumshoos）。
    "choose:",
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
    # 第九十批（2026-11-05）：上游数据集把 Pokémon ex 的 **Tera 规则文本**
    # （「As long as this Pokémon is on your Bench, prevent all damage done to
    # this Pokémon by attacks」）归进了 `attacks[]`，解析产物只有一条
    # `passive:bench_immune_dmg`。该文本是**常驻被动**，引擎只在
    # `_is_bench_immune()`（battle.py:10367）读 `pdef.abilities[].tag`；
    # 留在 `attacks.effects` 里会被 `_supported_attack_tags` 滤空 → 整卡拒收
    # （PalossandEX / Miraidon ex / Koraidon ex 因此长期在池外）。
    # → 转写为卡级被动词条（`location="field"`，legals 已跳过 `passive:` 前缀，
    #   battle.py:453），与池内 29 张人工/派生卡的既有写法一致。
    # ⚠ **禁止**把 `passive:*` 加进 `ATTACK_TAG_PREFIXES`：那会让被动在**出招时**
    #   才触发（攻击才有免伤）—— 语义反转的静默错位。
    # 仅当该招式**全部** tag 都是 `passive:` 时才转写；混合 tag 一律走原逻辑
    # （宁可整卡拒收，也不猜哪条是招式、哪条是被动）。
    passive_abilities: list = []
    pseudo_attack_idx: set = set()
    for _i, a in enumerate(card.get("attacks") or []):
        _it = items.get(("attack", a.get("name"))) or {}
        _raw = _it.get("engine_tags") or []
        if _it.get("unsupported") or not _raw:
            continue
        if not all(str(t).startswith("passive:") for t in _raw):
            continue
        pseudo_attack_idx.add(_i)
        for t in _raw:
            passive_abilities.append({
                "name": a.get("name") or "",
                "name_zh": a.get("name_zh") or "",
                "tag": str(t),
                "location": "field",
                "desc": a.get("effect") or "",
                "desc_zh": a.get("effect_zh") or "",
                "cost": "",
            })
    attacks = []
    for _i, a in enumerate(card.get("attacks") or []):
        if _i in pseudo_attack_idx:
            continue                        # 已转写为卡级被动，不再作为招式
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
            # 发动代价（2026-11-04 第四十九批）：「You must … in order to use
            # this Ability.」—— 由解析链剥句得到，引擎在效果**之前**支付。
            "cost": str((it or {}).get("cost") or ""),
        })
    # 伪招式转写来的卡级被动（见上方 Tera 说明）合并进 abilities
    abilities.extend(passive_abilities)
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
        # ⚠ 道具必须**只认 tool: tag**（或 `ko_prize_minus:`，见第八十五批）：
        # 道具卡面里常夹带「draw / search」等触发式子效果，若按普通训练家 tag
        # 准入，就会变成**无条件生效**（静默加强）。这里要求：唯一的可执行 tag
        # 必须是 tool:* 或 ko_prize_minus:*（被动奖赏修正，引擎按 `fp.tool` 前缀
        # 在击倒点读取，不参与 `_do_trainer` 派发）。
        tool_tags = [t for t in tags
                     if str(t).startswith("tool:")
                     or str(t).startswith("ko_prize_minus:")]
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
    # 打出**前置条件**（2026-11-04 第五十九批）：「You can use this card only if
    # you have any Tera Pokémon in play.」→ `TrainerDef.play_gate`。
    # ⚠ 与 `play_cost` 一样**不是** tag，白名单管不到 → 必须显式搬运；
    # 漏搬 = 门槛静默消失（卡变成随时可打），且对局里完全看不出来。
    _pg = str((it or {}).get("play_gate") or "")
    if _pg:
        entry["play_gate"] = _pg
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
