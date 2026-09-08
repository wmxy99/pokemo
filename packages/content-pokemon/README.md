# packages/content-pokemon · 宝可梦内容包（NonCommercial）

**许可**：PolyForm Noncommercial 1.0.0（见 LICENSE.md）—— 本目录内容仅限**非商业**用途。

## 范围与红线

| 可放（非原文转写） | ❌ 不可放（永不提交） |
|-------------------|---------------------|
| 自造卡效果结构化**样例**（原创代名） | Kaggle 竞赛卡牌数据（禁止再分发） |
| 规则要点的**自述转写**（非官方原文） | 官方规则书 / Compendium 原文 |
| 卡池/禁限变更的摘要笔记 | 官方卡图 / 商标资产 |
| 数据获取指引（链接，非内容） | 任何受版权保护全文 |

## 数据获取指引（用户本机自行执行，不随仓库分发）

1. Kaggle「pokemon-tcg-ai-battle」竞赛页下载 `EN_Card_Data.csv`（需 Kaggle 账号）；
2. 或运行 `../card-db/scripts/fetch_pokemontcg.py`（pokemontcg.io API，需本机联网）；
3. 导入与解析步骤见 `../card-db/README.md`。

## samples/

放置经校验的自造样例（非官方文本转写），作为 effects schema 的回归样本。
