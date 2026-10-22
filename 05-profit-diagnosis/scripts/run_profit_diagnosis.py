#!/usr/bin/env python3
"""利润异常诊断 —— 编排入口（只读，v3：取数已前置到 agent）。

串联：前置校验 → **取数（agent 调客户端 MCP 元工具并落盘 out/raw/）** →
校验组装(fetch_profit.assemble_all) → 归因(diagnose) → 渲染(render)。

v3 前置校验（不再需要 SELLFOX_MCP_KEY —— Key 只在客户端 MCP 配置里）：
  1. 会话内能调 `mcp__Sellfox-MCP__{help,search,action}`（agent 取数面前提）；
  2. `out/raw/` 是否已有取数落盘（缺失 → 提示按 workflow.md「取数计划」由 agent 落盘）；
  3. 演示环境指纹运行时识别 → 顶部水印。

自定义报表（489 宽表）已不在本技能取数范围：宽表因子归因由 custom-report-analysis
技能承接（固定分工，报告固定披露）；赔偿条数核对仍走 get_amazon_reimbursement_report。

用法：
  python scripts/run_profit_diagnosis.py                                  # 默认今天，本地 Markdown
  python scripts/run_profit_diagnosis.py --end 2026-09-30 --period-days 30
  python scripts/run_profit_diagnosis.py --shop-ids 10001,10002 --out /tmp/profit.md
  python scripts/run_profit_diagnosis.py --caliber shipment               # 切换利润口径
  python scripts/run_profit_diagnosis.py --check-only                     # 只做前置校验
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from _bootstrap import SKILL_ROOT, load_diagnosis, load_shops, load_thresholds

from diagnose import diagnose  # noqa: E402
from fetch_profit import FetchResult, assemble_all, resolve_windows  # noqa: E402
from render import render_report  # noqa: E402
from report_html import write_html_report  # noqa: E402  （HTML 主报告出口：默认只写同名 .html，HTML 即报告）

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
RAW_DIR = SKILL_ROOT / "out" / "raw"


def _parse_date(text: str | None) -> date:
    if not text:
        return date.today()
    return date.fromisoformat(text)


def _resolve_shop_ids(override: str | None) -> list[str]:
    if override:
        return [s.strip() for s in override.split(",") if s.strip()]
    scope = load_shops().get("default_scope") or []
    return [str(s) for s in scope]


def precheck(raw_dir: Path = RAW_DIR) -> list[str]:
    """返回告警列表（空 = 全部就绪）。不抛异常，让技能能继续以降级模式运行。"""
    warnings: list[str] = []
    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        warnings.append(
            f"取数落盘为空（{raw_dir}）：v3 取数面由 agent 完成，"
            "请按 references/workflow.md 的「取数计划」调用 mcp__Sellfox-MCP__{search,action} "
            "并把每步信封落盘到 out/raw/，再重跑本脚本。"
        )
    return warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="利润异常诊断（只读，v3：取数已前置到 agent）")
    ap.add_argument("--end", default=None, help="报告期末日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--period-days", type=int, default=None, help="本期天数（默认读 diagnosis.yaml → compare.period_days）")
    ap.add_argument("--shop-ids", default=None, help="逗号分隔店铺 ID（默认读 shops.yaml）")
    ap.add_argument("--caliber", default=None, help="利润口径 settlement / shipment（默认读配置）")
    ap.add_argument("--asin", default=None,
                    help="请求诊断的 ASIN（逗号分隔）。用于核对上游 search_contents 过滤是否生效："
                         "0 命中会在报告显式披露且不下任何归因结论（不传则用信封 scope 记录的请求值）")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--check-only", action="store_true", help="只做前置校验（含落盘检查）")
    ap.add_argument("--out", default=None, help="输出报告路径（默认打印到 stdout；写出时 HTML 即报告）")
    ap.add_argument("--keep-md", action="store_true",
                    help="--out 时额外写出同名 .md（调试/测试用；默认只写 .html）")
    return _run(ap.parse_args(argv))


def _run(args: argparse.Namespace) -> int:
    end = _parse_date(args.end)
    cfg = load_diagnosis()
    if args.period_days:
        cfg.setdefault("compare", {})["period_days"] = args.period_days
    compare = cfg.get("compare") or {}
    period_days = int(compare.get("period_days", 30))
    if args.caliber:
        cfg.setdefault("caliber", {})["default"] = args.caliber

    shop_ids = _resolve_shop_ids(args.shop_ids)
    raw_dir = Path(args.raw_dir) if args.raw_dir else RAW_DIR
    raw_dir = raw_dir if raw_dir.is_absolute() else (SKILL_ROOT / raw_dir)

    print("== 前置校验 ==")
    warns = precheck(raw_dir)
    for w in warns:
        print(f"⚠️ {w}")
    if not warns:
        print("✅ 前置校验通过（取数已落盘）")
    if args.check_only:
        return 0
    if any("取数落盘为空" in w for w in warns):
        print(
            "❌ 无取数落盘，无法组装数据。请先由 agent 按 references/workflow.md 取数计划落盘。",
            file=sys.stderr,
        )
        return 2

    try:
        cur_start, cur_end, base_start, base_end, window_warnings = resolve_windows(
            end, period_days, max_lookback_days=int(compare.get("max_lookback_days", 180))
        )
    except ValueError as exc:
        print(f"❌ 环比窗口非法：{exc}", file=sys.stderr)
        print("   请调小 --period-days，或改用按月查询（date_type=1）。", file=sys.stderr)
        return 1

    try:
        result: FetchResult = assemble_all(
            shop_ids=shop_ids,
            cur_start=cur_start,
            cur_end=cur_end,
            base_start=base_start,
            base_end=base_end,
            skill_cfg=cfg,
            raw_dir=raw_dir,
            window_warnings=window_warnings,
        )
    except SystemExit as exc:  # assemble_all 对空落盘目录给出行动指引
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 运行异常：{exc}", file=sys.stderr)
        return 1

    profit_th = (load_thresholds().get("profit") or {})
    report = diagnose(
        msku_rows=result.msku_rows,
        skill_cfg=cfg,
        profit_thresholds=profit_th,
    )

    currency = cfg.get("currency") or {}
    out_cfg = cfg.get("output") or {}
    fetch_meta = {
        "sources": result.sources,
        "coverage": result.coverage,
        "warnings": result.warnings,
        "demo_fingerprint": result.demo_fingerprint,
        "caliber": result.caliber,
        "report_currency": currency.get("report_currency", "USD"),
        "cost_currency": currency.get("cost_currency", "CNY"),
        "top_n": int(out_cfg.get("top_n_loss", 20)),
        "include_deferred_section": bool(out_cfg.get("include_deferred_section", True)),
        # 修复（评测问题 #1）：计数/状态按真实落盘信封给出（未取数 / 无数据 / 已取数 N 批 M 行 /
        # 取数失败（error_code：X）），由 fetch_profit.assemble_all 计算后如实渲染。
        "reimbursement_count": result.reimbursement_status,
        # 修复（评测问题 #3）：把失败步骤交给渲染层，「取数失败」与「无数据」在核心结论区分。
        "msku_failed": [s for s in result.failed_steps if s.startswith("MSKU")],
        # 修复（评测线上问题 N3）：请求侧 ASIN（CLI 显式值优先，缺省用信封 scope 记录的请求值）。
        "requested_asins": (
            [a.strip() for a in args.asin.split(",") if a.strip()] if args.asin
            else result.requested_asins
        ),
        # v3：脚本零网络，此计数 = 实际消费的 agent 落盘信封数（零落盘 → 0 → 渲染层显式警示）。
        "mcp_calls": result.mcp_calls,
    }
    if result.mcp_calls == 0:
        print(
            "⚠️ 本次未消费任何取数落盘信封（mcp_calls=0）。报告为空不代表当期无异常，"
            "请按 references/workflow.md 取数计划落盘后重跑。",
            file=sys.stderr,
        )
    markdown = render_report(
        report, fetch_meta=fetch_meta,
        cur_start=cur_start, cur_end=cur_end,
        base_start=base_start, base_end=base_end,
    )

    if args.out:
        out_path = Path(args.out)
        # HTML 即报告：--out 仅作命名基准，默认只写 .html；--keep-md 时额外写 .md
        html_path = write_html_report(markdown, out_path, keep_md=args.keep_md)
        if args.keep_md:
            print(f"✅ 已写入 {html_path}（md 留档：{out_path}）")
        else:
            print(f"✅ 已写入 {html_path}")
    else:
        print(markdown)

    print("\n提示：本技能只读，只出诊断与归因；改广告请用技能 6，清货请用技能 2。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
