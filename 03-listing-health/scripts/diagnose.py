#!/usr/bin/env python3
"""Listing 健康体检 —— 诊断脚本。

读取 fetch_listing_data.py 产出的 JSON，按配置阈值分诊，输出按严重度排序的问题清单。
所有阈值/参数来自配置，绝不硬编码；拿不到的字段标「需人工核查」，空数据标「无数据」。

用法：
    python scripts/diagnose.py --in data.json --out problems.json
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Optional

import listing_common as lc


def _problem(level: str, check: str, rec: Dict[str, Any], evidence: str, source: str,
             advice: str, need_manual: bool = False, note: str = "") -> Dict[str, Any]:
    return {
        "severity": level,
        "check": check,
        "asin": lc.key_of(rec.get("asin") or rec.get("parentAsin")) or "未知",
        "shop": lc.key_of(rec.get("shopId")) or "未知",
        "title": lc.key_of(rec.get("title")) or "",
        "evidence": evidence,
        "source": source,           # 字段所属工具（归因逐字段标注）
        "advice": advice,
        "need_manual": need_manual,
        "note": note,
    }


def diagnose(data: Dict[str, Any]) -> Dict[str, Any]:
    cfg = lc.load_skill_config("listing")
    shared_listing = lc.load_shared_section("thresholds", "listing")  # 通用阈值

    # 通用阈值以 _shared/config/thresholds.yaml → listing 为【单一真源】；
    # 技能 config/listing.yaml 不再重复定义，这里仅保留兜底默认值（缺失时用）。
    require_winner = (cfg.get("buybox") or {}).get("require_winner", True)
    rating_drop_abs = shared_listing.get("rating_drop_abs", (cfg.get("rating") or {}).get("drop_abs", 0.2))
    bsr_drop_pct = (cfg.get("bsr") or {}).get("rank_drop_pct", 30)
    sales_drop_pct = (cfg.get("sales") or {}).get("drop_pct_vs_30d", 30)
    required_groups = (cfg.get("labels") or {}).get("required_groups") or []
    # buybox_pct_min / sessions_zero_days / conversion 段仅属宽表判定（buyBoxPercent/sessions），
    # v1.7.0 起不再读取（宽表字段由 custom-report-analysis 技能承接）。

    meta = data.get("meta") or {}
    online_rows = data.get("online") or []

    problems: List[Dict[str, Any]] = []
    manual: List[str] = []

    for rec in online_rows:
        # --- P0：不可售 / 被删除 ---
        status_raw = lc.key_of(rec.get("onlineStatus"))
        status = (status_raw or "").strip().lower()
        if status and status != "active":
            level = "P0"
            if status in ("inactive", "in_active"):
                ev = f"onlineStatus={status_raw}（不可售/被抑制）"
            elif status == "delete":
                ev = f"onlineStatus={status_raw}（已删除）"
            else:
                ev = f"onlineStatus={status_raw}（未知状态值，需人工核查）"
            problems.append(_problem(
                level, "不可售/被删除", rec, ev,
                "get_online_product_page_list.onlineStatus",
                "立即登录卖家后台核查账户健康/绩效通知（MCP 拿不到原因，需人工核查）",
                need_manual=(status not in ("inactive", "in_active", "delete")),
            ))
        elif not status_raw:
            manual.append(f"ASIN {lc.key_of(rec.get('asin')) or '?'}：onlineStatus 无数据（标「无数据」，非 0）")

        # --- P1：丢购物车（buyBoxPercent 为宽表字段，由 custom-report-analysis 承接；
        #     此处仅用 buyBoxWinner 布尔退化信号）---
        if require_winner:
            winner = lc.is_true(rec.get("buyBoxWinner"))
            if winner is False:
                problems.append(_problem(
                    "P1", "丢购物车", rec, "buyBoxWinner=false（瞬时信号）",
                    "get_online_product_page_list.buyBoxWinner",
                    "口径降级：无买盒占比，仅有布尔；建议持续观察并人工核查跟卖",
                    need_manual=True, note="买盒占比（buyBoxPercent）为宽表字段，由 custom-report-analysis 承接",
                ))
            elif winner is None:
                manual.append(f"ASIN {lc.key_of(rec.get('asin')) or '?'}：buyBoxWinner 无数据")

        # --- P1 无流量 / P2 有流量无转化：sessions/conversionRate 为宽表字段
        #     （由 custom-report-analysis 技能承接），本技能固定标「需人工核查」---
        manual.append(
            f"ASIN {lc.key_of(rec.get('asin')) or '?'}：无流量/有流量无转化检查不可用"
            "（未接入自定义报表，由 custom-report-analysis 承接）")

        # --- P2：BSR 恶化（基线来自 data.meta.baseline，可选）---
        cur_rank = lc.bsr_rank(rec)
        baseline = (meta.get("baseline") or {}).get(lc.record_id(rec)) or {}
        prev_rank = lc.to_float(baseline.get("bsrRank"))
        if cur_rank is not None and prev_rank:
            if cur_rank > prev_rank * (1 + float(bsr_drop_pct) / 100.0):
                problems.append(_problem(
                    "P2", "BSR 恶化", rec, f"bsrRank 名次 {prev_rank:.0f} → {cur_rank:.0f}"
                    f"（+{(cur_rank - prev_rank) / prev_rank * 100:.0f}%，阈值 {bsr_drop_pct}%）",
                    "get_online_product_page_list.bsrRank",
                    "核查关键词与类目节点；类目名次需人工核查", need_manual=True,
                ))
        elif cur_rank is not None and not prev_rank:
            manual.append(f"ASIN {lc.key_of(rec.get('asin')) or '?'}：BSR 名次 {cur_rank:.0f} 无基线，需历史快照")

        # --- P2：销量衰退（现成窗口字段）---
        d7 = lc.to_float(rec.get("day7SaleNum"))
        d30 = lc.to_float(rec.get("day30SaleNum"))
        if d7 is not None and d30 is not None and d30 > 0:
            expected7 = d30 / 30.0 * 7.0
            if expected7 > 0 and d7 < expected7 * (1 - float(sales_drop_pct) / 100.0):
                problems.append(_problem(
                    "P2", "销量衰退", rec,
                    f"day7SaleNum={d7:.0f} 低于 day30 折算近 7 天 {expected7:.1f}（阈值 {sales_drop_pct}%）",
                    "get_online_product_page_list.day7SaleNum/day30SaleNum",
                    "结合 BSR 与流量排查（可能断货/权重下滑）", need_manual=False,
                ))

        # --- P2：评分下滑（需基线）---
        rating = lc.to_float(rec.get("rating"))
        prev_rating = lc.to_float(baseline.get("rating"))
        if rating is not None and prev_rating is not None and (prev_rating - rating) >= float(rating_drop_abs):
            problems.append(_problem(
                "P2", "评分下滑", rec, f"rating {prev_rating} → {rating}（阈值 {rating_drop_abs}）",
                "get_online_product_page_list.rating",
                "核查近期差评主题（评论原文拿不到，需人工核查）", need_manual=True,
            ))
        elif rating is not None and prev_rating is None:
            manual.append(f"ASIN {lc.key_of(rec.get('asin')) or '?'}：rating={rating} 无基线，需历史快照")

        # --- P3：标签缺失 ---
        if required_groups:
            present: set = set()
            for lab in (rec.get("labels") or []):
                if isinstance(lab, dict):
                    for k in ("labelName", "labelType", "groupType"):
                        if lab.get(k):
                            present.add(str(lab[k]))
            for pl in (rec.get("productLabelList") or []):
                if isinstance(pl, dict):
                    if pl.get("groupType"):
                        present.add(str(pl["groupType"]))
                    for d in (pl.get("labelDetailList") or []):
                        if isinstance(d, dict) and d.get("labelType"):
                            present.add(str(d["labelType"]))
            missing = [g for g in required_groups if str(g) not in present]
            if missing:
                problems.append(_problem(
                    "P3", "标签缺失", rec, f"缺少标签分组：{', '.join(map(str, missing))}",
                    "get_online_product_page_list.labels/productLabelList",
                    "按团队标签体系统一打标", need_manual=False,
                ))

    # --- 断货提示（退化路径提示，不进入补货逻辑）---
    for rec in online_rows:
        # P0 已删除/不可售的商品不再给「断货拖累排名」建议（商品本身已下架，补货提示是噪声）
        status_raw = lc.key_of(rec.get("onlineStatus"))
        status = (status_raw or "").strip().lower()
        if status and status != "active":
            continue
        qty = lc.to_float(rec.get("quantity"))
        if qty == 0:
            problems.append(_problem(
                "P2", "断货拖累排名", rec,
                "quantity=0（availableDays 为宽表字段，由 custom-report-analysis 承接，需人工核查）",
                "get_online_product_page_list.quantity",
                "补货建议见技能 2（库存健康）；本技能仅提示可能拖累排名",
                need_manual=True,
            ))

    # --- 降级/拿不到 → 需人工核查清单（宽表字段固定由 custom-report-analysis 承接）---
    for item in ((cfg.get("degradation") or {}).get("manual_when_no_report") or []):
        manual.append(f"{item}（未接入自定义报表，由 custom-report-analysis 承接）")
    manual.extend([
        "竞品 BuyBox 价 / 竞品售价（MCP 无字段）",
        "类目平均转化率基准（MCP 无字段）",
        "账户健康 / 绩效通知 / 政策合规状态（MCP 无接口）",
        "评论原文 / 差评明细文本（MCP 无接口）",
    ])

    problems = lc.sort_problems(problems)
    return {
        "meta": {
            "generated_at": meta.get("generated_at"),
            "degraded": True,  # 固定：未接入自定义报表（custom-report-analysis 承接）
            "by_label": meta.get("by_label"),
            "demo_fingerprint": meta.get("demo_fingerprint"),
            "window": meta.get("window"),
            "statuses_scanned": meta.get("statuses_scanned"),
            "shop_ids": meta.get("shop_ids"),
            "disclosures": meta.get("disclosures") or [],
            "errors": meta.get("errors") or [],
            "counts": {
                "online_rows": len(online_rows),
                "problems": len(problems),
            },
        },
        "problems": problems,
        "manual_verification": sorted(set(manual)),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Listing 健康体检诊断")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", default="problems.json")
    args = ap.parse_args(argv)

    data = lc.read_json(args.inp)
    result = diagnose(data)
    lc.write_json(args.out, result)

    c = result["meta"]["counts"]
    print(f"✓ 诊断完成：在线 {c['online_rows']} 行 → 问题 {c['problems']} 条 → {args.out}")
    if result["meta"]["degraded"]:
        print("⚠️ 口径降级：未接入自定义报表（由 custom-report-analysis 技能承接）")
    if result["meta"]["demo_fingerprint"]:
        print(f"⚠️ 演示环境：{result['meta']['demo_fingerprint']}")
    if result["meta"]["errors"]:
        for e in result["meta"]["errors"]:
            print(f"✗ {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())