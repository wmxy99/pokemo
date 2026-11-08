# -*- coding: utf-8 -*-
"""PokéLab Agent · 状态编码器

把 engine.Battle.view() 的结构化观测编码为**定长特征向量**（供策略网络/MCTS 先验），
并把 legal_actions 编码为**动作特征**（每候选一向量）。

v1 编码设计（实测 STATE_DIM=51 / ACTION_DIM=14，以模块常量推断值为准）：
  - 状态向量：双方 active（血量比/伤害/分色能量/状态/阶段）· 后备数量与前3槽血量 ·
    奖赏 · 资源计数（牌库/手牌/supporter/附能/回合）
  - 动作向量：verb one-hot(7) + 参数数值归一(3) + 训练家 tag one-hot(4)
    对「不可知细节」（对手手牌）一律不编码 → 保证仅用公开信息。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

ENGINE_TYPES = ("grass", "fire", "water", "lightning", "psychic", "fighting",
                "darkness", "metal", "dragon", "colorless")

# ⚠ 第一百三十五批（S1a）**故意没有**把 `choose` / `stop` 加进来：
#   加进去就 +2 维 → action_dim 14→16 / 31→33 → 现有 `artifacts/k1_rich.pt`
#   全部不兼容（6 个加载用例 + api 的最小训练用例当场红）。
#   「加动词 + 重训 + 重测 K1」必须**同一个 PR** 完成（方案 §6.3 / S1b），
#   否则要么权重报废、要么编码层静默把 choose 当 pass（两者都不可接受）。
#   在此之前：动作字符串仍原样带 `verb="choose"/"stop"` 传给引擎（规则侧正确），
#   只是策略特征里两者都落成「未知动词 → 全零 one-hot」—— 这是**策略特征缺口**，
#   不是规则缺口；S1b 一并修。
VERBS = ("play", "attach", "attack", "retreat", "evolve", "trainer", "pass")
TRAINER_TAGS = ("poffin", "ultra_ball", "hammer", "boss")


def _norm_hp(d: dict) -> float:
    """血量归一化（参考系 max 320 → [0,1]）"""
    if not d:
        return 0.0
    return min(1.0, (d.get("hp", 0) - d.get("dmg", 0)) / 320.0)


def _energy_vec(d: dict) -> list:
    """能量分色计数 → 10 维（截断 6/色）"""
    em = (d or {}).get("energy_map") or {}
    return [min(6.0, float(em.get(t, 0))) for t in ENGINE_TYPES]


def encode_state(view: dict, cfg: Optional["EncoderConfig"] = None) -> list:
    """view → 定长状态特征 list[float]

    无 cfg 时回退到会话激活配置（set_active_encoder）→ 默认 None = 基础 51 维。
    cfg.features 追加特征域（bad-case 迭代入口：'hand' / 'meta'）。
    """
    cfg = cfg if cfg is not None else _ACTIVE_CFG
    me, opp = view["me_state"], view["opp_state"]
    s: list[float] = []
    ma, oa = me["active"], opp["active"]
    # 双方 active：血量/伤害比/能量(10)/状态(5)/阶段
    s += [_norm_hp(ma)]
    s += [min(1.0, (ma.get("dmg", 0) or 0) / 320.0) if ma else 0.0]
    s += _energy_vec(ma)
    s += [1.0 if t in (ma.get("status") or []) else 0.0
          for t in ("poison", "burn", "sleep", "paralyze", "confuse")] \
        if ma else [0.0] * 5
    s += [float((ma or {}).get("stage", 0))]
    s += [_norm_hp(oa)]
    s += [min(1.0, (oa.get("dmg", 0) or 0) / 320.0) if oa else 0.0]
    s += _energy_vec(oa)
    s += [1.0 if t in (oa.get("status") or []) else 0.0
          for t in ("poison", "burn", "sleep", "paralyze", "confuse")] \
        if oa else [0.0] * 5
    s += [float((oa or {}).get("stage", 0))]
    # 后备（固定前 3 槽：数量 + 每槽血比）
    mb, ob = me["bench"], opp["bench"]
    s += [float(len(mb))]
    for fp in mb[:3]:
        s += [_norm_hp(fp)]
    for _ in range(3 - min(3, len(mb))):
        s += [0.0]
    s += [float(len(ob))]
    for fp in ob[:3]:
        s += [_norm_hp(fp)]
    for _ in range(3 - min(3, len(ob))):
        s += [0.0]
    # 奖赏与资源
    s += [float(me["prizes_left"]), float(opp["prizes_left"])]
    s += [float(me.get("deck_count", 0)), float(me.get("hand_count", 0))]
    s += [float(me.get("supporter_used", 0)),
          float(me.get("attached_energy_this_turn", 0))]
    s += [float(view.get("turn", 0))]
    # 可选特征域（bad-case 迭代：cfg.features）
    if cfg is not None:
        for name in cfg.features:
            s += _extra_features(view, name)
    return s


def _extra_features(view: dict, name: str) -> list:
    me, opp = view["me_state"], view["opp_state"]
    if name == "hand":
        # 我方手牌构成（公开：我方手牌全可见）
        hand = me.get("hand") or []
        pk = sum(1 for c in hand if c.get("type") == "pokemon")
        en = sum(1 for c in hand if c.get("type") == "energy")
        tr = sum(1 for c in hand if c.get("type") in ("item", "supporter"))
        return [min(1.0, pk / 10.0), min(1.0, en / 10.0),
                min(1.0, tr / 10.0), min(1.0, len(hand) / 10.0)]
    if name == "meta":
        mp = me.get("prizes_left", 6)
        op = opp.get("prizes_left", 6)
        turn = float(view.get("turn", 0))
        return [min(1.0, turn / 100.0),
                mp / max(1.0, mp + op),
                # S3（2026-11-08）：奖赏卡**真发牌**后，开局牌库是 60-7-6=47 张，
                # 归一化分母必须跟着从 60 改到 47 —— 否则这个特征永远到不了 1.0
                # （实测 47/60=0.78），网络看到的「牌库充足」区间被整体压缩。
                # ⚠ 该口径与「只计数」模式（real_prizes=False，分母仍是 60）不同；
                #   权重若在旧口径上训练，切换会有一次性的分布偏移（见 README）。
                min(1.0, me.get("deck_count", 0) / 47.0),
                min(1.0, me.get("discard_count", 0) / 60.0)]
    raise ValueError(f"未知特征域 {name}（可选 hand/meta）")


def encode_action(view: dict, action: dict,
                  cfg: Optional["EncoderConfig"] = None) -> list:
    """单个合法动作 → 动作特征

    基础块（14 维，历史行为）：verb one-hot(7) + 参数归一(3) + tag one-hot(4)

    ⚠ **基础块的一个结构性缺陷**（K1 攻坚持平时定位到）：`index/attack/target`
    只是**原始下标归一化**，而下标与卡牌身份无关 —— 例如 `{"verb":"attach",
    "target":0}` 与 `"target":1` 只差 0.0 vs 0.167，网络**无法知道**目标是谁
    （战斗位 60HP 电花 还是 后备 70HP 电光鼠、已附几张能量、是否将被打倒）。
    实测该引擎的 attach 决策空间就是 5 个下标 → **策略无法区分"能量该附给谁"**。

    富块（`cfg.action_features == "rich"`，+17 维）：把"**被作用实体**"公开属性
    编码进去（HP/已受伤/能量数/阶级/ex/状态/被打倒风险/撤退费/最大招式伤害…
    + 招式自身的伤害与费用）。全部来自 `view` 公开信息，不使用隐藏信息。
    """
    cfg = cfg if cfg is not None else _ACTIVE_CFG
    verb = action.get("verb", "pass")
    a: list[float] = [0.0] * len(VERBS)
    if verb in VERBS:
        a[VERBS.index(verb)] = 1.0
    # 数值参数（归一化到 [0,1]）
    a += [min(1.0, action.get("index", 0) / 10.0),
          min(1.0, action.get("attack", 0) / 5.0),
          min(1.0, action.get("target", 0) / 6.0)]
    tag = action.get("tag", "")
    tg = [1.0 if tag == t else 0.0 for t in TRAINER_TAGS] if tag else [0.0] * 4
    a += tg
    if cfg is not None and getattr(cfg, "action_features", "basic") == "rich":
        a += _rich_action_features(view, action)
    return a


def _rich_action_features(view: dict, action: dict) -> list:
    """富块（定长 17 维）：描述动作**直接作用的实体**（下标 → 卡牌属性）

    下标约定（实测自 `engine/battle.py` 的 legal_actions 生成处）：
      · `attach.target`：0 = 战斗位；bi+1 = 后备 bi
      · `retreat.bench`：bi（**不 +1**）；被撤退的是战斗位
      · `play.index`：手牌下标
      · `attack.attack`：招式下标（招式信息在 action 自身里，含 damage/cost_map）
    """
    ms = (view or {}).get("me_state") or {}
    active = ms.get("active")
    bench = ms.get("bench") or []
    hand = ms.get("hand") or []
    verb = action.get("verb", "pass")

    ent = None                       # 被作用的宝可梦
    card = None                      # 被作用的卡（手牌）
    if verb == "attach":
        t = action.get("target", 0) or 0
        ent = active if t == 0 else (bench[t - 1] if 1 <= t <= len(bench) else None)
    elif verb == "retreat":
        bi = action.get("bench", 0) or 0
        ent = bench[bi] if 0 <= bi < len(bench) else None
    elif verb in ("play", "evolve"):
        i = action.get("index", 0) or 0
        card = hand[i] if 0 <= i < len(hand) else None
        if card is not None and card.get("type") == "pokemon":
            ent = card                  # 手牌宝可梦也能算实体特征
    elif verb == "attack":
        ent = active
    elif verb == "pass":
        pass

    ent = ent or {}
    card = card or {}
    atks = ent.get("attacks") or []
    hp = float(ent.get("hp", 0) or 0)
    dmg = float(ent.get("dmg", 0) or 0)
    max_dmg = max([float(x.get("damage", 0) or 0) for x in atks], default=0.0)
    ctype = card.get("type") or ""
    cost_total = 0.0
    for pair in (action.get("cost_map") or []):
        try:
            cost_total += float(pair[1])
        except Exception:                                # noqa: BLE001
            pass
    return [
        1.0 if ent is active and ent else 0.0,
        min(1.0, max(0.0, hp - dmg) / 320.0),
        min(1.0, dmg / 320.0),
        min(1.0, float(ent.get("energy", 0) or 0) / 8.0),
        min(1.0, float(ent.get("stage", 0) or 0) / 2.0),
        1.0 if ent.get("ex") else 0.0,
        min(1.0, len(ent.get("status") or []) / 3.0),
        1.0 if (ent and dmg >= hp > 0) else 0.0,          # 将被击倒风险
        min(1.0, float(ent.get("retreat", 0) or 0) / 4.0),
        min(1.0, max_dmg / 320.0),
        min(1.0, len(atks) / 4.0),
        1.0 if ctype == "pokemon" else 0.0,
        1.0 if ctype == "energy" else 0.0,
        1.0 if ctype in ("item", "supporter") else 0.0,
        min(1.0, hp / 320.0),
        min(1.0, float(action.get("damage", 0) or 0) / 320.0),
        min(1.0, cost_total / 5.0),
    ]


RICH_ACTION_EXTRA = 17


def infer_config_from_state_dict(sd: dict) -> Optional["EncoderConfig"]:
    """从 checkpoint 的权重形状**反推编码配置**（None = legacy 51/14）

    为什么需要：权重与编码器强绑定（state_dim / action_dim 不同），而
    `load_net` 过去按**当前会话**配置建网 —— 拿 rich 权重去评估时直接
    `size mismatch`（实测：`action_enc.0.weight` 31 vs 14），且调用方得记着
    先 `set_active_encoder`。改为从权重自动推断，既少一类坑，也让评估脚本
    （k1_accept / calibrate_value）无需额外参数。

    可推断的组合（当前工程实际使用的两种）：
      · state 51 + action 14 → legacy/basic（返回 None，与历史行为一致）
      · state 59 + action 31 → full(hand+meta) + rich
    其它维度抛错要求显式配置 —— **不猜**（feature 顺序会影响向量布局）。
    """
    try:
        sdim = int(sd["ctx.0.weight"].shape[1])          # (hidden, state_dim)
        adim = int(sd["action_enc.0.weight"].shape[1])   # (hidden//2, action_dim)
    except Exception:                                    # noqa: BLE001
        return None
    base_state = STATE_DIM
    if sdim == base_state and adim == 14:
        return None
    if sdim == base_state + 8 and adim == 31:
        return EncoderConfig(features=("hand", "meta"), action_features="rich")
    if sdim == base_state + 8 and adim == 14:
        return EncoderConfig(features=("hand", "meta"))
    if sdim == base_state and adim == 31:
        return EncoderConfig(action_features="rich")
    raise ValueError(
        f"无法从权重推断编码配置（state_dim={sdim} / action_dim={adim}）—— "
        f"请显式 set_active_encoder() 指定；**不要**用当前会话默认值硬套"
        f"（维度不符会报错，语义不符会静默算错）。")


def apply_config_from_state_dict(sd: dict) -> Optional["EncoderConfig"]:
    """按 checkpoint 推断并**激活**编码配置；返回推断结果（None = legacy）"""
    cfg = infer_config_from_state_dict(sd)
    set_active_encoder(cfg)
    return cfg


def legal_actions_from_view(view: dict) -> list:
    return view.get("legal") or [{"verb": "pass"}]


STATE_DIM = None  # 惰性求值（由首次 encode_state 推断，见模块底部探针）


@dataclass(frozen=True)
class EncoderConfig:
    """编码配置（bad-case 迭代入口）。

    features 可选域：'hand'（我方手牌构成 +4）· 'meta'（回合/资源比例 +4）。
    组合维度 = 51 + 4·n，由 encoder_dim() 给出。

    action_features：'basic'（14 维，历史行为）或 'rich'（+17 维，见 encode_action
    的说明 —— 把"被作用实体"的属性编码进动作特征）。**改动它必须重新训练**。
    """
    features: tuple = ()
    device: str = "cpu"
    action_features: str = "basic"


# 会话级激活配置：set_active_encoder(cfg) 后，无参 encode_state/encoder_dim
# 自动采用扩展特征域（训练/eval 用 --features full 开启；默认 None=51 维 legacy）。
_ACTIVE_CFG: Optional["EncoderConfig"] = None


def set_active_encoder(cfg: Optional["EncoderConfig"]) -> None:
    """设置会话级编码配置（传 None 恢复 legacy 51 维）"""
    global _ACTIVE_CFG
    _ACTIVE_CFG = cfg


def active_encoder() -> Optional["EncoderConfig"]:
    return _ACTIVE_CFG


def encoder_dim(cfg: Optional[EncoderConfig] = None) -> int:
    """状态维度：传入 cfg 优先，否则用会话激活配置；均无 → 51 legacy"""
    cfg = cfg if cfg is not None else _ACTIVE_CFG
    if cfg is None:
        return STATE_DIM
    return STATE_DIM + sum(
        4 for f in cfg.features
        if f in ("hand", "meta"))  # 未知域抛错交给 encode 路径


def action_dim(cfg: Optional[EncoderConfig] = None) -> int:
    """动作特征维度（随 cfg.action_features 变化）。

    **构网时必须用它而不是常量 `ACTION_DIM`** —— 否则 rich 模式下
    `action_enc` 的输入维度会与编码输出不匹配（直接报错，或更糟：静默错位）。
    `ACTION_DIM` 仅作为 legacy 常量保留（历史脚本兼容）。
    """
    cfg = cfg if cfg is not None else _ACTIVE_CFG
    base = len(VERBS) + 3 + len(TRAINER_TAGS)
    if cfg is not None and getattr(cfg, "action_features", "basic") == "rich":
        return base + RICH_ACTION_EXTRA
    return base


def _probe_dim() -> int:
    probe = {"me": 0, "turn": 1, "phase": "main", "finished": False,
             "me_state": {"hand": [], "active": None, "bench": [],
                          "prizes_left": 6, "deck_count": 60, "hand_count": 0,
                          "discard_count": 0, "supporter_used": 0,
                          "attached_energy_this_turn": 0},
             "opp_state": {"hand_count": 0, "active": None, "bench": [],
                           "prizes_left": 6, "deck_count": 60,
                           "discard_count": 0},
             "legal": []}
    return len(encode_state(probe))


STATE_DIM = _probe_dim()
ACTION_DIM = len(encode_action({}, {"verb": "pass"}))
