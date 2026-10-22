#!/usr/bin/env python3
"""每日经营日报 —— 编排入口（run_daily_report.py，v3）。

串联：前置校验 → **取数（agent 调客户端 MCP 并落盘 out/raw/）** → 校验组装(fetch_data)
→ 分析(analyze) → 渲染(render_report)。报告仅本地 HTML，不外发（飞书能力 v1.7.0 已移除）。

v3 前置校验：
  1. 客户端已配置 Sellfox-MCP（脚本不需要 SELLFOX_MCP_KEY —— Key 只在客户端配置里）；
  2. ``out/raw/`` 是否已有取数落盘（缺失 → 提示按 workflow.md「取数计划」由 agent 落盘）；
  3. 演示环境指纹运行时识别 → 顶部水印（演示数据仅加水印，不外发）。

用法::

    # 0) agent 先按 references/workflow.md 取数计划调 mcp__Sellfox-MCP__* 并落盘 out/raw/
    python run_daily_report.py                 # 校验落盘 → 组装 → 分析 → 渲染
    python run_daily_report.py --date 2026-09-29
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
    ap.add_argument("--caliber", choices=["settlement", "shipment"],
                    help="利润口径覆盖（透传 fetch_data.py；用户点名发货/结算口径时使用，缺省读配置）")
    ap.add_argument("--marketplace", default=None,
                    help="请求的站点过滤（如 US；透传 fetch_data.py，用于核验过滤生效性并回显范围）")
    ap.add_argument("--keep-md", action="store_true",
                    help="渲染层额外写出同名 .md（调试/测试用；默认只写 .html）")
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
        print("✅ 前置校验通过（_shared 可用 / 取数已落盘）")
    if args.check_only:
        return 0
    if any("取数落盘为空" in w for w in warns):
        print("❌ 无取数落盘，无法组装数据包。请先由 agent 按 workflow.md 取数计划落盘。", file=sys.stderr)
        return 2

    bundle = work / "bundle.json"
    analysis = work / "analysis.json"
    report = work / "daily_report.md"          # 仅作 --out 命名基准（HTML 即报告）

    steps = [
        [sys.executable, str(_THIS.parent / "fetch_data.py"),
         "--out", str(bundle)] + (["--date", args.date] if args.date else [])
        + (["--caliber", args.caliber] if args.caliber else [])
        + (["--marketplace", args.marketplace] if args.marketplace else []),
        [sys.executable, str(_THIS.parent / "analyze.py"),
         "--in", str(bundle), "--out", str(analysis)],
        [sys.executable, str(_THIS.parent / "render_report.py"),
         "--analysis", str(analysis), "--bundle", str(bundle), "--out", str(report)]
        + (["--keep-md"] if args.keep_md else []),
    ]
    for cmd in steps:
        print(f"\n== 执行：{' '.join(Path(c).name for c in cmd[:1])} ==")
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"❌ 步骤失败（exit {rc}）：{' '.join(cmd)}", file=sys.stderr)
            return rc

    done = f"（md 留档：{report}）" if args.keep_md else ""
    print(f"\n🎉 完成。日报：{report.with_suffix('.html')}{done}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
