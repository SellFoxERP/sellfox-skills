#!/usr/bin/env python3
"""库存健康与补货 —— 渲染层（Markdown 输出，纯函数）。

模板见 references/output-template.md。诚实边界区块为必填，不可省略。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from replenishment import PlanResult, PlanRow

LAYER_LABEL = {
    "by_sku": "配置(SKU)",
    "by_supplier": "配置(供应商)",
    "by_shipping_method": "配置(运输方式)",
    "default": "默认值⚠️",
}


def lead_label(row: PlanRow) -> str:
    if row.lead_source == "本次填写":
        return "本次填写"
    if row.lead_source == "默认值":
        return "默认值⚠️"
    return LAYER_LABEL.get(row.lead_layer, row.lead_source or "无数据")


def _fmt(value: Optional[float], digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "无数据"
    return f"{value:,.{digits}f}{suffix}"


def _inbound_cell(row: PlanRow) -> str:
    return "无数据" if row.inbound_total is None else _fmt(row.inbound_total, 0)


def render_replenishment_sheet(rows: List[PlanRow], *, top_n: int = 20) -> str:
    """补货建议单。列含关键的「周期来源」。"""
    lines = [
        "| MSKU | ASIN | 可售天数 | 到货周期 | 周期来源 | 在途 | 缺口 | 建议补货量 | 紧急度 | 负责采购 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    shown = 0
    for row in rows:
        if row.urgency not in ("P0", "P1", "P2"):
            continue
        if shown >= top_n:
            break
        lines.append(
            "| {msku} | {asin} | {days} | {lead} | {src} | {inb} | {gap} | {sug} | {urg} | 需人工指派 |".format(
                msku=row.msku, asin=row.asin or "无数据",
                days=_fmt(row.available_days, 1, " 天"), lead=_fmt(row.lead_days, 1, " 天"),
                src=lead_label(row), inb=_inbound_cell(row),
                gap=_fmt(row.gap, 0), sug=_fmt(row.suggested, 0),
                urg=row.urgency,
            )
        )
        shown += 1
    if shown == 0:
        lines.append("| — | — | 本批次无 P0/P1/P2 补货项 | — | — | — | — | — | — | — |")
    return "\n".join(lines)


def _bucket(rows: List[PlanRow], grouped: Dict[str, List[PlanRow]]) -> str:
    lines: List[str] = []
    for key in sorted(grouped):
        lines.append(f"\n### {key}（{len(grouped[key])} 项）")
        lines.append("")
        lines.append("| MSKU | ASIN | 店铺 | 可售天数 | 日均销量 | 在途 | 库龄271+ | 说明 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for row in grouped[key]:
            notes = "；".join(row.notes + row.actions) or "—"
            lines.append(
                "| {m} | {a} | {s} | {d} | {v} | {i} | {p} | {n} |".format(
                    m=row.msku, a=row.asin or "无数据", s=row.shop_name or "无数据",
                    d=_fmt(row.available_days, 1), v=_fmt(row.velocity, 1),
                    i=_inbound_cell(row),
                    p=_fmt(row.age_271_pct, 1, "%"), n=notes,
                )
            )
    return "\n".join(lines)


def render_report(
    result: PlanResult,
    *,
    fetch_meta: Dict[str, Any],
    on_date: Optional[date] = None,
) -> str:
    on_date = on_date or date.today()
    out: List[str] = []
    demo = fetch_meta.get("demo_fingerprint")

    if demo:
        out.append(f"> ⚠️ **演示数据，非真实经营数据**（指纹：`{demo}`）。请勿据此下采购决策。\n")

    out.append(f"# 📊 库存健康体检与补货建议 | {on_date.strftime('%Y-%m-%d')}\n")

    # 结论摘要
    c = result.counts
    out.append("## 核心结论\n")
    out.append(f"- 🔴 P0 来不及：**{c.get('P0', 0)}** 项 ｜ 🟠 P1 有缺口：**{c.get('P1', 0)}** 项 "
               f"｜ 🟡 P2 预警：**{c.get('P2', 0)}** 项 ｜ ✅ 健康：**{c.get('健康', 0)}** 项 "
               f"｜ ❓ 数据不足：**{c.get('未知', 0)}** 项")
    out.append(f"- 滞销清理：**{c.get('③ 滞销清理', 0)}** 项")
    if fetch_meta.get("no_valid_data"):
        # 主源不可用：全 0 结论不可采信，绝不静默空报告
        out.append(
            f"- ⚠️ **主源不可用，本报告无有效数据项**（{fetch_meta.get('degrade_reason') or '原因未知'}）"
            "——核心结论按 0 计仅表示无数据，不可作为「全部健康」的依据。"
        )
    elif fetch_meta.get("degraded"):
        # 降级横幅（v1.7.0 固定口径：未接入自定义报表，由 custom-report-analysis 技能承接）
        reason = fetch_meta.get("degrade_reason") or "未接入自定义报表（由 custom-report-analysis 技能承接）"
        out.append(
            f"- ⚠️ **口径降级**：{reason}，本报告按在线产品数据"
            "（day30SaleNum / inventoryManage）自算；可售天数与建议补货量均为**自算口径**"
            "（非赛狐 availableDays / suggestNumSum），缺失字段的行标「需人工核查」。"
        )
    out.append("")

    # 双向矩阵
    out.append("## 双向矩阵（断货 ↔ 滞销）\n")
    out.append("```")
    out.append("              高销量")
    out.append(f"   ① 紧急补货 ({c.get('① 紧急补货', 0):>2}) │ ② 健康 ({c.get('② 健康', 0):>2})")
    out.append("  ─────────────────┼──────────────────→ 库存水位")
    out.append(f"   ③ 滞销清理 ({c.get('③ 滞销清理', 0):>2}) │ ④ 低效观察 ({c.get('④ 低效观察', 0):>2})")
    out.append("              低销量")
    out.append("```\n")
    out.append(f"补货观察：{c.get('① 补货观察', 0)} 项；数据不足：{c.get('⑤ 数据不足', 0)} 项（单独列示，不计入紧急补货或健康）。\n")

    # 补货建议单
    out.append("## 补货建议单\n")
    out.append("> 「周期来源」列标明该行到货周期的可信度：配置命中 / 本次填写 / **默认值⚠️**。")
    out.append("> 「负责采购」由使用者自行指派 —— MCP 无采购人字段，本技能不编造。\n")
    out.append(render_replenishment_sheet(
        result.rows, top_n=int(fetch_meta.get("top_n", 20))
    ))
    out.append("")

    # 分象限明细
    grouped: Dict[str, List[PlanRow]] = {}
    for row in result.rows:
        grouped.setdefault(row.quadrant, []).append(row)
    out.append("## 象限明细")
    out.append(_bucket(result.rows, grouped))
    out.append("")

    # 数据说明（必填）
    out.append("---\n")
    out.append("## 数据说明\n")
    out.append(f"- 数据源：赛狐 ERP ｜ 涉及工具：{', '.join(fetch_meta.get('sources', [])) or '无'}")
    out.append(f"- 报表窗口：{fetch_meta.get('window_days', 30)} 天 ｜ 截止：{on_date.strftime('%Y-%m-%d')} 23:59")
    out.append(f"- 币种：售价口径 {fetch_meta.get('sales_currency', 'USD')} ｜ 成本口径 {fetch_meta.get('cost_currency', 'CNY')}"
               "（跨币种不直接相减，换算须标注汇率来源与日期）")
    coverage = fetch_meta.get("coverage") or []
    if coverage:
        out.append("- 取数覆盖范围（含截断披露）：")
        for line in coverage:
            out.append(f"  - {line}")

    warnings = list(result.warnings) + list(fetch_meta.get("warnings") or [])
    if warnings:
        out.append("\n**⚠️ 本次运行警告**")
        for w in warnings:
            out.append(f"- {w}")

    out.append("\n### ⚠️ 本技能未能覆盖（需人工核查）")
    out.append("- **采购下单** —— 赛狐 MCP 无采购接口，本技能只出建议，执行靠人")
    out.append("- **安全库存修改** —— 只能读 `domesticSafeStock`/`overseaSafeStock`，不能改")
    out.append("- **长期仓储费金额** —— 未配置费率时需人工核查，不编造")
    out.append("- **供应商 / 采购负责人** —— 需在配置或人工指派")
    out.append("- **每 SKU 的供应商/运输方式映射** —— MCP 不返回，需人工维护")
    if result.unresolved_lead_mskus:
        sample = "、".join(result.unresolved_lead_mskus[:5])
        more = "…" if len(result.unresolved_lead_mskus) > 5 else ""
        out.append(f"- **到货周期未命中配置** —— {sample}{more} 等 {len(result.unresolved_lead_mskus)} 个 MSKU 用了默认值")
    out.append("")
    return "\n".join(out)