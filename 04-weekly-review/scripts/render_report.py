#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""渲染层（周报 / 月报）。

输入 `computed.json` → 输出 Markdown（严格遵循 references/report-template.md 的结构）。
只做渲染，不做取数、不做写入。数据说明区块为**必填**，缺失即视为报告不合格。

可选：`--mermaid` 输出趋势图的 Mermaid 源码（若接了图表 MCP 可替换）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import skill_paths  # noqa: F401  （bootstrap 供后续可能的加载）

skill_paths.bootstrap()  # 挂载 _shared/scripts，供下方 report_html 导入

from report_html import write_html_report  # noqa: E402  （HTML 主报告出口：默认只写同名 .html，HTML 即报告）

NA = "不适用"
NEEDS_REVIEW = "⚠️ 需人工核查"


def _n(v: Optional[float], digits: int = 2, money: bool = False) -> str:
    if v is None:
        return NA
    if money:
        return f"{v:,.{digits}f}"
    return f"{v:,.{digits}f}"


def _pct(v: Optional[float], digits: int = 1) -> str:
    return NA if v is None else f"{v:+.{digits}f}%"


def _grade_icon(grade: str) -> str:
    return {"达标": "🟢", "预警": "🟡", "未达标": "🔴"}.get(grade, "⚪")


def _trend_mermaid(trend: Dict[str, Any]) -> Optional[str]:
    series = trend.get("series")
    if not series:
        return None
    points = [p for p in series if p.get("gmv") is not None]
    if not points:
        return None
    labels = [str(p["day"]) for p in points]
    values = [round(float(p["gmv"]), 2) for p in points]
    def line(name, vals):
        return f'    "{name}" : [' + ", ".join(str(v) for v in vals) + "]"
    return (
        "```mermaid\nxychart-beta\n"
        f'    title "GMV 趋势"\n'
        f"    x-axis [{', '.join(labels)}]\n"
        f"    y-axis \"GMV\"\n"
        f"{line('GMV', values)}\n```"
    )


def render(computed: Dict[str, Any], include_mermaid: bool = True) -> str:
    meta = computed["meta"]
    plan = meta["period"]
    win = plan["windows"]["current"]
    agg = computed["aggregate"]
    cur = agg["current"]
    L: List[str] = []

    title = "月报" if plan["period"] == "month" else "周报"
    period_label = f'{win["start_iso"]} ~ {win["end_iso"]}'
    L.append(f"# 📈 经营{title}｜{period_label}")

    # 演示环境水印（铁律 11）
    if meta.get("mock"):
        L.append("")
        L.append(f"> ⚠️ **演示数据，非真实经营数据**（指纹：{meta.get('demo_fingerprint')}）。"
                 "不可用于任何业务决策。")
    L.append("")

    # ---- 核心结论 ----
    # 本期利润取数 failed/缺失 ≠ 无异常：缺数据时绝不输出「未触发异常项」。
    cur_src = (computed.get("source_status") or {}).get("profit_current") or {}
    cur_failed = cur_src.get("status") in ("error", "degraded")
    cur_code = cur_src.get("error_code") or ""
    L.append("## 核心结论")
    if cur_failed:
        head = (f"本期利润数据取数失败（error_code：{cur_code}）" if cur_code
                else "本期利润数据缺失或取数失败")
        L.append(f"- ⚠️ **{head}**，异常判定不可用——缺数据不能表述为无异常。")
    if computed["anomalies"]:
        for f in computed["anomalies"]:
            icon = {"red": "🔴", "yellow": "🟡", "info": "ℹ️"}.get(f["level"], "•")
            L.append(f"- {icon} **{f['title']}** — {f['detail']}")
    elif not cur_failed:
        L.append("- 本期未触发配置阈值内的异常项。")
    L.append("")

    if cur.get("by_currency"):
        L.extend(["## 返回样本按币种汇总", "", "| 币种 | 销售额 | 毛利 | 销量 |", "|---|---|---:|---:|"])
        for currency, values in cur["by_currency"].items():
            # 金额走 _n（千分位/两位小数），避免裸浮点尾数（15224.099999999999）外露
            L.append(f"| {currency} | {_n(values.get('gmv'))} | "
                     f"{_n(values.get('gross_profit'))} | {_n(values.get('sales_num'))} |")
        L.append("\n请求的聚合币种未在返回行中统一；总金额和金额环比不计算。\n")
    # ---- 目标达成率 ----
    L.append("## 目标达成率")
    ach = computed["achievement"]
    if ach["status"] != "ok":
        L.append(f"> ⚠️ {ach.get('note', '未配置目标值，无法计算达成率')}")
    else:
        L.append(f"口径：{ach['basis']} vs {ach['month_key']} 月目标")
        L.append("")
        L.append("| 指标 | 实际 | 目标 | 达成率 | 评级 |")
        L.append("|---|---:|---:|---:|---|")
        for it in ach["items"]:
            rate = NA if it["rate"] is None else f"{it['rate']:.1f}%"
            L.append(f"| {it['label']} | {_n(it['actual'])} | {_n(it['target'])} | {rate} | "
                     f"{_grade_icon(it['grade'])} {it['grade']} |")
    L.append("")

    # ---- 核心指标与环比/同比 ----
    L.append("## 核心指标")
    L.append("| 指标 | 本期 | 上期 | 环比 | 上月同期 | 去年同期 |")
    L.append("|---|---:|---:|---:|---:|---:|")
    d = computed["deltas"]

    def cur_cell(s: str) -> str:
        """本期列：取数 failed/缺失时，「不适用」须升级为「⚠️ 需人工核查」——
        缺数据是待处理事件，不是口径上的不适用。"""
        return NEEDS_REVIEW if (cur_failed and s == NA) else s

    def row(label, key, is_pct=False, delta_key=None):
        c = cur.get(key)
        p = agg["prev"].get(key)
        pm = agg["prev_month_same_period"].get(key)
        yy = agg["yoy"].get(key)
        def f(v):
            if v is None:
                return NA
            return f"{v*100:.1f}%" if is_pct else _n(v)
        # 环比列取 deltas 键：广告花费的环比口径是 ad_cost_rate（占比）而非绝对额
        delta_pct = d['prev'].get((delta_key or key) + '_pct')
        return (f"| {label} | {cur_cell(f(c))} | {f(p)} | {_pct(delta_pct)} | "
                f"{f(pm)} | {f(yy)} |")
    L.append(row("GMV", "gmv"))
    L.append(row("销量", "sales_num"))
    L.append(row("毛利", "gross_profit"))
    def _rate(v):
        return NA if v is None else f"{v*100:.1f}%"
    def _pp(c, p):
        # 率的环比用百分点（23.0% vs 21.5% → +1.5pp），不用相对百分比（+7.0% 必被误读）
        return NA if (c is None or p is None) else f"{(c - p) * 100:+.1f}pp"
    L.append(f"| 毛利率 | {cur_cell(_rate(cur.get('gross_profit_rate')))} | "
             f"{_rate(agg['prev'].get('gross_profit_rate'))} | "
             f"{_pp(cur.get('gross_profit_rate'), agg['prev'].get('gross_profit_rate'))} | "
             f"{_rate(agg['prev_month_same_period'].get('gross_profit_rate'))} | "
             f"{_rate(agg['yoy'].get('gross_profit_rate'))} |")
    L.append(row("广告花费", "ad_cost", delta_key="ad_cost_rate"))
    def _pctcell(v: Optional[float]) -> str:
        return NA if v is None else f"{v:.1f}%"
    L.append(f"| 广告成本占比 | {cur_cell(_pctcell(cur.get('ad_cost_rate')))} | "
             f"{_pctcell(agg['prev'].get('ad_cost_rate'))} | "
             f"{_pp(cur.get('ad_cost_rate'), agg['prev'].get('ad_cost_rate'))} | "
             f"{_pctcell(agg['prev_month_same_period'].get('ad_cost_rate'))} | "
             f"{_pctcell(agg['yoy'].get('ad_cost_rate'))} |")
    # 退款率是字段小数原值（0.031），须换算成百分比并带单位（3.1%），不能裸显 0.03
    L.append(f"| 退款率 | {cur_cell(_rate(cur.get('refund_rate')))} | "
             f"{_rate(agg['prev'].get('refund_rate'))} | — | "
             f"{_rate(agg['prev_month_same_period'].get('refund_rate'))} | — |")
    L.append(f"| 件均售价（销售额/销量） | {cur_cell(_n(cur.get('avg_order_value')))} | "
             f"{_n(agg['prev'].get('avg_order_value'))} | — | "
             f"{_n(agg['prev_month_same_period'].get('avg_order_value'))} | — |")
    # 流量/广告/退款等 489 字段宽表指标已由 custom-report-analysis 技能承接，本表不再输出。

    L.append("")

    # ---- 趋势 ----
    L.append("## 趋势")
    trend = computed["trend"]
    if trend["status"] != "ok":
        L.append(f"> ⚠️ 趋势图不可用：{trend.get('source', '')}（{trend.get('note', '无数据')}）")
    else:
        L.append(f"来源：{trend['source']}")
        if trend.get("charData"):
            L.append("")
            L.append("数据源已返回 `charData`（可由图表 MCP 直接渲染）。")
        if include_mermaid and trend.get("series"):
            mm = _trend_mermaid(trend)
            if mm:
                L.append("")
                L.append(mm)
    L.append("")

    # ---- 榜单 ----
    rk = computed["rankings"]
    # 标识列字段名未经核实 → 缺失处显示「⚠️ 需人工核查」而非静默的「不适用」。
    ident_na = NA if rk.get("identifier_verified") else NEEDS_REVIEW
    for key, label in (("top", rk["top_label"]), ("bottom", rk["bottom_label"])):
        L.append(f"## {label}")
        rows = rk[key]
        if not rows:
            L.append("> 无数据。")
        else:
            if not rk.get("identifier_verified"):
                L.append("> ⚠️ 标识列（MSKU/ASIN/标题）字段名待核实："
                         "get_profit_report_by_msku 的标识字段名未在 01/06 中确认，"
                         "取不到的行标「需人工核查」。")
            # 标题列全缺时整列隐藏（全「不适用」是噪音）；币种缺失显示 — 而非「未知」
            has_title = any(r.get("title") for r in rows)
            head = "| MSKU | ASIN |" + (" 标题 |" if has_title else "") + " 币种 | GMV | 毛利 | 毛利率 | 广告花费 |"
            sep = "|---|---|" + ("---|" if has_title else "") + "---|---:|---:|---:|---:|"
            L.append(head)
            L.append(sep)
            for r in rows:
                gpr = r.get("gross_profit_rate")
                gpr_s = NA if gpr is None else f"{gpr*100:.1f}%"
                title_cell = (f" {(r.get('title') or ident_na)} |" if has_title else "")
                L.append(f"| {r.get('msku') or ident_na} | {r.get('asin') or ident_na} |"
                         f"{title_cell} {r.get('currency') or '—'} | "
                         f"{_n(r.get('gmv'))} | {_n(r.get('gross_profit'))} | "
                         f"{gpr_s} | {_n(r.get('ad_cost'))} |")
        L.append("")

    # ---- 店铺明细（L2）----
    L.append("## 店铺明细")
    shops = computed.get("per_shop") or []
    if not shops:
        L.append("> 无数据。")
    else:
        L.append("| 店铺（币种） | GMV | 销量 | 毛利 | 毛利率 | 广告成本占比 |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for s in shops:
            gpr = s.get("gross_profit_rate")
            gpr_s = NA if gpr is None else f"{gpr*100:.1f}%"
            acr = s.get("ad_cost_rate")
            acr_s = NA if acr is None else f"{acr:.1f}%"
            L.append(f"| {s['shop']}（{s.get('currency', '未知')}） | {_n(s.get('gmv'))} | {_n(s.get('sales_num'))} | "
                     f"{_n(s.get('gross_profit'))} | {gpr_s} | {acr_s} |")
    L.append("")

    # ---- 数据说明（必填）----
    L.append("---")
    L.append("### 数据说明")
    L.append(f"- 利润口径：{meta['profit_caliber_type']}（"
             f"{'结算口径' if meta['profit_caliber_type']=='settlement' else '发货口径'}）")
    L.append(f"- 币种：{meta['currency']}")
    L.append(f"- 业务日边界：以赛狐 bizDay 为准（{plan.get('timezone_mode','bizday')}）")
    L.append("- 数据源：赛狐 ERP（get_profit_report_by_shop / _msku"
             " / get_product_analyze_detail）")
    L.append("- 口径说明：流量（sessions）/ACOS/广告点击率/转化率/可售天数等宽表指标"
             "由 custom-report-analysis 技能承接，本报告不含。")
    cov = computed["coverage"]
    if cov.get("mixed_currency"):
        L.append(f"- ⚠️ **多币种且未折算**（{', '.join(cov.get('currencies') or [])}）："
                 "已按币种分组，**禁止跨币种直接相加**。")
    if cov.get("disclosures"):
        L.append("- 取数覆盖与降级：")
        for line in cov["disclosures"]:
            L.append(f"  - {line}")

    L.append("")
    L.append("### ⚠️ 本报告未能覆盖（赛狐 MCP 无对应数据源）")
    for item in [
        "账户健康 / 绩效通知 — 请人工登录 Seller Central 核查",
        "买家消息 / 待回工单 — MCP 无客服接口",
        "政策合规 / 绩效风险 — MCP 无此接口",
        "评论原文 — 仅有聚合指标（好评率 / 差评数）",
        "广告活动级归因（servingStatus=BUDGET_CAPPED 等）— 取值未经实测验证，需人工核查",
    ]:
        L.append(f"- {item}")
    L.append("")
    if meta.get("mock"):
        L.append("> ⚠️ 演示数据，非真实经营数据 —— 本报告不可作为经营依据。")
    return "\n".join(L) + "\n"


def _main() -> None:
    ap = argparse.ArgumentParser(description="周报/月报渲染（只读）")
    ap.add_argument("--in", dest="inp", default="computed.json")
    ap.add_argument("--out", default="weekly_report.md")
    ap.add_argument("--keep-md", action="store_true",
                    help="额外写出同名 .md（调试/测试用；默认只写 .html）")
    ap.add_argument("--no-mermaid", action="store_true")
    args = ap.parse_args()

    computed = json.loads(Path(args.inp).read_text(encoding="utf-8"))
    md = render(computed, include_mermaid=not args.no_mermaid)
    out_path = Path(args.out)
    # HTML 即报告：--out 仅作命名基准，默认只写 .html；--keep-md 时额外写 .md（纯调试/测试开关）
    html_path = write_html_report(md, out_path, keep_md=args.keep_md)
    if args.keep_md:
        print(f"✅ 渲染完成 → {html_path}（md 留档：{out_path}，{len(md.splitlines())} 行）")
    else:
        print(f"✅ 渲染完成 → {html_path}（{len(md.splitlines())} 行）")


if __name__ == "__main__":
    _main()