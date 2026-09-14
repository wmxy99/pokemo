# -*- coding: utf-8 -*-
"""合规声明与自查（6.1 内容合规 · 6.2 数据隐私）

对外提供**机器可读**的合规声明，供前端页脚、报告导出与外部审计读取；
配套 `docs/COMPLIANCE.md` 人读版本。声明文本以产品需求为准，代码只做**如实呈现**
（不篡改条款），并附带**当前实现状态**（哪些措施已生效、如何验证）。

红线修订（v1.1，2026-09-14）
---------------------------
原 C2「不使用官方卡图 / 不复现卡表原文」按新政策调整为：
**卡牌图像与卡表原文可用，但必须标注权利方，且不得声称与权利方存在关联或获其背书**。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

from fastapi import APIRouter, Depends

from .auth import Principal, current_user, require_roles

router = APIRouter(prefix="/compliance", tags=["compliance"])

_REPO = Path(__file__).resolve().parents[3]
DOC = _REPO / "docs" / "COMPLIANCE.md"

# 统一对外数据声明（供各模块复用，避免文案漂移）
DATA_DECLARATION = (
    "Unofficial community data · 非官方数据 · 卡牌名称/规则文本/图像版权归 "
    "Nintendo · Creatures Inc. · GAME FREAK inc. · The Pokémon Company 所有；"
    "本项目为社区非商业研究项目，与权利方无关联（PRD C1）")

# AI/机器生成内容标识（供报告与响应复用）
AI_NOTICE = ("本内容由机器/AI 自动生成，仅供研究参考，可能不完全准确；"
             "请以官方规则与卡面原文为准。")

# 权利方标注（合规 6.1：使用卡名/规则文本/图像时须显著标注）
ATTRIBUTION = ("卡牌名称 / 规则文本 / 图像版权归 Nintendo · Creatures Inc. · "
               "GAME FREAK inc. · The Pokémon Company 所有；本项目为非官方、"
               "非商业的社区研究项目，与权利方无关联、未获其背书。")

RIGHTS_HOLDERS = [
    "Nintendo",
    "Creatures Inc.",
    "GAME FREAK inc.",
    "The Pokémon Company",
]

# ---------------- 6.1 内容合规 ----------------
CONTENT_STATEMENT = (
    "本项目涉及的宝可梦卡牌相关名称与规则仅用于教学研究与算法演示，相关版权与商标归属"
    "其权利方；项目不复制、不传播官方卡牌图像与卡表原文，对战结果与平衡性报告仅供研究"
    "参考；对AI生成的效果解析与对战解说应以显著方式予以标识，因不当使用产生的责任由"
    "使用者承担。"
)

# ---------------- 6.2 数据隐私 ----------------
PRIVACY_STATEMENT = (
    "用户账号、卡组配置与对战记录应采取HTTPS加密传输与数据库存储加密措施，支持本地化"
    "部署；调用大模型API前对文本中的敏感信息进行脱敏，严格遵守数据保护法规，不得将"
    "用户数据用于服务以外用途或向第三方提供。"
)

# AI 生成内容的标识契约（供前端与外部系统对齐）
AI_LABELING = {
    "policy": ("对 AI/机器生成的效果解析与对战解说，必须在展示处以显著方式标注"
               "「AI 生成」，并给出模型/后端与免责提示；不得作为官方规则呈现。"),
    "required_fields": ["ai_generated", "generated_by", "ai_notice"],
    "where": [
        "卡牌效果解析结果（K2：/cards/{id}.ai_generated 与解析报告）",
        "对战解说（commentator 提示词产出，标注后展示）",
        "训练/评估报告中的机器生成结论（K1/K2 报告）",
    ],
    "ui_requirement": "前端须以徽标/提示条展示，禁止与人工撰写内容混排无标注",
}

# 红线（v1.1 修订版）
RED_LINES = [
    {"id": "C1", "title": "非官方与非商业",
     "statement": "项目为社区非商业开源项目，不代表权利方，不得声称关联或获其背书。",
     "status": "enforced"},
    {"id": "C2", "title": "素材使用与标注",
     "statement": ("卡牌名称、规则文本与卡牌图像可在遵守 6.1 的前提下使用，"
                   "但须标注权利方出处；不复制、不传播与本研究无关的官方美术素材。"),
     "status": "enforced", "revised_from": "不使用官方卡图/卡表原文（v1.0）"},
    {"id": "C3", "title": "数据来源透明",
     "statement": "所有对外数据标注来源与「非官方」声明，差异透明（灰名单机制）。",
     "status": "enforced"},
    {"id": "C4", "title": "AI 产出标识",
     "statement": "AI/机器生成内容显著标识，不得冒充官方规则或人工结论。",
     "status": "enforced"},
    {"id": "C5", "title": "差异透明（引擎覆盖）",
     "statement": "引擎未建模的卡如实标记（engine_ready=false），可浏览/组卡但不可对战。",
     "status": "enforced"},
]


def _ai_status() -> dict:
    """AI 标识实现状态"""
    return {
        "enforced": True,
        "fields": AI_LABELING["required_fields"],
        "examples": {
            "k2_parse": "agent.effects.parse_effects(...) 返回 ai_generated=True、"
                        "generated_by=rule|llm、ai_notice",
            "library": "/cards/{id} 响应含 ai_generated/ai_notice（effects_json 来源标记）",
            "watchdog": "/stats/watchdog 的 K2 行标注后端与免责",
        },
    }


def _privacy_impl() -> dict:
    """隐私措施实现状态（从 agent.privacy 与 crypto 读实时状态）"""
    status: dict = {}
    try:
        import sys
        for pkg in ("agent", "engine"):
            p = _REPO / "packages" / pkg
            if p.exists() and str(p) not in sys.path:
                sys.path.insert(0, str(p))
        from agent import privacy as _p                      # noqa: WPS433
        status = _p.privacy_status()
    except Exception as e:                                   # noqa: BLE001
        status = {"error": f"privacy 模块不可用：{e}"}
    try:
        from . import crypto as _crypto                      # noqa: WPS433
        enc = _crypto.status()
    except Exception as e:                                   # noqa: BLE001
        enc = {"available": False, "enabled": False, "error": str(e)}
    return {
        "statement": PRIVACY_STATEMENT,
        "transport": {
            "https_supported": True,
            "how": ("uvicorn --ssl-keyfile/--ssl-certfile，或置于 HTTPS 反向代理之后"
                    "（见 docs/COMPLIANCE.md §HTTPS 部署）"),
            "self_signed_helper": "apps/api/scripts/make_dev_cert.py（本地自签，仅开发用）",
        },
        "storage": {
            "password_hashing": "pbkdf2_sha256$200000$<salt>$<hash>（HMAC-SHA256，无明文）",
            "field_encryption": "apps/api/app/crypto.py（Fernet，可选启用）",
            "encryption_env": "POKELAB_FIELD_ENC=1 启用；POKELAB_FIELD_KEY 或 data/.field_key",
            "live": enc,
        },
        "local_deployment": {
            "supported": True,
            "local_only_env": "POKELAB_LOCAL_ONLY=1",
            "effect": "禁止一切外部网络调用（外部 LLM 端点被拒绝）",
        },
        "llm_call": {
            "desensitize_before_send": True,
            "rules": status.get("desensitize_rules", []),
            "audit_enabled": status.get("audit_enabled"),
            "audit_log": status.get("audit_log"),
            "audit_content": "仅端点/模型/字符数/脱敏计数，不记录原文",
            # v1.2：云端模型端点可由**后台可视化配置**（admin）
            "endpoint_config": {
                "supported": True,
                "where": "后台 → 模型配置（GET/PUT /admin/llm/config，仅 admin）",
                "priority": "后台配置 > 环境变量 POKELAB_LLM_BASE/KEY/MODEL",
                "key_storage": ("API Key **无条件 Fernet 加密**入库（不受 "
                                "POKELAB_FIELD_ENC 开关影响）；页面仅回显尾 4 位；"
                                "加密不可用时拒绝落库而非写明文"),
                "local_only_hard_block": True,
                "local_only_note": ("POKELAB_LOCAL_ONLY=1 时后台**拒绝保存外部端点**"
                                    "（403），仅允许本机/内网端点（自建 vLLM/Ollama）"),
                "test_endpoint": "POST /admin/llm/test（真实调用一次，同样脱敏 + 审计）",
                "scope": ["K2 卡牌效果解析", "对战解说（带 AI 生成标识 C4）"],
            },
        },
        "no_secondary_use": {
            "statement": "用户数据仅用于本服务运行（对战/训练/统计），不用于其它用途、"
                         "不向第三方提供。",
            "enforcement": ["本地模式可完全离线", "外发前脱敏", "全量调用可审计"],
        },
        "live_status": status,
    }


@router.get("")
def compliance(p: Principal = Depends(current_user)):
    """合规声明（机器可读）+ 当前实现状态"""
    return {
        "version": "1.1",
        "updated": datetime.date.today().isoformat(),
        "content_compliance": {
            "id": "6.1",
            "statement": CONTENT_STATEMENT,
            "rights_holders": RIGHTS_HOLDERS,
            "usage_policy": {
                "card_names_and_rules": "permitted_with_attribution",
                "card_images": "permitted_with_attribution",
                "decklist_text": "permitted_with_attribution",
                "official_artwork_bundling": "not_included",
            },
            # 6.1 原文与 C2(v1.1) 的口径关系（避免「不复制、不传播」被误读为
            # 「卡牌名称/规则文本/图像一律不可用」——那与 C2 v1.1 修订相冲突）
            "usage_policy_note": (
                "6.1 原文中的「不复制、不传播官方卡牌图像与卡表原文」按红线 C2(v1.1) "
                "解释为：不打包、不传播与本研究无关的官方美术素材。"
                "卡牌名称、规则文本与卡牌图像在本研究范围内可用，"
                "但必须显著标注权利方，且不得声称与权利方存在关联或获其背书。"),
            "attribution_required": True,
            "attribution_template": (
                "卡牌名称 / 规则文本 / 图像版权归 Nintendo · Creatures Inc. · "
                "GAME FREAK inc. · The Pokémon Company 所有；本项目为非官方、"
                "非商业的社区研究项目，与权利方无关联。"),
            "research_only": True,
            "no_endorsement": True,
            "liability": "因不当使用产生的责任由使用者承担。",
            "ai_labeling": AI_LABELING,
            "ai_status": _ai_status(),
        },
        "data_privacy": {"id": "6.2", **_privacy_impl()},
        "red_lines": RED_LINES,
    "declaration": "Unofficial community project · Non-commercial · "
                   "Pokémon © Nintendo/Creatures/GAME FREAK/TPC",
    "summary": ("非商用社区研究项目；卡牌名称/规则文本/图像可用但须标注权利方且不得声称"
                "关联；AI 生成内容显著标识；隐私数据加密传输存储并支持完全本地化部署。"),
        "doc": str(DOC.relative_to(_REPO)).replace("\\", "/"),
        "doc_exists": DOC.exists(),
    }


@router.get("/audit")
def audit_tail(limit: int = 50,
               p: Principal = Depends(require_roles("admin"))):
    """外部调用审计（管理员）：仅端点/字符数/脱敏计数，不含原文"""
    try:
        import sys
        pkg = _REPO / "packages" / "agent"
        if pkg.exists() and str(pkg) not in sys.path:
            sys.path.insert(0, str(pkg))
        from agent import privacy as _p                      # noqa: WPS433
        return _p.read_audit(limit=max(1, min(limit, 500)))
    except Exception as e:                                   # noqa: BLE001
        return {"error": str(e), "entries": [], "total": 0}
