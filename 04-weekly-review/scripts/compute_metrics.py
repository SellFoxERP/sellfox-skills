#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计算层（周报 / 月报）。

输入：`fetch_weekly_data.py` 产出的中间 JSON。
输出：`computed.json`，含汇总指标、环比/同比、目标达成率、Top/Bottom 榜单、
      趋势序列、异常标记、覆盖范围与口径披露。

原则
----
* 阈值、目标值、分级线一律读配置（_shared/config/thresholds.yaml、targets.yaml），绝不硬编码。
* 分母为 0 → 不输出 inf/NaN，标「不适用」；字段缺失 → 标「需人工核查」；空数据 → 「无数据」，不写 0。
* 多币种且未折算 → 不跨币种相加，按币种分组，并显式告警。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import skill_paths

SHARED_ROOT = skill_paths.bootstrap()
from config_loader import ConfigLoader  # type: ignore  # noqa: E402

NA = "不适用"
NEEDS_REVIEW = "⚠️ 需人工核查"
NO_DATA = "无数据"


def _num(value: Any) -> Optional[float]:
    """把赛狐返回的数值（可能是字符串）安全转 float；转不了返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum(rows: List[Dict[str, Any]], field: Optional[str]) -> Optional[float]:
    if not field:
        return None
    vals = [_num(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def _unwrap(value: Any) -> Any:
    """宽表里 asin/msku 等字段常为 ``[{"key": "..."}]``；取内部标量。"""
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        return value.get("key") or value.get("value") or value.get("name")
    return value


def _first_present(row: Dict[str, Any], keys: Optional[List[str]]) -> Any:
    for k in keys or []:
        if not k:
            continue
        v = _unwrap(row.get(k))
        if v not in (None, ""):
            return v
    return None


def _weighted_rate(rows: List[Dict[str, Any]], rate_field: str, weight_field: str) -> Optional[float]:
    """按权重字段加权平均比率（比率以「百分比数值」或小数给出均可，仅做加权）。"""
    num = 0.0
    den = 0.0
    for r in rows:
        rate = _num(r.get(rate_field))
        w = _num(r.get(weight_field))
        if rate is None or w is None or w <= 0:
            continue
        num += rate * w
        den += w
    return (num / den) if den > 0 else None


def _pct_change(cur: Optional[float], base: Optional[float]) -> Optional[float]:
    if cur is None or base in (None, 0):
        return None
    return (cur - base) / base * 100.0


def _fmt_pct(v: Optional[float], digits: int = 1) -> str:
    return NA if v is None else f"{v:+.{digits}f}%"


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

def aggregate_profit(rows: List[Dict[str, Any]], fm: Dict[str, str]) -> Dict[str, Any]:
    """把利润报表行（可能是 店铺×日）聚合成总览指标。"""
    if not rows:
        return {"status": NO_DATA}
    groups = {}
    for row in rows:
        groups.setdefault(str(_unwrap(row.get("currency")) or "未知"), []).append(row)
    if len(groups) > 1:
        return {"status": "mixed_currency", "rows": len(rows),
                "sales_num": _sum(rows, fm["sales_num"]),
                "by_currency": {key: aggregate_profit(value, fm) for key, value in groups.items()}}
    gmv = _sum(rows, fm["gmv"])
    sales = _sum(rows, fm["sales_num"])
    gp = _sum(rows, fm["gross_profit"])
    ad_cost_raw = _sum(rows, fm["ad_cost"])
    ad_cost = abs(ad_cost_raw) if ad_cost_raw is not None else None
    return {
        "status": "ok",
        "currency": next(iter(groups)),
        "rows": len(rows),
        "gmv": gmv,
        "sales_num": sales,
        "gross_profit": gp,
        "gross_profit_rate": (gp / gmv) if (gp is not None and gmv) else None,
        "ad_cost": ad_cost,
        "ad_cost_rate": (ad_cost / gmv * 100.0) if (ad_cost is not None and gmv) else None,
        "refund_rate": _weighted_rate(rows, fm["refund_rate"], fm["gmv"]),
        "avg_order_value": (gmv / sales) if (gmv is not None and sales) else None,
    }


def per_shop(rows: List[Dict[str, Any]], fm: Dict[str, str]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        # `shopId` 已在 06 确认；不臆造 shopName/shop_id 等未核实字段名。
        key = str(_unwrap(r.get("shopId")) or "未知店铺")
        grouped.setdefault(key, []).append(r)
    out = []
    for key, grows in grouped.items():
        agg = aggregate_profit(grows, fm)
        agg["shop"] = key
        agg["currency"] = "/".join(distinct_currencies(grows)) or "未知"
        out.append(agg)
    out.sort(key=lambda x: (x.get("gmv") or 0), reverse=True)
    return out


def distinct_currencies(rows: List[Dict[str, Any]]) -> List[str]:
    cur = set()
    for r in rows:
        # 只认 06 确认的 `currency`；不臆造 currencyCode 等未核实字段名。
        c = _unwrap(r.get("currency"))
        if c:
            cur.add(str(c))
    return sorted(cur)


# ---------------------------------------------------------------------------
# 目标达成率
# ---------------------------------------------------------------------------

def _month_key(window: Dict[str, Any], which: str = "start") -> str:
    """从窗口取 ``YYYY-MM``。兼容 date 对象与 JSON 往返后的字符串。"""
    v = window.get(which)
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m")
    return str(v)[:7]


def achievement(plan: Dict[str, Any], targets: Dict[str, Any],
                mtd_agg: Dict[str, Any], cur_agg: Dict[str, Any]) -> Dict[str, Any]:
    """计算达成率。周报用「本月累计 vs 月目标」；月报用「本月 vs 月目标」。"""
    out: Dict[str, Any] = {"status": "no_target", "items": []}
    monthly = (targets.get("monthly") or {})
    cur_win = plan["windows"]["current"]
    mtd_win = plan["windows"]["month_to_date"]

    # 月报：直接比当前完整月；周报：比本月累计（周目标未配置时的显式口径降级）。
    # ⚠️ month_key 必须与 basis 同月：周报的 basis 是 month_to_date（anchor 所在月），
    #    因此 month_key 取自 mtd_win，而非「本期窗口」的起始月。月初时本期窗口常落在
    #    上个月（如 anchor=2026-10-01 → 本期 09-21~09-27），若取 cur_win 会把 10 月累计
    #    去比 9 月目标，达成率与评级全错。
    if plan["period"] == "month":
        month_key = _month_key(cur_win, "end")
        basis, basis_label = cur_agg, "本月"
    else:
        month_key = _month_key(mtd_win, "start")
        basis, basis_label = mtd_agg, "本月累计"

    target = monthly.get(month_key)
    if not target:
        out["note"] = (f"targets.yaml 未配置 {month_key} 的月目标（也无周目标配置），"
                       f"无法计算达成率；请补充后重跑。")
        return out

    out["status"] = "ok"
    out["basis"] = basis_label
    out["month_key"] = month_key
    grade_cfg = targets.get("achievement") or {}
    excellent_min = grade_cfg.get("excellent_min", 100)
    warning_min = grade_cfg.get("warning_min", 80)

    def grade(rate: Optional[float]) -> str:
        if rate is None:
            return NO_DATA
        if rate >= excellent_min:
            return "达标"
        if rate >= warning_min:
            return "预警"
        return "未达标"

    mapping = [
        ("GMV", "gmv", target.get("gmv")),
        ("毛利", "gross_profit", target.get("gross_profit")),
        ("毛利率%", "gross_profit_rate_pct", target.get("gross_profit_rate")),
        ("广告成本占比%", "ad_cost_rate", target.get("ad_cost_rate_max")),
    ]
    actual_map = {
        "gmv": basis.get("gmv"),
        "gross_profit": basis.get("gross_profit"),
        "gross_profit_rate_pct": (basis.get("gross_profit_rate") * 100.0)
        if basis.get("gross_profit_rate") is not None else None,
        "ad_cost_rate": basis.get("ad_cost_rate"),
    }
    for label, key, tgt in mapping:
        actual = actual_map.get(key)
        if tgt in (None, 0) or actual is None:
            out["items"].append({"label": label, "actual": actual, "target": tgt,
                                 "rate": None, "grade": NO_DATA})
            continue
        inverted = key == "ad_cost_rate"       # 广告成本占比：越低越好
        rate = (tgt / actual * 100.0) if inverted and actual else (actual / tgt * 100.0)
        out["items"].append({"label": label, "actual": actual, "target": tgt,
                             "rate": rate, "grade": grade(rate),
                             "direction": "lower_is_better" if inverted else "higher_is_better"})
    return out


# ---------------------------------------------------------------------------
# 榜单
# ---------------------------------------------------------------------------

def rankings(rows: List[Dict[str, Any]], fm: Dict[str, str], n: int,
             ident: Dict[str, Any]) -> Dict[str, Any]:
    """构造 Top/Bottom 榜。标识列（MSKU/ASIN/标题）字段名取自配置的候选列表。

    ⚠️ `get_profit_report_by_msku` 的标识字段名未在 `01`/`06` 中确认（06 中
    `msku`/`asin`/`sku`/`title` 的「出现于」均不含该工具）。因此这里**不臆造**字段名：
    只在配置候选里命中时取值；全部落空则标识列留空，并统计 `identifier_unresolved`
    交由上层标「⚠️ 需人工核查」，绝不静默显示成「不适用」。
    """
    out = []
    unresolved = 0
    for r in rows[:n]:
        msku = _first_present(r, ident.get("msku_candidates"))
        asin = _first_present(r, ident.get("asin_candidates"))
        title = _first_present(r, ident.get("title_candidates"))
        if msku is None and asin is None and title is None:
            unresolved += 1
        ad_raw = _num(r.get(fm["ad_cost"]))
        out.append({
            "msku": msku,
            "asin": asin,
            "title": title,
            # `currency` 已在 06 确认；跨币种行混排时靠它标注各行原币种（未折算）。
            "currency": str(_unwrap(r.get("currency")) or ""),
            "gmv": _num(r.get(fm["gmv"])),
            "sales_num": _num(r.get(fm["sales_num"])),
            "gross_profit": _num(r.get(fm["gross_profit"])),
            "gross_profit_rate": _num(r.get(fm["gross_profit_rate"])),
            "ad_cost": abs(ad_raw) if ad_raw is not None else None,
        })
    return {"rows": out, "identifier_unresolved": unresolved}


# ---------------------------------------------------------------------------
# 趋势
# ---------------------------------------------------------------------------

def build_trend(plan: Dict[str, Any], rows: List[Dict[str, Any]], fm: Dict[str, str],
                char_data: Any, points_max: int) -> Dict[str, Any]:
    if len(distinct_currencies(rows)) > 1:
        note = "不拼接不同币种金额为一条趋势线"
        if char_data:
            # charData 本身可用（单币种序列），但与本期多币种行不可加总 → 显式披露「已获取、未采用」，
            # 不静默丢弃取数产出（2-3 次网关调用）。
            note += "；已获取 charData（单币种序列）但多币种不可加总，未采用"
        return {"status": "mixed_currency", "source": "返回行含多种币种", "note": note}
    if char_data:
        return {"status": "ok", "source": "get_product_analyze_detail.charData", "charData": char_data}
    # 退化路径：按 bizDay 从利润报表日粒度聚合
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        # `bizDay` 已在 06 确认为利润报表日粒度字段（yyyyMMdd）；不臆造 statDate 等。
        day = str(_unwrap(r.get("bizDay")) or "")
        if day:
            by_day.setdefault(day, []).append(r)
    if not by_day:
        return {"status": NO_DATA, "source": "无 charData，且利润报表无 bizDay 日粒度",
                "note": NEEDS_REVIEW}
    series = []
    for day in sorted(by_day)[-points_max:]:
        agg = aggregate_profit(by_day[day], fm)
        series.append({"day": day, "gmv": agg.get("gmv"), "gross_profit": agg.get("gross_profit")})
    return {"status": "ok", "source": "利润报表 bizDay 日粒度（charData 缺失，降级）", "series": series}


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

def anomalies(cur: Dict[str, Any], prev: Dict[str, Any], pms: Dict[str, Any],
              thresholds: Dict[str, Any], rank_ok: bool) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    th = thresholds or {}

    def add(level: str, title: str, detail: str):
        flags.append({"level": level, "title": title, "detail": detail})

    sales_th = (th.get("sales") or {})
    d_prev = _pct_change(cur.get("gmv"), prev.get("gmv"))
    if d_prev is not None and d_prev <= -abs(sales_th.get("drop_pct_vs_7d", 30)):
        add("red", "销售额环比下滑", f"本期 GMV 较上期 {_fmt_pct(d_prev)}（阈值 -{sales_th.get('drop_pct_vs_7d')}%）")
    d_pms = _pct_change(cur.get("gmv"), pms.get("gmv"))
    if d_pms is not None and d_pms <= -abs(sales_th.get("drop_pct_vs_30d", 25)):
        add("red", "销售额低于上月同期", f"较上月同期 {_fmt_pct(d_pms)}（阈值 -{sales_th.get('drop_pct_vs_30d')}%）")

    gp_d = _pct_change(cur.get("gross_profit"), prev.get("gross_profit"))
    profit_th = (th.get("profit") or {})
    if gp_d is not None and gp_d <= -abs(profit_th.get("drop_pct_vs_30d", 25)):
        add("red", "毛利环比下滑", f"较上期 {_fmt_pct(gp_d)}")

    pr = cur.get("gross_profit_rate")
    if pr is not None and pr * 100 < (profit_th.get("gross_profit_rate_min", 0) or 0):
        add("red", "毛利率跌破下限", f"本期毛利率 {pr*100:.1f}%")

    ad_th = (th.get("ad") or {})
    acr = cur.get("ad_cost_rate")
    if acr is not None and acr > ad_th.get("acos_abs_max", 35):
        add("yellow", "广告成本占比偏高", f"本期 {acr:.1f}%（阈值 {ad_th.get('acos_abs_max')}%）")

    as_th = (th.get("after_sales") or {})
    rr = cur.get("refund_rate")
    rr_base = pms.get("refund_rate")
    rr_rise = _pct_change(rr, rr_base)
    if rr_rise is not None and rr_rise >= as_th.get("refund_rate_rise_pct_vs_30d", 50):
        add("yellow", "退款率上升", f"较上月同期 {_fmt_pct(rr_rise)}")

    if not rank_ok:
        add("info", "榜单覆盖不足", "Top/Bottom 榜单行数不足或被截断，结论仅供参考")
    return flags


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def compute(data: Dict[str, Any], shared_root: Optional[str] = None) -> Dict[str, Any]:
    cfg = skill_paths.load_skill_config("weekly")
    loader = ConfigLoader(shared_root)
    thresholds = loader.load("thresholds")
    targets = loader.load("targets")

    fm = cfg["profit_report_fields"]
    d = data["data"]
    meta = dict(data["meta"])
    plan = meta["period"]

    agg = {
        "current": aggregate_profit(d.get("profit_current") or [], fm),
        "prev": aggregate_profit(d.get("profit_prev") or [], fm),
        "prev_month_same_period": aggregate_profit(d.get("profit_prev_month_same_period") or [], fm),
        "month_to_date": aggregate_profit(d.get("profit_month_to_date") or [], fm),
        "yoy": aggregate_profit(d.get("profit_yoy") or [], fm),
    }

    deltas = {}
    for key, label in (("prev", "环比(上期)"), ("prev_month_same_period", "对比上月同期"),
                       ("yoy", "同比(去年同期)")):
        base = agg.get(key, {})
        if base.get("currency") != agg["current"].get("currency"):
            base = {}
        deltas[key] = {
            "label": label,
            "gmv_pct": _pct_change(agg["current"].get("gmv"), base.get("gmv")),
            "gross_profit_pct": _pct_change(agg["current"].get("gross_profit"), base.get("gross_profit")),
            "sales_num_pct": _pct_change(agg["current"].get("sales_num"), base.get("sales_num")),
            "gross_profit_rate_pct": _pct_change(agg["current"].get("gross_profit_rate"),
                                                 base.get("gross_profit_rate")),
            "ad_cost_rate_pct": _pct_change(agg["current"].get("ad_cost_rate"),
                                            base.get("ad_cost_rate")),
        }

    # 币种校验
    cur_currencies = distinct_currencies(d.get("profit_current") or [])
    if cur_currencies:
        meta["currency"] = " / ".join(cur_currencies) + "（返回行币种，未另作折算）"
    mixed_currency = len(cur_currencies) > 1

    ach = achievement(plan, targets, agg["month_to_date"], agg["current"])

    rank_cfg = cfg["rankings"]
    ident_cfg = cfg.get("rank_identifiers") or {}
    ident_verified = bool(ident_cfg.get("verified"))
    top_rows = d.get("rank_top") or []
    bottom_rows = d.get("rank_bottom") or []
    top_res = rankings(top_rows, fm, rank_cfg["top"]["n"], ident_cfg)
    bottom_res = rankings(bottom_rows, fm, rank_cfg["bottom"]["n"], ident_cfg)
    min_rows = rank_cfg.get("min_rows_for_rank", 5)
    rank_ok = len(top_rows) >= min_rows and len(bottom_rows) >= min_rows

    trend = build_trend(plan, d.get("profit_current") or [], fm,
                        d.get("char_data"), cfg["trend"]["points_max"])

    flags = anomalies(agg["current"], agg["prev"], agg["prev_month_same_period"], thresholds,
                      rank_ok)

    # 覆盖范围 / 披露
    disclosures = []
    for name, src in data["sources"].items():
        if src.get("truncation"):
            disclosures.append(f"{name}: {src['truncation']}")
        if src["status"] in ("degraded", "error", "no_data"):
            disclosures.append(f"{name}: [{src['status']}] {src.get('note') or src['tool']}")
    # 榜单标识列字段名未核实 → 显式披露（不静默显示成「不适用」）
    ident_unresolved = top_res["identifier_unresolved"] + bottom_res["identifier_unresolved"]
    if not ident_verified:
        note = ("榜单标识列（MSKU/ASIN/标题）：get_profit_report_by_msku 的标识字段名未在"
                " 01/06 中核实（06 中 msku/asin/sku/title 的「出现于」均不含该工具）；"
                "取不到的行标「⚠️ 需人工核查」，请用真实响应核对字段名后回填 "
                "weekly.yaml → rank_identifiers。")
        if ident_unresolved:
            note += f"（本期有 {ident_unresolved} 行标识列未取到）"
        disclosures.append(f"rank_identifiers: {note}")
    # 榜单跨币种未折算：Top/Bottom 把多币种行按原币种混排，必须在数据说明披露
    rank_currencies = sorted({str(r.get("currency"))
                              for r in (top_res["rows"] + bottom_res["rows"])
                              if r.get("currency")})
    if len(rank_currencies) > 1:
        disclosures.append(
            f"rankings: ⚠️ 榜单跨币种未折算（{', '.join(rank_currencies)}），"
            "各行金额为原币种（见榜单「币种」列），不可跨币种直接比较或相加。")
    # 多币种时已取得的 charData 被整体弃用 → 数据说明必须披露「已获取、未采用」
    if trend.get("status") == "mixed_currency" and d.get("char_data"):
        disclosures.append(
            "trend: charData 已获取（单币种序列），但本期利润行多币种、不可加总，未采用。")
    # 同比基期为按月 yyyyMM（整月口径，超 6 个月窗口按契约改按月）→ 必须标注口径差异
    if ((data.get("sources") or {}).get("profit_yoy") or {}).get("yoy_month_granularity"):
        if plan.get("period") == "month":
            disclosures.append(
                "yoy: 同比基期为整月口径（按月 yyyyMM 取整月），与本期按日窗口聚合口径不同，"
                "仅作方向参考。")
        else:
            disclosures.append(
                "yoy: 同比基期为整月口径（按月 yyyyMM 取整月），与本期周窗口非等长，"
                "仅作方向参考。")
    # 榜单覆盖量化：「仅覆盖 N 行」必须带服务端全量总数（无 total 则「总数未知」），
    # 全量不足 Top K 需求时用户才能判断头部集中度结论的覆盖上限。
    rank_src = (data.get("sources") or {}).get("rank_top") or {}
    rank_total = rank_src.get("rank_rows_total")
    rank_fetched = len(top_rows)
    if rank_fetched:
        total_desc = f"{rank_total} 行" if isinstance(rank_total, int) else "总数未知"
        top_need = int(rank_cfg["top"]["n"])
        if isinstance(rank_total, int) and rank_total < top_need:
            tail = (f"服务端全量 {total_desc} < Top {top_need} 需求，Top {top_need} 不可达，"
                    "头部集中度结论按现有全量理解（非截断）。")
        else:
            tail = f"覆盖以取回行数为上限（Top {top_need} / Bottom {int(rank_cfg['bottom']['n'])} 本地排序）。"
        disclosures.append(
            f"rankings: 榜单覆盖量化：本次取回 {rank_fetched} 行（服务端全量 {total_desc}）；{tail}")

    # 本期利润取数状态（结构化透传给渲染层：failed ≠ 无数据 ≠ 无异常，
    # 渲染层据此把本期列标「⚠️ 需人工核查」并在核心结论写明取数失败）
    cur_src = (data.get("sources") or {}).get("profit_current") or {}
    source_status = {"profit_current": {"status": cur_src.get("status") or "ok",
                                        "error_code": cur_src.get("error_code") or ""}}

    return {
        "meta": meta,
        "aggregate": agg,
        "per_shop": per_shop(d.get("profit_current") or [], fm),
        "deltas": deltas,
        "achievement": ach,
        "rankings": {"top": top_res["rows"], "bottom": bottom_res["rows"],
                     "top_label": rank_cfg["top"]["label"],
                     "bottom_label": rank_cfg["bottom"]["label"],
                     "identifier_verified": ident_verified,
                     "identifier_unresolved": ident_unresolved},
        "trend": trend,
        "anomalies": flags,
        "source_status": source_status,
        "coverage": {
            "currencies": cur_currencies,
            "mixed_currency": mixed_currency,
            "disclosures": disclosures,
        },
        "targets_configured": ach["status"] == "ok",
    }


def _main() -> None:
    ap = argparse.ArgumentParser(description="周报/月报计算（只读）")
    ap.add_argument("--in", dest="inp", default="weekly_data.json")
    ap.add_argument("--out", default="computed.json")
    ap.add_argument("--shared-root", default=None)
    args = ap.parse_args()

    data = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    result = compute(data, shared_root=args.shared_root)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                              encoding="utf-8")

    a = result["aggregate"]["current"]
    print(f"✅ 计算完成 → {args.out}")
    print(f"  本期 GMV={a.get('gmv')} 毛利={a.get('gross_profit')} 毛利率="
          f"{None if a.get('gross_profit_rate') is None else round(a['gross_profit_rate']*100,1)}%")
    if result["achievement"]["status"] != "ok":
        print(f"  ⚠️ 达成率：{result['achievement'].get('note')}")
    for f in result["anomalies"]:
        print(f"  [{f['level']}] {f['title']} — {f['detail']}")


if __name__ == "__main__":
    _main()