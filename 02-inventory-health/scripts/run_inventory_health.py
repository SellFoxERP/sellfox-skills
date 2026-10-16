#!/usr/bin/env python3
"""库存健康与补货 —— 编排入口（run_inventory_health.py，v3）。

串联：前置校验 → **取数（agent 调客户端 MCP 并落盘 out/raw/）** → 校验组装
（fetch_inventory.assemble_all，零网络）→ 计算（replenishment.compute）→ 渲染（render）→ 可选写飞书。

v3 前置校验（--check-only）：
  1. 客户端已配置 Sellfox-MCP（脚本不再需要 ``SELLFOX_MCP_KEY`` —— Key 只在客户端配置里）；
  2. ``reports.yaml`` 的 ``inventory.report_id`` 是否已回填（缺失 → 助手自动发现，无可用报表则降级）；
  3. ``out/raw/`` 是否已有取数落盘（缺失 → 提示按 workflow.md「取数计划」由 agent 落盘）；
  4. 演示环境指纹运行期识别 → 顶部水印 + 默认阻断写飞书。

用法：
  # 0) agent 先按 references/workflow.md 取数计划调 mcp__Sellfox-MCP__* 并落盘 out/raw/
  python scripts/run_inventory_health.py --check-only          # 只做前置校验（含落盘检查）
  python scripts/run_inventory_health.py                       # 组装 → 计算 → 渲染
  python scripts/run_inventory_health.py --date 2026-09-29 --out out/inventory.md
  python scripts/run_inventory_health.py --feishu              # 写入飞书（演示环境默认阻断）
  python scripts/run_inventory_health.py --answers '{"MOCK-SKU-001":45}' --write-back

设计约束（对照 SKILL.md）：
  * 计算面零网络：只读 out/raw/ 落盘与配置文件，不 import 旧 sellfox_client。
  * report_id 缺失 → 降级并显式标注口径降级。
  * 到货周期解析不到 → 一次性批量询问（--answers 供非交互/测试）。
  * 演示环境命中 → 输出水印 + 默认阻断写飞书；仅 --allow-demo-write 显式放行。
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


def _resolve_report_id(loader: ConfigLoader, override: str | None) -> str | None:
    if override:
        return override
    reports = loader.load("reports").get("reports") or {}
    inv = reports.get("inventory") or {}
    return (inv.get("id") or "").strip() or None


def precheck(report_id: str | None, raw_dir: Path | None = None) -> list[str]:
    """返回告警列表（空 = 全部就绪）。不抛异常，让技能能继续以降级模式运行。"""
    warnings: list[str] = []

    if not report_id:
        warnings.append(
            "尚未取得适用库存报表：本次走在线产品降级口径（可售天数/建议量为自算）。"
            "报表由助手按 ONBOARDING.md 自动发现，无需商家手工配置。"
        )

    raw_dir = Path(raw_dir) if raw_dir else SKILL_DIR / "out" / "raw"
    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        warnings.append(
            f"取数落盘为空（{raw_dir}）：v3 取数面由 agent 完成，"
            "请按 references/workflow.md 的「取数计划」调用 mcp__Sellfox-MCP__{search,action} "
            "并把每步信封落盘到 out/raw/，再重跑本脚本。"
        )

    return warnings


def _print_first_run_guidance(loader: ConfigLoader) -> None:
    """首轮引导：与现行 ONBOARDING 自动发现口径一致 —— 不要求商家创建报表。"""
    from merchant_setup import is_deferred
    if not is_deferred('inventory'):
        print('尚未取得适用增强报表；由助手按 ONBOARDING.md 自动发现并验证（无需商家创建/配置报表），当前先用简版。')
    # 输出实际所缺指标口径（reports.yaml → inventory.fields_hint），不凭记忆编造清单
    hint = ((loader.load('reports').get('reports') or {}).get('inventory') or {}).get('fields_hint')
    if hint:
        print(f'本次使用简版，缺少的自定义报表指标（{hint}）将以自算口径或「需人工核查」在报告中说明。')
    else:
        print('本次使用简版，缺少自定义报表支持的指标将在报告中说明。')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="库存健康体检与补货建议（只读；v3 计算面零网络）")
    ap.add_argument("--date", default=None, help="业务日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--report-id", default=None, help="覆盖 reports.yaml 的 inventory.id")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--check-only", action="store_true", help="只做前置校验（含 out/raw 落盘检查）")
    ap.add_argument("--answers", default=None, help='本次填写的到货周期 JSON，如 \'{"SKU":30}\'')
    ap.add_argument("--write-back", action="store_true", help="把本次填写回写 lead_time.yaml")
    ap.add_argument("--out", default=None, help="输出 Markdown 路径（默认打印到 stdout）")
    ap.add_argument("--shared-root", default=None, help="覆盖 _shared 路径")
    ap.add_argument("--feishu", action="store_true", help="写入飞书文档（演示环境默认阻断）")
    ap.add_argument(
        "--allow-demo-write",
        action="store_true",
        help="显式放行演示环境写入飞书（仍加水印）；不加此开关则演示指纹一律阻断",
    )
    ap.add_argument("--folder-token", default=None, help="飞书文件夹 token")
    return _run(ap.parse_args(argv))


def _run(args: argparse.Namespace) -> int:
    on_date = _parse_date(args.date)
    skill_cfg = _skill_cfg()
    loader = ConfigLoader(shared_root=shared_root(args.shared_root))

    report_id = _resolve_report_id(loader, args.report_id)

    # 前置校验：report_id / out/raw 落盘（v3 取数面由 agent 完成，脚本零网络零 Key）
    raw_dir = Path(args.raw_dir).expanduser() if args.raw_dir else SKILL_DIR / "out" / "raw"
    print("== 前置校验 ==")
    warns = precheck(report_id, raw_dir)
    for w in warns:
        print(f"⚠️ {w}")
    if not warns:
        print("✅ 前置校验通过（report_id 已配置 / 取数已落盘）")
    if args.check_only:
        return 0
    if any("取数落盘为空" in w for w in warns):
        print("❌ 无取数落盘，无法组装数据。请先由 agent 按 workflow.md 取数计划落盘。", file=sys.stderr)
        return 2
    if not report_id:
        _print_first_run_guidance(loader)

    # 校验 + 组装（零网络）：读取 agent 落盘的 out/raw/*.json
    try:
        result: FetchResult = assemble_all(
            raw_dir, report_id=report_id, skill_cfg=skill_cfg,
        )
    except SystemExit as exc:  # assemble_all 对空落盘目录抛 SystemExit（附操作指引）
        print(f"{exc.code}", file=sys.stderr)
        return 2

    if not result.records:
        print("⚠️ 未取回任何 MSKU 记录（可能是无数据，而非失败）。请核对落盘文件与店铺范围。")

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
        out_path.write_text(markdown, encoding="utf-8")
        print(f"✅ 已写入 {out_path}")
    else:
        print(markdown)

    if args.feishu:
        return _write_feishu(markdown, args, result)

    print("\n提示：本技能只出建议，不下采购单。到货周期可在 lead_time.yaml 维护。")
    return 0


def _write_feishu(markdown: str, args: argparse.Namespace, result: FetchResult) -> int:
    try:
        from feishu_writer import FeishuError, FeishuWriter
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 无法加载 feishu_writer：{exc}", file=sys.stderr)
        return 1
    try:
        writer = FeishuWriter()
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 飞书初始化失败（需 app_id/app_secret）：{exc}", file=sys.stderr)
        return 1

    # 演示环境默认阻断：只有显式传 --allow-demo-write 才放行（且文档顶部已带水印）。
    # 绝不根据「是否命中演示指纹」自动放行 —— 那样会让闸门永不放行，等于没闸门。
    override = bool(getattr(args, "allow_demo_write", False))
    try:
        writer.guard_demo({"rows": result.records, "_fp": result.demo_fingerprint}, override=override)
    except FeishuError as exc:
        print(f"⛔ 已阻断写入飞书：{exc}", file=sys.stderr)
        return 3
    if result.demo_fingerprint:
        print(
            "⚠️ 检测到演示环境，已在 --allow-demo-write 显式放行下写入，"
            "文档顶部含演示水印。"
        )

    title = markdown.splitlines()[0].lstrip("# ").strip() or "库存健康体检"
    doc_id = writer.create_doc(title, folder_token=args.folder_token)
    blocks = writer.markdown_to_blocks(markdown)  # 内含 PII 文本脱敏兜底
    writer.write_blocks(doc_id, blocks)
    print(f"✅ 已写入飞书文档：{doc_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
