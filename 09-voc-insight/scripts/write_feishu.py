#!/usr/bin/env python3
"""评论 VOC 归因 —— Step 6（可选）：把 VOC 报告写入飞书。

复用 ``_shared/scripts/feishu_writer.py``。安全约束（铁律 8/11）：
- **演示环境默认阻断**：``guard_demo`` 命中指纹即抛错；确需写入须 ``--override-demo``，
  且报告顶部已由 render_report 加水印。
- **PII 脱敏**：写前对整份内容做一次脱敏兜底（``mask_pii_in_text``），
  本报告通常不含订单 PII，但评论原文可能夹带昵称/邮箱，兜底防漏。
- **写后 read-back 校验**：写入后回读文档，确认块数与关键锚点存在（写操作安全要求）。

用法：
    python scripts/write_feishu.py --report out/voc-report.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import voc_common as vc

SKILL_DIR = Path(__file__).resolve().parents[1]


def _p(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (SKILL_DIR / p)


def main() -> int:
    ap = argparse.ArgumentParser(description="把 VOC 报告写入飞书（演示环境默认阻断，写后 read-back）")
    ap.add_argument("--report", default="out/voc-report.md")
    ap.add_argument("--title", default=None)
    ap.add_argument("--override-demo", action="store_true")
    args = ap.parse_args()

    report_path = _p(args.report)
    if not report_path.exists():
        print(f"❌ 找不到报告：{report_path}")
        return 2
    markdown = report_path.read_text(encoding="utf-8")

    vc._shared_import()
    from feishu_writer import FeishuError, FeishuWriter, mask_pii_in_text  # type: ignore

    # 1) PII 兜底脱敏
    markdown = mask_pii_in_text(markdown)

    voc_cfg = vc.load_skill_config("voc")
    folder = (voc_cfg.get("delivery") or {}).get("feishu_folder_token") or None

    try:
        writer = FeishuWriter(folder_token=folder)
    except FeishuError as exc:
        print(f"⚠️ 无法初始化飞书写入（缺少 FEISHU_APP_ID / FEISHU_APP_SECRET）：{exc}")
        print("   报告已保留在本地：", report_path)
        return 3

    # 2) 演示环境闸门
    try:
        fp = writer.guard_demo(markdown, override=args.override_demo)
    except FeishuError as exc:
        print(f"⛔ 演示环境阻断写入：{exc}")
        print("   如需强制写入，请显式传 --override-demo（文档仍带演示水印）。")
        return 4
    if fp:
        print(f"⚠️ 检测到演示指纹「{fp}」：已按 override 放行，文档将带演示水印。")

    # 3) 创建文档 + 写入
    title = args.title or f"评论 VOC 归因报告（{report_path.stem}）"
    doc_id = writer.create_doc(title)
    blocks = writer.markdown_to_blocks(markdown)
    writer.write_blocks(doc_id, blocks)
    print(f"✅ 已写入飞书：document_id={doc_id}（块数 {len(blocks)}）")

    # 4) 写后 read-back 校验（写操作安全：确认锚点确实落盘）
    anchors = ["## 2.", "## 4.", "本技能不做什么"]
    try:
        back = _read_back(writer, doc_id)
        if back is None:
            print("⚠️ 写后 read-back 未取到内容：请人工在文档中确认完整性。")
        else:
            missing = [a for a in anchors if a.replace("## ", "") not in back]
            if missing:
                print(f"⚠️ 写后校验发现缺失锚点：{missing} —— 请人工核查文档完整性。")
            else:
                print("✅ 写后 read-back 校验通过（关键区块齐全）。")
    except Exception as exc:
        print(f"⚠️ 写后校验失败（不影响已写入内容）：{exc}")
    return 0


def _read_back(writer, document_id: str):
    """回读文档 blocks 并拼出纯文本，用于写后校验。

    ``feishu_writer.FeishuWriter`` 未内置 read_back，这里直接走其私有
    ``_request`` / ``_tenant_token``（feishu_writer 的公共 API 未变时稳定）。
    """
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/blocks?page_size=500"
    token = writer._tenant_token()
    resp = writer._request("GET", url, None, token=token)
    if resp.get("code") not in (0, None):
        return None
    items = (resp.get("data") or {}).get("items") or []
    chunks = []
    for block in items:
        for key in ("text", "heading1", "heading2", "heading3", "heading4", "bullet", "quote"):
            node = block.get(key)
            if isinstance(node, dict):
                for el in node.get("elements") or []:
                    run = (el or {}).get("text_run") or {}
                    if run.get("content"):
                        chunks.append(str(run["content"]))
    return "\n".join(chunks)


if __name__ == "__main__":
    sys.exit(main())
