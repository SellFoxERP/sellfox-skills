#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写入层（周报 / 月报）—— 飞书文档 + 管理群通知。

安全机制（全部强制，不可绕过）：
  1) 演示环境默认**阻断**写入（`FeishuWriter.guard_demo`，需显式 override 且有水印）。
  2) 写入前对来源数据做 **PII 脱敏**（`mask_pii` / markdown 文本脱敏），并在文末标注「已脱敏」。
  3) 飞书文档默认**不公开分享**；应用须先被加为文档协作者，否则写入 403。
  4) 创建文档限流 3 次/秒 → 由 FeishuWriter 内部串行节流。

本技能是**只读技能**，写入目标仅为「报告文档」，不涉及任何赛狐侧写操作。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

import skill_paths

SHARED_ROOT = skill_paths.bootstrap()
from feishu_writer import FeishuError, FeishuWriter, mask_pii, mask_pii_in_text  # type: ignore  # noqa: E402


def push(
    markdown_path: str,
    computed_path: str,
    override_demo: bool = False,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = skill_paths.load_skill_config("weekly")
    fcfg = cfg.get("feishu") or {}
    if not fcfg.get("enabled", False):
        return {"status": "skipped",
                "note": "config/weekly.yaml → feishu.enabled=false，未推送。设为 true 后重跑。"}

    markdown = Path(markdown_path).read_text(encoding="utf-8")
    computed: Dict[str, Any] = {}
    if Path(computed_path).exists():
        computed = json.loads(Path(computed_path).read_text(encoding="utf-8"))

    writer = FeishuWriter(folder_token=fcfg.get("folder_token") or None, blocking_demo=True)

    # 演示闸门：把整份计算产物交给指纹检测（含店铺名/ASIN/图片域名等）
    payload_for_demo = computed or markdown
    try:
        fp = writer.guard_demo(payload_for_demo, override=override_demo)
    except FeishuError as exc:
        return {"status": "blocked_demo", "note": str(exc)}
    if fp:
        markdown = "> ⚠️ 演示数据，非真实经营数据\n\n" + markdown

    # PII 脱敏（订单类字段；报告主体已不含买家信息，文本兜底仍执行）
    safe_computed = mask_pii(computed) if computed else computed
    safe_md = mask_pii_in_text(markdown)
    safe_md += "\n\n---\n> 本文档已脱敏：不含买家邮箱 / 姓名 / 地址等 PII。\n"

    if not title:
        meta = (safe_computed.get("meta") or {}) if safe_computed else {}
        plan = (meta.get("period") or {}) if isinstance(meta, dict) else {}
        win = ((plan.get("windows") or {}).get("current") or {}) if plan else {}
        kind = "月报" if plan.get("period") == "month" else "周报"
        title = f"经营{kind} {win.get('start_iso','')}~{win.get('end_iso','')}".strip()

    doc_id = writer.create_doc(title)
    blocks = writer.markdown_to_blocks(safe_md)       # 内部二次文本脱敏
    writer.write_blocks(doc_id, blocks)
    return {"status": "ok", "document_id": doc_id, "title": title,
            "demo_fingerprint": fp, "note": "已脱敏；文档默认不公开分享。"}


def _main() -> None:
    ap = argparse.ArgumentParser(description="周报/月报写入飞书（默认关闭，需配置）")
    ap.add_argument("--markdown", default="weekly_report.md",
                    help="飞书通道载荷的 Markdown 文件：render_report.py --keep-md 产物或自备文件"
                         "（默认 weekly_report.md，即 --keep-md 的默认产物）")
    ap.add_argument("--computed", default="computed.json")
    ap.add_argument("--title", default=None)
    ap.add_argument("--override-demo", action="store_true",
                    help="显式放行演示数据写入（仍加水印）；默认阻断")
    args = ap.parse_args()

    result = push(args.markdown, args.computed, override_demo=args.override_demo, title=args.title)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()