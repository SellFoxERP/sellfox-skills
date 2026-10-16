"""Step 4：产出「素材需求单」asset-brief.json。

本技能与生图/生视频 MCP **完全解耦**：这里只产出**需求描述 + 提示词**（JSON 契约），
真正的生图由 AI 助手 按 ``references/asset-brief-spec.md`` 的适配器契约调用外部 MCP。

契约（见 references/asset-brief-spec.md）::

    {
      "product": {"name","asin_ref","category","marketplace"},
      "assets": [
        {"id","type","role","spec",{"prompt","negative_prompt"}},
        {"id","type":"video","role","spec",{"prompt","storyboard":[...]}}
      ]
    }

设计要点：
- **只描述、不生成**。生成前必须由 ``estimate_generation`` 报数量与成本待确认。
- 规格（尺寸/时长/比例）一律来自 ``config/listing_spec.yaml``，缺失则标「需人工核查」。
- 提示词是**模板起点**，最终由 AI 助手 结合商品卖点补全；此处保证结构完整。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from _common import Notes, load_skill_yaml

DEFAULT_NEGATIVE = "文字, 水印, 边框, logo, 人物肖像, 侵权图案, 变形, 模糊"


def _spec_block(assets_cfg: Dict[str, Any], key: str, notes: Notes, label: str) -> Dict[str, Any]:
    """取某类素材规格；缺失字段标「需人工核查」而不是编造。"""
    block = assets_cfg.get(key) or {}
    if not block:
        notes.need_manual(f"素材规格 listing_spec.assets.{key}（{label}）未配置，需人工核查")
    for field, value in block.items():
        if value is None:
            notes.need_manual(f"{label}的 {field} 未配置，需人工核查")
    return dict(block)


def _prompt_for(role: str, product_name: str, marketplace: str, style: str) -> str:
    """生成提示词模板（占位符 {product} 由 AI 助手 结合卖点补全）。"""
    base = {
        "主图": f"专业电商主图，纯白背景，居中展示 {product_name}，"
                f"柔和均匀布光，无阴影干扰，商品清晰锐利，商业摄影质感",
        "场景图": f"{product_name} 的真实使用场景，自然光与暖色调，"
                  "突出卖点与尺寸感，构图整洁，留白适中，生活方式摄影风格",
        "A+模块图": f"{product_name} 的功能特写/细节图，展示材质与工艺，"
                    f"可配合图标化信息排版，风格统一，适配 {marketplace} 站点审美",
        "信息图": f"{product_name} 的卖点信息图，图文排版清晰，突出核心参数，避免绝对化用语",
        "生活方式图": f"人物在真实生活场景中使用 {product_name}，情绪自然，"
                      "不出现可识别人脸与品牌 logo",
    }
    return base.get(role, f"{product_name} 的电商素材，{style} 风格，清晰专业")


def build_brief(
    *,
    product_name: str,
    marketplace: str,
    asin_ref: Optional[str] = None,
    category: Optional[str] = None,
    plan: Optional[Dict[str, Any]] = None,
    notes: Optional[Notes] = None,
) -> Dict[str, Any]:
    """按素材计划展开 asset-brief.json。

    :param plan: ``config/launch.yaml`` 的 ``asset_plan``；缺省用保守默认。
    """
    notes = notes or Notes()
    spec = load_skill_yaml("listing_spec")
    assets_cfg = spec.get("assets") or {}
    launch = load_skill_yaml("launch")
    style = (launch.get("adapter") or {}).get("prompt_style", "ecommerce")

    plan = plan or launch.get("asset_plan") or {}
    n_main = int(plan.get("main_image", 1) or 0)
    n_scene = int(plan.get("scene_image", 0) or 0)
    n_aplus = int(plan.get("aplus_module", 0) or 0)
    n_video = int(plan.get("video", 0) or 0)
    n_info = int(plan.get("infographic", 0) or 0)
    n_life = int(plan.get("lifestyle", 0) or 0)

    assets: List[Dict[str, Any]] = []

    main_spec = _spec_block(assets_cfg, "main_image", notes, "主图")
    for i in range(1, n_main + 1):
        assets.append({
            "id": f"main-{i:02d}", "type": "image", "role": "主图",
            "spec": main_spec,
            "prompt": _prompt_for("主图", product_name, marketplace, style),
            "negative_prompt": DEFAULT_NEGATIVE,
        })

    scene_spec = _spec_block(assets_cfg, "scene_image", notes, "场景图")
    for i in range(1, n_scene + 1):
        assets.append({
            "id": f"scene-{i:02d}", "type": "image", "role": "场景图",
            "spec": scene_spec,
            "prompt": _prompt_for("场景图", product_name, marketplace, style)
                      + f"（第 {i} 个角度/场景，避免与前一张雷同）",
            "negative_prompt": DEFAULT_NEGATIVE,
        })

    aplus_spec = _spec_block(assets_cfg, "aplus_module", notes, "A+模块图")
    for i in range(1, n_aplus + 1):
        assets.append({
            "id": f"aplus-{i:02d}", "type": "image", "role": "A+模块图",
            "spec": aplus_spec,
            "prompt": _prompt_for("A+模块图", product_name, marketplace, style)
                      + f"（第 {i} 个模块）",
            "negative_prompt": DEFAULT_NEGATIVE,
        })

    for i in range(1, n_info + 1):
        assets.append({
            "id": f"info-{i:02d}", "type": "image", "role": "信息图",
            "spec": {"aspect": "1:1"},
            "prompt": _prompt_for("信息图", product_name, marketplace, style),
            "negative_prompt": DEFAULT_NEGATIVE,
        })

    for i in range(1, n_life + 1):
        assets.append({
            "id": f"life-{i:02d}", "type": "image", "role": "生活方式图",
            "spec": {"aspect": "1:1"},
            "prompt": _prompt_for("生活方式图", product_name, marketplace, style),
            "negative_prompt": DEFAULT_NEGATIVE,
        })

    video_spec = _spec_block(assets_cfg, "video", notes, "主图视频")
    for i in range(1, n_video + 1):
        storyboard = [
            {"t": "0-3s", "shot": "产品特写", "prompt": f"{product_name} 正面特写，快速抓住注意力"},
            {"t": "3-10s", "shot": "核心卖点", "prompt": "演示 1~2 个核心卖点，字幕简洁"},
            {"t": "10-20s", "shot": "使用场景", "prompt": f"{product_name} 在真实场景中的使用过程"},
            {"t": "20-30s", "shot": "收尾/规格", "prompt": "尺寸/规格/包装展示，品牌收尾"},
        ]
        assets.append({
            "id": f"video-{i:02d}", "type": "video", "role": "主图视频",
            "spec": video_spec,
            "prompt": f"{product_name} 的 15~30 秒带货短视频脚本，保持商品原图不变形",
            "storyboard": storyboard,
        })

    if not assets:
        notes.warn("素材计划为空：asset_plan 未配置任何素材，请检查 config/launch.yaml")

    return {
        "product": {
            "name": product_name,
            "asin_ref": asin_ref,
            "category": category,
            "marketplace": marketplace,
        },
        "assets": assets,
    }


def count_assets(brief: Dict[str, Any]) -> Dict[str, int]:
    """统计各类素材数量（成本闸的输入）。"""
    counts = {"image": 0, "video": 0}
    for a in brief.get("assets", []):
        t = a.get("type", "image")
        counts[t] = counts.get(t, 0) + 1
    counts["total"] = sum(counts.values())
    return counts


def estimate_generation(brief: Dict[str, Any], notes: Optional[Notes] = None) -> Dict[str, Any]:
    """估算生成成本 —— **只报数与估算，不执行任何生成**。

    单价来自 ``config/launch.yaml → estimator``。未配置单价时标「需人工核查」，
    绝不按 0 元估算（0 和「没拿到」是两回事）。
    """
    notes = notes or Notes()
    launch = load_skill_yaml("launch")
    est = launch.get("estimator") or {}
    currency = est.get("currency", "USD")
    unit_img = est.get("cost_per_image")
    unit_vid = est.get("cost_per_video")
    counts = count_assets(brief)

    items: List[Dict[str, Any]] = []
    total: Optional[float] = 0.0
    for kind, unit in (("image", unit_img), ("video", unit_vid)):
        n = counts.get(kind, 0)
        if n == 0:
            continue
        if unit is None:
            notes.need_manual(f"未配置{kind}单价（launch.yaml→estimator），本次成本无法估算，需人工核查")
            items.append({"type": kind, "count": n, "unit_cost": None,
                          "subtotal": None, "currency": currency})
            total = None
        else:
            subtotal = float(unit) * n
            items.append({"type": kind, "count": n, "unit_cost": unit,
                          "subtotal": round(subtotal, 2), "currency": currency})
            if total is not None:
                total += subtotal

    return {
        "counts": counts,
        "items": items,
        "estimated_total": None if total is None else round(total, 2),
        "currency": currency,
        "require_confirmation": bool(est.get("require_confirmation", True)),
        "note": "实际计费以所选生图/生视频 MCP 为准；此处仅作数量与量级确认。",
    }


def _main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description="产出素材需求单 asset-brief.json")
    ap.add_argument("--name", required=True, help="产品名")
    ap.add_argument("--marketplace", default="US")
    ap.add_argument("--asin")
    ap.add_argument("--category")
    ap.add_argument("--out")
    args = ap.parse_args()

    notes = Notes()
    brief = build_brief(
        product_name=args.name, marketplace=args.marketplace,
        asin_ref=args.asin, category=args.category, notes=notes,
    )
    est = estimate_generation(brief, notes)
    payload = {"asset_brief": brief, "generation_estimate": est,
               "manual_checks": notes.manual_checks, "warnings": notes.warnings}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())