#!/usr/bin/env python3
"""每日经营日报 —— 分析层（analyze.py）。

输入：fetch_data.py 产出的 bundle.json
输出：analysis.json（结论速览 / 七步明细 / 红线 / 归因线索 / 无数据与需人工核查项）

原则：
  * 阈值一律读 ``thresholds.yaml``，**不硬编码**。
  * 空数据标「无数据」，**不写 0**；拿不到标「需人工核查」，**不编造**。
  * v1.7.0 起不取自定义报表宽表：宽表专属指标（流量/CVR、广告点击、库龄分桶、
    补货建议、MSKU 级销量环比、退款/退货全量计数）固定标
    「⚠️ 需人工核查——由 custom-report-analysis 技能承接」。
  * 店铺级基线优先用 30 天切片（profit_shop_range），按日切片算均值。

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

#: 宽表专属指标固定口径（v1.7.0 起不取自定义报表，由 custom-report-analysis 技能承接）
WIDE_TABLE_NOTE = "宽表字段（由 custom-report-analysis 技能承接）— ⚠️ 需人工核查"


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
    envelope_rows_total = 0  # 信封行数（coverage 口径），与展开后的日条目数区分
    for metric in sorted(metrics):
        metric_rows = metrics[metric] or []
        envelope_rows_total += len(metric_rows)
        for r in metric_rows:
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
        "envelope_rows": envelope_rows_total,
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
    campaigns = (data.get("campaigns") or {}).get("itemList") or []
    orders = (data.get("orders_sample") or {}).get("rows") or []

    if not store_rows:
        analysis["no_data_sources"].append("get_profit_report_by_shop（店铺级利润）")
    if not campaigns:
        analysis["no_data_sources"].append("get_sp_campaign_list（SP 广告活动）")

    anomalies: List[Dict[str, Any]] = []

    # ---------- 结论速览 ----------
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
    # 宽表（get_custom_report_page_list）已不在取数范围：ACOS/CVR 为宽表字段，固定不可用
    ad_cost = ad_sales = acos = sessions = orders_cnt = cvr = None

    analysis["l1"] = {
        "gmv": gmv, "gross_profit": gross, "gross_profit_rate": gpr,
        "units": units, "acos": acos, "cvr": cvr, "by_currency": grouped_money if len(groups) > 1 else {},
        "currency": next(iter(groups)) if len(groups) == 1
        else ("多币种（分组展示）" if groups else (bundle.get("config") or {}).get("currency", "USD")),
        "store_count": len(store_rows),
    }

    # ---------- STEP 01 账户 / 订单（P0，覆盖最低）----------
    # 退款/退货全量计数（refundNum/refundPrice/returnSaleNum）为宽表字段 → 固定需人工核查
    analysis["steps"]["01_account"] = {
        "refund_num": None,
        "refund_amount": None,
        "return_num": None,
        "anomaly_orders_sampled": len(orders),
        "manual": [
            "账户健康 / 绩效通知 — MCP 无接口，需人工登录 Seller Central",
            "买家消息 / 待回工单 — MCP 无客服接口",
            "停售 / 抑制原因 — 仅能以 onlineStatus 近似，拿不到原因",
            "退款/退货全量计数（refundNum/returnSaleNum）— 宽表字段，由 custom-report-analysis 技能承接",
        ],
    }

    # ---------- STEP 02 销售（P0）----------
    # MSKU 级销量环比（saleNum/sevenSaleNum）为宽表字段 → 固定需人工核查，仅保留店铺级
    sales_anoms: List[Dict[str, Any]] = []

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
        "note": "" if rng else "未取 30 天基线切片（profit_shop_range），店铺级偏离度不可用；MSKU 级环比为宽表指标（custom-report-analysis 承接）",
    }
    anomalies.extend(store_anoms)
    anomalies.extend(sales_anoms)

    # ---------- STEP 03 广告 ----------
    states = {"enabled": 0, "paused": 0, "archived": 0, "unknown": 0}
    for c in campaigns:
        st = str(c.get("state") or "").lower()
        states[st if st in states else "unknown"] += 1
    # 广告花费/销售额/ACOS（adCost/adSalePrice）与高花费零转化（adOrderNum）为宽表字段
    # → 固定需人工核查（由 custom-report-analysis 承接）；本步仅保留活动结构状态。
    ad_zero: List[Dict[str, Any]] = []
    analysis["steps"]["03_ads"] = {
        "ad_cost": None, "ad_sales": None, "acos": None,
        "campaign_states": states,
        "acos_anomaly": None,
        "high_cost_zero_order": ad_zero[:10],
        "note": ("广告花费/销售额/ACOS 为宽表字段（由 custom-report-analysis 技能承接）— ⚠️ 需人工核查；"
                 "servingStatus 字段存在，但 BUDGET_CAPPED 取值未经实测验证，不作为判定条件。"),
    }

    # ---------- STEP 04 库存 ----------
    # 可售天数（availableDays）/库龄分桶/补货建议（suggestNumSum）为宽表字段 → 固定需人工核查
    analysis["steps"]["04_inventory"] = {
        "warnings": "断货预警（availableDays/库龄分桶/补货建议）为宽表字段——由 custom-report-analysis 技能承接，本节固定标「⚠️ 需人工核查」。",
        "warn_count": 0,
        "critical_count": 0,
    }

    # ---------- STEP 05 转化 ----------
    analysis["steps"]["05_conversion"] = {
        "sessions": sessions, "orders": orders_cnt, "cvr": cvr,
        "buybox_below": [],
        "note": "sessions/conversionRate/buyBoxPercent 为宽表字段（由 custom-report-analysis 技能承接）— ⚠️ 需人工核查；"
                "本步在无宽表数据时不可判定。02 看结果（销售额/订单），05 看过程（流量/转化/购物车）。",
    }

    # ---------- STEP 06 售后 ----------
    # 退款率/差评数（refundRate/lowReviewNum）为宽表字段 → 固定需人工核查
    analysis["steps"]["06_after_sales"] = {
        "refund_rate": None, "refund_num": None, "return_num": None,
        "low_review_num": None,
        "manual": ["评论原文 — MCP 仅有聚合指标（好评率 / 差评数），无评论内容",
                   "退款率/差评数（refundRate/lowReviewNum）— 宽表字段，由 custom-report-analysis 技能承接"],
    }

    # ---------- STEP 07 政策（完全无法自动化）----------
    analysis["steps"]["07_policy"] = {
        "status": HUMAN,
        "manual": ["政策合规 — MCP 无此接口", "绩效风险 — MCP 无此接口"],
    }

    # ---------- 跨平台销量（可选步骤 5；多币种不跨币种合计）----------
    analysis["multiplatform"] = _multiplatform_summary(data)

    # ---------- 今日红线（按 |偏离度| 排序，≤5）----------
    # 库存紧急项属宽表字段（availableDays）→ 已移交 custom-report-analysis，不再进红线
    max_items = int((th.get("redline") or {}).get("max_items", 5))
    positives = [a for a in anomalies if a.get("deviation_pct") is not None]
    positives.sort(key=lambda x: abs(x["deviation_pct"]), reverse=True)
    redlines = positives[:max_items]
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