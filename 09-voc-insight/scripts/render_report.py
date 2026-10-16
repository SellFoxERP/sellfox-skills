#!/usr/bin/env python3
"""评论 VOC 归因 —— Step 4：渲染 VOC 报告（Markdown）。

模板见 ``references/report-template.md``。本模块只做渲染，不取数、不写系统。
强制包含：顶部数据说明（含口径降级/演示水印/截断披露/币种无关说明）、
问题 ASIN、主题归因（模式 vs 个别反馈）、行动建议、需人工核查清单、诚实边界。

用法：
    python scripts/render_report.py --signals out/signals.json --analysis out/voc_analysis.json \
        --out out/voc-report.md
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import voc_common as vc

SKILL_DIR = Path(__file__).resolve().parents[1]


def _p(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (SKILL_DIR / p)


def _load(path: str, default: Any) -> Any:
    fp = _p(path)
    return vc.read_json(str(fp)) if fp.exists() else default


def _fmt(v: Any) -> str:
    if v is None:
        return "无数据"
    f = vc.to_float(v)
    if f is None:
        return str(v)
    return f"{f:g}"


_ERROR_CODE_RE = re.compile(r"error_code=([A-Za-z0-9_]+)")


def _error_codes(signals: Dict[str, Any]) -> List[str]:
    """提取取数失败的 error_code 列表（结构化 error_details 优先，兼容旧版 errors 字符串）。"""
    codes: List[str] = []
    for e in (signals.get("error_details") or []):
        code = (e or {}).get("error_code")
        if code and str(code) not in codes:
            codes.append(str(code))
    if not codes:
        for e in (signals.get("errors") or []):
            m = _ERROR_CODE_RE.search(str(e))
            if m and m.group(1) not in codes:
                codes.append(m.group(1))
    return codes


def _header(signals: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    win = signals.get("window") or {}
    src = signals.get("source") or {}
    lines: List[str] = []
    lines.append(f"# 评论 VOC 归因报告（{_dt.date.today().isoformat()}）")
    lines.append("")
    lines.append("## 0. 数据说明")
    lines.append("")

    fp = signals.get("demo_fingerprint")
    if fp:
        lines.append(f"> ⚠️ **演示数据，非真实经营数据**（命中指纹：{fp}）。")
    degraded = src.get("degraded")
    if degraded and degraded != "none":
        # 降级横幅：优先用 fetch_signals 按三态归因记录的 degraded_banner
        # （未配置 / 发现失败 / 候选验证被拒，文案如实），旧版 signals 退回第一条 warning。
        banner = src.get("degraded_banner") or (signals.get("warnings") or [""])[0] \
            or "⚠️ 口径降级：缺少自定义报表 report_id。"
        lines.append(f"> {banner}")
    banner = analysis.get("banner")
    if banner:
        lines.append(f"> {banner}")
    lines.append("")
    lines.append(f"- 覆盖范围：店铺 {'、'.join(signals.get('_shops') or ['全部'])}")
    lines.append(f"- 时间窗口：近 {win.get('days', '?')} 天"
                 f"（当前 {win.get('current', ['?','?'])[0]} ~ {win.get('current', ['?','?'])[-1]}；"
                 f"基线 前一窗口 {win.get('previous', ['?','?'])[0]} ~ {win.get('previous', ['?','?'])[-1]}）")
    lines.append("- 数据源（字段归属，逐字段标注）：")
    lines.append(f"  - `lowReviewNum` / `reviewNum` / `reviewRate` — `get_custom_report_page_list`"
                 f"（489 宽表，report_id={'已配置' if src.get('report_id') else '⚠️ 未配置'}）")
    lines.append("  - `rating` / `ratingCount` — `get_custom_report_page_list`、`get_online_product_page_list`")
    lines.append(f"  - 评论原文 — 外部评论 MCP（{'已接入' if not analysis.get('degraded') else '⚠️ 未接入 / 无数据'}）")
    # 取数失败逐条披露（workflow.md §7：数据说明必须记录 error_code；该源已按无数据处理）
    err_details = signals.get("error_details") or []
    errs = signals.get("errors") or []
    if err_details:
        lines.append("- 取数失败（该源已按无数据处理，需人工核查）：")
        for e in err_details:
            retry = (e or {}).get("retryable")
            retry_text = {True: "是", False: "否"}.get(retry, "未知")
            msg = (e or {}).get("message") or ""
            lines.append(
                f"  - 步骤 {(e or {}).get('step')}（工具 {(e or {}).get('tool')}）："
                f"error_code={(e or {}).get('error_code') or '未知'}；是否可重试：{retry_text}"
                + (f"；message：{msg}" if msg else "")
            )
    elif errs:
        # 旧版 signals（无 error_details）：原文兜底渲染，保证 error_code 全文可见
        lines.append("- 取数失败（该源已按无数据处理，需人工核查）：")
        for e in errs:
            lines.append(f"  - {e}")
    # 截断披露：按语义识别，不能匹配「截断」二字 ——
    # TruncationInfo.describe() 的真实文案是「已达 max_rows=N 上限，其余未纳入统计。」
    # （不含「截断」），而「未截断」的文案反而含该词，按字面匹配会把真截断报成「未触发」。
    _TRUNC_MARKERS = ("已达 max_rows", "未纳入统计", "其余未纳入")
    truncs = [
        d for d in (signals.get("disclosures") or [])
        if any(m in d for m in _TRUNC_MARKERS)
    ]
    if truncs:
        for t in truncs:                      # 多条截断披露全部列出，不只取第一条
            lines.append(f"- 截断披露：{t}")
    else:
        lines.append("- 截断披露：未触发截断")
    lines.append("- 币种：本报告只涉及**评论数与评分**（计数/评分，与币种无关），未做任何跨币种相减。")
    lines.append("")
    return "\n".join(lines)


def _flagged_section(signals: Dict[str, Any]) -> str:
    flagged = signals.get("flagged") or []
    lines = ["## 1. 问题 ASIN（来自赛狐聚合指标，零外部依赖）", ""]
    if not flagged:
        # 空结果的三种真实原因各说各话：取数失败 ≠ 缺历史基线 ≠ 未触发阈值
        codes = _error_codes(signals)
        snapshot_intro = ""
        if codes:
            lines.append(f"取数失败（error_code：{'、'.join(codes)}），原因见「0. 数据说明」；"
                         "无法定位问题 ASIN，不代表没有问题。")
        elif not (signals.get("counts") or {}).get("previous_rows"):
            lines.append("缺少历史基线，无法判断评分是否下降；不代表没有问题。")
            snapshot_intro = "以下展示已取到的评分快照。"
        else:
            lines.append("本次可比较样本未触发配置阈值。")
        observed = signals.get("observed") or []
        if observed:
            if snapshot_intro:
                lines.append(snapshot_intro)
            lines.extend(["", "| ASIN | 评分 | 评分数 |", "|---|---|---|"])
            for row in observed:
                lines.append(f"| {vc.key_of(row.get('asin')) or '无数据'} | {_fmt(row.get('rating'))} | {_fmt(row.get('ratingCount'))} |")
        lines.append("")
        return "\n".join(lines)
    lines.append("| ASIN | 店铺 | 标题 | 差评数(前→今) | 评分(前→今) | 好评率(前→今) | 命中信号 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in flagged:
        m = r.get("metrics") or {}
        low = m.get("lowReviewNum") or {}
        rate = m.get("rating") or {}
        rr = m.get("reviewRate") or {}
        sigs = "、".join(f.get("signal", "") for f in (r.get("flags") or []))
        lines.append(
            f"| {r.get('asin')} | {r.get('shop_id') or '-'} | {(r.get('title') or '-')[:28]} "
            f"| {_fmt(low.get('prev'))} → {_fmt(low.get('now'))} "
            f"| {_fmt(rate.get('prev'))} → {_fmt(rate.get('now'))} "
            f"| {_fmt(rr.get('prev'))} → {_fmt(rr.get('now'))} "
            f"| {sigs} |"
        )
    lines.append("")
    lines.append("> 排序依据：影响面权重（销量 × 命中信号数），仅为**排序**用，不是精确指标。")
    lines.append("")
    return "\n".join(lines)


def _attribution_section(analysis: Dict[str, Any]) -> str:
    lines = ["## 2. 差评主题归因（评论原文）", ""]
    if analysis.get("degraded") or not (analysis.get("counts") or {}).get("reviews_total"):
        lines.append("⚠️ **未接入外部评论源 / 无原文数据 → 本报告不做主题归因。**")
        lines.append("")
        lines.append("本技能**不会**在没有评论原文的情况下编造主题分布。")
        lines.append("如需归因，请按 `adapters/README.md` 接入评论源后重跑。")
        lines.append("")
        return "\n".join(lines)

    method = analysis.get("method") or {}
    lines.append(f"> 方法论：**只在多个独立评论印证时才报告「模式」**。"
                 f"判定线：{method.get('pattern_rule')}。未达线者列为「个别反馈」，"
                 f"明确标注**不构成模式** —— 把单条差评当普遍问题是最危险的误报。")
    lines.append("")

    patterns = analysis.get("patterns") or []
    lines.append("### 2.1 已印证的「模式」（可据以行动）")
    lines.append("")
    if not patterns:
        lines.append("无：本次没有任何主题达到多源印证阈值。")
        lines.append("")
    for r in patterns:
        lines.append(f"#### [{r['label']}] {r['review_count']} 条独立评论 / "
                     f"{r['distinct_reviewers']} 位评论者（归因来源：{r['attribution_source']}）")
        lines.append(f"- 建议动作：{r['action']}")
        for q in r.get("quotes") or []:
            lines.append(f"  > {q}")
        lines.append("")

    mentions = analysis.get("mentions") or []
    lines.append("### 2.2 个别反馈（样本不足，**不构成模式**）")
    lines.append("")
    if not mentions:
        lines.append("无。")
        lines.append("")
    for r in mentions:
        lines.append(f"- [{r['label']}] {r['review_count']} 条 —— {r['reason']}")
    lines.append("")
    return "\n".join(lines)


def _actions_section(analysis: Dict[str, Any], optimization: Optional[Dict[str, Any]]) -> str:
    lines = ["## 3. 行动建议（按主题 → 动作）", ""]
    patterns = analysis.get("patterns") or []
    if not patterns:
        lines.append("无已印证模式，暂无基于原文的行动建议。")
        lines.append("")
    else:
        lines.append("| 主题 | 证据 | 建议动作 | 归属 |")
        lines.append("|---|---|---|---|")
        for r in patterns:
            owner = {
                "quality": "供应链", "sizing": "运营/设计", "color": "设计/摄影",
                "shipping": "物流", "description": "运营/文案", "other": "运营（人工阅读）",
            }.get(r["theme"], "运营")
            lines.append(f"| {r['label']} | {r['review_count']} 条独立评论 | {r['action']} | {owner} |")
        lines.append("")
    if optimization and optimization.get("briefs"):
        lines.append("### 3.1 优化素材需求单（交生图 MCP，可选）")
        lines.append("")
        lines.append("> ⚠️ 只**描述**需求，不直接生图。生图按次收费，须先确认数量与成本。")
        lines.append("")
        for b in optimization["briefs"]:
            lines.append(f"- `{b.get('id')}` [{b.get('theme')}] {b.get('type')}：{b.get('prompt')}")
        lines.append("")
    return "\n".join(lines)


_MARK_PREFIX_RE = re.compile(r"^(?:⚠️|⬜|✅|❌|⏸)\s*")


def _item_key(text: str) -> str:
    """人工核查条目的去重键：去装饰前缀 + 去全部空白（措辞略异也能命中）。"""
    return "".join(_MARK_PREFIX_RE.sub("", text or "").split())


def _manual_section(signals: Dict[str, Any], analysis: Dict[str, Any]) -> str:
    items: List[str] = []
    items += list(signals.get("manual_checks") or [])
    for w in (signals.get("warnings") or []):
        if "演示" in w or "降级" in w:
            # 统一由本节补「- ⚠️ 」前缀，先剥掉 warning 自带的标记，避免「⚠️ ⚠️」双前缀
            items.append(_MARK_PREFIX_RE.sub("", w))
    if analysis.get("degraded"):
        items.append("评论原文与主题归因：需人工核查（未接入外部评论源）。")
    if not signals.get("source", {}).get("report_id"):
        # 降级原因属上游失败（发现失败/候选验证被拒）时，manual_checks 已带如实文案，
        # 再补「需配置 report_id」会把上游故障误导成用户没配置 → 只在真·未配置时补
        reason = (signals.get("source") or {}).get("degraded_reason") or "not_configured"
        if reason == "not_configured" and not any("lowReviewNum" in it for it in items):
            items.append("lowReviewNum / reviewNum / reviewRate：需自定义报表 report_id。")
    items.append("评论原文的完整性与代表性（外部源抽样可能偏倚）：需人工判断。")
    items.append("外部评论源的合规性（ToS/授权/法律）：需人工确认。")
    lines = ["## 4. 需人工核查清单", ""]
    seen = set()
    for it in items:
        key = _item_key(it)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- ⚠️ {it}")
    lines.append("")
    return "\n".join(lines)


def _boundary_section() -> str:
    return "\n".join([
        "## 5. 本技能不做什么（诚实边界）",
        "",
        "- **不做全量评论归因**：外部评论源多为抽样/受限，结论代表抽样范围内的反馈，不等于总体。",
        "- **不把单条差评当普遍问题**：未达多源印证阈值的只列为「个别反馈」。",
        "- **赛狐官方 MCP 拿不到评论原文**：只有聚合指标；原文靠外部 MCP，可用性取决于是否接入。",
        "- **不修改任何 Listing、不反馈供应链系统**：只出建议；执行由人完成。",
        "- **外部评论源按次/按条计费**，且抓取型源可能有 ToS/法律风险 —— 由使用者自行确认。",
        "- 空数据标「无数据」，未写 0；降级口径已在上方标注。",
        "",
    ])


def render(signals: Dict[str, Any], analysis: Dict[str, Any],
           optimization: Optional[Dict[str, Any]]) -> str:
    parts = [
        _header(signals, analysis),
        _flagged_section(signals),
        _attribution_section(analysis),
        _actions_section(analysis, optimization),
        _manual_section(signals, analysis),
        _boundary_section(),
    ]
    return "\n".join(parts)


def run(args: argparse.Namespace) -> int:
    signals = _load(args.signals, {})
    analysis = _load(args.analysis, {})
    optimization = _load(args.optimization, None) if args.optimization else None
    md = render(signals, analysis, optimization)
    out = _p(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"已写出 {out}（{len(md.splitlines())} 行）")
    if args.print:
        print("\n" + md)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="渲染评论 VOC 报告（Markdown）")
    p.add_argument("--signals", default="out/signals.json")
    p.add_argument("--analysis", default="out/voc_analysis.json")
    p.add_argument("--optimization", default=None, help="可选：优化素材需求单 JSON")
    p.add_argument("--out", default="out/voc-report.md")
    p.add_argument("--print", action="store_true", help="同时在终端打印报告")
    return run(p.parse_args())


if __name__ == "__main__":
    try:
        import sys
        sys.exit(main())
    except SystemExit:
        raise
