"""Step 8：把各步骤结果渲染成「上架素材包」Markdown（严格按模板）。

模板见 ``references/listing-package-template.md``。本模块只做渲染，不取数、不写系统。
演示环境命中的水印、需人工核查项、数据来源标注都必须出现在输出里。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import Notes, ensure_shared_path


def _bullets_md(bullets: Optional[List[str]]) -> str:
    if not bullets:
        return "- ⚠️ 五点描述待补全（需人工核查/由 AI 助手 生成）"
    return "\n".join(f"{i}. {b}" for i, b in enumerate(bullets, 1))


def _assets_md(brief: Optional[Dict[str, Any]]) -> str:
    if not brief or not brief.get("assets"):
        return "⚠️ 无素材需求单（asset_plan 为空或未执行 build_asset_brief）"
    lines = ["| id | 类型 | 角色 | 关键规格 | 提示词（摘要） |", "|---|---|---|---|---|"]
    for a in brief["assets"]:
        spec = a.get("spec") or {}
        spec_brief = ", ".join(f"{k}={v}" for k, v in spec.items() if v is not None)
        prompt = str(a.get("prompt", ""))[:60].replace("\n", " ")
        lines.append(f"| {a.get('id')} | {a.get('type')} | {a.get('role')} | "
                     f"{spec_brief or '⚠️需人工核查'} | {prompt}… |")
    return "\n".join(lines)


def _compliance_md(compliance: Optional[Dict[str, Any]], spec_check: Optional[Dict[str, Any]]) -> str:
    if not compliance:
        return "⚠️ 未执行合规检查"
    parts: List[str] = []
    hits = compliance.get("banned_word_hits") or []
    if hits:
        parts.append("**禁用/高风险表述命中：**")
        for h in hits:
            parts.append(f"- `{h['word']}` 出现在 **{h['field']}**")
    else:
        parts.append("- 未命中已配置的禁词表（不代表平台一定通过，仍需人工复核）")
    parts.append(f"\n> {compliance.get('disclaimer', '')}")
    if compliance.get("ai_label_required"):
        parts.append(f"\n**AI 素材披露提示：** {compliance.get('ai_disclosure', '')}")

    if spec_check:
        parts.append("\n**字符/字节校验：**")
        for c in spec_check.get("checks", []):
            limit = c.get("limit")
            limit_s = str(limit) if limit is not None else "⚠️需人工核查"
            parts.append(f"- {c['field']}: {c['size']} {c['unit']} / 上限 {limit_s} → {c['status']}")
    return "\n".join(parts)


def _pricing_md(pricing: Optional[Dict[str, Any]]) -> str:
    if not pricing:
        return "⚠️ 未执行定价测算"
    def display(key, suffix=""):
        value = pricing.get(key)
        return "无数据（需人工核查）" if value is None else f"{value}{suffix}"
    lines = [
        f"- 售价口径币种：**{display('price_currency')}**；成本口径币种：**{display('cost_currency')}**",
        f"- 售价：{display('selling_price')}",
        f"- 采购成本（换算是否可用见数据说明）：{display('purchase_cost')}",
        f"- 成本合计：{pricing.get('total_cost') if pricing.get('cost_complete') else '⚠️ 不完整，需人工核查'}",
        f"- 预估毛利：{display('estimated_profit')}；预估毛利率：{display('estimated_margin_pct', '%')}",
    ]
    headtrip_src = pricing.get("headtrip_source")
    if headtrip_src:
        headtrip = (pricing.get("cost_breakdown") or {}).get("headtrip")
        headtrip_s = "无数据（需人工核查）" if headtrip is None else str(headtrip)
        lines.append(f"- 头程：{headtrip_s}（来源：{headtrip_src}）")
    if pricing.get("suggested_price") is not None:
        lines.append(f"- 目标毛利率 {pricing.get('target_margin_pct')}% 倒推建议售价：**{pricing.get('suggested_price')}**")
    return "\n".join(lines)


def render_package(
    *,
    product_name: str,
    marketplace: str,
    copy: Optional[Dict[str, Any]] = None,
    brief: Optional[Dict[str, Any]] = None,
    generation_estimate: Optional[Dict[str, Any]] = None,
    compliance: Optional[Dict[str, Any]] = None,
    spec_check: Optional[Dict[str, Any]] = None,
    pricing: Optional[Dict[str, Any]] = None,
    baseline: Optional[Dict[str, Any]] = None,
    notes: Optional[Notes] = None,
    date_str: Optional[str] = None,
) -> str:
    """渲染完整上架包 Markdown。"""
    notes = notes or Notes()
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    copy = copy or {}

    header = f"📦 Listing Package: {product_name} | {marketplace} | {date_str}"
    blocks: List[str] = []

    demo_fp = (baseline or {}).get("demo_fingerprint")
    if demo_fp:
        blocks.append(f"> ⚠️ **演示数据，非真实经营数据**（指纹：{demo_fp}）。本包不得作为真实定价/文案依据。")

    blocks.append(f"# {header}\n")

    # 1. 市场与竞品调研（外部 MCP 未接入时标人工核查）
    blocks.append("## 1. 市场与竞品调研")
    analysis = (baseline or {}).get("analysis") or []
    if analysis:
        blocks.append(f"- 赛狐商品分析记录：{len(analysis)} 条（含类目/BSR/上架时间）")
    else:
        blocks.append("- ⚠️ 需人工核查：竞品/关键词情报需外部 MCP（Tavily/Sorftime/Keepa）或人工调研补充")
    blocks.append("")

    # 2. Listing 文案
    blocks.append("## 2. Listing 文案")
    blocks.append(f"**标题 Title：** {copy.get('title') or '⚠️ 待补全'}")
    blocks.append(f"\n**五点 Bullets：**\n{_bullets_md(copy.get('bullets'))}")
    blocks.append(f"\n**搜索词 Search Terms：** {copy.get('search_terms') or '⚠️ 待补全'}")
    blocks.append(f"\n**商品描述 / A+：** {copy.get('description') or '⚠️ 待补全'}")
    blocks.append("")

    # 3. 素材需求单
    blocks.append("## 3. 素材需求单 asset-brief.json")
    blocks.append(_assets_md(brief))
    if generation_estimate:
        counts = generation_estimate.get("counts", {})
        total = generation_estimate.get("estimated_total")
        total_s = f"{total} {generation_estimate.get('currency')}" if total is not None else "⚠️ 需人工核查（未配置单价）"
        blocks.append(f"\n**生成数量：** 图片 {counts.get('image', 0)} 张 + 视频 {counts.get('video', 0)} 个"
                      f"（合计 {counts.get('total', 0)}）｜**预估成本：** {total_s}")
        blocks.append(f"> {generation_estimate.get('note', '')}")
    blocks.append("")

    # 4. 生成的素材（外部 MCP 产出，本技能不直接生成）
    blocks.append("## 4. 生成的素材文件")
    blocks.append("- ⚠️ 生图/生视频由外部 MCP 完成；执行前须经数量与成本确认闸。未确认时本区块留空。")
    blocks.append("")

    # 5. 合规检查
    blocks.append("## 5. 合规检查清单（写前咨询，永不写入）")
    blocks.append(_compliance_md(compliance, spec_check))
    blocks.append("")

    # 6. 定价与利润测算
    blocks.append("## 6. 定价与利润测算")
    blocks.append(_pricing_md(pricing))
    blocks.append("")

    # 7. 待人工确认项
    blocks.append("## 7. ⚠️ 待人工确认项")
    if notes.manual_checks:
        blocks.extend(f"- {m}" for m in notes.manual_checks)
    else:
        blocks.append("- （无）")
    blocks.append("")

    # 数据说明 / 边界
    blocks.append("---")
    blocks.append("### ⚠️ 本技能未能覆盖 / 需人工核查（诚实边界）")
    blocks.append("- **不能自动刊登商品**：赛狐 MCP 无创建/修改 Listing 的接口，最后一公里由人工在后台完成。")
    blocks.append("- **字符/字节限制**：亚马逊各品类确切限制本次未验证，须按目标站点最新官方要求核对。")
    blocks.append("- **商标/专利/资质**：本技能无相关数据库访问能力，仅提示、不下结论。")
    blocks.append("- **评论原文**：赛狐仅有聚合指标（rating/ratingCount），无评论内容。")
    blocks.append("- **头程成本**：优先取商品行 `headTripCost`（get_online_product_page_list，实测返回）；缺值时需接自定义报表宽表（`headTripPrice`）或人工核查。")
    if notes.warnings:
        blocks.append("\n### 数据说明与警示")
        blocks.extend(f"- {w}" for w in notes.warnings)
    blocks.append(f"\n数据日期：{date_str} ｜ 数据源：赛狐 ERP（SellFox MCP）")
    return "\n".join(blocks)


def _main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description="渲染上架素材包 Markdown")
    ap.add_argument("--input-json", required=True, help="包含各步骤结果的 JSON")
    ap.add_argument("--out")
    ap.add_argument("--keep-md", action="store_true",
                    help="额外留档同名 .md（默认只写 .html，仅供调试/飞书载荷）")
    args = ap.parse_args()
    payload = json.loads(open(args.input_json, encoding="utf-8").read())
    md = render_package(
        product_name=payload.get("product_name", "未命名商品"),
        marketplace=payload.get("marketplace", "US"),
        copy=payload.get("copy"),
        brief=payload.get("asset_brief"),
        generation_estimate=payload.get("generation_estimate"),
        compliance=payload.get("compliance"),
        spec_check=payload.get("copy_spec_check"),
        pricing=payload.get("pricing"),
        baseline=payload.get("baseline"),
    )
    if args.out:
        out_path = Path(args.out)
        html_path = None
        if ensure_shared_path():  # HTML 主报告出口（仅同名 .html；--keep-md 才附 .md）
            from report_html import write_html_report  # type: ignore  # 由 _shared 提供

            html_path = write_html_report(md, out_path, keep_md=args.keep_md)
        else:  # pragma: no cover  # _shared 缺位时降级为只写 md
            out_path.write_text(md, encoding="utf-8")
        print(f"已写入 {html_path or out_path}")
    else:
        print(md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())