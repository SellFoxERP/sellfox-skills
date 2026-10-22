"""Step 2：上架基线数据 —— 落盘校验与组装（v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：

- **取数面 = agent**：在会话里调 `mcp__Sellfox-MCP__{help,search,action}`，
  按 `references/workflow.md` 的**取数计划**把每步完整信封落盘到 `out/raw/`。
- **本脚本 = 计算面**：校验落盘文件（存在性/status/空数据/演示指纹/coverage 截断），
  组装出与旧 HTTP 取数器**完全相同形状**的 baseline ——
  `build_asset_brief` / `check_compliance` / `estimate_pricing` / `render_package` 零改动。

落盘步骤名（与取数计划一致）：

    online_product.json    get_online_product_page_list → data.rows（Listing 属性 + 成本/费用/价格 + FBA 库存）
    product_analyze.json   get_product_analyze_list     → data.rows（类目 / BSR / 上架时间）

降级语义（沿袭旧「缺 Key 离线骨架」）：**缺落盘 → 实时字段标「⚠️ 需人工核查」，
骨架照出**——脚本不因取数缺失而崩溃，仍产出结构完整的上架包。

组装层还做两件事（线上评测 2026-10-16 发现）：

- **analysis 行与目标 ASIN 本地关联**：上游忽略 asin 过滤返回无关行时，按行内
  ``asinList``（线上实测为列表形；白名单同步保留该键）与目标 ASIN 求交，无交集的
  行不进区块 1，并在 warnings 披露「已剔除 N 条」。
- **信封级 warnings 汇总去重**进 notes（渲染层落「数据说明与警示」），
  遵守 `_shared/SELLFOX_CLIENT.md` §5「报告保留 warnings」。

用法::

    # 1) agent 先按 workflow.md 取数计划落盘 out/raw/*.json
    # 2) 校验 + 组装：
    python3 scripts/fetch_baseline.py --raw-dir out/raw
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from _common import Notes, SKILL_ROOT, as_float, ensure_shared_path, unwrap_value

# v3 计算面共享组件（只读落盘数据，零网络）。
_SHARED_MISSING = (
    "无法从 _shared/ 导入 sellfox_offline：请在 config/paths.yaml 填写 shared_root，"
    "或设置环境变量 SELLFOX_SHARED_ROOT，并确保 _shared/ 已随技能一并安装。"
)
if not ensure_shared_path() or importlib.util.find_spec("sellfox_offline") is None:
    raise SystemExit(_SHARED_MISSING)
from sellfox_offline import (  # noqa: E402
    RawEnvelope,
    detect_demo_environment,
    is_no_data,
    load_envelope,
)

# 只保留上架包需要的字段，避免 79/36 字段撑爆上下文（对齐日报技能的做法）。
# ⚠️ 落盘信封保留服务端原始行，裁剪发生在组装层——baseline 形状与旧版一致。
ONLINE_PRODUCT_FIELDS: Sequence[str] = (
    "id", "shopId", "marketplaceId",
    "asin", "parentAsin", "sku", "fnsku", "spu",
    "title", "mainImage", "brand",
    "onlineStatus", "switchFulfillmentTo",
    "standardPrice", "listingPrice", "salePrices", "shippingPrice",
    "buyBoxWinner", "isVariation", "variationChildStr", "itemDimensions",
    "quantity", "warehousing", "unsellable", "totalInventory",
    "saleNum", "day7SaleNum", "day14SaleNum", "day30SaleNum",
    "profitPrice", "profitRate",
    "fbaFees", "referralFee", "variableClosingFee", "purchaseCost",
    "bsrRank", "rating", "ratingCount", "fullCid", "openDate",
    "currency", "inventoryManage",
)

PRODUCT_ANALYZE_FIELDS: Sequence[str] = (
    "spu", "asin", "asinList", "parentAsin", "sku", "title", "mainImage",
    "categoryNameList", "bsrRank", "openDate", "currency",
    "saleNum", "salePrice", "onlineStatus",
)

#: 落盘步骤 → 列表字段（来自契约 pagination 元数据，两工具均为分页类 rows）。
RAW_STEPS: Dict[str, str] = {
    "online_product": "rows",
    "product_analyze": "rows",
}

#: 默认落盘目录（listing_launch.py 与本脚本共用）。
DEFAULT_RAW_DIR = SKILL_ROOT / "out" / "raw"


def _trim(row: Dict[str, Any], keep: Sequence[str]) -> Dict[str, Any]:
    """按白名单裁剪一条记录；缺失字段不补 0，直接不出现。"""
    return {k: row[k] for k in keep if k in row}


def _row_asins(row: Dict[str, Any]) -> List[str]:
    """取一条 analysis 行携带的 ASIN 集合。

    线上实测行为 ``asinList`` 列表形（标量 ``asin`` 被裁掉）；离线合成行两者皆有。
    """
    out: List[str] = []
    raw_list = row.get("asinList")
    if isinstance(raw_list, list):
        out.extend(str(a) for a in raw_list if a)
    scalar = unwrap_value(row.get("asin"))
    if scalar:
        out.append(str(scalar))
    return out


def _load_step(
    raw_dir: Path, step: str, notes: Notes
) -> tuple:
    """读取并校验一个落盘信封。

    :returns: ``(envelope, rows)``——信封缺失/损坏/失败时为 ``(None, [])``，
              并把影响写进 ``notes``（缺落盘 → 需人工核查，骨架照出）。
    """
    path = raw_dir / f"{step}.json"
    if not path.exists():
        notes.need_manual(
            f"缺落盘 {path}：v3 取数面由 agent 完成，请按 references/workflow.md 取数计划"
            f"调 mcp__Sellfox-MCP__{{search,action}} 落盘后重跑；相关实时字段需人工核查。"
        )
        return None, []

    try:
        env = load_envelope(path)
    except Exception as exc:
        notes.need_manual(f"落盘 {path.name} 无法解析（{exc}）：相关字段需人工核查。")
        return None, []

    if env.status == "failed":
        notes.need_manual(
            f"落盘 {path.name} status=failed（error_code={env.error_code}，"
            f"{env.message or ''}）：相关字段需人工核查。"
        )
        return env, []

    if env.partial:
        notes.warn(f"{path.name} status=partial：数据可采信，但必须在数据说明中披露。")

    # 演示指纹（铁律 11 扩充版）：扫整个信封。
    fp = detect_demo_environment(env.obj)
    if fp:
        notes.warn(f"⚠️ 演示数据，非真实经营数据（指纹：{fp}）")

    # coverage 截断必披露（不信 pagination.has_more）。
    cov = env.coverage or {}
    if cov.get("truncated"):
        notes.warn(
            f"⚠️ {path.name} 仅覆盖 {cov.get('fetched_count')} 行"
            f"（服务端报告约 {cov.get('total_count')} 行），已达 max_rows 上限，其余未纳入。"
        )

    if is_no_data(env.data):
        return env, []
    return env, env.rows(RAW_STEPS[step])


def _merge_env_warnings(envs: Sequence[Optional[RawEnvelope]], notes: Notes) -> None:
    """把两步信封级 warnings 汇总去重进 notes（渲染层落「数据说明与警示」）。

    同一条 warning 出现在多个信封时合并来源（如「online_product、product_analyze」）；
    含上游 ``documented_schema_mismatch`` 之类的指示，报告必须保留（SELLFOX_CLIENT.md §5）。
    """
    sources: Dict[str, List[str]] = {}
    for env in envs:
        if env is None:
            continue
        for w in (env.obj.get("warnings") or []):
            sources.setdefault(str(w), []).append(env.path.stem)
    for w, steps in sources.items():
        notes.warn(f"取数信封 warnings（{'、'.join(steps)}）：{w}")


def summarize_product(row: Dict[str, Any]) -> Dict[str, Any]:
    """把一条在线产品记录整理成上架包可用的「商品基线」。

    币种铁律：顶层 ``currency``（USD，售价口径）与 ``inventoryManage.currency``
    （CNY，成本口径）**不同**，此处分别保留，绝不直接相减。
    """
    inv = row.get("inventoryManage") or {}
    return {
        "asin": unwrap_value(row.get("asin")),
        "parent_asin": unwrap_value(row.get("parentAsin")),
        "sku": unwrap_value(row.get("sku")),
        "msku": unwrap_value(row.get("msku")),
        "fnsku": unwrap_value(row.get("fnsku")),
        "commodity_name": unwrap_value(row.get("commodityName")),
        "title": unwrap_value(row.get("title")),
        "brand": unwrap_value(row.get("brand")),
        "main_image": unwrap_value(row.get("mainImage")),
        "marketplace": unwrap_value(row.get("marketplaceId")) or unwrap_value(row.get("shopId")),
        "online_status": row.get("onlineStatus"),
        "fulfillment": row.get("switchFulfillmentTo"),
        "is_variation": row.get("isVariation"),
        "variation_children": row.get("variationChildStr"),
        "dimensions": row.get("itemDimensions"),
        # 售价口径（顶层 currency，实测 USD）
        "price_currency": row.get("currency"),
        "standard_price": as_float(row.get("standardPrice")),
        "listing_price": as_float(unwrap_value(row.get("listingPrice"))),
        "sale_prices": as_float(row.get("salePrices")),
        "shipping_price": as_float(row.get("shippingPrice")),
        "buybox_winner": row.get("buyBoxWinner"),
        # 成本/费用口径（注意来源与币种）
        "purchase_cost": as_float(row.get("purchaseCost")),
        "referral_fee": as_float(row.get("referralFee")),
        "fba_fees": as_float(row.get("fbaFees")),
        "variable_closing_fee": as_float(row.get("variableClosingFee")),
        # 头程：线上实测商品行内返回 headTripCost（成本口径，业务口径未验证），
        # estimate_pricing 消费；缺值需人工核查（宽表口径转 custom-report-analysis 技能）。
        "headtrip_cost": as_float(row.get("headTripCost")),
        "profit_price": as_float(row.get("profitPrice")),
        "profit_rate": as_float(row.get("profitRate")),
        # 库存
        "total_inventory": as_float(row.get("totalInventory")),
        "quantity": as_float(row.get("quantity")),
        "inventory_currency": inv.get("currency") if isinstance(inv, dict) else None,
        # 评论（仅聚合，无原文）
        "rating": as_float(row.get("rating")),
        "rating_count": as_float(row.get("ratingCount")),
        "bsr_rank": row.get("bsrRank"),
        "category": row.get("fullCid"),
        "open_date": unwrap_value(row.get("openDate")),
    }


def build_baseline(
    *,
    raw_dir: Optional[Path] = None,
    notes: Optional[Notes] = None,
    target_asins: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """校验落盘信封并组装商品基线（零网络）。

    :param target_asins: 目标 ASIN 集合（如 ``--search-type asin`` 的搜索内容）。
        提供时 analysis 行按行内 ``asinList`` 与之求交，无交集的行不进基线
        （上游忽略 asin 过滤时的本地兜底），并在 warnings 披露剔除数量。
    :returns: ``{"products": [...], "analysis": [...], "demo_fingerprint": str|None}``
              ——形状与旧 HTTP 取数器完全一致，下游脚本零改动。
    """
    notes = notes or Notes()
    raw_dir = Path(raw_dir) if raw_dir else DEFAULT_RAW_DIR
    baseline: Dict[str, Any] = {"products": [], "analysis": [], "demo_fingerprint": None}
    demo_fp: Optional[str] = None

    def _scan_demo(env: Optional[RawEnvelope]) -> None:
        nonlocal demo_fp
        if env is None:
            return
        fp = detect_demo_environment(env.obj)
        if fp and not demo_fp:
            demo_fp = fp

    # 1) 在线产品（Listing 属性 + 成本/费用/价格 + FBA 库存）。
    online_env, online_rows = _load_step(raw_dir, "online_product", notes)
    _scan_demo(online_env)
    baseline["products"] = [
        summarize_product(r) for r in online_rows if isinstance(r, dict)
    ]
    if online_env is not None and not baseline["products"]:
        notes.warn(
            "get_online_product_page_list 落盘无数据（data 空/rows 空）——不是失败；"
            "请确认 ASIN/MSKU/站点是否正确，或该品尚未在赛狐在线产品中。"
        )

    # 2) 商品分析（类目 / BSR / 上架时间）——失败不阻断主流程。
    analyze_env, analyze_rows = _load_step(raw_dir, "product_analyze", notes)
    _scan_demo(analyze_env)
    analysis_rows = [r for r in analyze_rows if isinstance(r, dict)]
    # 目标 ASIN 本地关联：products 在 listing_launch 有本地精确匹配兜底，analysis 此前
    # 没有——上游忽略 asin 过滤返回无关行时照报区块 1。这里按 asinList 求交剔除并披露。
    if target_asins:
        wanted = {str(a) for a in target_asins if a}
        total = len(analysis_rows)
        analysis_rows = [r for r in analysis_rows if set(_row_asins(r)) & wanted]
        dropped = total - len(analysis_rows)
        if dropped:
            notes.warn(
                f"analysis 行 {total} 条中 {dropped} 条与目标 ASIN 无关联"
                f"（上游过滤疑似未生效），已剔除"
            )
    baseline["analysis"] = [
        _trim(r, PRODUCT_ANALYZE_FIELDS) for r in analysis_rows
    ]

    # 信封级 warnings 汇总去重（SELLFOX_CLIENT.md §5：报告保留 warnings）。
    _merge_env_warnings((online_env, analyze_env), notes)

    if demo_fp:
        baseline["demo_fingerprint"] = demo_fp
    return baseline


def _main() -> int:  # pragma: no cover - 手工排障用
    ap = argparse.ArgumentParser(description="校验落盘并组装上架基线（零网络）")
    ap.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--out", help="把组装结果写成 JSON（缺省打印到 stdout）")
    args = ap.parse_args()

    notes = Notes()
    baseline = build_baseline(raw_dir=Path(args.raw_dir), notes=notes)
    payload = {
        "baseline": baseline,
        "warnings": notes.warnings,
        "manual_checks": notes.manual_checks,
        "raw_dir": str(args.raw_dir),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)
    for w in notes.warnings:
        print(f"⚠️ {w}", file=sys.stderr)
    for m in notes.manual_checks:
        print(f"🔎 {m}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
