#!/usr/bin/env python3
"""计算面：校验落盘 → 组装 → 桥接与自检 → out/analysis.json（零网络）。

前置：agent 已按 references/workflow.md 取数计划落盘 out/raw/，并写好
out/raw/analysis_plan.json（口径/窗口/粒度/成本层级，形如：

    {"caliber": "settlement", "granularity": "msku", "cost_layer": "batch",
     "current": {"start": "20261007", "end": "20261020"},
     "base":    {"start": "20260923", "end": "20261006"}}

用法：
    python3 scripts/run_analysis.py [--raw-dir out/raw] [--out out]
    python3 scripts/run_analysis.py --selfcheck        # 内置合成断言（含真实信封形态）

退出码：0 = 已计算（含警告）｜1 = plan/配置错误｜2 = 无可用数据（聚合信封缺失或 failed）。

线上形态兼容（2026-10-16 评测修正）：
- bizDay/bizDate 可能是 int → 全链路日期键 _day_key() 一律归一为 str；
- 成本费用符号以实测为准（线上曾见正号存储）→ 贷项日按「主符号方向」判定，不假设成本恒负；
- 产品分析行是 asinList/mskuList + fieldsMap 嵌套形态（扁平形态也兼容）；
- 聚合行按业务键（店铺/MSKU/日期）去重，剔除数披露「上游重复行 N→M」。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import SKILL_ROOT, load_analysis_config  # noqa: E402
from sellfox_offline import (  # noqa: E402
    RawEnvelope, detect_demo_environment, is_no_data, load_envelope,
)

CFG = load_analysis_config()
BRIDGE: List[str] = list(CFG.get("bridge_fields") or [
    "orderIncome", "orderExpend", "serviceFeeExpend", "refund",
    "purchasePcFeeBatch", "headTripPcFeeBatch", "cpcCost"])
SINGLE_ITEMS = {  # 单项已含于组合字段：混进同一张 Δ 表即双计（自检一）
    "commission", "fbaFulfillmentFee", "fbaWeightBasedFee", "storageFee",
    "storageRenewalBilling", "refundSales", "refundManagementFee",
    "purchasePcFee", "headTripPcFee", "cpcSettlementCost", "cpcAdExpensesCost",
}
TOLERANCE = float(CFG.get("degradation", {}).get("residual_tolerance", 0.01))
# 日序列检查的贷项字段族（费用/成本类；符号方向按主符号判定，见 attribution-rules.md）
CREDIT_FIELDS = ["cpcCost", "serviceFeeExpend", "orderExpend",
                 "purchasePcFeeBatch", "headTripPcFeeBatch"]
ZERO_RUN_MIN_DAYS = 3          # 停投断点：连续 ≥3 天（按去重日期数）费用为 0
TAIL_DAYS = 3                  # 结算滞后：看尾部 3 天
AD_ORDER_FIELDS = ("adSpOrderNum", "cpcSalesNum")  # 停投对照的广告单量字段链


def num(v: Any) -> Optional[float]:
    """金额与率值字符串/JSON number 都可能（线上两种都实测过）；null/省略/解析失败 → None，绝不当 0。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ssum(vals: List[Optional[float]]) -> float:
    return sum(x for x in vals if x is not None)


def ssum_or_none(vals: List[Optional[float]]) -> Optional[float]:
    """全缺 → None（「无数据」≠ 0，N11）：桥接字段一行都没有时不许渲染成 0.00。"""
    xs = [x for x in vals if x is not None]
    return sum(xs) if xs else None


def _day_key(r: Dict[str, Any]) -> str:
    """日期键归一为 str：线上 bizDay 实测为 int（月粒度 bizDay=0 → 用 bizDate）。"""
    v = r.get("bizDay")
    if v in (None, "", 0, "0"):
        v = r.get("bizDate")
    return str(v) if v not in (None, "") else ""


def _row_business_key(r: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    """聚合行业务键：shopInfos[].shopId + mskuList + bizDay（取可得字段）。

    店铺与 MSKU 都拿不到 → 返回 None（不去重，避免误删不同业务的行）。
    """
    shp = r.get("shopInfos")
    sid = shp[0].get("shopId") if (isinstance(shp, list) and shp
                                   and isinstance(shp[0], dict)) else None
    msku = r.get("mskuList") if isinstance(r.get("mskuList"), list) else None
    if sid is None and not msku:
        return None
    return (sid, tuple(msku or ()), _day_key(r))


def dedup_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """上游重复行去重（同业务键保留首行）；返回 (去重后行, {'before': N, 'after': M})。"""
    seen, out = set(), []
    for r in rows:
        k = _row_business_key(r)
        if k is None or k not in seen:
            if k is not None:
                seen.add(k)
            out.append(r)
    return out, {"before": len(rows), "after": len(out)}


def summarize_rows(rows: List[Dict[str, Any]],
                   signs: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """窗口聚合：桥接（按主符号归一为对毛利贡献方向）+ 整体率（自算）+ 零销售行剔除。"""
    live = [r for r in rows if num(r.get("productSales"))]
    gp = ssum([num(r.get("grossProfit")) for r in rows])
    sale = ssum([num(r.get("productSales")) for r in live])
    raw = {k: ssum_or_none([num(r.get(k)) for r in rows]) for k in BRIDGE}
    signs = signs or {}
    bridge: Dict[str, Optional[float]] = {}
    for k, v in raw.items():
        if v is None:
            bridge[k] = None
        elif k == "orderIncome" or signs.get(k, -1) <= 0:
            bridge[k] = v          # 收入项 / 负号存储：代数和即贡献
        else:
            bridge[k] = -v         # 正号存储的成本项 → 对毛利贡献为负（F1 归一）
    bridge_total = ssum([v for v in bridge.values() if v is not None]) if any(
        v is not None for v in bridge.values()) else None
    return {
        "rows": len(rows),
        "gp": gp,
        "sale": sale,
        "margin": (gp / sale) if sale else None,   # 分母 0 → None，不写 0%
        "bridge": bridge,
        "bridge_total": bridge_total,
        "residual": (None if bridge_total is None else gp - bridge_total),
        "skipped_zero_sales": len(rows) - len(live),
    }


def _collect_envelopes(raw_dir: Path, pattern: str) -> List[RawEnvelope]:
    out: List[RawEnvelope] = []
    for p in sorted(raw_dir.glob(pattern)):
        env = load_envelope(p)
        if env is not None:
            out.append(env)
    return out


def _usable_rows(envs: List[RawEnvelope], list_key: Optional[str] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for env in envs:
        if env.status == "failed":
            continue  # failed 不进计算，错误在 coverage 区披露
        if list_key:
            got = env.rows(list_key) or env.rows()
        else:
            got = env.rows()
        rows.extend([r for r in got if isinstance(r, dict)])
    return rows


def _env_notes(envs: List[RawEnvelope], label: str) -> List[Dict[str, Any]]:
    notes = []
    for env in envs:
        note: Dict[str, Any] = {
            "step": label, "file": env.path.name, "status": env.status,
            "rows": len(env.rows()), "warnings": env.warnings[:5],
        }
        if env.error_code:
            note["error_code"] = env.error_code
        if env.status == "failed":
            note["message"] = env.message
        cov, pag = env.coverage or {}, env.pagination or {}
        complete, has_more = cov.get("complete"), pag.get("has_more")
        if complete is False and has_more is False:
            # 实测矛盾：coverage.complete=false ∧ has_more=false 同时出现 → 以 totalPage 为准（workflow.md §四）
            note["coverage_conflict"] = (
                f"coverage.complete=false 与 has_more=false 矛盾；按 data.totalPage="
                f"{(env.obj.get('data') or {}).get('totalPage')} 判定，口径行须披露")
        notes.append(note)
    return notes


def _currency_desc(rows: List[Dict[str, Any]], envs: List[RawEnvelope] = ()) -> str:
    """币种口径行文案：从两期行的 currency 集合生成（单币种/多币种分开未折算/未知）。"""
    cs = {r.get("currency") for r in rows
          if isinstance(r, dict) and isinstance(r.get("currency"), str) and r.get("currency")}
    if not cs:  # 行里没有时退回信封级 currency（两期行都空才走得到）
        cs = {e.obj.get("currency") for e in envs
              if isinstance(e.obj.get("currency"), str)}
        cs.discard(None)
    if not cs:
        return "未知"
    if len(cs) == 1:
        return sorted(cs)[0]
    return "/".join(sorted(cs)) + " 分开未折算"


def _dominant_signs(rows: List[Dict[str, Any]],
                    fields: Optional[List[str]] = None) -> Dict[str, int]:
    """每个字段在给定行上的主符号（中位数符号；+1/-1/0=不可判）。

    符号约定以实测为准（线上曾见成本正号存储）：不假设成本恒负。
    用中位数而非求和：单日大额冲回不会把整列判向翻反。
    """
    signs: Dict[str, int] = {}
    for f in (fields or CREDIT_FIELDS):
        vals = [v for v in (num(r.get(f)) for r in rows)
                if v is not None and v != 0]   # 0（停投/无费用日）不携带符号信息
        if not vals:
            signs[f] = 0
            continue
        med = statistics.median(vals)
        signs[f] = 1 if med > 0 else (-1 if med < 0 else 0)
    return signs


def _credit_rows(rows: List[Dict[str, Any]],
                 signs: Dict[str, int]) -> List[Dict[str, Any]]:
    """贷项日/异常日：字段取值符号与主符号相反且非零的那天（正号存储下负值日 = 冲回）。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        for f in CREDIT_FIELDS:
            s = signs.get(f) or 0
            if s == 0:
                continue  # 主符号不可判（全缺或正负相抵）→ 不判贷项
            v = num(r.get(f))
            if v is not None and v != 0 and ((v > 0) != (s > 0)):
                out.append({"day": _day_key(r), "field": f, "value": v})
    return out


def daily_checks(daily_envs: List[RawEnvelope], agg_sum: Dict[str, Any],
                 base_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """日序列四查：单日贷项 / 停投断点 / 集中入账 / 结算滞后 + 分段交叉校验。"""
    days: List[Dict[str, Any]] = []
    for env in daily_envs:
        if env.status == "failed":
            continue
        for r in env.rows():
            if isinstance(r, dict):
                days.append(r)
    days.sort(key=_day_key)
    # 按去重日期聚合（同一天多 MSKU 行：费用合计为 0 且广告单量为 0 才算停投天；天数按日期数计）
    day_map: Dict[str, List[Dict[str, Any]]] = {}
    for r in days:
        day_map.setdefault(_day_key(r), []).append(r)
    ordered_days = sorted(day_map)
    checks: Dict[str, Any] = {"days": len(days), "distinct_days": len(ordered_days)}

    # ① 单日贷项：费用字段取值符号与主符号（两期全部行求和）相反且非零的那天
    signs = _dominant_signs(days + list(base_rows or []))
    checks["credit_signs"] = signs
    checks["credit_days"] = _credit_rows(days, signs)

    # ② 停投断点：某项费用连续 ≥N 天（去重日期数）合计为 0 且广告单量同步归零
    has_ad_orders = any(num(r.get(f)) is not None
                        for r in days for f in AD_ORDER_FIELDS)
    if not has_ad_orders:
        checks["zero_run_stalls"] = []
        checks["stall_check_note"] = (
            "单量字段缺失（" + "/".join(AD_ORDER_FIELDS) + " 全缺），停投断点不可判")
    else:
        stalls: List[Dict[str, Any]] = []
        for f in ("cpcCost",):
            run_start, run_len = None, 0
            for d in ordered_days:
                rs = day_map[d]
                vals = [num(r.get(f)) for r in rs]
                vsum = ssum(vals)
                present = any(v is not None for v in vals)
                ad_orders = ssum([num(r.get(g)) for r in rs for g in AD_ORDER_FIELDS])
                zero = present and vsum == 0 and ad_orders == 0
                if zero:
                    run_len += 1
                    run_start = run_start or d
                else:
                    if run_len >= ZERO_RUN_MIN_DAYS:
                        stalls.append({"field": f, "from": run_start, "days": run_len})
                    run_start, run_len = None, 0
            if run_len >= ZERO_RUN_MIN_DAYS:
                stalls.append({"field": f, "from": run_start, "days": run_len})
        checks["zero_run_stalls"] = stalls

    # ③ 费用集中入账：赔偿/清算族只落在少数天
    event_fields = ["compensationFee", "recoveryAmount", "guaranteeClaim",
                    "reimbursedAmount", "liquidation"]
    concentration = []
    for f in event_fields:
        hit_days = sorted({_day_key(r) for r in days
                           if (num(r.get(f)) or 0) != 0})
        if 0 < len(hit_days) <= 3:
            concentration.append({"field": f, "days": hit_days})
    checks["concentrated_fees"] = concentration

    # ④ 结算滞后：尾部日均 vs 前段
    sales = [num(r.get("productSales")) or 0 for r in days]
    if len(sales) >= TAIL_DAYS * 2:
        tail_avg = sum(sales[-TAIL_DAYS:]) / TAIL_DAYS
        body_avg = sum(sales[:-TAIL_DAYS:]) / (len(sales) - TAIL_DAYS)
        checks["settlement_lag"] = {
            "tail_avg": round(tail_avg, 2), "body_avg": round(body_avg, 2),
            "droop": tail_avg < body_avg * 0.7,
        }

    # 交叉校验：日分段各项加总 == 聚合行同字段（容差；聚合侧无数据不比对）
    cross = []
    for f in BRIDGE:
        daily_sum = ssum([num(r.get(f)) for r in days])
        agg_v = agg_sum["bridge"].get(f)
        if agg_v is not None and abs(daily_sum - agg_v) > TOLERANCE:
            cross.append({"field": f, "daily_sum": daily_sum, "agg": agg_v})
    checks["cross_check_mismatch"] = cross

    # 贷项剔除后 Δ：基期剔除贷项日后重算桥接（账面 Δ / 剔后 Δ 两个都报）
    checks["base_ex_credit_bridge"] = None  # 由主流程按窗口填
    return checks


# ---------------------------------------------------------------- 榜提取（N6）
def _rank_extract(r: Dict[str, Any]) -> Dict[str, Any]:
    """兼容两种 analyze 行形态：
    - 扁平：asin/msku/grossProfit/productSales 顶层键；
    - 嵌套（真实 get_product_analyze_list 形态）：asinList/mskuList 列表 + fieldsMap
      指标字典（利润列常量是 profitPrice，销量 saleNum）。
    """
    fm = r.get("fieldsMap") if isinstance(r.get("fieldsMap"), dict) else {}

    def ident(*names: str) -> Optional[str]:
        for n in names:
            v = r.get(n)
            if isinstance(v, list) and v and isinstance(v[0], str):
                return v[0]
            if isinstance(v, str) and v:
                return v
        return None

    def pick(*names: str) -> Any:
        for n in names:
            if fm.get(n) is not None:
                return fm.get(n)
            if r.get(n) is not None:
                return r.get(n)
        return None

    return {
        "asin": ident("asinList", "asin"),
        "msku": ident("mskuList", "msku"),
        "shopInfos": r.get("shopInfos"),
        "grossProfit": pick("grossProfit", "profitPrice"),
        "grossProfitRate": pick("grossProfitRate", "profitRate"),
        "salesNum": pick("salesNum", "saleNum"),
        "productSales": pick("productSales"),
    }


def _rank_dedup(ext: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """榜行跨信封/跨源按商品标识（asin，缺则 msku）去重（G1）。

    desc/asc 双发回落源叠加 + 上游排序被忽略（两信封行集相同）时，同一标识会出现两次；
    同标识保留 |grossProfit| 更大的一条（并列保留首条）；无标识行不冒险去重，原样保留。
    返回 (去重后行, {'before': N, 'after': M})。
    """
    out: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for x in ext:
        ident = x.get("asin") or x.get("msku")
        key = str(ident) if isinstance(ident, str) and ident.strip() else None
        if key is None:
            out.append(x)                      # 无标识：不去重
        elif key not in seen:
            seen[key] = len(out)
            out.append(x)
        elif abs(num(x.get("grossProfit")) or 0) > abs(num(out[seen[key]].get("grossProfit")) or 0):
            out[seen[key]] = x                 # 同标识保留 |grossProfit| 更大者（并列保留首条）
    return out, {"before": len(ext), "after": len(out)}


def build_analysis(raw_dir: Path, plan: Dict[str, Any]) -> Dict[str, Any]:
    agg_cur = _collect_envelopes(raw_dir, "agg_current*.json")
    agg_base = _collect_envelopes(raw_dir, "agg_base*.json")
    if not agg_cur and not agg_base:
        raise SystemExit(2)

    notes = (_env_notes(agg_cur, "agg_current") + _env_notes(agg_base, "agg_base"))
    rows_cur, rows_base = _usable_rows(agg_cur), _usable_rows(agg_base)
    # 上游重复行去重（同业务键只计一次），剔除数进 periods[].dedup 披露
    rows_cur, dedup_cur = dedup_rows(rows_cur)
    rows_base, dedup_base = dedup_rows(rows_base)
    demo_fp = None
    for envs in (agg_cur, agg_base,
                 _collect_envelopes(raw_dir, "daily_*.json"),
                 _collect_envelopes(raw_dir, "daily_*/*.json"),
                 _collect_envelopes(raw_dir, "rank_*.json"),
                 _collect_envelopes(raw_dir, "rank_*/*.json"),
                 _collect_envelopes(raw_dir, "order_*.json"),   # 订单侧指纹也触发水印
                 _collect_envelopes(raw_dir, "order_*/*.json"),
                 _collect_envelopes(raw_dir, "shops.json")):
        for env in envs:
            demo_fp = demo_fp or detect_demo_environment(env.obj)

    bridge_signs = _dominant_signs(rows_cur + rows_base, fields=BRIDGE)
    sum_cur = summarize_rows(rows_cur, bridge_signs)
    sum_base = summarize_rows(rows_base, bridge_signs)
    sum_cur["dedup"], sum_base["dedup"] = dedup_cur, dedup_base
    result: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": {
            "skill": "sales-profit-analysis",
            "caliber": plan.get("caliber"),
            "granularity": plan.get("granularity"),
            "cost_layer": plan.get("cost_layer"),
            "current": plan.get("current"), "base": plan.get("base"),
        },
        "demo_fingerprint": demo_fp,
        "coverage": notes,
        "currencies": _currency_desc(rows_cur + rows_base, agg_cur + agg_base),
        "bridge_signs": bridge_signs,
        "bridge_sign_note": (
            "正号存储：Δ 表费用项已按主符号归一为对毛利贡献方向（利好为正）"
            if any(s > 0 for s in bridge_signs.values()) else None),
        "no_data": {"current": not rows_cur and bool(agg_cur),
                    "base": not rows_base and bool(agg_base)},
        "periods": {"current": sum_cur, "base": sum_base},
    }

    # 按店铺分组（granularity=shop 或行带店铺键时给结构段；F2）
    def _shop_key(r: Dict[str, Any]) -> Optional[str]:
        infos = r.get("shopInfos")
        if isinstance(infos, list) and infos and isinstance(infos[0], dict):
            sid = infos[0].get("shopId") or infos[0].get("shopName")
            if sid:
                return str(sid)
        return str(r["shopId"]) if r.get("shopId") else None

    shop_groups: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for label, rows_p in (("current", rows_cur), ("base", rows_base)):
        for r in rows_p:
            k = _shop_key(r)
            if k:
                shop_groups.setdefault(k, {}).setdefault(label, []).append(r)
    if shop_groups and (plan.get("granularity") == "shop" or len(shop_groups) > 1):
        result["by_shop"] = {
            k: {p: summarize_rows(g.get(p, []), bridge_signs) for p in ("current", "base")}
            for k, g in shop_groups.items()
        }

    # 桥接 Δ 表（只用组合字段 = 自检一：同层不双计；缺侧 → None=无数据，不写 0）
    delta: Dict[str, Optional[float]] = {}
    for f in BRIDGE:
        c, b = sum_cur["bridge"].get(f), sum_base["bridge"].get(f)
        delta[f] = None if (c is None and b is None) else (c or 0.0) - (b or 0.0)
    result["delta"] = {"bridge": delta,
                       "gp": sum_cur["gp"] - sum_base["gp"],
                       "margin_pp": (None if (sum_cur["margin"] is None or sum_base["margin"] is None)
                                     else (sum_cur["margin"] - sum_base["margin"]) * 100)}

    # 日序列（变化归因类默认做）；两期 daily 信封（含 failed）都进 coverage（N4）
    daily_envs = (_collect_envelopes(raw_dir, "daily_current/*.json")
                  + _collect_envelopes(raw_dir, "daily_current*.json"))
    base_envs = (_collect_envelopes(raw_dir, "daily_base/*.json")
                 + _collect_envelopes(raw_dir, "daily_base*.json"))
    if daily_envs:
        notes.extend(_env_notes(daily_envs, "daily_current"))
    if base_envs:
        notes.extend(_env_notes(base_envs, "daily_base"))
    if daily_envs and rows_cur:
        base_rows = _usable_rows(base_envs)
        dc = daily_checks(daily_envs, sum_cur, base_rows=base_rows)
        # 基期剔除贷项后的 Δ（账面 Δ / 剔后 Δ 都报）；贷项日 = 与主符号相反且非零
        credit_days_cur = {c["day"] for c in dc["credit_days"]}
        if base_rows:
            base_credit_days = {c["day"] for c in _credit_rows(base_rows, dc["credit_signs"])}
            kept = [r for r in base_rows if _day_key(r) not in base_credit_days]
            sum_base_ex = summarize_rows(kept)
            dc["base_ex_credit_bridge"] = {
                "excluded_days": sorted(base_credit_days),
                "base_gp_ex_credit": sum_base_ex["gp"],
                "delta_gp_ex_credit": sum_cur["gp"] - sum_base_ex["gp"],
            }
            dc.setdefault("credit_days_union", sorted(credit_days_cur | base_credit_days))
        result["daily_checks"] = dc

    # 榜（产品分析，只用于排序方向；哨兵行剔除；嵌套/扁平两形态兼容）
    # 全部 rank_* 信封按文件名是否含 base 分桶——降级重试的 rank_profit_* 等回落源也进榜（F3）
    _seen_paths = set()
    rank_all: List[RawEnvelope] = []
    for env in (_collect_envelopes(raw_dir, "rank_*.json")
                + _collect_envelopes(raw_dir, "rank_*/*.json")):
        if env.path not in _seen_paths:
            _seen_paths.add(env.path)
            rank_all.append(env)
    rank: Dict[str, Any] = {}
    for period in ("current", "base"):
        envs = [e for e in rank_all if ("base" in e.path.name) == (period == "base")]
        if envs:  # rank 信封（含 failed）始终进 coverage（N5）
            notes.extend(_env_notes(envs, f"rank_{period}"))
        rows = _usable_rows(envs, list_key="rows")  # 兼容 rows 与 reportList 两种 list_key
        ext = [_rank_extract(r) for r in rows]
        ext, rank_dedup = _rank_dedup(ext)          # 跨信封按商品标识去重（G1）
        live = [x for x in ext
                if (num(x.get("productSales")) or 0) != 0 or (num(x.get("salesNum")) or 0) != 0]
        by_profit = sorted(live, key=lambda x: num(x.get("grossProfit")) or 0)
        pos = [x for x in by_profit if (num(x.get("grossProfit")) or 0) > 0]
        neg = [x for x in by_profit if (num(x.get("grossProfit")) or 0) < 0]
        note = "与站点层不同基，仅排序；赚钱榜只收正值、亏损榜只收负值"
        if rank_dedup["before"] > rank_dedup["after"]:
            note += (f"；已按商品标识跨源去重 {rank_dedup['before']}→{rank_dedup['after']}"
                     "（desc/asc 双信封与回落源叠加时同标识只计一次，保留 |grossProfit| 更大者）")
        rank[period] = {
            "top": pos[-10:][::-1],
            "bottom": neg[:10],
            "skipped_zero_sales": len(rows) - len(live),
            "dedup": rank_dedup,
            "note": note,
        }
    if rank_all:
        result["rankings"] = rank

    result["selfchecks"] = _selfchecks(result)
    return result


def _selfchecks(result: Dict[str, Any]) -> Dict[str, Any]:
    """四自检结果（真值为过，问题进 warnings，不凑系数）。"""
    sc: Dict[str, Any] = {}
    sc["bridge_fields_only"] = not any(
        f in SINGLE_ITEMS for f in result["delta"]["bridge"])       # ① 同层不双计
    sc["residual"] = {                                           # ② 残差如实报
        "current": result["periods"]["current"]["residual"],
        "base": result["periods"]["base"]["residual"],
        "note": "契约未给官方加总公式；禁止调系数凑平；桥接字段全缺 → null=无数据（非 0）"}
    sc["sentinel_excluded"] = {                                  # ③ 哨兵行剔除
        "current": result["periods"]["current"]["skipped_zero_sales"],
        "base": result["periods"]["base"]["skipped_zero_sales"]}
    sc["margin_self_computed"] = True                            # ④ 整体率 = Σ分子÷Σ分母
    return sc


# ------------------------------------------------------------------ selfcheck
def _write_env(path: Path, rows: List[Dict[str, Any]], list_key: str = "reportList",
               status: str = "complete") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": status, "data": {list_key: rows},
        "coverage": {"complete": True, "truncated": False},
        "warnings": [], "trace_id": "selfcheck"}, ensure_ascii=False), encoding="utf-8")


def _selfcheck_a() -> None:
    """场景 A：负号存储（哨兵剔除/桥接残差/停投断点/贷项剔后Δ/双计拒绝/率自算）。"""
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td)
        rows_base = [
            {"bizDay": "20261001", "productSales": "1000", "grossProfit": "200",
             "orderIncome": "1000", "orderExpend": "-300", "serviceFeeExpend": "-100",
             "refund": "-50", "purchasePcFeeBatch": "-200", "headTripPcFeeBatch": "-80",
             "cpcCost": "-70", "salesNum": "10"},
            # 基期单日贷项：cpcCost 与主符号（负）相反（冲回）→ 剔除后 Δ 必须重算
            {"bizDay": "20261003", "productSales": "1000", "grossProfit": "330",
             "orderIncome": "1000", "orderExpend": "-300", "serviceFeeExpend": "-100",
             "refund": "-50", "purchasePcFeeBatch": "-200", "headTripPcFeeBatch": "-80",
             "cpcCost": "60", "salesNum": "10"},
            # 零销售哨兵行：率值字段是哨兵，必须剔除
            {"bizDay": "20261004", "productSales": "0", "grossProfit": "-10",
             "grossProfitRate": "0", "roi": "-1", "profitRate": "0E-7",
             "orderIncome": "0", "orderExpend": "-5", "serviceFeeExpend": "-5",
             "refund": "0", "purchasePcFeeBatch": "0", "headTripPcFeeBatch": "0",
             "cpcCost": "0", "salesNum": "0"},
        ]
        rows_cur = [
            {"bizDay": "20261015", "productSales": "1100", "grossProfit": "150",
             "orderIncome": "1100", "orderExpend": "-350", "serviceFeeExpend": "-120",
             "refund": "-60", "purchasePcFeeBatch": "-220", "headTripPcFeeBatch": "-80",
             "cpcCost": "-120", "salesNum": "11"},
        ]
        _write_env(raw / "agg_current.json", rows_cur)
        _write_env(raw / "agg_base.json", rows_base)
        # 日序列（两期都要）：本期 cpcCost 连续 3 天为 0 且广告单归零 → 停投断点（天数为日期数）；
        # 基期 20261003 是贷项日（cpcCost 与主符号相反）→ 剔除后 Δ 必须与账面 Δ 不同
        daily = [{"bizDay": f"2026101{d}", "productSales": "50", "grossProfit": "5",
                  "cpcCost": "0" if d >= 2 else "-10",
                  "adSpOrderNum": "0" if d >= 2 else "1",
                  "orderIncome": "50", "orderExpend": "-30", "serviceFeeExpend": "-10",
                  "refund": "0", "purchasePcFeeBatch": "-5", "headTripPcFeeBatch": "0"}
                 for d in range(1, 5)]
        _write_env(raw / "daily_current" / "seg1.json", daily)
        _write_env(raw / "daily_base" / "seg1.json", rows_base)
        plan = {"caliber": "settlement", "granularity": "msku", "cost_layer": "batch",
                "current": {"start": "20261015", "end": "20261015"},
                "base": {"start": "20261001", "end": "20261004"}}
        result = build_analysis(raw, plan)

        assert result["periods"]["base"]["skipped_zero_sales"] == 1, "哨兵行未剔除"
        assert result["periods"]["base"]["sale"] == 2000.0, "零销售行进分母"
        b = result["periods"]["base"]
        assert abs(b["residual"]) < TOLERANCE, f"桥接残差异常 {b['residual']}"
        assert result["selfchecks"]["bridge_fields_only"], "Δ 表混入单项（双计）"
        dc = result["daily_checks"]
        assert dc["zero_run_stalls"], "停投断点未检出"
        assert dc["zero_run_stalls"][0]["days"] == 3, "停投天数应按去重日期数计"
        assert dc["credit_signs"]["cpcCost"] == -1, "主符号判定失败（负号存储）"
        assert dc["credit_days"] == [], f"负号存储下本期正常费用日被误判贷项：{dc['credit_days']}"
        ex = dc["base_ex_credit_bridge"]
        assert ex and ex["excluded_days"] == ["20261003"], \
            f"基期贷项日（与主符号相反的正值日）未检出：{ex}"
        assert ex and ex["delta_gp_ex_credit"] != result["delta"]["gp"], "贷项剔除后 Δ 未变化"
        assert dc["credit_days_union"] == ["20261003"], "贷项并集日应为 str 并可排序"
        assert abs(result["periods"]["current"]["margin"] - 150 / 1100) < 1e-9, "整体率非自算"


def _selfcheck_b() -> None:
    """场景 B：正号存储 + int bizDay（线上真实信封形态）。

    成本费用全为正值（线上实测形态）→ 主符号=+；单日 cpcCost 负值 = 真实冲回，
    必须检出并进剔后 Δ；int bizDay 全链路不崩、日期键一律 str。
    """
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td)
        normal = {"productSales": "1000", "salesNum": "10", "currency": "USD",
                  "orderIncome": "1000", "orderExpend": "300", "serviceFeeExpend": "100",
                  "refund": "50", "purchasePcFeeBatch": "200", "headTripPcFeeBatch": "80",
                  "cpcCost": "120", "grossProfit": "150"}
        credit = dict(normal, bizDay=20261008, cpcCost=-273.6, grossProfit="543.6")
        rows_cur = [dict(normal, bizDay=20261015)]
        rows_base = [dict(normal, bizDay=20261001), dict(normal, bizDay=20261003), credit]
        _write_env(raw / "agg_current.json", rows_cur)
        _write_env(raw / "agg_base.json", rows_base)
        _write_env(raw / "daily_current" / "seg1.json", rows_cur)   # bizDay 是 int（线上形态）
        _write_env(raw / "daily_base" / "seg1.json", rows_base)
        plan = {"caliber": "settlement", "granularity": "msku", "cost_layer": "batch",
                "current": {"start": "20261015", "end": "20261015"},
                "base": {"start": "20261001", "end": "20261008"}}
        result = build_analysis(raw, plan)  # int bizDay 不再 TypeError（N1）

        dc = result["daily_checks"]
        assert dc["credit_signs"]["cpcCost"] == 1, "正号存储主符号应为 +"
        assert dc["credit_days"] == [], f"正号存储下正常费用日被误判贷项（N2 假阳性）：{dc['credit_days']}"
        ex = dc["base_ex_credit_bridge"]
        assert ex and ex["excluded_days"] == ["20261008"], \
            f"正号存储下真实冲回（负值日）未检出：{ex}"
        assert all(isinstance(d, str) for d in ex["excluded_days"]), "日期键未归一为 str"
        assert ex["base_gp_ex_credit"] == 300.0, f"剔后基期毛利应为两正常日之和：{ex}"
        assert ex["delta_gp_ex_credit"] == 150.0 - 300.0, "剔后 Δ 计算错误"
        assert dc["credit_days_union"] == ["20261008"], "int 日期混排应归一为 str 后排序"
        assert result["currencies"] == "USD", "币种应从行 currency 集合生成"
        bs = result["bridge_signs"]
        assert bs["orderExpend"] == 1 and bs["cpcCost"] == 1, "正号存储桥接主符号应为 +（中位数判定）"
        assert abs(result["periods"]["base"]["residual"]) < 1e-6, \
            f"正号存储桥接未按主符号归一，残差 {result['periods']['base']['residual']}"
        assert abs(result["periods"]["current"]["residual"]) < 1e-6, "正号存储本期桥接未归一（F1）"
        assert result["bridge_sign_note"], "正号归一未披露"


def _selfcheck_c() -> None:
    """场景 C：嵌套 analyze 行（asinList/mskuList + fieldsMap）与扁平形态双兼容（N6）。"""
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td)
        _write_env(raw / "agg_current.json", [
            {"bizDay": "20261015", "productSales": "100", "grossProfit": "30",
             "orderIncome": "100", "orderExpend": "50", "serviceFeeExpend": "10",
             "refund": "0", "purchasePcFeeBatch": "5", "headTripPcFeeBatch": "5",
             "cpcCost": "0"}])
        nested = [
            {"asinList": ["B0TOP9"], "mskuList": ["M-TOP-9"],
             "fieldsMap": {"profitPrice": "812.45", "saleNum": "300"}},
            {"asinList": ["B0LOSS1"], "mskuList": ["M-LOSS-1"],
             "fieldsMap": {"profitPrice": "-273.6", "saleNum": "90"}},
            {"asinList": ["B0LOSS2"], "mskuList": ["M-LOSS-2"],
             "fieldsMap": {"profitPrice": "-88.1", "saleNum": "40"}},
            {"asinList": ["B0TOP1"], "mskuList": ["M-TOP-1"],
             "fieldsMap": {"profitPrice": "455.2", "saleNum": "210"}},
        ]
        _write_env(raw / "rank_current.json", nested, list_key="rows")
        flat = [
            {"asin": "B0FLAT1", "msku": "M-FLAT-1", "productSales": "500",
             "grossProfit": "800", "salesNum": "50"},
            {"asin": "B0FLAT2", "msku": "M-FLAT-2", "productSales": "300",
             "grossProfit": "-350", "salesNum": "9"},
        ]
        _write_env(raw / "rank_base.json", flat, list_key="rows")
        plan = {"caliber": "settlement", "granularity": "msku", "cost_layer": "batch",
                "current": {"start": "20261015", "end": "20261015"},
                "base": {"start": "20261001", "end": "20261007"}}
        result = build_analysis(raw, plan)

        rk = result["rankings"]
        assert rk["current"]["top"], "嵌套形态行被当零销售剔除、榜单消失（N6）"
        assert rk["current"]["top"][0]["asin"] == "B0TOP9", "标识应取 asinList[0]"
        assert float(rk["current"]["top"][0]["grossProfit"]) == 812.45, "金额应取 fieldsMap.profitPrice"
        assert [r["asin"] for r in rk["current"]["bottom"]] == ["B0LOSS1", "B0LOSS2"], \
            "亏损榜应从嵌套 fieldsMap 提取负值"
        assert rk["base"]["top"][0]["asin"] == "B0FLAT1", "扁平形态提取回归"
        assert float(rk["base"]["bottom"][0]["grossProfit"]) == -350.0
        steps = {n["step"] for n in result["coverage"]}
        assert {"rank_current", "rank_base"} <= steps, "rank 信封未始终进 coverage（N5）"


def selfcheck() -> int:
    """内置合成断言：哨兵剔除 / 桥接残差 / 贷项 Δ 重算 / 停投断点 / 双计拒绝 / 率自算
    + 真实信封形态三例（int bizDay、正号存储贷项、嵌套 analyze 行）。"""
    _selfcheck_a()
    _selfcheck_b()
    _selfcheck_c()
    print("✅ selfcheck: 哨兵剔除/桥接残差/停投断点/贷项剔后Δ/双计拒绝/率自算"
          "+ int日期/正号存储贷项/嵌套analyze行 全部通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="out/raw")
    ap.add_argument("--out", default="out")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        return selfcheck()

    raw_dir = SKILL_ROOT / args.raw_dir if not Path(args.raw_dir).is_absolute() else Path(args.raw_dir)
    plan_path = raw_dir / "analysis_plan.json"
    if not plan_path.exists():
        print("❌ 缺少 out/raw/analysis_plan.json（口径/窗口/粒度/成本层级），先补计划再跑", file=sys.stderr)
        return 1
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan.get("caliber"):
        print("❌ 口径未锁（caliber 为空）：先与用户确认 settlement/shipment，别拿默认空串硬跑", file=sys.stderr)
        return 1

    result = build_analysis(raw_dir, plan)
    out_dir = SKILL_ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "analysis.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    usable = bool(result["periods"]["current"]["rows"] or result["periods"]["base"]["rows"])
    print(f"分析完成 → {out}（demo={result['demo_fingerprint']}, "
          f"零销售剔除 cur/base={result['selfchecks']['sentinel_excluded']['current']}/"
          f"{result['selfchecks']['sentinel_excluded']['base']}）")
    return 0 if usable else 2


if __name__ == "__main__":
    raise SystemExit(main())
