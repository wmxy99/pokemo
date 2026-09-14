# PokéLab AI · 合规声明（v1.1 / 2026-09-14）

> 本文件是本项目的**权威合规文本**。代码侧以 `apps/api/app/compliance.py` 提供**机器可读**
> 版本（`GET /compliance`），站内以「合规声明」页（`apps/web/src/pages/CompliancePage.jsx`）
> 内嵌展示；**三者必须一致**。
>
> **需求来源与追溯**：条款源自 PRD **附录 A（6.1 内容合规）** 与 **附录 B（6.2 数据隐私）**，
> 对应需求编号 **F9.1 / F9.2**（验收追溯表见 PRD 附录 A §A.5）。
> **修订顺序**：PRD 附录 → 本文件 → `compliance.py` → 前端渲染。

---

## 6.1 内容合规声明（原文）

> 本项目涉及的宝可梦卡牌相关名称与规则仅用于教学研究与算法演示，相关版权与商标归属其
> 权利方；项目不复制、不传播官方卡牌图像与卡表原文，对战结果与平衡性报告仅供研究参考；
> 对AI生成的效果解析与对战解说应以显著方式予以标识，因不当使用产生的责任由使用者承担。

### 落地要求

| 条款 | 落地实现 | 验证方式 |
|---|---|---|
| 仅用于教学研究与算法演示 | 页脚与研究声明常驻；报告带「仅供研究参考」 | `GET /`、`GET /compliance`、页脚 |
| 版权与商标归属权利方 | 统一 `ATTRIBUTION` 文案（Nintendo · Creatures Inc. · GAME FREAK inc. · The Pokémon Company） | `GET /compliance` → `content_compliance.attribution_template` |
| 对战结果/平衡性报告仅供研究参考 | 批量报告、矩阵、K1–K4 看门狗均带声明与诚实标注 | `/batch/jobs/{id}/report`、`/stats/summary`、`/stats/watchdog` |
| **AI 生成内容显著标识** | 解析结果带 `ai_generated` / `generated_by` / `ai_notice`；UI 显示「AI 生成」徽标 | `/cards/{id}.effects_parsed`、`/cards?…` 列表项、K2 报告 |
| 责任由使用者承担 | `liability` 字段常驻声明 | `GET /compliance` |

### 素材使用政策（红线 C2 修订）

| 素材 | 政策 | 说明 |
|---|---|---|
| 卡牌名称 | ✅ 可用（须标注权利方） | 事实性数据 |
| 规则文本 / 卡面效果文本 | ✅ 可用（须标注权利方） | 功能机制引用 |
| 卡牌图像 | ✅ 可用（须标注权利方） | 通过 `image_url` 字段引用；**本项目不打包与本研究无关的官方美术素材** |
| 声称与权利方关联或获其背书 | ❌ 禁止 | 页脚与声明明确「非官方、无关联、未获背书」 |

> **v1.0 → v1.1 变更**：原「不使用官方卡图 / 不复现卡表原文」调整为「**可用但须标注权利方
> 且不得声称关联**」。变更同时要求：**AI 生成内容必须显著标识**（新增 C4）。

> **口径澄清（避免误读）**：6.1 原文中的「项目不复制、不传播官方卡牌图像与卡表原文」
> 按 C2(v1.1) 解释为「**不打包、不传播与本研究无关的官方美术素材**」；
> **卡牌名称、规则文本与卡牌图像在本研究范围内可用**，但必须显著标注权利方出处，
> 且不得声称与权利方存在关联或获其背书。该澄清同源出现在
> `GET /compliance` → `content_compliance.usage_policy_note`（机器可读），
> 站内 [合规声明页](/app/ → 合规声明) 亦直接渲染该字段。

### AI 生成内容标识契约

| 字段 | 取值 | 含义 |
|---|---|---|
| `ai_generated` | `true` | 该内容由机器/AI 自动生成 |
| `generated_by` | `rule` / `llm` | 生成方式（离线规则基线 / 大模型） |
| `ai_notice` | 文本 | 免责提示：仅供研究参考、以官方规则为准 |

覆盖范围：卡牌效果解析（K2）、对战解说、训练/评估报告的机器结论。
**UI 要求**：以徽标或提示条展示，禁止与人工撰写内容混排且无标注。

---

## 6.2 数据隐私保护（原文）

> 用户账号、卡组配置与对战记录应采取HTTPS加密传输与数据库存储加密措施，支持本地化部署；
> 调用大模型API前对文本中的敏感信息进行脱敏，严格遵守数据保护法规，不得将用户数据用于
> 服务以外用途或向第三方提供。

### 落地实现

| 要求 | 实现 | 代码 / 环境变量 |
|---|---|---|
| HTTPS 加密传输 | 支持 `uvicorn --ssl-keyfile/--ssl-certfile`；或置于 HTTPS 反向代理后 | `docs/COMPLIANCE.md §HTTPS 部署` |
| 数据库存储加密 | 口令 **PBKDF2-SHA256(20 万次)** 单向哈希；用户卡表原文可选 **Fernet** 字段加密 | `apps/api/app/crypto.py` · `POKELAB_FIELD_ENC=1` |
| 支持本地化部署 | 全栈可离线运行（引擎/前端/SQLite）；本地模式**禁止一切外部调用** | `POKELAB_LOCAL_ONLY=1` |
| 调用大模型前脱敏 | 外发前替换邮箱/手机/证件/卡号/密钥/本地路径/自定义敏感词 | `packages/agent/agent/privacy.py` `desensitize()` |
| 不使用于服务以外用途 | 本地模式可完全离线；外发仅发送脱敏文本；全量调用可审计 | `GET /compliance/audit`（admin） |
| 不向第三方提供 | 默认无任何第三方上报；外部 LLM 仅在显式配置且非本地模式时可用 | `POKELAB_LLM_BASE` + `POKELAB_LOCAL_ONLY=0` |

### 云端模型端点配置（v1.2 新增）

云端大模型的端点/模型名/密钥可由管理员在**后台可视化配置**（无需改环境变量）。
本节如实披露其与 6.2 的关系：

| 事项 | 实现 | 验证方式 |
|---|---|---|
| 配置入口 | 后台 → 「模型配置」页（仅 `admin`） | `GET/PUT/DELETE /admin/llm/config` |
| 生效优先级 | **后台配置 > 环境变量**（`POKELAB_LLM_BASE/KEY/MODEL`）；保存即时生效，无需重启 | `agent.llmclient.source()` 返回 `runtime`/`env`/`none` |
| 密钥存储 | API Key **无条件 Fernet 加密**入库；页面仅回显尾 4 位；**加密不可用时拒绝落库**（不写明文） | `apps/api/app/crypto.py` `encrypt_secret()` · `settings` 表 `encrypted=1` |
| **本地模式硬禁** | `POKELAB_LOCAL_ONLY=1` 时**拒绝保存外部端点**（403），仅允许本机/内网端点（自建 vLLM / Ollama）；连通性测试同样被拒 | `PUT /admin/llm/config` · `POST /admin/llm/test` |
| 连通性测试 | 真实调用一次，同样经脱敏与审计 | `POST /admin/llm/test`（返回 `elapsed_ms` / 脱敏计数） |
| 生效范围 | K2 卡牌效果解析 · **对战解说**（返回值带 AI 生成标识，见 C4） | `POST /battles/{id}/commentary` |
| 降级行为 | 未配置 / 调用失败 / 被本地模式拦截 → 回退离线规则基线并**如实标注**（`rule(fallback)` / `rule(privacy-blocked)`），不阻断功能 | `/cards/{id}` 的 `effects_parsed.backend` · 解说的 `generated_by` |

> **不变量**：无论配置从哪里来（后台或环境变量），**所有**外发都经过
> `agent.llmclient.chat()` 的「端点校验 → 脱敏 → 调用 → 审计」四步；
> 绕过该入口直接 `urllib` 调用外部端点属违规实现（评审应拒绝）。


### 脱敏规则

邮箱 · 中国手机号 · 证件号 · 银行卡号 · API 密钥（`sk-…`）· `Authorization` 头 ·
Windows 用户目录路径 · `账号@主机`；另支持调用方传入业务敏感词（用户名、卡组名等）。
命中以占位符替换（`[邮箱]` / `[手机号]` / `[用户信息]` …），**原文不外发、不落盘**。

### 审计

每次外部 LLM 调用写一条 JSONL 记录：时间、端点主机、是否外部、模型、提示/发送字符数、
脱敏命中数、响应字符数、耗时、成功与否。**不记录原文**。
默认路径 `~/.pokelab/llm_audit.jsonl`，`POKELAB_AUDIT=0` 可关闭。

---

## HTTPS 部署

### 方式一：直接由 uvicorn 提供 TLS（自签证书，开发/内网）

```bash
# 1) 生成自签证书（仅本地/内网使用；生产请用受信任 CA）
python apps/api/scripts/make_dev_cert.py --out apps/api/data/certs

# 2) 以 HTTPS 启动
python -m uvicorn apps.api.app.main:app \
  --host 0.0.0.0 --port 8443 \
  --ssl-keyfile apps/api/data/certs/dev_key.pem \
  --ssl-certfile apps/api/data/certs/dev_cert.pem
# → https://127.0.0.1:8443/app/
```

### 方式二：反向代理终结 TLS（推荐用于生产）

```nginx
server {
  listen 443 ssl;
  server_name pokelab.example;
  ssl_certificate     /etc/ssl/pokelab/fullchain.pem;
  ssl_certificate_key /etc/ssl/pokelab/privkey.pem;
  location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; }
}
```

> 若启用 HSTS 与严格 TLS 策略，请先在测试环境验证再上线；本地开发可用方式一。

---

## 纯本地（数据不出本地）部署

```bash
export POKELAB_LOCAL_ONLY=1          # 禁止一切外部网络调用
export POKELAB_FIELD_ENC=1           # 启用字段加密（可选）
export POKELAB_AUDIT=1               # 保留调用审计
python -m uvicorn apps.api.app.main:app --port 8000
# LLM 只能指向本机/内网：POKELAB_LLM_BASE=http://127.0.0.1:9997/v1
```

`POKELAB_LOCAL_ONLY=1` 时，任何指向外部主机的 LLM 端点都会被拒绝
（`PrivacyError`），解析自动回退离线规则基线并**如实报告**，不会静默外发。

---

## 自查清单（上线前逐项确认）

- [ ] `GET /compliance` 返回 6.1/6.2 声明与最新实现状态
- [ ] 页脚可见权利方标注与「非官方 · 非商业」声明
- [ ] 所有 AI 生成内容展示处均有「AI 生成」标识
- [ ] 批量报告 / 矩阵 / 看门狗均带研究参考声明与诚实标注
- [ ] 生产环境为 HTTPS（或已置于 HTTPS 反向代理后）
- [ ] `POKELAB_FIELD_ENC=1` 且密钥文件权限受限（如启用字段加密）
- [ ] 需要完全离线时设置 `POKELAB_LOCAL_ONLY=1` 并验证外部端点被拒
- [ ] `~/.pokelab/llm_audit.jsonl` 存在且不含原文（抽查）
