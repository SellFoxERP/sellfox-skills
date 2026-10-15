#!/usr/bin/env python3
"""评论 VOC 归因 —— Step 3：差评主题聚类 + 多源印证 + 行动建议。

核心方法论（本技能的立身之本）：
    **只在多个独立评论印证时才报告某个「模式(pattern)」。**
    把单条差评当普遍问题，是最危险的误报 —— 会误导供应链改模具、改文案。
    因此每个主题分两档：
      - pattern  ：≥ min_independent_reviews 条独立评论 且 ≥ min_distinct_reviewers 个评论者
      - mention  ：样本不足，仅作「个别反馈」列出，明确标注「不构成模式」

归因分两层（可解释性 + 语义）：
    1. 规则初筛：``config/voc.yaml → themes[*].keywords`` 命中的主题（本脚本，可复现）；
    2. 语义归因：AI 助手 阅读原文后写入 ``--llm-themes``（可选 JSON：{review_id: [theme,...]}），
       与规则结果**取并集**并标注来源 —— 词典永远抓不全自然语言。

用法：
    python scripts/analyze_themes.py --signals out/signals.json --reviews out/reviews.json \
        --out out/voc_analysis.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import voc_common as vc

SKILL_DIR = Path(__file__).resolve().parents[1]

# 主题 → 行动建议（归因决定动作；与方案 §技能9「输出行动建议」一致）。
# 文案外置在这里便于维护；阈值不在这里（阈值一律进 config/voc.yaml）。
THEME_ACTIONS: Dict[str, Dict[str, str]] = {
    "quality": {
        "action": "反馈供应链：核查来料/工艺/抽检标准，必要时改模具或换供应商",
        "asset_hint": None,
    },
    "sizing": {
        "action": "补尺寸图/尺码表；核对 listing 标注的尺寸与实物是否一致",
        "asset_hint": "size_chart",
    },
    "color": {
        "action": "校准主图色差（实拍 vs 渲染）；补充多角度/不同光线下实拍",
        "asset_hint": "color_fidelity",
    },
    "shipping": {
        "action": "评估物流商/包装（破损、延迟）；核查 FBA 入仓与尾程",
        "asset_hint": None,
    },
    "description": {
        "action": "改文案/图片使描述与实物一致（描述不符是最高频的可自主修复项）",
        "asset_hint": "lifestyle",
    },
    "other": {
        "action": "人工阅读原文归类；本类未归入已知主题，不代表没问题",
        "asset_hint": None,
    },
}


def _negative_threshold(reviews: List[Dict[str, Any]], max_rating: float) -> List[Dict[str, Any]]:
    """只保留差评（rating ≤ max_rating）。rating 缺失的样本单列，不默认算差评。"""
    out = []
    for r in reviews or []:
        rating = vc.to_float(r.get("rating"))
        if rating is None:
            continue
        if rating <= max_rating:
            out.append(r)
    return out


def _cluster(reviews: List[Dict[str, Any]], compiled: Dict[str, List[str]],
             llm_themes: Dict[str, List[str]]) -> Dict[str, Dict[str, Any]]:
    """把差评按主题聚簇；一条评论可命中多主题（多标签，不强行单选）。"""
    clusters: Dict[str, Dict[str, Any]] = {}

    def bucket(theme: str) -> Dict[str, Any]:
        return clusters.setdefault(theme, {
            "theme": theme,
            "review_ids": [],
            "reviewers": set(),
            "quotes": [],
            "rule_hits": 0,
            "llm_hits": 0,
        })

    for r in reviews:
        rid = str(r.get("id"))
        text = str(r.get("text") or "")
        rule = set(vc.match_themes(text, compiled))
        semantic = set(llm_themes.get(rid) or [])
        merged = rule | semantic
        for theme in merged:
            b = bucket(theme)
            if rid not in b["review_ids"]:
                b["review_ids"].append(rid)
            who = r.get("reviewer_id") or r.get("reviewer_name")
            if who:
                b["reviewers"].add(str(who))
            if theme in rule:
                b["rule_hits"] += 1
            if theme in semantic:
                b["llm_hits"] += 1
            # 引文最多留 3 条，且已脱敏（正文截断，去掉昵称）
            if len(b["quotes"]) < 3 and text:
                snippet = text.strip().replace("\n", " ")[:160]
                b["quotes"].append(snippet)
        if not merged:
            bucket("other")

    return clusters


def _to_records(clusters: Dict[str, Dict[str, Any]], themes_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    for theme, b in clusters.items():
        recs.append({
            "theme": theme,
            "label": vc.theme_label(themes_cfg, theme),
            "review_count": len(b["review_ids"]),
            "distinct_reviewers": len(b["reviewers"]),
            "rule_hits": b["rule_hits"],
            "llm_hits": b["llm_hits"],
            "quotes": b["quotes"],
            "review_ids": b["review_ids"],
        })
    return recs


def _classify(records: List[Dict[str, Any]], pcfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按多源印证规则，把每个主题判为 pattern / mention / none。"""
    min_reviews = int(pcfg.get("min_independent_reviews", 3))
    min_reviewers = int(pcfg.get("min_distinct_reviewers", 2))
    require_distinct = bool(pcfg.get("require_distinct_reviewers", True))

    for rec in records:
        reasons: List[str] = []
        is_pattern = rec["review_count"] >= min_reviews
        if not is_pattern:
            reasons.append(f"独立评论 {rec['review_count']} 条 < 阈值 {min_reviews}")
        if require_distinct and rec["distinct_reviewers"] < min_reviewers:
            is_pattern = False
            reasons.append(f"独立评论者 {rec['distinct_reviewers']} 人 < 阈值 {min_reviewers}")
        rec["level"] = "pattern" if is_pattern else ("mention" if rec["review_count"] > 0 else "none")
        rec["reason"] = "；".join(reasons) if reasons else "满足多源印证阈值"
        act = THEME_ACTIONS.get(rec["theme"], THEME_ACTIONS["other"])
        rec["action"] = act["action"]
        rec["asset_hint"] = act["asset_hint"]
        # 归因来源标注：规则 / 语义 / 二者
        rec["attribution_source"] = (
            "rule+semantic" if rec["rule_hits"] and rec["llm_hits"]
            else ("rule" if rec["rule_hits"] else ("semantic" if rec["llm_hits"] else "rule"))
        )
    # 排序：pattern 优先（按评论数降序），再 mention
    recs_sorted = sorted(
        records,
        key=lambda r: (0 if r["level"] == "pattern" else (1 if r["level"] == "mention" else 2),
                       -r["review_count"]),
    )
    return recs_sorted


def run(args: argparse.Namespace) -> int:
    voc_cfg = vc.load_skill_config("voc")
    themes_cfg = voc_cfg.get("themes") or {}
    pcfg = voc_cfg.get("pattern") or {}
    compiled = vc.compile_theme_keywords(themes_cfg)

    def _p(path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (SKILL_DIR / p)

    signals = vc.read_json(str(_p(args.signals))) if _p(args.signals).exists() else {"flagged": []}
    reviews_payload = vc.read_json(str(_p(args.reviews))) if _p(args.reviews).exists() else {"reviews": []}
    reviews = reviews_payload.get("reviews") or []
    llm_themes: Dict[str, List[str]] = {}
    if args.llm_themes and _p(args.llm_themes).exists():
        llm_themes = vc.read_json(str(_p(args.llm_themes))) or {}

    max_rating = float(args.negative_max_rating)
    negative = _negative_threshold(reviews, max_rating)

    clusters = _cluster(negative, compiled, llm_themes)
    records = _classify(_to_records(clusters, themes_cfg), pcfg)

    patterns = [r for r in records if r["level"] == "pattern"]
    mentions = [r for r in records if r["level"] == "mention"]

    # 按 ASIN 汇总（有多少差评、命中哪些主题）
    by_asin: Dict[str, Dict[str, Any]] = {}
    for r in negative:
        asin = str(r.get("asin") or "未知")
        entry = by_asin.setdefault(asin, {"asin": asin, "negative_reviews": 0, "themes": set()})
        entry["negative_reviews"] += 1
        for theme in set(vc.match_themes(str(r.get("text") or ""), compiled)) | set(llm_themes.get(str(r.get("id"))) or []):
            entry["themes"].add(theme)
    asin_records = [
        {"asin": a, "negative_reviews": v["negative_reviews"], "themes": sorted(v["themes"])}
        for a, v in by_asin.items()
    ]
    asin_records.sort(key=lambda x: -x["negative_reviews"])

    # 若没有原文（降级），明确标注不做主题归因
    degraded = reviews_payload.get("degraded") == "no_external_data" or not reviews
    banner = reviews_payload.get("banner") or (
        (voc_cfg.get("degradation") or {}).get("no_external_reviews", {}).get("banner", "")
        if degraded else ""
    )

    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "method": {
            "pattern_rule": f"≥{pcfg.get('min_independent_reviews', 3)} 条独立评论 且 "
                            f"≥{pcfg.get('min_distinct_reviewers', 2)} 位独立评论者",
            "attribution": "规则关键词初筛 ∪ AI 助手 语义归因（可解释，词典不完全）",
            "negative_rating_max": max_rating,
        },
        "counts": {
            "reviews_total": len(reviews),
            "negative_reviews": len(negative),
            "patterns": len(patterns),
            "mentions": len(mentions),
        },
        "themes": records,
        "patterns": patterns,
        "mentions": mentions,
        "by_asin": asin_records,
        "flagged_asins": signals.get("flagged") or [],
        "degraded": degraded,
        "banner": banner,
        "themes_config_used": sorted(themes_cfg.keys()),
    }
    vc.write_json(args.out, payload)

    print(f"差评原文 {len(negative)} 条 / 总 {len(reviews)} 条")
    print(f"印证为「模式」的主题：{len(patterns)} 个")
    for r in patterns:
        print(f"  ✅ [{r['label']}] {r['review_count']} 条独立评论 / {r['distinct_reviewers']} 位评论者"
              f" → {r['action']}")
    if mentions:
        print(f"样本不足（仅个别反馈，不构成模式）：{len(mentions)} 个")
        for r in mentions:
            print(f"  · [{r['label']}] {r['review_count']} 条 —— {r['reason']}")
    if degraded:
        print(f"\n{banner}")
    print(f"已写出 {args.out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="差评主题聚类 + 多源印证（评论 VOC 归因核心）")
    p.add_argument("--signals", default="out/signals.json")
    p.add_argument("--reviews", default="out/reviews.json")
    p.add_argument("--llm-themes", default=None, help="AI 助手 语义归因结果 JSON：{review_id: [theme]}（可选）")
    p.add_argument("--negative-max-rating", default=3.0, type=float, help="差评星级上界（≤ 视为差评）")
    p.add_argument("--out", default="out/voc_analysis.json")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
