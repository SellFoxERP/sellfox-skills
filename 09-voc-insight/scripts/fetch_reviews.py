#!/usr/bin/env python3
"""评论 VOC 归因 —— Step 2：取评论原文（外部 MCP，可插拔 adapter）。

本脚本**不内置任何评论源**，也不自己发 HTTP 到外部服务。它做三件事：

1. **成本闸**：根据「问题 ASIN 数」× adapter 的计费口径，算出预计调用次数/条数，
   未确认（未传 ``--confirm-external``）时输出 ``HOLD``，**不得继续**。外部源按次/按条收费。
2. **规范化 + 脱敏**：把 AI 助手（或人工）按 adapter 契约取回的原始评论，
   用 ``config/field-map.yaml → external_review_contract`` 规范化，并掩码评论者昵称。
3. **降级**：未接入 / 未确认 / 取回为空 → 不改写为「无差评」，而是标记 ``degraded``，
   由下游按聚合指标模式出报告（见 ``config/voc.yaml → degradation``）。

真正的 MCP 调用由 AI 助手 按 ``adapters/<你选的>.md`` 执行（串行），
再用 ``--reviews-json`` 把结果喂回本脚本。

用法：
    # 1) 看成本闸（不调外部，只报数）
    python scripts/fetch_reviews.py --signals out/signals.json --adapter-file adapters/example_voc_reviews.md

    # 2) 确认后，把 AI 助手 取回的评论规范化
    python scripts/fetch_reviews.py --signals out/signals.json \
        --reviews-json raw_reviews.json --confirm-external --out out/reviews.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import voc_common as vc

SKILL_DIR = Path(__file__).resolve().parents[1]

_REQUIRED = ("id", "asin", "rating", "text")


def _read_adapter(adapter_file: Optional[str]) -> Dict[str, Any]:
    """从 adapter 的 md 里解析首个 fenced ```yaml 块，取出契约字段。

    复用 _shared 的最小 YAML 解析器（``_mini_yaml_load``），因此含注释/嵌套都能正确解析。
    解析不到就返回空，由调用方标「需人工核查」。
    """
    if not adapter_file:
        return {}
    path = Path(adapter_file)
    if not path.is_absolute():
        path = SKILL_DIR / path
    if not path.exists():
        return {"_error": f"adapter 文件不存在：{path}"}

    text = path.read_text(encoding="utf-8")
    import re as _re

    m = _re.search(r"```ya?ml\s*\n(.*?)```", text, _re.DOTALL)
    block = m.group(1) if m else text
    try:
        vc._shared_import()
        from config_loader import _mini_yaml_load  # type: ignore
        data = _mini_yaml_load(block)
    except Exception as exc:
        return {"_error": f"adapter 解析失败：{exc}"}
    if not isinstance(data, dict):
        return {}
    data["_file"] = str(path)
    return data


def _review_cost(adapter: Dict[str, Any], est_reviews: int, est_calls: int) -> Dict[str, Any]:
    """估算成本。adapter 只给计费**口径名**，金额由 voc.yaml/使用者提供；未知即标需人工核查。"""
    cost_key = adapter.get("cost_key") or "需人工核查"
    return {
        "basis": cost_key,
        "est_calls": est_calls,
        "est_reviews": est_reviews,
        "unit_price": None,   # 本次调研无法得知你所选源的实际单价 → 不臆造
        "est_total": None,
        "currency": None,
        "note": "⚠️ 需人工核查：未配置外部评论源单价，无法估算金额。调用前请确认预算与合规性。",
    }


def _normalize(raw: Any, contract: Dict[str, Any], output_map: Dict[str, Any]) -> Dict[str, Any]:
    """把一条原始评论规范化为契约字段。

    先按 adapter 的 ``output_map``（MCP 字段 → 契约字段）重映射，再校验必需字段。
    缺必需字段则记为 invalid（不猜、不编造）。
    """
    if not isinstance(raw, dict):
        return {"valid": False, "reason": "非对象"}
    # adapter 输出映射：{契约字段: MCP字段名}
    mapped: Dict[str, Any] = {}
    for contract_field, src_field in (output_map or {}).items():
        if isinstance(src_field, str) and src_field in raw and raw.get(src_field) not in (None, ""):
            mapped[contract_field] = raw.get(src_field)
    # 未出现在 output_map 里的契约字段，按同名字段直取（宽容路径）
    for field in contract.get("required", []) + contract.get("optional", []):
        if field not in mapped and field in raw and raw.get(field) not in (None, ""):
            mapped[field] = raw.get(field)

    missing = [f for f in contract.get("required", []) if mapped.get(f) in (None, "")]
    if missing:
        return {"valid": False, "reason": f"缺必需字段：{','.join(missing)}", "raw": mapped}
    # 昵称脱敏（铁律 8）
    mapped["reviewer_name"] = vc.mask_reviewer(mapped.get("reviewer_name"))
    mapped["valid"] = True
    return mapped


def run(args: argparse.Namespace) -> int:
    voc_cfg = vc.load_skill_config("voc")
    ext_cfg = voc_cfg.get("external_reviews") or {}
    privacy = voc_cfg.get("privacy") or {}
    contract = (vc.load_skill_config("field-map") or {}).get("external_review_contract") or {}

    signals_path = Path(args.signals)
    if not signals_path.is_absolute():
        signals_path = SKILL_DIR / signals_path
    if not signals_path.exists():
        print(f"❌ 找不到 signals 文件：{signals_path}（先跑 fetch_signals.py）")
        return 2
    signals = vc.read_json(str(signals_path))
    flagged = signals.get("flagged") or []

    adapter = _read_adapter(args.adapter_file or ext_cfg.get("adapter"))
    max_calls = int(ext_cfg.get("max_calls_per_run", 40))
    max_reviews = int((voc_cfg.get("max_rows") or {}).get("review_fetch", 200))

    # ── 成本闸 ──
    est_calls = min(len(flagged), max_calls)
    est_reviews = est_calls * 1  # 每次调用至少取 1 条；实际条数由 adapter 的 limit 决定
    cost = _review_cost(adapter, est_reviews, est_calls)
    require_conf = bool(ext_cfg.get("require_confirmation", True))
    enabled = bool(ext_cfg.get("enabled"))
    connected = bool(adapter.get("mcp_name"))
    gate_action = "PROCEED"
    gate_reasons: List[str] = []

    if not enabled:
        gate_action = "HOLD"
        gate_reasons.append("external_reviews.enabled=false：未启用外部评论源。")
    if not connected:
        gate_action = "HOLD"
        gate_reasons.append("未配置 adapter（external_reviews.adapter 为空）：无可用的评论源。")
    if require_conf and not args.confirm_external:
        gate_action = "HOLD"
        gate_reasons.append(
            f"待确认：将对 {est_calls} 个 ASIN 发起外部调用（上限 {max_calls} 次），"
            f"计费口径 {cost['basis']}，金额需人工核查。传 --confirm-external 表示已确认。"
        )

    print("── 外部评论取数成本闸 ──")
    print(f"  adapter        : {adapter.get('mcp_name') or '（未配置）'}")
    print(f"  预计调用次数    : {est_calls}（上限 {max_calls}）")
    print(f"  计费口径        : {cost['basis']}")
    print(f"  预计金额        : ⚠️ 需人工核查（未配置单价）")
    print(f"  闸门状态        : {gate_action}")
    for r in gate_reasons:
        print(f"    · {r}")

    normalized: List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []
    degraded = "none"
    banner = ""

    if args.reviews_json:
        raw_path = Path(args.reviews_json)
        if not raw_path.is_absolute():
            raw_path = SKILL_DIR / raw_path
        try:
            raw = vc.read_json(str(raw_path))
        except Exception as exc:
            raw = []
            invalid.append({"reason": f"读取 reviews-json 失败：{exc}"})
        # 支持两种形态：{asin: [reviews]} 或平铺 [reviews]
        output_map = adapter.get("output_map") or {}
        flat: List[Dict[str, Any]] = []
        if isinstance(raw, dict):
            for asin, lst in raw.items():
                for item in (lst or []):
                    it = dict(item) if isinstance(item, dict) else {}
                    it.setdefault("asin", asin)
                    flat.append(it)
        elif isinstance(raw, list):
            flat = [x for x in raw if isinstance(x, dict)]
        for item in flat:
            norm = _normalize(item, contract, output_map)
            (normalized if norm.get("valid") else invalid).append(norm)
    else:
        # 没有原文 → 降级：不发外部调用（即使闸门 PROCEED，也需有数据才继续）
        degraded = "no_external_data"
        banner = (voc_cfg.get("degradation") or {}).get("no_external_reviews", {}).get("banner", "")
        print(f"\n{banner}")

    if gate_action == "HOLD" and not args.reviews_json:
        print("\n⏸ 闸门 HOLD 且无原文输入 → 本次按【聚合指标模式】继续，不做主题归因。")

    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "adapter": adapter,
        "gate": {"action": gate_action, "reasons": gate_reasons, "cost": cost},
        "source_signals": str(signals_path),
        "reviews": normalized,
        "invalid": invalid,
        "counts": {
            "reviews": len(normalized),
            "invalid": len(invalid),
            "flagged_asins": len(flagged),
        },
        "degraded": degraded,
        "banner": banner,
        "warnings": ([banner] if banner else []),
    }
    vc.write_json(args.out, payload)

    print(f"\n规范化评论：{len(normalized)} 条（无效 {len(invalid)} 条）")
    if invalid:
        for inv in invalid[:3]:
            print(f"  ⚠️ 无效样本：{inv.get('reason')}")
    print(f"已写出 {args.out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="取评论原文（外部 MCP adapter + 成本闸 + 脱敏规范化）")
    p.add_argument("--signals", default="out/signals.json", help="fetch_signals.py 的输出")
    p.add_argument("--adapter-file", default=None, help="adapter 声明文件（md）")
    p.add_argument("--reviews-json", default=None, help="AI 助手 按 adapter 取回并落盘的原始评论")
    p.add_argument("--confirm-external", action="store_true", help="确认外部调用与成本（放行成本闸）")
    p.add_argument("--out", default="out/reviews.json", help="输出 JSON 路径（相对技能根）")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
