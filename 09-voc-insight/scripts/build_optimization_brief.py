#!/usr/bin/env python3
"""评论 VOC 归因 —— Step 5（可选）：把「已印证的模式」转成优化素材需求单。

与技能 8 同构：本脚本**只产出素材需求单（描述），不直接生图**。
生图按次收费，须先过成本闸（``optimization_brief.enabled`` 且需人工确认）。

触发条件：某主题被评为 **pattern**（多源印证）且属于 ``themes_to_brief``
（默认 sizing / color / description —— 这三类可自主修复）。

用法：
    python scripts/build_optimization_brief.py --analysis out/voc_analysis.json \
        --out out/optimization-brief.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List

import voc_common as vc

SKILL_DIR = Path(__file__).resolve().parents[1]

# 主题 → 素材类型与提示词模板（**只描述需求**，正文由 AI 助手/设计师补全）。
_ASSET_TEMPLATES: Dict[str, Dict[str, str]] = {
    "sizing": {
        "type": "尺寸对比图 / 尺码表",
        "prompt": "生成一张清晰的尺寸示意图：标注产品关键尺寸（长宽高/适用体型），"
                  "与常见参照物对比，帮助买家正确选择尺码。风格与现主图一致。",
    },
    "color": {
        "type": "多角度实拍 / 主图色差校准",
        "prompt": "生成/校准产品主图：在标准白光下呈现真实颜色与材质，"
                  "避免渲染导致的色差；补充 2~3 个不同光线的角度图。",
    },
    "description": {
        "type": "A+ / 详情页文案与场景图",
        "prompt": "生成场景图或 A+ 模块：直观呈现产品的实际用途、材质、随附配件，"
                  "使买家在下单前对产品有一致预期（针对「描述不符」类差评）。",
    },
}


def run(args: argparse.Namespace) -> int:
    voc_cfg = vc.load_skill_config("voc")
    ob = voc_cfg.get("optimization_brief") or {}

    p = Path(args.analysis)
    if not p.is_absolute():
        p = SKILL_DIR / p
    analysis = vc.read_json(str(p)) if p.exists() else {}

    if not ob.get("enabled"):
        payload = {
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "enabled": False,
            "note": "optimization_brief.enabled=false：未产出素材需求单（默认关闭，避免产生生图费用）。",
            "briefs": [],
        }
        vc.write_json(args.out, payload)
        print(payload["note"])
        return 0

    themes_to_brief = set(ob.get("themes_to_brief") or ["sizing", "color", "description"])
    patterns = analysis.get("patterns") or []
    briefs: List[Dict[str, Any]] = []
    for r in patterns:
        theme = r.get("theme")
        if theme not in themes_to_brief:
            continue
        tpl = _ASSET_TEMPLATES.get(theme)
        if not tpl:
            continue
        briefs.append({
            "id": f"voc-{theme}",
            "theme": theme,
            "label": r.get("label"),
            "type": tpl["type"],
            "prompt": tpl["prompt"],
            "evidence": {
                "review_count": r.get("review_count"),
                "distinct_reviewers": r.get("distinct_reviewers"),
                "quotes": r.get("quotes") or [],
            },
            "_source": "外部评论原文（多源印证）",
        })

    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "enabled": True,
        "adapter": ob.get("adapter") or "",
        "briefs": briefs,
        "counts": {"briefs": len(briefs)},
        "cost_gate": {
            "required": True,
            "note": "⚠️ 生图按次收费：本需求单**只描述、不生成**。生成前必须报数量与成本并经人工确认。",
            "est_calls": len(briefs),
            "unit_price": None,
            "est_total": None,
        },
    }
    vc.write_json(args.out, payload)
    print(f"素材需求单：{len(briefs)} 条（只描述，不生成；生成前需人工确认成本）")
    print(f"已写出 {args.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把已印证模式转成优化素材需求单（只描述不生图）")
    ap.add_argument("--analysis", default="out/voc_analysis.json")
    ap.add_argument("--out", default="out/optimization-brief.json")
    import sys
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
