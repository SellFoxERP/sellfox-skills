#!/usr/bin/env python3
"""Listing 健康体检 —— 渲染脚本。

把 problems.json 渲染成 Markdown（按 references/report-template.md 的模板），
可选推送到飞书（写入前自动 PII 脱敏 + 演示环境阻断）。

用法：
    python scripts/render_report.py --in problems.json --out report.md
    python scripts/render_report.py --in problems.json --push-feishu --folder <token>
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from typing import Any, Dict, List, Optional

import listing_common as lc

_CHECK_SHORT = {
    "不可售/被删除": "不可售/被删除",
    "丢购物车": "丢购物车",
    "无流量": "无流量",
    "有流量无转化": "有流量无转化",
    "BSR 恶化": "BSR 恶化",
    "销量衰退": "销量衰退",
    "评分下滑": "评分下滑",
    "断货拖累排名": "断货拖累排名",
    "标签缺失": "标签缺失",
}


def _window_text(window: Optional[Dict[str, Any]]) -> str:
    if not window:
        return "未记录"
    return (f"近 {window.get('short_days')} 天与近 {window.get('long_days')} 天"
            f"（{window.get('start')} ~ {window.get('end')}；{window.get('timezone_note', '')}）")


def render(result: Dict[str, Any]) -> str:
    meta = result.get("meta") or {}
    problems: List[Dict[str, Any]] = result.get("problems") or []
    manual: List[str] = result.get("manual_verification") or []
    today = _dt.date.today().isoformat()

    lines: List[str] = []
    head = f"# Listing 健康体检报告（{today}）"
    if meta.get("demo_fingerprint"):
        head = f"> ⚠️ 演示数据，非真实经营数据（指纹：{meta['demo_fingerprint']}）\n\n" + head
    lines.append(head)
    lines.append("")

    # --- 0. 数据说明 ---
    lines.append("## 0. 数据说明")
    lines.append("")
    shops = meta.get("shop_ids") or []
    lines.append(f"- 覆盖范围：{len(shops) if shops else '全部'} 个店铺；扫描状态："
                 f"{', '.join(meta.get('statuses_scanned') or []) or '未记录'}")
    lines.append(f"- 时间窗口：{_window_text(meta.get('window'))}")
    lines.append("- 数据源：`get_online_product_page_list`（主）；`get_product_analyze_list`（可选）")
    if meta.get("report_configured"):
        lines.append("- 数据源：`get_custom_report_page_list`（daily_ops 宽表）已启用")
    else:
        lines.append("- ⚠️ **口径降级**：缺少自定义报表（report_id）→ "
                     "sessions / conversionRate / buyBoxPercent / 类目排名 / 差评数**不可用**，标「需人工核查」")
    discs = meta.get("disclosures") or []
    if discs:
        for d in discs:
            lines.append(f"- ⚠️ 截断披露：本次仅覆盖 Top N，其余未纳入（{d}）")
    else:
        lines.append("- 截断披露：未触发截断")
    if meta.get("demo_fingerprint"):
        lines.append(f"- 演示环境：⚠️ 命中指纹 `{meta['demo_fingerprint']}`，数值不可用于业务判断")
    else:
        lines.append("- 演示环境：未命中")
    lines.append("- 币种：价格字段为 USD；`inventoryManage.currency` 为 CNY（未直接相减）")
    errs = meta.get("errors") or []
    if errs:
        lines.append("- 取数失败项：")
        for e in errs:
            lines.append(f"  - ✗ {e}")
    lines.append("")

    # --- 1. 红线清单 ---
    redline = [p for p in problems if p.get("severity") in ("P0", "P1")]
    lines.append("## 1. 红线清单（P0/P1）")
    lines.append("")
    if not redline:
        lines.append("无 P0/P1 问题。")
    else:
        for p in redline:
            flag = "（需人工核查）" if p.get("need_manual") else ""
            lines.append(f"- **[{p['severity']}] {_CHECK_SHORT.get(p['check'], p['check'])}** "
                         f"— {p.get('asin')}（{p.get('shop')}）：{p.get('evidence')}{flag}")
    lines.append("")

    # --- 2. 问题清单 ---
    lines.append("## 2. 问题清单（按 P0→P3 排序）")
    lines.append("")
    if not problems:
        lines.append("未发现问题（或数据不足，见第 3 节）。")
    for p in problems:
        note = f"｜ {p['note']}" if p.get("note") else ""
        flag = "｜ ⚠️ 需人工核查" if p.get("need_manual") else ""
        lines.append(f"### [{p['severity']}] {_CHECK_SHORT.get(p['check'], p['check'])} — "
                     f"{p.get('asin')}（{p.get('shop')}）")
        lines.append(f"- 证据：{p.get('evidence')}{note}")
        lines.append(f"  字段来源：{p.get('source')}")
        lines.append(f"- 建议：{p.get('advice')}{flag}")
        lines.append("")

    # --- 3. 需人工核查清单 ---
    lines.append("## 3. 需人工核查清单")
    lines.append("")
    if manual:
        for m in manual:
            lines.append(f"- ⚠️ {m}")
    else:
        lines.append("无。")
    lines.append("")

    # --- 4. 诚实边界 ---
    lines.append("## 4. 本技能不做什么（诚实边界）")
    lines.append("")
    lines.append("- 本技能只诊断不修改，**不会改动任何 Listing**（改标题/主图/价格/关键词均无接口，也不该做）。")
    lines.append("- 以下数据拿不到，需人工核查：竞品价格、类目平均 CVR、账户健康/政策合规、评论原文、抑制原因。")
    lines.append("- 空数据标「无数据」，未写 0；口径降级与截断已在上方标注。")
    lines.append("")
    return "\n".join(lines)


def push_feishu(markdown: str, result: Dict[str, Any], folder: str, title: str) -> int:
    """可选：推送到飞书。演示环境默认阻断；写入前经 mask_pii。"""
    try:
        from feishu_writer import FeishuWriter  # type: ignore
    except Exception as exc:
        print(f"✗ 无法加载 feishu_writer：{exc}", file=sys.stderr)
        return 2
    try:
        writer = FeishuWriter()
    except Exception as exc:
        print(f"✗ 飞书凭证缺失或初始化失败：{exc}", file=sys.stderr)
        return 3
    # 演示环境默认阻断写入
    fp = (result.get("meta") or {}).get("demo_fingerprint")
    try:
        writer.guard_demo(result, override=False)  # 命中演示指纹 → 抛错阻断
    except Exception as exc:
        print(f"✗ 演示环境阻断写入（{fp}）：{exc}", file=sys.stderr)
        return 4
    try:
        doc_id = writer.create_doc(title, folder_token=folder or None)
        blocks = writer.markdown_to_blocks(markdown)  # 内部自动 PII 脱敏，并标注「已脱敏」
        writer.write_blocks(doc_id, blocks)
    except Exception as exc:
        print(f"✗ 飞书写入失败：{exc}", file=sys.stderr)
        return 5
    print(f"✓ 已写入飞书文档：{doc_id}（PII 已脱敏）")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Listing 健康体检报告渲染")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", default="report.md")
    ap.add_argument("--push-feishu", action="store_true")
    ap.add_argument("--folder", default="")
    ap.add_argument("--title", default="")
    args = ap.parse_args(argv)

    result = lc.read_json(args.inp)
    markdown = render(result)
    lc.write_json  # noqa: B018  (保持 import 一致性)
    from pathlib import Path
    Path(args.out).write_text(markdown, encoding="utf-8")
    print(f"✓ 报告已渲染 → {args.out}")

    if args.push_feishu:
        title = args.title or f"Listing 健康体检报告 {_dt.date.today().isoformat()}"
        return push_feishu(markdown, result, args.folder, title)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())