#!/usr/bin/env python3
"""利润异常诊断 —— 渲染层。

输出「利润异常清单 + 归因」。设计要点：

  * **每条归因都带来源工具** —— 171+489 字段来自两个不通用报表，不标来源的归因无法复核。
  * **顶部必须标利润口径**（settlement / shipment）—— 不标注必然与财务对不上账。
  * **延期结算与赔偿单列一节** —— 这两类是最容易漏看的钱（deferredAmount 等只在自定义报表）。
  * **诚实边界区块必填** —— 覆盖范围（含截断披露）/ 币种 / 需人工核查项 / 演示水印。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

try:
    from .diagnose import DiagnosisReport, MskuDiagnosis
except ImportError:  # pragma: no cover
    from diagnose import DiagnosisReport, MskuDiagnosis  # type: ignore


def _fmt(value: Optional[float], *, money: bool = True) -> str:
    if value is None:
        return "无数据"
    if money:
        return f"{value:,.2f}"
    return f"{value:,.4f}"


CALIBER_LABEL = {
    "settlement": "settlement（结算口径，赛狐默认）",
    "shipment": "shipment（发货口径）",
}


def render_report(
    report: DiagnosisReport,
    *,
    fetch_meta: Dict[str, Any],
    cur_start: date,
    cur_end: date,
    base_start: date,
    base_end: date,
) -> str:
    """渲染 Markdown 报告。"""
    caliber = fetch_meta.get("caliber", "settlement")
    demo_fp = fetch_meta.get("demo_fingerprint")
    degraded = bool(fetch_meta.get("degraded"))
    top_n = int(fetch_meta.get("top_n", 20))
    include_deferred = bool(fetch_meta.get("include_deferred_section", True))

    lines: List[str] = []
    lines.append(f"# 利润异常诊断 | {cur_start} ~ {cur_end}")
    lines.append("")

    if demo_fp:
        lines.append(
            f"> ⚠️ **演示数据，非真实经营数据**（指纹：`{demo_fp}`）。"
            "本报告数值不可用于任何业务口径验证；写飞书默认阻断。"
        )
        lines.append("")

    # ---- 口径声明（不可省略）----
    lines.append("## 利润口径（必读）")
    lines.append("")
    lines.append(f"- **利润口径：{CALIBER_LABEL.get(caliber, caliber)}**")
    lines.append(
        f"- 本期窗口：`{cur_start}` ~ `{cur_end}`；基期窗口：`{base_start}` ~ `{base_end}`（等长、不重叠）"
    )
    lines.append(
        f"- 报表币种：`{fetch_meta.get('report_currency', 'USD')}`；成本口径币种：`{fetch_meta.get('cost_currency', 'CNY')}`"
        "（两者不直接相减；本技能不做跨币种合并，汇率变化在「数据说明」中提示）"
    )
    if degraded:
        lines.append(
            "- ⚠️ **口径降级**：未配置自定义报表 `report_id`，本次只用 `get_profit_report_by_msku`。"
            "采购成本/头程/赔偿/延期结算类归因**不可用**，见下方「需人工核查」。"
        )
    lines.append("")

    # ---- 核心结论 ----
    totals = report.totals or {}
    mcp_calls = int(fetch_meta.get("mcp_calls", 0) or 0)
    # 「失败」与「无数据」必须区分（评测问题 #3）：MSKU 主源取数失败时按真实状态给文案。
    msku_failed = [str(s) for s in (fetch_meta.get("msku_failed") or [])]
    lines.append("## 核心结论")
    lines.append("")
    if report.mskus:
        lines.append(
            f"本期共诊断 **{totals.get('msku_count', 0)}** 个 MSKU，"
            f"其中 **{totals.get('anomaly_count', 0)}** 个毛利下滑且可归因"
            f"（严重 {totals.get('severe_count', 0)} 个），"
            f"合计毛利下滑 **{_fmt(totals.get('total_drop'))}**（{fetch_meta.get('report_currency', 'USD')}）。"
        )
    elif mcp_calls == 0:
        # 空报告 + 零调用：绝不能读成「当期无异常」，必须显式区分。
        lines.append(
            "⚠️ **本次未发起任何赛狐 MCP 取数调用（mcp_calls=0）**，报告为空**不代表当期无异常**，"
            "而是取数未执行。请检查店铺范围/参数配置与 MCP 连通性后重跑。"
        )
    elif msku_failed:
        lines.append(
            f"本次已发起 **{mcp_calls}** 次赛狐 MCP 取数调用，但 MSKU 利润报表取数失败"
            f"（{ '；'.join(msku_failed) }），未取回可诊断的 MSKU 记录 —— **取数失败，原因见数据说明**。"
        )
    else:
        lines.append(
            f"本次已发起 **{mcp_calls}** 次赛狐 MCP 取数调用，但未取回可诊断的 MSKU 记录"
            "（可能为「无数据」，而非失败；覆盖面见下方「数据说明」）。"
        )
    lines.append("")

    # ---- 范围防御（评测线上问题 N3）：请求 ASIN 0 命中披露 ----
    # 上游 search_type/search_contents 过滤可能被忽略（实测返回了别的 MSKU），
    # 请求值与取回样本不符时显式披露，且不下任何归因结论 —— 答非所问不如不答。
    requested_asins = [str(a).strip() for a in (fetch_meta.get("requested_asins") or []) if str(a).strip()]
    wanted_asins = {a.upper() for a in requested_asins}
    asin_miss = bool(wanted_asins) and not any(
        str(d.asin or "").strip().upper() in wanted_asins for d in report.mskus
    )
    if asin_miss:
        lines.append(
            "> ⚠️ 请求的 ASIN " + "、".join(f"`{a}`" for a in requested_asins)
            + " 未命中任何 MSKU 行（上游 search_type/search_contents 过滤可能未生效）。"
            "本次取回样本与请求范围不符，**不下任何归因结论**；请核对 ASIN 或改用 msku/sku 维度重新取数。"
        )
        lines.append("")

    if report.mskus:
        lines.extend(["## 已取回样本的毛利对比", "", "| MSKU | 基期毛利 | 本期毛利 | 变化 |", "|---|---|---|---|"])
        for item in report.mskus[:top_n]:
            lines.append(f"| {item.msku} | {_fmt(item.baseline_gross_profit)} | {_fmt(item.current_gross_profit)} | {_fmt(item.gross_profit_delta)} |")
        lines.append("")
    # ---- 异常清单 + 归因 ----
    lines.append("## 异常清单与归因")
    lines.append("")
    if asin_miss:
        # 范围防御：请求 ASIN 0 命中 → 不对「取回的别的 MSKU」下归因结论（答非所问）。
        lines.append(
            "⚠️ 请求的 ASIN 未命中任何取回行（上游过滤可能未生效）：跳过归因结论，"
            "先核对请求的 ASIN/MSKU 维度并重新取数。"
        )
        lines.append("")
    else:
        shown = 0
        for diag in report.mskus:
            if not (diag.has_anomaly and diag.driver_factors):
                continue
            if shown >= top_n:
                break
            shown += 1
            lines.extend(_render_one(diag, currency=fetch_meta.get("report_currency", "USD")))
            lines.append("")

        if shown == 0 and report.mskus:
            lines.append("本期无 MSKU 同时满足「毛利下滑」与「可归因」两个条件。")
            lines.append("")
        if shown >= top_n and len([d for d in report.mskus if d.has_anomaly and d.driver_factors]) > top_n:
            lines.append(
                f"> ⚠️ 异常清单仅展示前 {top_n} 条（`output.top_n_loss`），其余未纳入。"
            )
            lines.append("")

    # ---- 延期结算与赔偿（易漏的钱）----
    if include_deferred:
        lines.extend(_render_deferred(report, fetch_meta))
        lines.append("")

    # ---- 数据说明（必填）----
    lines.append("## 数据说明")
    lines.append("")
    lines.append("**取数覆盖范围**")
    lines.append("")
    lines.append(f"- 数据源：{'、'.join(fetch_meta.get('sources', [])) or '无'}")
    for cov in fetch_meta.get("coverage", []):
        lines.append(f"- {cov}")
    lines.append("")
    if fetch_meta.get("warnings"):
        lines.append("**警示**")
        lines.append("")
        # 多信封 warnings 去重（eval3-2026-10-22 N1）：每个消费的信封各贡献一条同名警示，
        # 同一句重复 3~4 行——保留首条，重复次数以（×N）标注。
        warn_counts: Dict[str, int] = {}
        for warn in fetch_meta["warnings"]:
            key = str(warn)
            warn_counts[key] = warn_counts.get(key, 0) + 1
        for warn, n in warn_counts.items():
            lines.append(f"- {warn}" + (f"（×{n}）" if n > 1 else ""))
        lines.append("")

    # 需人工核查汇总
    gaps: List[str] = []
    for diag in report.mskus:
        for f in diag.unavailable_factors:
            gaps.append(f"`{diag.msku}` · {f.reason}")
        for g in diag.data_gaps:
            gaps.append(f"`{diag.msku}` · {g}")
    if gaps:
        lines.append("**⚠️ 需人工核查（本技能未取到或不可用，绝不编造）**")
        lines.append("")
        for g in sorted(set(gaps))[:30]:
            lines.append(f"- {g}")
        if len(set(gaps)) > 30:
            lines.append(f"- …（共 {len(set(gaps))} 项，仅列前 30）")
        lines.append("")

    lines.append("**本技能不做什么**")
    lines.append("")
    lines.append("- 不修改赛狐任何数据（只读）：不改价、不改广告、不改 Listing。")
    lines.append("- 不做因果推断：归因是**算术贡献度**，因子间相关（如销量↔佣金）需人工判断。")
    lines.append("- 不提供账户健康/绩效通知/买家消息/政策合规数据（赛狐 MCP 无此能力）→ 需人工核查。")
    lines.append("- 不计算税费细节与汇兑损益的完整重构（仅用报表自带 `rate`/`currencyList` 提示）。")
    lines.append("")
    return "\n".join(lines)


def _render_one(diag: MskuDiagnosis, *, currency: str) -> List[str]:
    out: List[str] = []
    flag = "🔴 严重" if diag.severe else "🟠 异常"
    out.append(f"### {flag} `{diag.msku}`" + (f" | ASIN {diag.asin}" if diag.asin else ""))
    out.append("")
    if diag.title:
        out.append(f"{diag.title}")
        out.append("")
    out.append(
        f"- 毛利：基期 {_fmt(diag.baseline_gross_profit)} → 本期 {_fmt(diag.current_gross_profit)}"
        f"（Δ {_fmt(diag.gross_profit_delta)} {currency}）"
    )
    if diag.gross_profit_rate_current is not None:
        out.append(
            f"- 毛利率：基期 {_fmt(diag.gross_profit_rate_baseline, money=False)} → "
            f"本期 {_fmt(diag.gross_profit_rate_current, money=False)}"
        )
    out.append("")
    out.append("**归因（按对毛利跌幅的贡献额降序，逐字段标注来源工具）**")
    out.append("")
    out.append("| 因子 | 来源工具 | 字段 | 基期 | 本期 | 贡献额 | 占跌幅 |")
    out.append("|---|---|---|---|---|---|---|")
    for f in diag.driver_factors:
        out.append(f.to_row())
    out.append("")
    # 符号校准披露（归因表脚注）：费用类因子实测存储符号与配置 profit_sign 假设不符时必列。
    cal_notes = sorted({f.sign_note for f in diag.factors if f.sign_calibrated and f.sign_note})
    if cal_notes:
        out.append("> ⚠️ **符号校准披露**（profit_sign 假设仅为缺省，已按实测存储符号归因）：")
        for note in cal_notes:
            out.append(f"> - {note}")
        out.append("")
    # 建议动作
    out.append("**建议动作**")
    out.append("")
    for f in diag.driver_factors:
        if f.hint:
            out.append(f"- **{f.label}**：{f.hint}（来源字段 `{f.raw_field}`）")
    out.append("")
    if diag.unavailable_factors:
        out.append("> ⚠️ 以下因子本期/基期数据缺失，**需人工核查**，未参与归因：")
        for f in diag.unavailable_factors:
            out.append(f"> - {f.reason}")
        out.append("")
    if diag.cross_check:
        for note in diag.cross_check:
            out.append(f"> 💱 {note}")
        out.append("")
    return out


def _render_deferred(report: DiagnosisReport, fetch_meta: Dict[str, Any]) -> List[str]:
    """延期结算与赔偿——最容易漏看的钱。"""
    out: List[str] = ["## 延期结算与赔偿（易漏的钱）", ""]
    rows: List[str] = []
    for diag in report.mskus:
        for f in diag.factors + diag.unavailable_factors:
            if f.canonical in (
                "deferred_amount", "reimbursement_amount",
                "warehouse_lost_amount", "missing_from_inbound_amount",
            ):
                cur = "无数据" if f.current is None else f"{f.current:,.2f}"
                base = "无数据" if f.baseline is None else f"{f.baseline:,.2f}"
                rows.append(f"| `{diag.msku}` | {f.label} | `{f.raw_field}` | {f.tool} | {base} | {cur} |")
    if rows:
        out.append("| MSKU | 项目 | 字段 | 来源工具 | 基期 | 本期 |")
        out.append("|---|---|---|---|---|---|")
        out.extend(rows[:50])
        if len(rows) > 50:
            out.append(f"| … | （共 {len(rows)} 行，仅列前 50） | | | | |")
        out.append("")
        out.append(
            "> ⚠️ 这些字段**只存在于 `get_custom_report_page_list`**（489 字段宽表），"
            "不在 `get_profit_report_by_msku`。若未配置自定义报表 `report_id`，此区块必然为空。"
        )
        out.append(
            "> 赔偿类可进一步用 `get_amazon_reimbursement_report` 交叉核对是否漏领"
            f"（本次记录数 / 状态：{fetch_meta.get('reimbursement_count', '未取数')}）。"
        )
    else:
        out.append(
            "本次未取到延期结算/赔偿类数据。"
            "原因可能为：(a) 未配置自定义报表 `report_id`（口径降级）；"
            "(b) 该期确无此类金额（**标「无数据」，不写 0**）；"
            "(c) 主源 `get_profit_report_by_msku` 取数失败或未取回行 —— 无 MSKU 锚点，"
            "自定义报表的延期/赔偿数值无法挂靠展示（失败原因见「数据说明」警示区）。"
        )
    out.append("")
    return out
