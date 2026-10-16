#!/usr/bin/env python3
"""每日经营日报 —— 分析层（analyze.py）。

输入：fetch_data.py 产出的 bundle.json
输出：analysis.json（L1 结论 / L2 七步 / 红线 / 归因线索 / 无数据与需人工核查项）

原则：
  * 阈值一律读 ``thresholds.yaml``，**不硬编码**。
  * 空数据标「无数据」，**不写 0**；拿不到标「需人工核查」，**不编造**。
  * 基线优先用报表现成窗口字段 ``sevenSaleNum`` / ``thirtySaleNum``（方案 A）。
  * ACOS 归因只用字段字典确认存在的字段（``adConversionRateCpc`` / ``adClickRate`` / ``cpc``）；
    ``servingStatus=BUDGET_CAPPED`` 不作为判定条件。

用法::

    python analyze.py --in out/bundle.json --out out/analysis.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_THIS = Path(__file__).resolve()
_SKILL_ROOT = _THIS.parent.parent
sys.path.insert(0, str(_THIS.parent))
from sharedpath import shared_root  # noqa: E402

_SHARED_ROOT = shared_root(_SKILL_ROOT)
sys.path.insert(0, str(_SHARED_ROOT / "scripts"))
from config_loader import load_yaml_file  # noqa: E402

_SKILL_CFG = load_yaml_file(_SKILL_ROOT / "config" / "daily_report.yaml")

NO_DATA = "无数据"
HUMAN = "⚠️ 需人工核查"


# --- 数值安全 ---------------------------------------------------------------
def num(v: Any) -> Optional[float]:
    """安全转数值；None/空/非数 → None（**不返回 0**，0 与「没拿到」是两回事）。"""
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "").strip())
        return f
    except (TypeError, ValueError):
        return None


def pct_dev(current: Optional[float], base: Optional[float]) -> Optional[float]:
    """偏离度 %： (current-base)/|base|*100。分母为 0/None → None（不输出 inf/NaN）。"""
    if current is None or base is None or base == 0:
        return None
    return (current - base) / abs(base) * 100.0


def fmt(v: Optional[float], digits: int = 2, unit: str = "") -> str:
    """渲染数值；None → 「无数据」。"""
    if v is None:
        return NO_DATA
    return f"{v:,.{digits}f}{unit}"


def _sum(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    """求和；若全部缺字段 → None（无数据），否则忽略缺失项累加。"""
    vals = [num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


# --- 异常判定 ---------------------------------------------------------------
def _anomaly(
    name: str,
    current: Optional[float],
    baseline: Optional[float],
    deviation: Optional[float],
    threshold: Optional[float],
    direction: str,
    clue: str,
    unit: str = "",
) -> Optional[Dict[str, Any]]:
    """构造一个异常项；未超阈值或数据缺失 → None。"""
    if deviation is None:
        return None
    triggered = (
        (direction == "down" and threshold is not None and deviation <= -abs(threshold))
        or (direction == "up" and threshold is not None and deviation >= abs(threshold))
        or (direction == "abs_up" and threshold is not None and current is not None and current > threshold)
    )
    if not triggered:
        return None
    return {
        "name": name,
        "current": current,
        "baseline": baseline,
        "deviation_pct": round(deviation, 1),
        "threshold": threshold,
        "direction": direction,
        "clue": clue,
        "unit": unit,
    }


# --- 跨平台销量汇总（可选步骤 6）---------------------------------------------
MULTI_VALUE_FIELDS = ("salePrice", "orderNum", "productNum")


def _multiplatform_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """汇总 bundle.data.multiplatform：按（指标, 平台, 币种）分组，不跨币种合计。

    兼容两种行形状（均为线上/离线实测）：
      * 扁平行：``{platformName: str, currency, salePrice, orderNum, ...}``；
      * 嵌套行：``{platformName: [str], productSaleDayOpenVo: [{day, currency, salePrice, ...}]}``。
    """
    metrics = (data.get("multiplatform") or {}).get("metrics") or {}
    groups: Dict[tuple, Dict[str, Any]] = {}
    platforms: Dict[str, None] = {}
    rows_total = 0
    for metric in sorted(metrics):
        for r in metrics[metric] or []:
            if not isinstance(r, dict):
                continue
            platform = r.get("platformName")
            if isinstance(platform, list):
                platform = platform[0] if platform else None
            platform = str(platform or "未知平台")
            entries = r.get("productSaleDayOpenVo")
            entries = entries if isinstance(entries, list) and entries else [r]
            for e in entries:
                if not isinstance(e, dict):
                    continue
                rows_total += 1
                platforms.setdefault(platform, None)
                cur_m = str(e.get("currency") or "未知")
                g = groups.setdefault(
                    (metric, platform, cur_m),
                    {"metric": metric, "platform": platform, "currency": cur_m, "rows": 0,
                     **{f: None for f in MULTI_VALUE_FIELDS}},
                )
                g["rows"] += 1
                for f in MULTI_VALUE_FIELDS:
                    v = num(e.get(f))
                    if v is not None:
                        g[f] = (g[f] or 0.0) + v
    return {
        "available": bool(groups),
        "rows": rows_total,
        "metrics": sorted(metrics),
        "platforms": list(platforms),
        "groups": sorted(groups.values(), key=lambda x: (x["metric"], x["platform"], x["currency"])),
        "note": "跨平台行含多币种时按币种分组展示（未折算），不跨币种直接合计；覆盖平台以上游实际返回为准。",
    }


def analyze(bundle: Dict[str, Any]) -> Dict[str, Any]:
    th = bundle.get("thresholds") or {}
    data = bundle.get("data") or {}
    analysis: Dict[str, Any] = {
        "report_date": bundle.get("report_date"),
        "config": bundle.get("config"),
        "demo_fingerprint": bundle.get("demo_fingerprint"),
        "truncations": bundle.get("truncations") or [],
        "degradations": list(bundle.get("degradations") or []),
        "human_check": list(bundle.get("human_check") or []),
        "no_data_sources": [],
        "l1": {},
        "steps": {},
        "redlines": [],
    }

    store = data.get("store_profit") or {}
    store_rows: List[Dict[str, Any]] = store.get("reportList") or []
    msku = data.get("msku_report") or {}
    msku_rows: List[Dict[str, Any]] = msku.get("rows") or []
    campaigns = (data.get("campaigns") or {}).get("itemList") or []
    orders = (data.get("orders_sample") or {}).get("rows") or []

    if not store_rows:
        analysis["no_data_sources"].append("get_profit_report_by_shop（店铺级利润）")
    if not msku_rows:
        analysis["no_data_sources"].append("get_custom_report_page_list（自定义报表宽表）")
    if not campaigns:
        analysis["no_data_sources"].append("get_sp_campaign_list（SP 广告活动）")

    anomalies: List[Dict[str, Any]] = []

    # ---------- L1 结论层（管理层）----------
    gmv = _sum(store_rows, "productSales")
    gross = _sum(store_rows, "grossProfit")
    units = _sum(store_rows, "salesNum")
    groups = {}
    for row in store_rows:
        groups.setdefault(str(row.get("currency") or "未知"), []).append(row)
    grouped_money = {key: {"gmv": _sum(rows, "productSales"), "gross_profit": _sum(rows, "grossProfit")}
                     for key, rows in groups.items()}
    if len(groups) > 1:
        gmv = gross = None
        analysis["degradations"].append("返回多种币种，未统一折算；金额按返回币种分别展示，不计算跨币种总金额。")
    gpr = (gross / gmv * 100.0) if (gmv and gross is not None) else None
    ad_cost = _sum(msku_rows, "adCost")
    ad_sales = _sum(msku_rows, "adSalePrice")
    acos = (ad_cost / ad_sales * 100.0) if (ad_cost is not None and ad_sales) else None
    sessions = _sum(msku_rows, "sessions")
    orders_cnt = _sum(msku_rows, "orderNum")
    cvr = (orders_cnt / sessions * 100.0) if (orders_cnt is not None and sessions) else None

    analysis["l1"] = {
        "gmv": gmv, "gross_profit": gross, "gross_profit_rate": gpr,
        "units": units, "acos": acos, "cvr": cvr, "by_currency": grouped_money if len(groups) > 1 else {},
        "currency": next(iter(groups)) if len(groups) == 1
        else ("多币种（分组展示）" if groups else (bundle.get("config") or {}).get("currency", "USD")),
        "store_count": len(store_rows),
    }

    # ---------- STEP 01 账户 / 订单（P0，覆盖最低）----------
    refunds = _sum(msku_rows, "refundNum")
    refund_amt = _sum(msku_rows, "refundPrice")
    returns = _sum(msku_rows, "returnSaleNum")
    analysis["steps"]["01_account"] = {
        "refund_num": refunds,
        "refund_amount": refund_amt,
        "return_num": returns,
        "anomaly_orders_sampled": len(orders),
        "manual": [
            "账户健康 / 绩效通知 — MCP 无接口，需人工登录 Seller Central",
            "买家消息 / 待回工单 — MCP 无客服接口",
            "停售 / 抑制原因 — 仅能以 onlineStatus 近似，拿不到原因",
        ],
    }
    if refunds is None and not msku_rows:
        analysis["steps"]["01_account"]["refund_num"] = NO_DATA

    # ---------- STEP 02 销售（P0）----------
    sales_anoms: List[Dict[str, Any]] = []
    msku_anoms: List[Dict[str, Any]] = []
    for r in msku_rows:
        sale = num(r.get("saleNum"))
        b7 = num(r.get("sevenSaleNum"))
        b30 = num(r.get("thirtySaleNum"))
        avg7 = (b7 / 7.0) if b7 is not None else None
        avg30 = (b30 / 30.0) if b30 is not None else None
        dev7 = pct_dev(sale, avg7)
        a = _anomaly(
            "销量", sale, avg7, dev7,
            (th.get("sales") or {}).get("drop_pct_vs_7d"),
            "down",
            "拆：订单量跌 vs 客单价跌 → 再看 Sessions 跌还是 CVR 跌",
        )
        if a:
            label = r.get("msku") or r.get("sku") or r.get("asin") or "?"
            a["msku"] = label
            a["shopId"] = r.get("shopId")
            a["name"] = f"销量 {label}（店铺 {r.get('shopId')}）"
            msku_anoms.append(a)
        if dev7 is not None and avg30:
            pass  # 30 天偏离在红线排序时一并体现（此处保留 avg30 备扩展）
    sales_anoms.extend(sorted(msku_anoms, key=lambda x: abs(x["deviation_pct"]), reverse=True)[:10])

    # 店铺级：有 slice_30d 才有基线，否则标「无基线」
    rng = (data.get("store_profit_range") or {}).get("reportList") or []
    store_baseline: Dict[str, Any] = {}
    for row in rng:
        sid = str(row.get("shopId") or row.get("id") or "")
        if sid:
            store_baseline.setdefault(sid, []).append(row)
    store_anoms: List[Dict[str, Any]] = []
    for row in store_rows:
        sid = str(row.get("shopId") or row.get("id") or "")
        cur = num(row.get("productSales"))
        series = store_baseline.get(sid) or []
        hist = [num(x.get("productSales")) for x in series if str(x.get("bizDay") or "") != str(bundle["report_date"]["compact"])]
        hist = [h for h in hist if h is not None]
        base = (sum(hist) / len(hist)) if hist else None
        dev = pct_dev(cur, base)
        a = _anomaly(
            f"店铺 {row.get('shopName') or sid} 销售额", cur, base, dev,
            (th.get("sales") or {}).get("drop_pct_vs_7d"), "down",
            "按店铺 → 再下钻 ASIN，看是流量跌还是转化跌",
        )
        if a:
            a["shopId"] = sid
            store_anoms.append(a)
    analysis["steps"]["02_sales"] = {
        "gmv": gmv, "units": units,
        "baseline_method": (bundle.get("config") or {}).get("baseline"),
        "msku_anomalies": sales_anoms,
        "store_anomalies": store_anoms,
        "store_baseline_available": bool(rng),
        "note": "" if rng else "未取 30 天基线数据（baseline=window_field），店铺级偏离度不可用",
    }
    anomalies.extend(store_anoms)
    anomalies.extend(sales_anoms)

    # ---------- STEP 03 广告 ----------
    states = {"enabled": 0, "paused": 0, "archived": 0, "unknown": 0}
    for c in campaigns:
        st = str(c.get("state") or "").lower()
        states[st if st in states else "unknown"] += 1
    acos_anom = None
    if acos is not None:
        acos_anom = _anomaly(
            "ACOS", acos, None, 0.0,
            (th.get("ad") or {}).get("acos_abs_max"), "abs_up",
            "查 cpc（竞价涨？）+ adClickRate（素材衰退？）+ adConversionRateCpc（转化跌？）",
            unit="%",
        )
    ad_zero = []
    for r in msku_rows:
        cost = num(r.get("adCost"))
        if cost is None:
            continue
        adord = num(r.get("adOrderNum"))
        if adord == 0 and cost >= (th.get("ad") or {}).get("zero_order_cost_min", 1e9):
            ad_zero.append({"msku": r.get("msku") or r.get("asin"), "adCost": cost})
    analysis["steps"]["03_ads"] = {
        "ad_cost": ad_cost, "ad_sales": ad_sales, "acos": acos,
        "campaign_states": states,
        "acos_anomaly": acos_anom,
        "high_cost_zero_order": ad_zero[:10],
        "note": "servingStatus 字段存在，但 BUDGET_CAPPED 取值未经实测验证，不作为判定条件。",
    }
    if acos_anom:
        anomalies.append(acos_anom)
    if not campaigns:
        analysis["steps"]["03_ads"]["campaign_states"] = NO_DATA

    # ---------- STEP 04 库存 ----------
    inv_th = th.get("inventory") or {}
    inv_warn: List[Dict[str, Any]] = []
    for r in msku_rows:
        days = num(r.get("availableDays"))
        if days is None:
            continue
        crit = days < inv_th.get("available_days_critical", 0)
        warn = days < inv_th.get("available_days_min", 0)
        if crit or warn:
            inv_warn.append({
                "msku": r.get("msku") or r.get("sku") or r.get("asin"),
                "shopId": r.get("shopId"),
                "availableDays": days,
                "level": "紧急" if crit else "预警",
                "inbound": {
                    "inboundWorking": num(r.get("inboundWorking")),
                    "inboundShipped": num(r.get("inboundShipped")),
                    "inTransit": num(r.get("inTransit")),
                },
                "clue": "availableDays + 在途（inboundWorking/inboundShipped）能否救急；suggestNumSum 为赛狐补货建议量",
            })
    inv_warn.sort(key=lambda x: x["availableDays"])
    analysis["steps"]["04_inventory"] = {
        "warnings": inv_warn[:20],
        "warn_count": len(inv_warn),
        "critical_count": sum(1 for x in inv_warn if x["level"] == "紧急"),
    }
    if not msku_rows:
        analysis["steps"]["04_inventory"]["warnings"] = NO_DATA

    # ---------- STEP 05 转化 ----------
    buybox_bad = []
    for r in msku_rows:
        bb = num(r.get("buyBoxPercent"))
        if bb is not None and bb < (th.get("listing") or {}).get("buybox_pct_min", 0):
            buybox_bad.append({"msku": r.get("msku") or r.get("asin"), "buyBoxPercent": bb})
    analysis["steps"]["05_conversion"] = {
        "sessions": sessions, "orders": orders_cnt, "cvr": cvr,
        "buybox_below": buybox_bad[:10],
        "note": "STEP 02 与 STEP 05 数据重叠：02 看结果（销售额/订单），05 看过程（流量/转化/购物车）。",
    }
    if not msku_rows:
        analysis["steps"]["05_conversion"]["cvr"] = NO_DATA

    # ---------- STEP 06 售后 ----------
    ref_rate = None
    rr_vals = [num(r.get("refundRate")) for r in msku_rows]
    rr_vals = [v for v in rr_vals if v is not None]
    if rr_vals:
        ref_rate = sum(rr_vals) / len(rr_vals)
    low_reviews = _sum(msku_rows, "lowReviewNum")
    analysis["steps"]["06_after_sales"] = {
        "refund_rate": ref_rate, "refund_num": refunds, "return_num": returns,
        "low_review_num": low_reviews,
        "manual": ["评论原文 — MCP 仅有聚合指标（好评率 / 差评数），无评论内容"],
    }
    if not msku_rows:
        analysis["steps"]["06_after_sales"]["refund_rate"] = NO_DATA

    # ---------- STEP 07 政策（完全无法自动化）----------
    analysis["steps"]["07_policy"] = {
        "status": HUMAN,
        "manual": ["政策合规 — MCP 无此接口", "绩效风险 — MCP 无此接口"],
    }

    # ---------- 跨平台销量（可选步骤 6；多币种不跨币种合计）----------
    analysis["multiplatform"] = _multiplatform_summary(data)

    # ---------- 今日红线（按 |偏离度| 排序，≤5）----------
    max_items = int((th.get("redline") or {}).get("max_items", 5))
    positives = [a for a in anomalies if a.get("deviation_pct") is not None]
    positives.sort(key=lambda x: abs(x["deviation_pct"]), reverse=True)
    redlines = positives[:max_items]
    # 库存紧急项也进红线（按紧急度）
    for w in [x for x in inv_warn if x["level"] == "紧急"][:max_items]:
        if len(redlines) >= max_items:
            break
        redlines.append({
            "name": f"库存紧急 {w['msku']}",
            "current": w["availableDays"], "baseline": None, "deviation_pct": None,
            "direction": "down", "clue": w["clue"], "unit": "天",
        })
    analysis["redlines"] = redlines
    analysis["redline_max_items"] = max_items

    return analysis


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="每日经营日报 - 分析")
    ap.add_argument("--in", dest="inp", default="out/bundle.json")
    ap.add_argument("--out", default="out/analysis.json")
    args = ap.parse_args(argv)

    inp = Path(args.inp)
    if not inp.is_absolute():
        inp = _SKILL_ROOT / inp
    if not inp.exists():
        print(f"❌ 找不到数据包：{inp}（先运行 fetch_data.py）", file=sys.stderr)
        return 2

    bundle = json.loads(inp.read_text(encoding="utf-8"))
    analysis = analyze(bundle)

    out = Path(args.out)
    if not out.is_absolute():
        out = _SKILL_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 分析已写出：{out}（红线 {len(analysis['redlines'])} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())