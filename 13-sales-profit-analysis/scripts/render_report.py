#!/usr/bin/env python3
"""渲染面：out/analysis.json → out/report.html（六段，口径行恒第一行；零网络；仅 HTML 出口）。

    python3 scripts/render_report.py [--analysis out/analysis.json] [--out out/report.md] [--keep-md]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import SKILL_ROOT, load_analysis_config  # noqa: E402
from report_html import write_html_report  # noqa: E402  # HTML 主报告出口（_bootstrap 已挂 _shared/scripts）

try:  # 订单明细脱敏用（共享底座）；无底座时渲染利润行不受影响
    from feishu_writer import mask_pii_in_text  # noqa: E402
except Exception:  # pragma: no cover
    def mask_pii_in_text(text: str) -> str:
        return text


def _n(v: Any, pct: bool = False) -> str:
    if v is None or v == "":
        return "无数据（非 0）"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if pct:
        return f"{v:.1%}"          # 比率一律 ×100（0.153 → 15.3%），杜绝 >1 时漏乘的百倍错误
    return f"{v:,.2f}"


def _signed(v: Any) -> str:
    """带符号金额；None → 无数据（非 0），不写 0.00。"""
    return "无数据（非 0）" if v is None else _n(v)


def _neg(v: Any) -> bool:
    try:
        return float(v) < 0
    except (TypeError, ValueError):
        return False


def _fmt_window(w: Dict[str, Any]) -> str:
    return f"{w.get('start')}~{w.get('end')}" if isinstance(w, dict) else str(w)


def render(a: Dict[str, Any]) -> str:
    cfg = load_analysis_config()
    th = cfg.get("thresholds", {})
    meta, cur, base = a["meta"], a["periods"]["current"], a["periods"]["base"]
    lines: List[str] = []

    # 水印（演示指纹 → 首行）
    if a.get("demo_fingerprint"):
        lines.append(f"> ⚠️ 演示/样本数据指纹（{a['demo_fingerprint']}）：结果不作为经营结论。")
        lines.append("")

    # 单窗口判定（base=None 的快照题：不拼「vs None」）
    base_win = meta.get("base")
    has_base_window = isinstance(base_win, dict) and bool(base_win.get("start") or base_win.get("end"))
    snapshot = bool(cur["rows"]) and not base["rows"] and not has_base_window

    # ① 口径行（恒第一实质行；币种从两期行 currency 集合生成，不硬编码）
    cov_desc = "；".join(
        f"{n['step']}/{n['file']}={n['status']}" + (f"（{n['error_code']}）" if n.get("error_code") else "")
        for n in a.get("coverage", []))
    conflict = [n.get("coverage_conflict") for n in a.get("coverage", [])
                if n.get("coverage_conflict")]
    win = (f"窗口={_fmt_window(meta['current'])}（单窗口快照，无对比基期）" if snapshot
           else f"窗口={_fmt_window(meta['current'])} vs {_fmt_window(base_win)}")
    # 时间粒度：窗口 8 位=按天（默认）、6 位=按月（用户点名）
    st = str((meta.get("current") or {}).get("start") or "")
    t_gran = "月（点名）" if len(st) == 6 else ("日" if len(st) == 8 else "未知")
    lines.append(
        f"① 口径={meta.get('caliber')}｜币种={a.get('currencies') or '多币种分开未折算'}｜"
        f"{win}｜"
        f"粒度={meta.get('granularity')}｜时间粒度={t_gran}｜成本层级={meta.get('cost_layer')}｜覆盖={cov_desc or '见数据说明'}"
        + (f"｜⚠️ {'；'.join(conflict)}" if conflict else ""))
    lines.append("")

    # ② 结论：先方向再额与率；单窗口快照只报本期额与率（不做方向判定）
    d_gp = a["delta"]["gp"]
    d_pp = a["delta"].get("margin_pp")
    if snapshot:
        lines.append(f"② 结论（单窗口快照，无对比基期）：本期毛利 {_n(cur['gp'])}；"
                     f"整体毛利率 {_n(cur['margin'], pct=True)}【自算】；无基期，不做方向判定。")
    elif cur["rows"] and base["rows"]:
        direction = "上升" if d_gp > 0 else ("下降" if d_gp < 0 else "持平")
        watch = th.get("gross_profit_mom_watch_pct", -5)
        alert = th.get("gross_profit_mom_alert_pct", -15)
        base_gp = base["gp"] or 0
        mom_pct = (d_gp / base_gp * 100) if base_gp else None
        flag = "🔴" if (mom_pct is not None and mom_pct < alert) else (
            "🟡" if (mom_pct is not None and mom_pct < watch) else "")
        lines.append(f"② 结论：毛利{direction} {flag}——本期 {_n(cur['gp'])} vs 基期 {_n(base['gp'])}"
                     f"（Δ {d_gp:+,.2f}" + (f"，环比 {mom_pct:+.1f}%" if mom_pct is not None else "") + "）；"
                     f"整体毛利率本期 {_n(cur['margin'], pct=True)} vs 基期 {_n(base['margin'], pct=True)}"
                     + (f"（{d_pp:+.1f}pp）【自算】" if d_pp is not None else "【自算】"))
    else:
        lines.append("② 结论：本期或基期无可用数据（非 0），不做方向判定——见数据说明。")
    lines.append("")

    # ③ 结构：两期桥接 Δ 表（只用组合字段 = 同层不双计）+ 残差；快照只报本期占比
    if snapshot:
        sign_note = ("（费用项已按主符号归一：利好为正）" if a.get("bridge_sign_note") else "")
        lines.append(f"③ 结构（本期桥接{sign_note}，单窗口快照）：")
        sale = cur.get("sale") or 0.0
        lines.append("| 项目 | 本期 | 占销售额 |")
        lines.append("|---|---|---|")
        for f, v in cur["bridge"].items():
            # 占比口径=占销售额（费用行取绝对值占比；收入恒 100%）——桥接净额做基数无业务含义
            share = (abs(v) / sale) if (v is not None and sale) else None
            lines.append(f"| {f} | {_n(v)} | {_n(share, pct=True)} |")
        if cur.get("residual") is not None:
            lines.append(f"\n残差（毛利 − 桥接合计）：本期 {cur['residual']:+,.2f}"
                         "；契约未给官方加总公式，如实报、不凑系数。")
        else:
            lines.append("\n桥接字段全缺 → 残差不可算（无数据，非 0）。")
        lines.append("")
    elif cur["rows"] and base["rows"]:
        sign_note = ("（费用项已按主符号归一为对毛利贡献方向：利好为正）"
                     if a.get("bridge_sign_note") else "")
        lines.append(f"③ 结构（桥接七项 Δ{sign_note}；缺侧=无数据非 0）：")
        lines.append("| 项目 | 本期 | 基期 | Δ |")
        lines.append("|---|---|---|---|")
        for f, v in a["delta"]["bridge"].items():
            lines.append(f"| {f} | {_n(cur['bridge'].get(f))} | {_n(base['bridge'].get(f))} | {_signed(v)} |")
        r_cur, r_base = cur.get("residual"), base.get("residual")
        resid_note = ("；契约未给官方加总公式，如实报、不凑系数。" if r_cur is not None or r_base is not None
                      else "；桥接字段全缺 → 残差不可算（无数据，非 0）。")
        lines.append(f"\n残差（毛利 − 桥接合计）：本期 {_signed(r_cur)} / 基期 {_signed(r_base)}" + resid_note)
        lines.append("")

    # 按店铺结构（granularity=shop 或多店铺行时给分店铺对照；F2）
    if a.get("by_shop"):
        def _gp_cell(p: Dict[str, Any]) -> str:
            # 缺期（rows=0）毛利不渲染空求和的 0.00，与毛利率列一致标无数据（G2）
            return "无数据（非 0）" if not p.get("rows") else _n(p.get("gp"))

        # 快照（无基期）不渲染基期列——整列「无数据」没有信息量
        if snapshot:
            lines.append("③b 按店铺（毛利与整体毛利率【自算】）：")
            lines.append("| 店铺 | 本期毛利 | 本期毛利率 |")
            lines.append("|---|---|---|")
            for sid, per in sorted(a["by_shop"].items()):
                c = per.get("current", {})
                lines.append(f"| {sid} | {_gp_cell(c)} | {_n(c.get('margin'), pct=True)} |")
        else:
            lines.append("③b 按店铺（毛利与整体毛利率【自算】，缺期=无数据非 0）：")
            lines.append("| 店铺 | 本期毛利 | 基期毛利 | 本期毛利率 | 基期毛利率 |")
            lines.append("|---|---|---|---|---|")
            for sid, per in sorted(a["by_shop"].items()):
                c, b = per.get("current", {}), per.get("base", {})
                lines.append(f"| {sid} | {_gp_cell(c)} | {_gp_cell(b)} |"
                             f" {_n(c.get('margin'), pct=True)} | {_n(b.get('margin'), pct=True)} |")
        lines.append("")

    # ④ 榜
    rank_failed = [n for n in a.get("coverage", [])
                   if str(n.get("step", "")).startswith("rank") and n.get("status") == "failed"]
    if a.get("rankings"):
        rk = a["rankings"].get("current", {})
        table = [f"| 赚钱 Top | {r.get('asin') or r.get('msku') or '—'} | {_n(r.get('grossProfit'))} |"
                 for r in rk.get("top", [])[:10]]
        table += [f"| 亏损 Bottom | {r.get('asin') or r.get('msku') or '—'} | {_n(r.get('grossProfit'))} |"
                  for r in rk.get("bottom", [])[:10] if _neg(r.get("grossProfit"))]
        lines.append("④ 榜（产品分析，与站点层不同基，仅排序）：")
        if table:
            lines.append("| 方向 | 标识 | 毛利 |")
            lines.append("|---|---|---|")
            lines.extend(table)
        elif rank_failed:
            for n in rank_failed:
                lines.append(f"- 排名工具取数失败（error_code={n.get('error_code')}），该次榜单不可得。")
        else:
            lines.append("- 本期榜为空（无正/负毛利行），不得据此说「没有亏损品」。")
        if rk.get("skipped_zero_sales"):
            lines.append(f"\n已剔除零销售行 {rk['skipped_zero_sales']} 行（哨兵率值不作真值）。")
        dd = rk.get("dedup") or {}
        if dd.get("before", 0) > dd.get("after", 0):
            lines.append(f"已按商品标识跨源去重 {dd['before']}→{dd['after']}"
                         "（desc/asc 双信封等重复源同标识只计一次）。")
        lines.append("")
    elif rank_failed:
        lines.append("④ 榜：不可得——")
        for n in rank_failed:
            lines.append(f"- 排名工具取数失败（error_code={n.get('error_code')}），榜单不可得"
                         "（failed ≠ 无数据，见数据说明）。")
        lines.append("")

    # ⑤ 归因证据（脚本给证据与置信素材，AI 收敛成 3~5 条；禁止 ⚪ 升 🟡）
    dc = a.get("daily_checks")
    if dc:
        lines.append("⑤ 归因证据（按 attribution-rules.md 收敛为 3~5 条；标注置信 🟢🟡⚪）：")
        if dc.get("credit_days") or (dc.get("base_ex_credit_bridge") or {}).get("excluded_days"):
            ex = dc.get("base_ex_credit_bridge") or {}
            signs = dc.get("credit_signs") or {}
            sign_desc = "，".join(f"{f}主符号={'+' if s > 0 else '-'}"
                                  for f, s in signs.items() if s) or "主符号不可判"
            lines.append(f"- 🟢 调整类贷项（按主符号方向判定，符号以实测为准：{sign_desc}）："
                         f"本期 {dc.get('credit_days') or []}；基期剔除日 {ex.get('excluded_days') or []}；"
                         f"账面 Δ {a['delta']['gp']:+,.2f} vs 剔后 Δ "
                         f"{ex.get('delta_gp_ex_credit', '未算')}（两个值都必须报）")
        if dc.get("zero_run_stalls"):
            lines.append(f"- 🟢 疑似停投断点：{dc['zero_run_stalls']}（不得写成\"成本优化\"，先确认谁停的）")
        if dc.get("stall_check_note"):
            lines.append(f"- ⚪ {dc['stall_check_note']}。")
        if dc.get("concentrated_fees"):
            lines.append(f"- 🟢 费用集中入账（按事件核对，非趋势）：{dc['concentrated_fees']}")
        if dc.get("settlement_lag", {}).get("droop"):
            sl = dc["settlement_lag"]
            lines.append(f"- 🟡 结算滞后疑似：尾部日均 {sl['tail_avg']} vs 前段 {sl['body_avg']}"
                         "（右端不完整会低估本期）")
        if dc.get("cross_check_mismatch"):
            mm = dc["cross_check_mismatch"]
            detail = "；".join(
                f"{m['field']} 日分段 {_signed(m['daily_sum'])} ≠ 聚合 {_signed(m['agg'])}"
                for m in mm[:7])
            lines.append(f"- ⚠️ 日分段加总 ≠ 聚合行（{len(mm)} 项不符：{detail}）"
                         "——不等先修取数再谈结论。")
        big = sorted(((k, v) for k, v in a["delta"]["bridge"].items() if v is not None),
                     key=lambda kv: kv[1])[:3]
        if big:
            lines.append("- Δ 最大的桥接项：" + "、".join(f"{k} {v:+,.2f}" for k, v in big)
                         + "（进对应子链：销售/成本/费用/退款，见 attribution-rules.md）")
        lines.append("")

    # ⑥ 局限 + 数据说明
    lines.append("⑥ 局限与数据说明：")
    for n in a.get("coverage", []):
        if n.get("error_code"):
            lines.append(f"- 步骤 {n['step']}/{n['file']} 取数失败（error_code={n['error_code']}）："
                         "该源缺失，不当作无数据。")
    if a.get("no_data", {}).get("current") or a.get("no_data", {}).get("base"):
        lines.append("- 存在空数据窗口：标「无数据（非 0）」，不写 0。")
    for label, p in (("本期", cur), ("基期", base)):
        dd = p.get("dedup") or {}
        if dd.get("before", 0) > dd.get("after", 0):
            lines.append(f"- 上游重复行 {label}：{dd['before']}→{dd['after']}"
                         "（按店铺/MSKU/日期业务键去重后汇总，重复行不计入）。")
    sc = a.get("selfchecks", {})
    if sc.get("sentinel_excluded"):
        se = sc["sentinel_excluded"]
        lines.append(f"- 剔除零销售行 cur/base={se['current']}/{se['base']}（哨兵率值不进排名）。")
    if a.get("demo_fingerprint"):
        lines.append("- 全部数值来自演示/样本数据，不作为经营结论。")
    lines.append("- 只报毛利（契约无净利字段）；净利需明列扣减项做【推算】。")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analysis", default="out/analysis.json")
    ap.add_argument("--out", default="out/report.md")
    ap.add_argument("--keep-md", action="store_true",
                    help="额外留档同名 .md（默认只写 .html，仅供调试/飞书载荷）")
    args = ap.parse_args()
    ap_path = Path(args.analysis)
    if not ap_path.is_absolute():
        ap_path = SKILL_ROOT / ap_path
    a = json.loads(ap_path.read_text(encoding="utf-8"))
    text = render(a)
    # 兜底脱敏（订单明细类内容不直接渲染，此为外发前的最后防线；行级文本脱敏）
    text = mask_pii_in_text(text)
    out = Path(args.out)
    if not out.is_absolute():
        out = SKILL_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    html_path = write_html_report(text, out, keep_md=args.keep_md)  # 仅 .html 主报告（--keep-md 才附 .md）
    print(f"报告已写出 → {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
