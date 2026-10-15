"""Step 5：合规检查 —— **写前咨询门，永不写入**（参考 listing-compliance 的只读检查思路）。

职责边界（必须严格遵守）：
- ✅ 只输出**风险提示**：禁词命中、商标/专利人工核查提示、品类审核提示、AI 素材披露提示、
  文案字符/字节超限提示。
- ❌ **不做任何修改**：不调用任何写工具，不改文案，不改 Listing。
- ❌ 不臆断侵权：商标/专利命中只提示「需人工核查」，绝不据此下结论。

词表与字符规范来自技能配置（``config/compliance.yaml`` / ``config/listing_spec.yaml``），
不硬编码在代码里。
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional

from _common import Notes, load_skill_yaml


def _iter_copy_texts(copy: Dict[str, Any]) -> List[Dict[str, str]]:
    """把文案结构攤平成 (字段名, 文本) 列表，便于统一扫描。"""
    out: List[Dict[str, str]] = []
    if not copy:
        return out
    if copy.get("title"):
        out.append({"field": "title", "text": str(copy["title"])})
    for i, b in enumerate(copy.get("bullets") or [], 1):
        out.append({"field": f"bullet-{i}", "text": str(b)})
    if copy.get("search_terms"):
        out.append({"field": "search_terms", "text": str(copy["search_terms"])})
    if copy.get("description"):
        out.append({"field": "description", "text": str(copy["description"])})
    if copy.get("aplus"):
        out.append({"field": "aplus", "text": str(copy["aplus"])})
    return out


def scan_banned_words(texts: List[Dict[str, str]], words: List[str]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for item in texts:
        low = item["text"].lower()
        for w in words:
            if w and str(w).lower() in low:
                hits.append({"field": item["field"], "word": w, "risk": "禁用/高风险表述"})
    return hits


def check_compliance(
    *,
    copy: Optional[Dict[str, Any]] = None,
    brief: Optional[Dict[str, Any]] = None,
    notes: Optional[Notes] = None,
) -> Dict[str, Any]:
    """执行合规检查，返回风险清单（不修改任何输入）。"""
    notes = notes or Notes()
    cfg = load_skill_yaml("compliance")
    banned = cfg.get("banned_words") or []
    trademark = cfg.get("trademark_watch") or []
    category_hints = cfg.get("category_approval_hints") or []
    ai_label_required = bool(cfg.get("ai_label_required", False))
    disclaimer = cfg.get("disclaimer", "")

    texts = _iter_copy_texts(copy or {})
    # 素材提示词也纳入禁词扫描（避免把禁用词写进生图提示词）。
    for a in (brief or {}).get("assets", []):
        texts.append({"field": f"asset:{a.get('id')}", "text": str(a.get("prompt", ""))})

    findings: Dict[str, Any] = {
        "banned_word_hits": scan_banned_words(texts, banned),
        "trademark_watch_hits": scan_banned_words(texts, trademark),
        "category_approval_hits": scan_banned_words(texts, category_hints),
        "ai_label_required": ai_label_required,
        "ai_disclosure": (
            "本套素材可能包含 AI 生成内容。部分平台/类目要求对 AI 生成或合成内容做披露，"
            "请在刊登前确认目标站点的最新要求。"
            if ai_label_required else ""
        ),
        "advisory_only": True,
        "disclaimer": disclaimer,
    }

    # 商标/专利：本技能无数据库访问能力，固定给出人工核查提示。
    notes.need_manual("商标/专利风险：本技能无商标/专利数据库访问能力，需人工核查。")
    if findings["category_approval_hits"]:
        notes.need_manual("命中品类审核关键词，需人工确认目标类目的审核/资质要求。")

    findings["manual_checks"] = list(notes.manual_checks)
    return findings


def validate_copy_against_spec(
    copy: Dict[str, Any],
    *,
    marketplace: str,
    category: Optional[str] = None,
    notes: Optional[Notes] = None,
) -> Dict[str, Any]:
    """按 ``config/listing_spec.yaml`` 校验文案字符/字节限制。

    未配置的限制（null）→ 标「需人工核查」，**绝不编造数字**。
    """
    notes = notes or Notes()
    spec = load_skill_yaml("listing_spec")
    merged: Dict[str, Any] = {}
    merged.update(spec.get("default") or {})
    merged.update((spec.get("by_marketplace") or {}).get(marketplace) or {})
    if category:
        merged.update((spec.get("by_category") or {}).get(category) or {})

    result: Dict[str, Any] = {"limits_source": "config/listing_spec.yaml", "checks": []}

    def _check(field: str, text: Optional[str], limit_key: str, unit: str) -> None:
        limit = merged.get(limit_key)
        value = text or ""
        size = len(value.encode("utf-8")) if unit == "bytes" else len(value)
        if limit is None:
            notes.need_manual(f"{field} 的字符限制（{limit_key}）未配置，需人工核查")
            result["checks"].append({
                "field": field, "size": size, "unit": unit,
                "limit": None, "status": "需人工核查",
            })
        else:
            result["checks"].append({
                "field": field, "size": size, "unit": unit,
                "limit": limit, "status": "OK" if size <= int(limit) else "超限",
            })

    _check("title", copy.get("title"), "title_max_chars", "chars")
    _check("search_terms", copy.get("search_terms"), "search_terms_max_bytes", "bytes")
    _check("description", copy.get("description"), "description_max_chars", "chars")

    bullet_limit = merged.get("bullet_max_chars")
    for i, b in enumerate(copy.get("bullets") or [], 1):
        if bullet_limit is None:
            notes.need_manual(f"bullet-{i} 的字符限制（bullet_max_chars）未配置，需人工核查")
            result["checks"].append({"field": f"bullet-{i}", "size": len(str(b)),
                                     "unit": "chars", "limit": None, "status": "需人工核查"})
        else:
            result["checks"].append({
                "field": f"bullet-{i}", "size": len(str(b)), "unit": "chars",
                "limit": bullet_limit,
                "status": "OK" if len(str(b)) <= int(bullet_limit) else "超限",
            })

    bullet_count_limit = merged.get("bullet_count")
    actual_count = len(copy.get("bullets") or [])
    if bullet_count_limit is None:
        notes.need_manual("五点数量限制（bullet_count）未配置，需人工核查")
        result["bullets"] = {"count": actual_count, "limit": None, "status": "需人工核查"}
    else:
        result["bullets"] = {
            "count": actual_count, "limit": bullet_count_limit,
            "status": "OK" if actual_count == int(bullet_count_limit) else "数量不符",
        }

    return result


def _main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description="合规检查（只读，永不写入）")
    ap.add_argument("--copy-json", required=True, help="文案 JSON 文件路径")
    ap.add_argument("--marketplace", default="US")
    ap.add_argument("--category")
    args = ap.parse_args()

    copy = json.loads(open(args.copy_json, encoding="utf-8").read())
    notes = Notes()
    compliance = check_compliance(copy=copy, notes=notes)
    spec_check = validate_copy_against_spec(
        copy, marketplace=args.marketplace, category=args.category, notes=notes
    )
    print(json.dumps({"compliance": compliance, "copy_spec_check": spec_check},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())