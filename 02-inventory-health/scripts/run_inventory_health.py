#!/usr/bin/env python3
"""库存健康与补货 —— 编排入口（run_inventory_health.py，v3）。

串联：前置校验 → **取数（agent 调客户端 MCP 并落盘 out/raw/）** → 校验组装
（fetch_inventory.assemble_all，零网络）→ 计算（replenishment.compute）→ 渲染（render）。
报告仅本地 HTML，不外发（飞书写入能力 v1.7.0 已移除；演示数据仅加顶部水印）。

v3 前置校验（--check-only）：
  1. 客户端已配置 Sellfox-MCP（脚本不需要 ``SELLFOX_MCP_KEY`` —— Key 只在客户端配置里）；
  2. ``out/raw/`` 是否已有取数落盘（缺失 → 提示按 workflow.md「取数计划」由 agent 落盘）；
  3. 演示环境指纹运行期识别 → 顶部水印（演示数据仅加水印，不外发）。

口径说明（v1.7.0）：本技能**不取自定义报表宽表**（get_custom_report_page_list / report_id
依赖已整体移除，宽表字段分析由 custom-report-analysis 技能承接）。主源为在线产品
`get_online_product_page_list`，可售天数与建议补货量按在线产品口径**自算**，
报告顶部固定标「口径降级」横幅。

用法：
  # 0) agent 先按 references/workflow.md 取数计划调 mcp__Sellfox-MCP__* 并落盘 out/raw/
  python scripts/run_inventory_health.py --check-only          # 只做前置校验（含落盘检查）
  python scripts/run_inventory_health.py                       # 组装 → 计算 → 渲染
  python scripts/run_inventory_health.py --date 2026-09-29 --out out/inventory.md
  python scripts/run_inventory_health.py --answers '{"MOCK-SKU-001":45}' --write-back

设计约束（对照 SKILL.md）：
  * 计算面零网络：只读 out/raw/ 落盘与配置文件，不 import 旧 sellfox_client。
  * 未接入自定义报表（由 custom-report-analysis 承接）→ 固定按在线产品口径自算并显式标注口径降级。
  * 到货周期解析不到 → 一次性批量询问（--answers 供非交互/测试）。
  * 演示环境命中 → 输出顶部水印（仅加水印，不外发）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from _shared_bootstrap import add_shared_to_path, shared_root

add_shared_to_path()

from config_loader import ConfigLoader, load_yaml_file  # noqa: E402
from report_html import write_html_report  # noqa: E402  （HTML 主报告出口：默认只写同名 .html，HTML 即报告）

from fetch_inventory import FetchResult, assemble_all  # noqa: E402
from replenishment import build_lead_time_specs, compute  # noqa: E402
from render import render_report  # noqa: E402

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent


def _skill_cfg() -> dict:
    path = SKILL_DIR / "config" / "inventory.yaml"
    return load_yaml_file(path) if path.exists() else {}


def _parse_date(text: str | None) -> date:
    if not text:
        return date.today()
    return date.fromisoformat(text)


def precheck(raw_dir: Path | None = None) -> list[str]:
    """返回告警列表（空 = 全部就绪）。不抛异常，让技能能继续以降级模式运行。"""
    warnings: list[str] = []

    raw_dir = Path(raw_dir) if raw_dir else SKILL_DIR / "out" / "raw"
    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        warnings.append(
            f"取数落盘为空（{raw_dir}）：v3 取数面由 agent 完成，"
            "请按 references/workflow.md 的「取数计划」调用 mcp__Sellfox-MCP__{search,action} "
            "并把每步信封落盘到 out/raw/，再重跑本脚本。"
        )

    return warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="库存健康体检与补货建议（只读；v3 计算面零网络）")
    ap.add_argument("--date", default=None, help="业务日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--check-only", action="store_true", help="只做前置校验（含 out/raw 落盘检查）")
    ap.add_argument("--answers", default=None, help='本次填写的到货周期 JSON，如 \'{"SKU":30}\'')
    ap.add_argument("--write-back", action="store_true", help="把本次填写回写 lead_time.yaml")
    ap.add_argument("--out", default=None, help="输出报告路径（默认打印到 stdout；写出时 HTML 即报告）")
    ap.add_argument("--keep-md", action="store_true",
                    help="--out 时额外写出同名 .md（调试/测试用；默认只写 .html）")
    ap.add_argument("--shared-root", default=None, help="覆盖 _shared 路径")
    return _run(ap.parse_args(argv))


def _run(args: argparse.Namespace) -> int:
    on_date = _parse_date(args.date)
    skill_cfg = _skill_cfg()
    loader = ConfigLoader(shared_root=shared_root(args.shared_root))

    # 前置校验：out/raw 落盘（v3 取数面由 agent 完成，脚本零网络零 Key）
    raw_dir = Path(args.raw_dir).expanduser() if args.raw_dir else SKILL_DIR / "out" / "raw"
    print("== 前置校验 ==")
    warns = precheck(raw_dir)
    for w in warns:
        print(f"⚠️ {w}")
    if not warns:
        print("✅ 前置校验通过（取数已落盘）")
    if args.check_only:
        return 0
    if any("取数落盘为空" in w for w in warns):
        print("❌ 无取数落盘，无法组装数据。请先由 agent 按 workflow.md 取数计划落盘。", file=sys.stderr)
        return 2

    # 校验 + 组装（零网络）：读取 agent 落盘的 out/raw/*.json
    try:
        result: FetchResult = assemble_all(raw_dir, skill_cfg=skill_cfg)
    except SystemExit as exc:  # assemble_all 对空落盘目录抛 SystemExit（附操作指引）
        print(f"{exc.code}", file=sys.stderr)
        return 2

    if not result.records:
        # 主源（在线产品）不可用：报告照常产出（横幅在 render 层），但 rc=2 信号「无有效数据」，
        # 不允许 rc=0 的全 0 项空报告被读成「体检通过」。
        print(
            "⚠️ 未取回任何 MSKU 记录：在线产品源不可用时本报告无有效数据项"
            "（可能是无数据，而非全部健康）。请核对落盘文件与店铺范围。",
            file=sys.stderr,
        )

    # 到货周期：解析不到 → 一次性批量询问（非交互用 --answers）
    override: dict | None = None
    if result.records:
        plan_probe = compute(result.records, loader=loader, skill_cfg=skill_cfg, on_date=on_date)
        if plan_probe.unresolved_lead_mskus:
            answers = json.loads(args.answers) if args.answers else {}
            specs = build_lead_time_specs(
                plan_probe,
                default_days=float((loader.load("lead_time").get("default") or 45)),
            )
            resolved = loader.ensure_params(
                specs,
                answers={k: v for k, v in answers.items() if k in plan_probe.unresolved_lead_mskus},
                allow_write_back=True,
                write_back_confirmed=True if args.write_back else None,
            )
            filled = {k: v for k, (v, _src) in resolved.items() if k in answers}
            if filled:
                override = {"by_sku": filled}

    plan = compute(
        result.records, loader=loader, skill_cfg=skill_cfg,
        on_date=on_date, lead_time_override=override,
    )

    currency = skill_cfg.get("currency") or {}
    fetch_meta = {
        "sources": result.sources,
        "coverage": result.coverage,
        "warnings": result.warnings,
        "demo_fingerprint": result.demo_fingerprint,
        "degraded": result.degraded,
        "degrade_reason": result.degrade_reason,
        "no_valid_data": result.no_valid_data,
        "window_days": int(skill_cfg.get("report_window_days", 30)),
        "sales_currency": currency.get("sales_currency", "USD"),
        "cost_currency": currency.get("cost_currency", "CNY"),
        "top_n": int((skill_cfg.get("output") or {}).get("top_n_each_quadrant", 20)),
    }
    markdown = render_report(plan, fetch_meta=fetch_meta, on_date=on_date)

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = SKILL_DIR / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # HTML 即报告：--out 仅作命名基准，默认只写 .html；--keep-md 时额外写 .md
        html_path = write_html_report(markdown, out_path, keep_md=args.keep_md)
        if args.keep_md:
            print(f"✅ 已写入 {html_path}（md 留档：{out_path}）")
        else:
            print(f"✅ 已写入 {html_path}")
    else:
        print(markdown)

    print("\n提示：本技能只出建议，不下采购单。到货周期可在 lead_time.yaml 维护。")
    return 2 if not result.records else 0


if __name__ == "__main__":
    raise SystemExit(main())
