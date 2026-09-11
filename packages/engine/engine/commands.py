# -*- coding: utf-8 -*-
"""PokéLab Engine · 自由文本指令解析（中文别名 → 合法动作）

用途：让「招式名 / 卡名 / 动词」本身成为可执行的指令输入，而不必记忆索引。
数据键仍是英文（`name`），中文既是**展示名**也是**输入别名**。

支持的写法（大小写、空格、标点均忽略）：

| 输入 | 解析结果 |
|---|---|
| `幻影俯冲` / `Phantom Dive` | `{"verb": "attack", "attack": 0}` |
| `攻击 幻影俯冲` / `出招 幻影俯冲` | 同上（动词前缀可省略或省略式） |
| `高级球` / `Ultra Ball` | `{"verb": "trainer", "hand": 3}` |
| `侦察哨戒` / `Recon Scout` | `{"verb": "ability", ...}`（取首个可用） |
| `附能量到后备1` / `贴能量 战斗位` | `{"verb": "attach", "target": n}` |
| `撤退` / `retreat` | `{"verb": "retreat", "bench": 0}` |
| `结束回合` / `pass` / `过` | `{"verb": "pass"}` |

模糊匹配按顺序：精确 → 前缀 → 包含 → 动词关键词。含歧义（多候选）时返回
`ambiguous=True` 并在 `candidates` 中给出可选项，由调用方决定。
"""
from __future__ import annotations

import re
from typing import Optional

# 名称型字段的优先顺序：指令里带哪个就用哪个（中文展示名优先，英文键兼容）
NAME_KEYS = ("attack_name", "attack_zh", "move", "move_name",
             "display", "name_zh", "name", "card", "card_name")

# 动词关键词（中文别名 → 引擎 verb）
VERB_ALIASES = {
    "pass": ("结束回合", "结束", "过", "不要了", "pass", "end turn", "endturn"),
    "retreat": ("撤退", "换人", "后撤", "retreat"),
    "attach": ("附能量", "贴能量", "上能量", "充能", "attach", "附能"),
    "attack": ("攻击", "出招", "打", "attack", "招式"),
    "trainer": ("训练家", "道具", "支援者", "使用卡", "trainer", "item"),
    "ability": ("特性", "能力", "ability"),
    "play": ("上场", "放置", "打出", "play"),
    "evolve": ("进化", "evolve"),
}

# 位置别名（attach / retreat 的目标）
SLOT_ALIASES = {
    0: ("战斗位", "前排", "active", "出战位", "场上"),
    1: ("后备1", "后备 1", "备战1", "bench 1", "后排1"),
    2: ("后备2", "后备 2", "备战2", "bench 2", "后排2"),
    3: ("后备3", "后备 3", "备战3", "bench 3", "后排3"),
    4: ("后备4", "后备 4", "备战4", "bench 4", "后排4"),
    5: ("后备5", "后备 5", "备战5", "bench 5", "后排5"),
}

_PUNCT = re.compile(r"[\s：:，,。.、\-_/\\（）()\[\]「」【】'\"!！?？]+")


def norm(text) -> str:
    """归一化：去空白/标点/全角半角差异，转小写"""
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = s.replace("ａ", "a").replace("　", " ")
    return _PUNCT.sub("", s)


def labels_of(action: dict) -> list:
    """一个动作可被称呼的名字（中文优先，其次英文，最后动词词）"""
    out = []
    for key in ("display", "name_zh", "name"):
        v = action.get(key)
        if v:
            out.append(v)
    verb = action.get("verb")
    if verb:
        out.append(verb)
        out.extend(VERB_ALIASES.get(verb, ()))
    return out


def _score(cand_norm: str, q: str) -> int:
    """匹配打分（0=不匹配；越大越好）"""
    if not cand_norm or not q:
        return 0
    if cand_norm == q:
        return 100
    if cand_norm.startswith(q) or q.startswith(cand_norm):
        return 80
    if q in cand_norm or cand_norm in q:
        return 60
    return 0


def best_match(candidates: list, text: str) -> Optional[int]:
    """在字符串候选列表中找最匹配的一项，返回下标（无匹配 → None）

    同分时取**最短候选**（更具体），避免长名吞掉短名。
    """
    q = norm(text)
    if not q:
        return None
    best, best_key = None, (0, 0)
    for i, c in enumerate(candidates):
        nc = norm(c)
        sc = _score(nc, q)
        if sc <= 0:
            continue
        key = (sc, -len(nc))
        if key > best_key:
            best, best_key = i, key
    return best


def _slot_name(slot: int) -> str:
    """卡位的人类可读名（0=战斗位，n=后备n）"""
    return "战斗位" if int(slot) == 0 else f"后备{int(slot)}"


def _slot_from_text(text: str) -> Optional[int]:
    """从文本里取「卡位/目标」编号（0=战斗位，n=后备n）；无则 None"""
    q = norm(text)
    for slot, names in SLOT_ALIASES.items():
        for n in names:
            if norm(n) and norm(n) in q:
                return slot
    # 「卡位N」「目标N」「后备N」等显式编号
    m = re.search(r"(?:卡位|目标|位置|target|slot)(\d+)", q)
    if m:
        return int(m.group(1))
    m = re.search(r"后备(\d+)", q)
    if m:
        return int(m.group(1))
    return None


def _hand_slot_from_text(text: str) -> Optional[int]:
    """从文本里取「手牌位」编号（如「进化 卡位3 → 目标1」中的 3）"""
    q = norm(text)
    m = re.search(r"(?:手牌|手卡|卡位)(\d+)", q)
    return int(m.group(1)) if m else None


def _starts_with_verb(text: str, verb: str) -> bool:
    q = norm(text)
    return any(q.startswith(norm(a)) for a in VERB_ALIASES.get(verb, ()))


def _choice_key(action: dict) -> tuple:
    """动作的「语义去重键」：同名的多张手牌副本视为同一选择

    手牌里可能有多张同名卡（如 3 张博士的研究），它们**可互换**，
    不应被当成歧义；而同一张卡打到不同目标（如老大的指令换不同后备）
    才是真正的多选。故这里忽略 `hand`/`index` 副本位，保留语义目标。
    """
    verb = action.get("verb")
    return (verb,
            action.get("attack"),
            action.get("name") or action.get("display"),
            action.get("pos"),
            action.get("target"),
            action.get("bench"))


def find_action(legal: list, text: str, verb: Optional[str] = None) -> dict:
    """自由文本 → 合法动作（返回 dict，含解析元信息）

    `verb` 可显式限定动作类型（如 API 传 command + verb="attack"）。
    返回：{ok, action, matched, candidates, ambiguous, error}
    """
    legal = legal or []
    q = norm(text)
    if not q:
        return {"ok": False, "action": None, "error": "空指令", "candidates": []}

    pool = [a for a in legal if (verb is None or a.get("verb") == verb)]

    # 0) 指令清单往返：文本恰好等于某动作的 label → 直接命中
    #    （保证「把 label 原样作为 command 提交」永远可用；同名同义副本直取首个，
    #      语义不同的同名动作仍按歧义处理，绝不静默改目标）
    label_hits = [a for a in legal if norm(command_label(a)) == q]
    if label_hits:
        seen, choices = set(), []
        for a in label_hits:
            k = _choice_key(a)
            if k not in seen:
                seen.add(k)
                choices.append(a)
        if len(choices) == 1:
            return {"ok": True, "action": dict(choices[0]),
                    "matched": command_label(choices[0]), "candidates": []}
        # 同名但目标不同 → 尝试用文本中的编号收窄
        hs, ts = _hand_slot_from_text(text), _slot_from_text(text)
        narrow = [a for a in choices
                  if (hs is None or int(a.get("hand", a.get("index", -1))) == hs)
                  and (ts is None or int(a.get("target", a.get("bench", -1))) == ts
                       or int(a.get("bench", -1)) + 1 == ts)]
        if len(narrow) == 1:
            return {"ok": True, "action": dict(narrow[0]),
                    "matched": command_label(narrow[0]), "candidates": []}
        return {"ok": False, "action": None, "matched": command_label(choices[0]),
                "ambiguous": True,
                "candidates": [dict(a) for a in choices],
                "error": (f"「{command_label(choices[0])}」有多个可选目标，"
                          f"请补上编号（如「… 后备1」）")}

    # 1) 位置类动词优先（attach / retreat：文本含「后备2」「战斗位」等）
    slot = _slot_from_text(text)
    if slot is not None:
        wants = "retreat" if _starts_with_verb(text, "retreat") else "attach"
        hits = [a for a in pool if a.get("verb") == wants]
        if wants == "retreat":
            # 撤退语义：slot = 要换上去的后备位（1 起）；未指明则取任意可用
            if slot >= 1:
                exact = [a for a in hits
                         if int(a.get("bench", 0)) + 1 == slot]
                if exact:
                    return {"ok": True, "action": dict(exact[0]),
                            "matched": f"撤退 → 后备{slot}", "candidates": []}
                avail = sorted({int(a.get("bench", 0)) + 1 for a in hits})
                return {"ok": False, "action": None,
                        "error": (f"后备{slot} 当前不可作为撤退目标"
                                  f"（可用：{avail or '无'}）"),
                        "candidates": [command_label(a) for a in hits]}
            if hits:
                return {"ok": True, "action": dict(hits[0]),
                        "matched": "撤退", "candidates": []}
        else:
            if not hits:
                return {"ok": False, "action": None,
                        "error": "本回合无法附能量（手牌无能量，或本回合已附过）",
                        "candidates": []}
            exact = [a for a in hits if int(a.get("target", 0)) == slot]
            if exact:
                return {"ok": True, "action": dict(exact[0]),
                        # matched 与 command_label 同格式，便于前端回显一致
                        "matched": command_label(exact[0]), "candidates": []}
            avail = sorted({int(a.get("target", 0)) for a in hits})
            # 明确指定了位置却不可用 → **报错而非静默改到别的位置**
            return {"ok": False, "action": None,
                    "error": (f"卡位{slot}（{_slot_name(slot)}）当前不可附能量"
                              f"（可用：{[_slot_name(s) for s in avail] or '无'}）"),
                    "candidates": [command_label(a) for a in hits]}

    # 2) 名称匹配（招式名 / 卡名 / 特性名，中文或英文）
    scored = []
    for a in pool:
        for lab in labels_of(a)[:3]:          # 只看 display/name_zh/name，避免动词词干扰
            sc = _score(norm(lab), q)
            if sc:
                scored.append((sc, len(norm(lab)), a, lab))
    if scored:
        scored.sort(key=lambda t: (-t[0], t[1]))
        top = scored[0]
        # 同分同长 → 按**语义去重键**判断是否真的多选（同名副本不算歧义）
        same = [t for t in scored if t[0] == top[0] and t[1] == top[1]]
        seen, choices = set(), []
        for t in same:
            k = _choice_key(t[2])
            if k not in seen:
                seen.add(k)
                choices.append(t)
        if len(choices) > 1:
            cands = [{**dict(t[2]), "label": command_label(t[2])}
                     for t in choices]
            return {"ok": False, "action": None, "matched": top[3],
                    "ambiguous": True, "candidates": cands,
                    # 附 label 版候选（前端可直接展示/点选）
                    "options": [c["label"] for c in cands],
                    "error": (f"「{top[3]}」有多个可选目标，请指明其中之一"
                              f"（如「{cands[0]['label']}」）")}
        return {"ok": True, "action": dict(top[2]), "matched": top[3],
                "candidates": []}

    # 3) 纯动词（如单独输入「结束回合」「撤退」）
    if verb is None:
        by_verb = {}
        for a in legal:
            by_verb.setdefault(a.get("verb"), []).append(a)
        for v in ("pass", "retreat", "attach", "attack", "ability",
                  "trainer", "play", "evolve"):
            if _starts_with_verb(text, v) and by_verb.get(v):
                return {"ok": True, "action": dict(by_verb[v][0]),
                        "matched": VERB_ALIASES[v][0], "candidates": []}

    return {"ok": False, "action": None,
            "error": f"无法从指令「{text}」匹配到当前可用动作",
            "candidates": [{**dict(a), "label": command_label(a)}
                           for a in pool][:12],
            "options": [command_label(a) for a in pool][:12]}


def resolve_action(legal: list, action: dict) -> Optional[dict]:
    """把「带名称但缺索引」的动作**补全**为 `legal` 中的合法动作

    例：`{"verb":"attack","attack_name":"幻影俯冲"}` → legal 中的
    `{"verb":"attack","attack":0,"display":"幻影俯冲",...}`。

    仅供「动作不带索引」的友好写法使用；补全结果**仍是 legal_actions 的成员**，
    因此不破坏 PRD F5「动作与 legal_actions 严格 1:1」的不变量。

    歧义保护：无名称也无位置提示、且存在多个候选时返回 None（由调用方报错）。
    """
    if not isinstance(action, dict):
        return None
    verb = action.get("verb")
    if not verb:
        return None
    pool = [a for a in (legal or []) if a.get("verb") == verb]
    if not pool:
        return None

    def _num(*keys):
        for k in keys:
            if action.get(k) is not None:
                try:
                    return int(action[k])
                except (TypeError, ValueError):
                    return None
        return None

    ts = _num("target", "target_pos")
    hs = _num("hand", "index")
    bs = _num("bench")
    atk = _num("attack")

    def _keep(a) -> bool:
        if atk is not None and int(a.get("attack", -1)) != atk:
            return False
        if ts is not None and int(a.get("target", -1)) != ts:
            return False
        if hs is not None and int(a.get("hand", a.get("index", -1))) != hs:
            return False
        if bs is not None and int(a.get("bench", -1)) != bs:
            return False
        return True

    cands = [a for a in pool if _keep(a)]
    if not cands:
        return None

    hint = None
    for k in NAME_KEYS:
        if action.get(k):
            hint = str(action[k])
            break
    if hint:
        labels = [(a.get("display") or a.get("name_zh") or a.get("name") or "")
                  for a in cands]
        i = best_match(labels, hint)
        return dict(cands[i]) if i is not None else None
    if ts is not None or hs is not None or bs is not None or atk is not None:
        return dict(cands[0])
    # 无任何提示：只有唯一候选才接受（避免静默替用户选目标）
    return dict(cands[0]) if len(cands) == 1 else None


def command_label(action: dict) -> str:
    """动作的可读指令文案（中文优先）——用于 UI 提示/自动补全

    满足**往返不变量**：`find_action(legal, command_label(a))` 应能还原出 `a`。
    """
    verb = action.get("verb", "?")
    verb_zh = {
        "pass": "结束回合", "retreat": "撤退", "attach": "附能量",
        "attack": "攻击", "trainer": "训练家", "ability": "特性",
        "play": "上场", "evolve": "进化",
    }.get(verb, verb)
    name = action.get("display") or action.get("name_zh") or action.get("name")
    if verb == "attach":
        return f"{verb_zh} → {_slot_name(action.get('target', 0))}"
    if verb == "retreat":
        return f"{verb_zh} → 后备{int(action.get('bench', 0)) + 1}"
    if verb == "evolve":
        onto = action.get("onto")
        if onto and name:
            return f"{verb_zh}：{onto} → {name}"
        return (f"{verb_zh}（卡位{action.get('hand', 0)} → "
                f"目标{action.get('target', 0)}）")
    if verb in ("trainer", "ability") and action.get("target") is not None:
        # 同名卡打到不同目标时必须区分（否则用户无从选择，见多目标 bos 场景）
        return (f"{verb_zh}：{name} → {_slot_name(action['target'])}"
                if name else f"{verb_zh} → {_slot_name(action['target'])}")
    return f"{verb_zh}：{name}" if name else verb_zh


def command_catalog(legal: list) -> list:
    """当前可用指令清单（供 UI 自动补全 / API 自描述）"""
    return [{"label": command_label(a), "verb": a.get("verb"),
             "action": dict(a)} for a in (legal or [])]


# ---------------- 组合指令（序列批量提交） ----------------

# 强分隔词：出现即视为步骤边界
SEQ_SEPARATORS = ("然后", "接著", "接着", "之后", "之後", "而后",
                  "再到", "然后", "then", ";", "；", "\n")
# 「再」单独成词时也是分隔（如「… 再结束回合」），但可能出现在卡名中，
# 故仅在切分后逐段校验（无法解析的段落会整体失败，误差可控）。


def split_sequence(text) -> list:
    """把组合指令切成有序步骤（保持顺序、去空白与空段）

    例：`附能量到后备1 然后结束回合` → ['附能量到后备1', '结束回合']
        `进化：多龙梅西亚 → 多龙奇; 攻击：幻影俯冲` → 两段

    注意：`→` 既出现在指令 label 内部（「附能量 → 战斗位」），也可作步骤分隔。
    因此先按强分隔词切分；**仅当强分隔词没切开**时才用箭头兜底，
    且要求箭头之后每段都以动词别名开头（`_looks_like_step_chain`），
    避免把「附能量 → 战斗位」误切成两步。
    """
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return [str(x).strip() for x in text if str(x).strip()]
    s = str(text).strip()
    if not s:
        return []

    parts = [s]
    for sep in SEQ_SEPARATORS:
        nxt = []
        for p in parts:
            nxt.extend(p.split(sep))
        parts = nxt
    parts = [p.strip() for p in parts if p and p.strip()]

    if len(parts) == 1 and ("→" in s or "->" in s):
        cand = [p.strip() for p in s.replace("->", "→").split("→")]
        if _looks_like_step_chain(cand):
            parts = [p for p in cand if p]

    # 段首单独的「再」→ 去掉（如「… 再 结束回合」）
    out = []
    for p in parts:
        if len(p) > 1 and p.startswith("再") and out:
            rest = p[1:].strip()
            if rest:
                out.append(rest)
                continue
        out.append(p)
    return [x for x in out if x]


def _looks_like_step_chain(parts: list) -> bool:
    """箭头切出的分段是否像「多步指令」而非「动词 → 目标」

    要求：≥2 段非空，且**第 2 段起**每段都以动词别名开头
    （如「结束回合」「攻击：X」）；否则视为内联箭头，不切。
    """
    segs = [p for p in parts if p]
    if len(segs) < 2:
        return False
    for seg in segs[1:]:
        q = norm(seg)
        if not q:
            return False
        if not any(any(q.startswith(norm(a)) for a in names)
                   for names in VERB_ALIASES.values()):
            return False
    return True


def sequence_hint() -> str:
    """组合指令用法提示（供 UI / 文档复用）"""
    return ("可用「然后 / 接着 / 再 / ;」串联多步，如"
            "「附能量到后备1 然后结束回合」；回合移交对手或某步失败即停止，"
            "并回报已执行步骤与剩余步骤")


# 单次组合指令的最大步数（防滥用；UI 与 API 共用）
MAX_SEQUENCE_STEPS = 8
