# 卡库中文本地化报告

- 生成时间：2026-10-27 15:51:47
- 卡表：`data/cleaned/cards_challenge.json`
- 语料：`data/i18n/zh_card_translations.json`（3341 条；来源 manifest + merged_zh）
- 生成器：`packages/card-db/scripts/apply_zh_translations.py`

## 1. 字段覆盖

| 字段 | 写入前 | 写入后 | 本次写入 | 缺译文 |
|---|---:|---:|---:|---:|
| 卡名 `name_zh` | 1267 | 1267 | 1267 | 0 |
| 招式名 `attacks[].name_zh` | 1559 | 1559 | 1559 | 0 |
| 招式效果 `attacks[].effect_zh` | 1026 | 1026 | 1026 | 0 |
| 特性名 `abilities[].name_zh` | 223 | 223 | 223 | 0 |
| 特性效果 `abilities[].effect_zh` | 223 | 223 | 223 | 0 |
| 其他文本 `other_text_zh` | 232 | 232 | 232 | 0 |
| 规则文本 `rule_zh` | 180 | 180 | 180 | 0 |

> 说明：英文字段仍是数据键；缺译文时前端回退英文，**不会**把英文塞进中文位。

## 2. 术语覆盖（人工指定）

| 英文 | 语料原译 | 统一为 |
|---|---|---|
| Night Stretcher | 夜晚担架 | 夜间担架 |
| Crushing Hammer | 粉碎锤 | 粉碎之锤 |
| Switch | 交换 | 换位 |
| Kieran | 基兰 | 乌栗 |
| Carmine | 卡茉 | 丹瑜 |
| Colress’s Tenacity | 阿克罗玛的毅力 | 阿克罗马的毅力 |
| Crispin | 克里平 | 克里森 |
| Kofu | 海岱 | 卡芙 |
| Briar | 布里亚 | 白蕾 |
| Amarys | 阿玛里斯 | 阿玛丽丝 |

## 3. 与既有中文名的差异（已按语料统一）

| 卡名 | 原值 | 新值 | 系列/编号 |
|---|---|---|---|
| Bloodmoon Ursaluna ex | 血月月月熊ex | 月月熊（血月）ex | TWM 141 |
| Dragapult ex | 多龙巴鲁托ex | 多龙巴鲁托 ex | TWM 130 |
| Fezandipiti ex | 吉雉鸡ex | 吉雉鸡 ex | SFA 38 |
| N’s Zoroark ex | N的索罗亚克ex | N 的索罗亚克 ex | JTG 98 |
| Night Stretcher | 夜之担架 | 夜间担架 | SFA 61 |
| Crushing Hammer | 破坏之锤 | 粉碎之锤 | SVI 168 |
| Crispin | 克里斯宾 | 克里森 | SCR 133 |

## 4. 校验

- 已检查字段：**4710**
- 空译文：0
- 无中文字符：0
- 丢失英文数字：0
- `{X}` 符号多重集不一致：20

> 符号不一致多为中文习惯写法（如 `宝可梦 ex` 不再带花括号），不等于规则错误；数字校验只要求英文中出现的数字在中文里仍存在，不把「a/an → 1 张」这类合理补足算作异常。

### 4.1 需人工复核的条目（最多 80 条）

| 类型 | 字段 | 卡名 | 详情 |
|---|---|---|---|
| symbol_mismatch | ability.effect_zh | Iron Thorns ex | 原文 ['{ex}', '{V}'] / 译文 [] |
| symbol_mismatch | attack.effect_zh | Shaymin | 原文 ['{ex}', '{V}'] / 译文 [] |
| symbol_mismatch | ability.effect_zh | Iron Crown ex | 原文 ['{ex}'] / 译文 [] |
| symbol_mismatch | ability.effect_zh | Farigiraf ex | 原文 ['{ex}'] / 译文 [] |
| symbol_mismatch | ability.effect_zh | Palafin | 原文 ['{ex}'] / 译文 [] |
| symbol_mismatch | attack.effect_zh | Zoroark | 原文 ['{ex}', '{V}'] / 译文 ['{V}'] |
| symbol_mismatch | ability.effect_zh | Munkidori ex | 原文 ['{ex}'] / 译文 [] |
| symbol_mismatch | ability.effect_zh | Pecharunt ex | 原文 ['{D}', '{ex}'] / 译文 ['{D}'] |
| symbol_mismatch | attack.effect_zh | Galvantula ex | 原文 ['{ex}', '{V}'] / 译文 ['{V}'] |
| symbol_mismatch | attack.effect_zh | Slowking | 原文 ['{ex}', '{V}'] / 译文 ['{V}'] |
| symbol_mismatch | attack.effect_zh | Flygon ex | 原文 ['{ex}', '{V}'] / 译文 ['{V}'] |
| symbol_mismatch | attack.effect_zh | Turtonator | 原文 ['{ex}'] / 译文 [] |
| symbol_mismatch | ability.effect_zh | Mega Gengar ex | 原文 ['{D}', '{ex}'] / 译文 ['{D}'] |
| symbol_mismatch | ability.effect_zh | Ludicolo | 原文 ['{G}', '{ex}'] / 译文 ['{G}'] |
| symbol_mismatch | attack.effect_zh | Moltres | 原文 ['{ex}'] / 译文 [] |
| symbol_mismatch | ability.effect_zh | Oricorio ex | 原文 ['{R}', '{ex}', '{R}', '{R}'] / 译文 ['{R}', '{R}', '{R}'] |
| symbol_mismatch | ability.effect_zh | Seviper | 原文 ['{D}', '{ex}'] / 译文 ['{D}'] |
| symbol_mismatch | ability.effect_zh | Linoone | 原文 ['{ex}'] / 译文 [] |
| symbol_mismatch | attack.effect_zh | Meowscarada | 原文 ['{ex}'] / 译文 [] |
| symbol_mismatch | attack.effect_zh | Miraidon ex | 原文 ['{ex}'] / 译文 [] |

## 5. 已知源语料质量提示（未自动改动）

源语料 `merge_report.md` 记录的质量异常（英文残留、`{ex}` 写法、数字多出、术语表差异等）不会被本脚本静默改写：

- 英文残留多为专有名词（`EX` / `PP` / `DNA` / `ACE SPEC`），可接受；
- `宝可梦 ex` 等中文写法与英文 `{ex}` 的花括号差异属展示风格；
- 数字多出通常是中文量词补足（`a card` → `1 张卡`）；
- 术语表冲突按 `GLOSSARY_EXTRA.md` 优先，已覆盖的 10 条见 §2。

