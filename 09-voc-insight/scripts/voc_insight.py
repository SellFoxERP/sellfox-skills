#!/usr/bin/env python3
"""评论 VOC 归因 —— 主编排脚本（v3：落盘检查 → 校验组装 → 归因 → 渲染）。

把「定位问题 ASIN → 取评论原文 → 主题归因 → 行动建议 → 渲染 →（可选）写飞书」串起来。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
- **取数面 = agent**：先按 `references/workflow.md` 的**取数计划**调
  `mcp__Sellfox-MCP__{search,action}`，把信封落盘 `out/raw/`。本编排启动时先做
  **落盘检查**，缺步骤则打印取数计划指引并退出（不瞎跑）。
- **计算面 = 脚本**：`fetch_signals.py`（校验+组装，零网络）→ `fetch_reviews.py`
  （外部评论成本闸/规范化，不发赛狐 HTTP）→ `analyze_themes.py` → `render_report.py`。
  脚本全程零 HTTP、零 Key（Key 只在客户端 MCP 配置里）。

用法示例::

    # 0) agent 先按 references/workflow.md 取数计划把信封落盘到 out/raw/
    # 1) 只跑赛狐侧（零外部依赖）：定位问题 ASIN
    python3 scripts/voc_insight.py --days 7

    # 2) 已接入外部评论源：先看成本闸，人工确认后喂入原文
    python3 scripts/voc_insight.py --reviews-json raw_reviews.json --confirm-external \
        --adapter-file adapters/example_voc_reviews.md

设计要点：
- **只读 + 外部 MCP**：本编排不调用任何赛狐写工具。
- **落盘缺失/无外部源也能定位失败**：落盘检查给指引；子步骤显式降级可继续，失败则中断，避免读取旧产物。
- **演示环境**：命中指纹即加水印，并默认阻断写飞书。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
RAW_DIR = SKILL_DIR / "out" / "raw"

#: 落盘检查关注的取数计划步骤（与 references/workflow.md 取数计划一致）。
RAW_STEPS = (
    ("custom_current.json", "get_custom_report_page_list（当前窗口 → rows）"),
    ("custom_previous.json", "get_custom_report_page_list（上一窗口 → rows）"),
    ("online_products.json", "get_online_product_page_list（降级源 → rows）"),
)


def _run(script: str, argv: list) -> int:
    """执行子脚本（顺序执行，便于定位失败步骤）。"""
    cmd = [sys.executable, str(SCRIPTS / script)] + argv
    print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR))
    return proc.returncode


def check_raw() -> int:
    """落盘检查：取数面（agent）是否已按取数计划落盘。缺则给指引并返回非零。"""
    present = [name for name, _ in RAW_STEPS if (RAW_DIR / name).exists()]
    others = sorted(p.name for p in RAW_DIR.glob("*.json")) if RAW_DIR.exists() else []
    extra = [f for f in others if f not in present]

    print("── 落盘检查（v3 取数面 = agent）──", flush=True)
    print(f"  目录：{RAW_DIR}")
    for name, desc in RAW_STEPS:
        mark = "✅" if name in present else "—"
        print(f"  {mark} {name:<22} {desc}")
    if extra:
        print(f"  ⚪ 其余落盘文件（不影响本技能）：{', '.join(extra)}")

    if not present:
        print(
            "\n❌ out/raw/ 为空：取数面尚未完成。请按 references/workflow.md 的「取数计划」执行：\n"
            "   1) search 拿契约（get_custom_report_page_list / get_online_product_page_list）→\n"
            "      action 必带当次 contract_version，参数类型按当次 inputSchema；\n"
            "   2) 把每步完整信封（status/data/coverage/pagination/trace_id）落盘：\n"
            "      out/raw/custom_current.json、custom_previous.json（report_id 已配置时），\n"
            "      或 out/raw/online_products.json（无 report_id 的降级源）；\n"
            "   3) 每步落盘后核对 status，空 data=无数据；然后重跑本编排。\n"
            "   调用纪律见 _shared/SELLFOX_CLIENT.md。"
        )
        return 2
    if "custom_current.json" not in present or "custom_previous.json" not in present:
        print("\n⚠️ 宽表两窗口落盘不全：若 report_id 已配置，fetch_signals 将因缺步骤停止"
              "（主路径必须 custom_current + custom_previous）；仅有 online_products.json 时走降级路径。")
    return 0


def run(args: argparse.Namespace) -> int:
    rc = check_raw()
    if rc != 0:
        return rc

    fetch_argv: list = []
    if args.days:
        fetch_argv += ["--days", str(args.days)]
    end_date = getattr(args, "end_date", None)  # 兼容旧调用方手搓的 Namespace
    if end_date:
        fetch_argv += ["--end-date", end_date]
    if args.report_id:
        fetch_argv += ["--report-id", args.report_id]
    fetch_argv += ["--out", "out/signals.json"]

    steps = [
        ("fetch_signals.py", fetch_argv),
        ("fetch_reviews.py", ["--signals", "out/signals.json", "--out", "out/reviews.json"]
         + (["--reviews-json", args.reviews_json] if args.reviews_json else [])
         + (["--adapter-file", args.adapter_file] if args.adapter_file else [])
         + (["--confirm-external"] if args.confirm_external else [])),
        ("analyze_themes.py", ["--signals", "out/signals.json", "--reviews", "out/reviews.json",
                               "--out", "out/voc_analysis.json"]
         + (["--llm-themes", args.llm_themes] if args.llm_themes else [])),
        ("render_report.py", ["--signals", "out/signals.json", "--analysis", "out/voc_analysis.json",
                              "--out", "out/voc-report.md"]
         # 报告出口仅 .html；--keep-md 只在需要飞书 md 载荷（--feishu）时附加
         + (["--keep-md"] if args.feishu else [])),
    ]

    for script, argv in steps:
        rc = _run(script, argv)
        if rc != 0:
            print(f"❌ {script} 失败（{rc}），停止流水线，避免读取旧产物。")
            return rc

    # 可选：优化素材需求单
    if args.with_optimization:
        rc = _run("build_optimization_brief.py",
             ["--analysis", "out/voc_analysis.json", "--out", "out/optimization-brief.json"])
        if rc != 0:
            return rc

    print("\n✅ 完成。报告：out/voc-report.html")
    if args.feishu:
        return _run("write_feishu.py", ["--report", "out/voc-report.md"]
             + (["--override-demo"] if args.override_demo else []))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="评论 VOC 归因（主编排，v3：先落盘检查再计算）")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--end-date", default=None, help="窗口结束日 YYYY-MM-DD（默认今天；透传 fetch_signals 固定环比窗口）")
    p.add_argument("--report-id", default=None, help="自定义报表 ID（默认读 _shared/config/reports.yaml）")
    p.add_argument("--reviews-json", default=None, help="外部 MCP 取回的评论原文（已落盘）")
    p.add_argument("--adapter-file", default=None, help="评论源 adapter 声明（md）")
    p.add_argument("--llm-themes", default=None, help="AI 助手 语义归因结果 JSON（可选）")
    p.add_argument("--confirm-external", action="store_true", help="确认外部调用与成本")
    p.add_argument("--with-optimization", action="store_true", help="产出优化素材需求单（可选）")
    p.add_argument("--feishu", action="store_true", help="写入飞书（演示环境默认阻断）")
    p.add_argument("--override-demo", action="store_true", help="显式覆盖演示环境阻断（仍加水印）")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
