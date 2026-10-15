#!/usr/bin/env python3
"""每日经营日报 —— 编排入口（run_daily_report.py，v3）。

串联：前置校验 → **取数（agent 调客户端 MCP 并落盘 out/raw/）** → 校验组装(fetch_data)
→ 分析(analyze) → 渲染(render_report) → 可选写飞书。

v3 前置校验：
  1. 客户端已配置 Sellfox-MCP（脚本不再需要 SELLFOX_MCP_KEY —— Key 只在客户端配置里）；
  2. ``reports.yaml`` 的 daily_ops ``report_id`` 是否已回填（缺失 → 提示去赛狐网页端创建并存降级）；
  3. ``out/raw/`` 是否已有取数落盘（缺失 → 提示按 workflow.md「取数计划」由 agent 落盘）；
  4. 演示环境指纹运行时识别 → 顶部水印 + 默认阻断写飞书。

用法::

    # 0) agent 先按 references/workflow.md 取数计划调 mcp__Sellfox-MCP__* 并落盘 out/raw/
    python run_daily_report.py                 # 校验落盘 → 组装 → 分析 → 渲染
    python run_daily_report.py --date 2026-09-29 --feishu
    python run_daily_report.py --check-only    # 只做前置校验
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

_THIS = Path(__file__).resolve()
_SKILL_ROOT = _THIS.parent.parent
sys.path.insert(0, str(_THIS.parent))
from sharedpath import shared_root as _resolve_shared_root  # noqa: E402

sys.path.insert(0, str(_THIS.parent))  # 允许导入同目录的 fetch/analyze/render


def _shared_root() -> str:
    import os

    root = os.environ.get("SELLFOX_SHARED_ROOT") or str(_resolve_shared_root(_SKILL_ROOT))
    return str(root or "")


def precheck() -> List[str]:
    """返回告警列表（空 = 全部就绪）。不抛异常，让技能能继续以降级模式运行。"""
    warnings: List[str] = []

    shared = _shared_root()
    if not shared or not Path(shared).exists():
        warnings.append(f"_shared 路径不可用（{shared or '未配置'}）：请设置 config/paths.yaml 的 shared_root。")
        return warnings

    try:
        sys.path.insert(0, str(Path(shared) / "scripts"))
        from config_loader import ConfigLoader

        reports = ConfigLoader(shared_root=shared).load("reports").get("reports") or {}
        rid = str((reports.get("daily_ops") or {}).get("id") or "").strip()
        if not rid:
            warnings.append(
                "reports.yaml 的 daily_ops.report_id 为空：L2 明细层将口径降级。"
                "缺少自定义报表支持的指标将在报告中说明。"
            )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"读取 reports.yaml 失败：{exc}")

    raw_dir = _SKILL_ROOT / "out" / "raw"
    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        warnings.append(
            f"取数落盘为空（{raw_dir}）：v3 取数面由 agent 完成，"
            "请按 references/workflow.md 的「取数计划」调用 mcp__Sellfox-MCP__{search,action} "
            "并把每步信封落盘到 out/raw/，再重跑本脚本。"
        )

    return warnings


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="每日经营日报 - 编排（v3：取数已前置到 agent）")
    ap.add_argument("--date", help="报告日期 yyyy-MM-dd（默认昨日）")
    ap.add_argument("--feishu", action="store_true", help="写入飞书")
    ap.add_argument("--override-demo", action="store_true", help="演示环境也强制写飞书")
    ap.add_argument("--check-only", action="store_true", help="只做前置校验")
    ap.add_argument("--workdir", default="out", help="中间产物目录（相对技能根目录）")
    args = ap.parse_args(argv)

    work = Path(args.workdir)
    if not work.is_absolute():
        work = _SKILL_ROOT / work

    print("== 前置校验 ==")
    warns = precheck()
    for w in warns:
        print(f"⚠️ {w}")
    if not warns:
        print("✅ 前置校验通过（_shared 可用 / report_id 已配置 / 取数已落盘）")
    if args.check_only:
        return 0
    if any("取数落盘为空" in w for w in warns):
        print("❌ 无取数落盘，无法组装数据包。请先由 agent 按 workflow.md 取数计划落盘。", file=sys.stderr)
        return 2

    bundle = work / "bundle.json"
    analysis = work / "analysis.json"
    report = work / "daily_report.md"

    steps = [
        [sys.executable, str(_THIS.parent / "fetch_data.py"),
         "--out", str(bundle)] + (["--date", args.date] if args.date else []),
        [sys.executable, str(_THIS.parent / "analyze.py"),
         "--in", str(bundle), "--out", str(analysis)],
        [sys.executable, str(_THIS.parent / "render_report.py"),
         "--analysis", str(analysis), "--bundle", str(bundle), "--out", str(report)]
        + (["--feishu"] if args.feishu else [])
        + (["--override-demo"] if args.override_demo else []),
    ]
    for cmd in steps:
        print(f"\n== 执行：{' '.join(Path(c).name for c in cmd[:1])} ==")
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"❌ 步骤失败（exit {rc}）：{' '.join(cmd)}", file=sys.stderr)
            return rc

    print(f"\n🎉 完成。日报：{report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
