"""技能 08-listing-launch 主编排脚本。

把「检查落盘 → 基线组装 → 文案骨架 → 素材需求单 → 成本闸 → 合规检查 → 定价测算 → 渲染」串起来。

用法示例::

    # 1) agent 先按 references/workflow.md 取数计划，把两个信封落盘：
    #    out/raw/online_product.json / out/raw/product_analyze.json
    # 2) 一键产出上架包（零网络，不需要 MCP Key）：
    python3 scripts/listing_launch.py \\
        --search-type asin --search-content B0XXXXXXXX \\
        --name "折叠收纳凳" --marketplace US \\
        --copy-json copy.json --out out/listing-package.md

设计要点：
- **v3 分工**：取数面 = agent 调 `mcp__Sellfox-MCP__{help,search,action}` 落盘 `out/raw/`；
  本脚本（计算面）零网络，只做「检查落盘 → fetch_baseline 校验组装」。
- **只读 + 外部 MCP**：本脚本不调用任何赛狐写工具；生图/生视频由 AI 助手 按
  references/asset-brief-spec.md 的适配器契约执行，且必须先过「成本确认闸」。
- **缺落盘也能跑**：`out/raw/` 缺信封时降级为「需人工核查」，仍产出结构完整的上架包
  （沿袭旧「无 Key 离线骨架」语义）。
- **演示环境**：命中指纹即在包顶加水印，并默认阻断写飞书。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from _common import Notes, SKILL_ROOT, LaunchError, load_skill_yaml
from build_asset_brief import build_brief, count_assets, estimate_generation
from check_compliance import check_compliance, validate_copy_against_spec
from estimate_pricing import estimate_pricing
from fetch_baseline import DEFAULT_RAW_DIR, RAW_STEPS, build_baseline
from render_package import render_package


def draft_copy_skeleton(product: Dict[str, Any], marketplace: str, notes: Notes) -> Dict[str, Any]:
    """文案骨架：脚本不编造营销文案，只给出可编辑结构；正文由 AI 助手 补全。"""
    base_title = product.get("title")
    if base_title:
        title = f"{base_title}"
    else:
        title = None
        notes.need_manual("原始标题取不到，标题文案需人工核查/由 AI 助手 生成")
    notes.need_manual("Listing 文案（标题/五点/搜索词/A+）由 AI 助手 按 references/amazon-listing-spec.md 生成后人工复核")
    return {
        "title": title,
        "bullets": [],
        "search_terms": None,
        "description": None,
        "aplus": None,
        "_status": "骨架（待补全）",
    }


def check_cost_gate(
    estimate: Dict[str, Any], *, confirmed: bool, notes: Notes
) -> Dict[str, Any]:
    """生成前成本确认闸：报数量与成本，未确认则不放行。"""
    counts = estimate.get("counts", {})
    total = estimate.get("estimated_total")
    gate = {
        "required": bool(estimate.get("require_confirmation", True)),
        "confirmed": confirmed,
        "counts": counts,
        "estimated_total": total,
        "currency": estimate.get("currency"),
    }
    if gate["required"] and not confirmed:
        gate["action"] = "HOLD"
        notes.warn(
            f"⏸ 生图/生视频按次收费：本次计划生成 {counts.get('total', 0)} 个素材"
            f"（图 {counts.get('image', 0)} + 视频 {counts.get('video', 0)}），"
            "预估成本见上。**未经人工确认前不得批量生成**（传 --confirm-assets 表示已确认）。"
        )
    else:
        gate["action"] = "PROCEED"
    return gate


def run(args: argparse.Namespace) -> int:
    notes = Notes()

    # 0) 检查落盘（v3：取数面由 agent 完成，本脚本零网络）。
    raw_dir = Path(args.raw_dir) if args.raw_dir else DEFAULT_RAW_DIR
    missing = [f"{step}.json" for step in RAW_STEPS if not (raw_dir / f"{step}.json").exists()]
    if missing:
        notes.warn(
            f"缺少落盘 {missing}：v3 取数面由 agent 完成——请按 references/workflow.md 的"
            "「取数计划」调 mcp__Sellfox-MCP__{search,action} 并把完整信封落盘 "
            f"{raw_dir}/ 后重跑。本次实时字段标「需人工核查」，骨架照出。"
        )

    # 1) fetch_baseline：校验落盘 + 组装基线（零网络；缺落盘时降级，不崩）。
    #    asin 检索时把目标 ASIN 传下去做 analysis 行本地关联（上游过滤未生效的兜底）。
    target_asins = [args.search_content] if args.search_type == "asin" else None
    try:
        baseline = build_baseline(raw_dir=raw_dir, notes=notes, target_asins=target_asins)
    except Exception as exc:
        baseline = {"products": [], "analysis": [], "demo_fingerprint": None}
        notes.need_manual(f"基线组装失败（{exc}）：实时字段需人工核查")

    keys = {"asin": "asin", "parentAsin": "parent_asin", "msku": "msku", "sku": "sku", "title": "title", "fnsku": "fnsku", "commodityName": "commodity_name"}
    field = keys.get(args.search_type)
    matches = [p for p in baseline.get("products", []) if field and str(p.get(field) or "") == args.search_content]
    if len(matches) == 1:
        product = matches[0]
    else:
        product = {}
        notes.need_manual(f"目标 {args.search_content} 精确匹配 {len(matches)} 个商品；不采用首行价格或属性，请明确唯一商品。")

    # 文案：优先用外部传入（AI 助手 生成后落盘），否则用骨架。
    if args.copy_json and Path(args.copy_json).exists():
        copy = json.loads(Path(args.copy_json).read_text(encoding="utf-8"))
    else:
        copy = draft_copy_skeleton(product, args.marketplace, notes)

    # 素材需求单 + 成本估算。
    brief = build_brief(
        product_name=args.name,
        marketplace=args.marketplace,
        asin_ref=product.get("asin") or args.search_content,
        category=args.category,
        notes=notes,
    )
    estimate = estimate_generation(brief, notes)
    gate = check_cost_gate(estimate, confirmed=bool(args.confirm_assets), notes=notes)

    # 合规检查（永不写入）+ 字符校验。
    compliance = check_compliance(copy=copy, brief=brief, notes=notes)
    spec_check = validate_copy_against_spec(
        copy, marketplace=args.marketplace, category=args.category, notes=notes
    )

    # 定价测算。
    pricing = estimate_pricing(product, notes=notes)

    # 渲染。
    md = render_package(
        product_name=args.name,
        marketplace=args.marketplace,
        copy=copy,
        brief=brief,
        generation_estimate=estimate,
        compliance=compliance,
        spec_check=spec_check,
        pricing=pricing,
        baseline=baseline,
        notes=notes,
    )

    out_dir = SKILL_ROOT / ((load_skill_yaml("launch").get("delivery") or {}).get("local_out_dir", "out"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = Path(args.out) if args.out else out_dir / f"listing-package-{stamp}.md"
    out_path.write_text(md, encoding="utf-8")

    # 附件：需求单 + 各步骤结构化结果，便于 AI 助手 后续调用外部 MCP。
    brief_path = out_path.with_suffix(".asset-brief.json")
    brief_path.write_text(
        json.dumps(
            {"asset_brief": brief, "generation_estimate": estimate, "cost_gate": gate},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    print(md)
    print(f"\n---\n已写入：{out_path}\n素材需求单：{brief_path}")
    print(f"成本闸：{gate['action']}（需确认={gate['required']}，已确认={gate['confirmed']}）")

    # 可选：写飞书（演示环境默认阻断）。
    if args.write_feishu:
        try:
            from _common import add_shared_to_path, resolve_shared_root  # 局部导入
            add_shared_to_path(resolve_shared_root(args.shared_root))
            from feishu_writer import FeishuWriter  # type: ignore

            writer = FeishuWriter()
            writer.guard_demo(baseline, override=bool(args.allow_demo_write))  # 命中演示→默认抛错
            doc_id = writer.create_doc(f"Listing Package: {args.name} {args.marketplace}")
            writer.write_blocks(doc_id, writer.markdown_to_blocks(md))
            print(f"已写入飞书文档：{doc_id}")
        except Exception as exc:
            notes.warn(f"写飞书失败/被拦截：{exc}")
            print(f"⚠️ 写飞书未完成：{exc}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="生成商品上架素材包（v3：agent 落盘 + 脚本零网络）")
    ap.add_argument("--search-type", default="asin",
                    help="asin/parentAsin/msku/sku/title/fnsku/commodityName（用于取数计划与 asin_ref）")
    ap.add_argument("--search-content", required=True, help="搜索内容（如 ASIN）")
    ap.add_argument("--name", required=True, help="产品名")
    ap.add_argument("--marketplace", default="US")
    ap.add_argument("--category", default=None)
    ap.add_argument("--raw-dir", default=None,
                    help="agent 落盘目录（默认 <skill>/out/raw；shop_ids/max_rows 等取数参数在取数计划里控制）")
    ap.add_argument("--copy-json", default=None, help="AI 助手 生成后的文案 JSON（可选）")
    ap.add_argument("--out", default=None, help="上架包输出路径")
    ap.add_argument("--confirm-assets", action="store_true", help="已确认素材数量与成本，放行生成")
    ap.add_argument("--write-feishu", action="store_true")
    ap.add_argument("--allow-demo-write", action="store_true", help="演示环境强制写飞书（危险）")
    ap.add_argument("--shared-root", default=None)
    return ap


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except LaunchError as exc:
        print(f"错误：{exc}")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())