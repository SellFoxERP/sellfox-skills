#!/usr/bin/env python3
"""库存健康与补货 —— 落盘校验与组装层（fetch_inventory.py，v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：在会话里调 `mcp__Sellfox-MCP__{help,search,action}`，
    按 `references/workflow.md` 的**取数计划**把每步完整信封落盘到 `out/raw/<step>.json`。
  * **本脚本 = 计算面**：校验落盘文件（存在性/status/空数据/演示指纹/coverage 截断），
    并组装出与旧版 HTTP 取数器**完全相同形状**的 FetchResult ——
    `replenishment.py` / `render.py` 零改动。

用法（通常不直接跑，入口是 run_inventory_health.py）::

    # 1) agent 先按 workflow.md 取数计划落盘 out/raw/*.json
    # 2) 校验 + 组装（汇总检查用）：
    python fetch_inventory.py --out out/assemble_summary.json
    python fetch_inventory.py --report-id DEMO-RPT-001   # 模拟宽表口径组装

落盘文件 ↔ 工具（与取数计划一致）：
    shops.json                   get_shop_page_list           → rows（分页参数 string）
    msku_<shopId>.json           get_custom_report_page_list  → rows（每店铺批一个；
                                                                report_id 缺 → 整步跳过走降级）
    online_products_<batch>.json get_online_product_page_list → rows（降级 / 补充源）
    fba_shipments.json           get_fba_shipment_page_list   → rows（可能 data:{} → 无数据）
    awd_inventory.json           get_awd_inventory_page_list  → rows（可能 data:{} → 无数据）

铁律对应（沿袭旧版口径）：
  * 分页硬上限 20 —— agent 单次 ≤1 页；本模块按 max_rows 做**组装侧硬截断**并披露。
  * 列表字段名因工具而异 —— 本技能四源均为 rows；信封解析统一走 sellfox_offline.RawEnvelope。
  * 空数据语义 —— 空字典记「无数据」，不写 0，不重试。
  * 维度字段是列表 —— msku=["..."] / asin=[{"key":"..."}]，统一走 first_scalar/pick 归一化。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from _shared_bootstrap import add_shared_to_path

add_shared_to_path()

from sellfox_offline import (  # noqa: E402
    RawEnvelope,
    detect_demo_environment,
    is_no_data,
    load_envelope,
)


# ---------------------------------------------------------------------------
# 归一化工具（与旧版一致：维度字段是列表，统一压成标量）
# ---------------------------------------------------------------------------
def first_scalar(value: Any) -> Any:
    """把赛狐的维度字段（列表 / {key:...}）压成标量。

    实测：msku=["MSKU-DEMO-001"]，asin=[{"key":"B0DEMO0001"}]，
    shopId=[{"key":"演示店铺001"}]，marketplaceId=[{"key":"US"}]。
    """
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            scalar = first_scalar(item)
            if scalar not in (None, ""):
                return scalar
        return None
    if isinstance(value, dict):
        for key in ("key", "value", "name", "label", "labelName"):
            if key in value:
                return first_scalar(value[key])
        return None
    return value


def pick(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            scalar = first_scalar(row[key])
            if scalar is not None and scalar != "":
                return scalar
    return None


def num(value: Any) -> Optional[float]:
    scalar = first_scalar(value)
    if scalar is None or isinstance(scalar, bool):
        return None
    if isinstance(scalar, (int, float)):
        return float(scalar)
    try:
        return float(str(scalar).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 规范化记录（字段裁剪：489 字段宽表只保留本技能需要的列）
# ---------------------------------------------------------------------------
CUSTOM_FIELDS = [
    ("msku", ("msku",)),
    ("sku", ("sku",)),
    ("asin", ("asin",)),
    ("title", ("title",)),
    ("shop_name", ("shopName", "shopId")),
    ("marketplace", ("marketplaceId", "marketplace")),
    # 可售天数（官方口径，直接采信）
    ("available_days", ("availableDays",)),
    ("fbm_available_days", ("fbmAvailableDays",)),
    # 补货建议量（官方口径）
    ("suggest_num_sum", ("suggestNumSum",)),
    # 动销 / 销量窗口（基线优先用现成字段，不另发调用）
    ("sales_velocity_month", ("salesVelocityMonth",)),
    ("thirty_sale_num", ("thirtySaleNum",)),
    ("seven_sale_num", ("sevenSaleNum",)),
    # 在途（可能全部缺失 → 记「无数据」，不是 0）
    ("inbound_working", ("inboundWorking",)),
    ("inbound_shipped", ("inboundShipped",)),
    ("inbound_receiving", ("inboundReceiving",)),
    ("in_transit", ("inTransit",)),
    # 库存构成 / 多仓
    ("local_available_qty", ("localAvailableQty",)),
    ("oversea_available_qty", ("overseaAvailableQty",)),
    ("fba_inventory", ("fbaInventory",)),
    ("fba_total_inventory", ("fbaTotalInventory",)),
    ("awd_available", ("awdAvailable",)),
    # 安全库存（只读）
    ("domestic_safe_stock", ("domesticSafeStock",)),
    ("oversea_safe_stock", ("overseaSafeStock",)),
    # 库龄分桶
    ("inv_age_0_30", ("invAge0to30Days",)),
    ("inv_age_31_60", ("invAge31to60Days",)),
    ("inv_age_61_90", ("invAge61to90Days",)),
    ("inv_age_0_90", ("invAge0to90Days",)),
    ("inv_age_91_180", ("invAge91To180Days",)),
    ("inv_age_181_270", ("invAge181To270Days",)),
    ("inv_age_271_365", ("invAge271To365Days",)),
    ("inv_age_365_plus", ("invAge365PlusDays",)),
    # 周转
    ("turnover_days_month", ("turnoverDaysMonth",)),
    ("turnover_rate_month", ("turnoverRateMonth",)),
    ("isr_month", ("isrMonth",)),
    # 资金占用（成本口径）
    ("fba_available_cost", ("fbaAvailableCost",)),
    ("oversea_stock_available_cost", ("overseaStockAvailableCost",)),
    ("domestic_stock_available_cost", ("domesticStockAvailableCost",)),
]


def normalize_row(row: Dict[str, Any], *, source: str, currency: Optional[str]) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"_source": source, "_currency": currency}
    for canonical, candidates in CUSTOM_FIELDS:
        # 末位 candidate 用规范名本身：降级源已按真实字段名预映射到规范名（见
        # ONLINE_PRODUCT_FIELDS），此处需能重新拾回；宽表原始行不含规范名，不受影响。
        rec[canonical] = pick(row, *candidates, canonical)
    # 降级源专属字段（不在 CUSTOM_FIELDS 中）：直接透传，供自算可售天数
    rec["online_available_qty"] = row.get("online_available_qty")
    # 数值化关键字段
    for key in (
        "available_days", "fbm_available_days", "suggest_num_sum", "sales_velocity_month",
        "thirty_sale_num", "seven_sale_num", "inbound_working", "inbound_shipped",
        "inbound_receiving", "in_transit", "local_available_qty", "oversea_available_qty",
        "fba_inventory", "fba_total_inventory", "awd_available", "domestic_safe_stock",
        "oversea_safe_stock", "inv_age_0_30", "inv_age_31_60", "inv_age_61_90",
        "inv_age_0_90", "inv_age_91_180", "inv_age_181_270", "inv_age_271_365",
        "inv_age_365_plus", "turnover_days_month", "turnover_rate_month", "isr_month",
        "fba_available_cost", "oversea_stock_available_cost", "domestic_stock_available_cost",
        "online_available_qty",
    ):
        rec[key] = num(rec.get(key))

    # 在途：全部缺失 == 无数据（None），任一有值 == 求和
    inbound_parts = [rec[k] for k in ("inbound_working", "inbound_shipped", "inbound_receiving", "in_transit")]
    present = [p for p in inbound_parts if p is not None]
    rec["inbound_total"] = sum(present) if present else None
    return rec


# 在线产品专属字段 → 本技能规范名（01 接口清单：get_online_product_page_list 顶层
# 暴露 day7SaleNum/day14SaleNum/day30SaleNum、quantity/warehousing/unsellable/totalInventory；
# inventoryManage 子对象含 available/inboundWorking/…/invAge*/inventoryCosts）。
# 这些名字与自定义报表（thirtySaleNum/salesVelocityMonth/fbaInventory…）不同，降级路径必须单独映射。
ONLINE_PRODUCT_FIELDS = [
    ("thirty_sale_num", ("day30SaleNum",)),          # 30 日销量 → 日均 = /30
    ("seven_sale_num", ("day7SaleNum",)),            # 7 日销量 → 日均 = /7
    ("fba_total_inventory", ("totalInventory",)),    # 顶层总库存
]


def _coalesce(*values: Any) -> Any:
    """取第一个非 None 值。**不能用 `or`** —— 真实的 0 销量/0 库存会被误当缺失。"""
    for value in values:
        if value is not None:
            return value
    return None


def _normalize_online_product(row: Dict[str, Any]) -> Dict[str, Any]:
    """降级源：get_online_product_page_list（inventoryManage 41 字段，currency=CNY）。

    自定义报表字段名（salesVelocityMonth/thirtySaleNum/fbaInventory/localAvailableQty…）
    **不适用于**该工具；这里除复用 CUSTOM_FIELDS 做同名字段兜底外，再按在线产品真实字段名
    （见 01 接口清单 / 06 字段字典）补一层映射，否则可售天数、日均销量全部取不到。
    """
    manage = row.get("inventoryManage") or {}
    if not isinstance(manage, dict):
        manage = {}
    merged: Dict[str, Any] = dict(row)
    # 把 inventoryManage 平铺到顶层，复用同一套字段映射
    for canonical, candidates in CUSTOM_FIELDS:
        if first_scalar(merged.get(canonical)) is None:
            for cand in candidates:
                if cand in manage and manage.get(cand) is not None:
                    merged[cand] = manage.get(cand)
                    break
    # 在线产品专属字段名映射（顶层优先，其次 inventoryManage）
    for canonical, candidates in ONLINE_PRODUCT_FIELDS:
        if first_scalar(merged.get(canonical)) is None:
            merged[canonical] = _coalesce(pick(row, *candidates), pick(manage, *candidates))
    # FBA 可售库存：inventoryManage.available 优先，退化到顶层 quantity / totalInventory。
    # 供降级口径自算可售天数（该工具无 availableDays/fbmAvailableDays）。
    if merged.get("online_available_qty") is None:
        merged["online_available_qty"] = _coalesce(
            pick(manage, "available"), pick(row, "quantity"),
            pick(row, "totalInventory"), pick(manage, "totalInventory"),
        )
    # 顶层 USD 售价 vs inventoryManage CNY 成本：不混算，成本类从 manage 取
    merged["fbaAvailableCost"] = manage.get("inventoryCosts", merged.get("fbaAvailableCost"))
    currency = first_scalar(manage.get("currency")) or first_scalar(row.get("currency"))
    rec = normalize_row(merged, source="online_product", currency=currency)
    if rec.get("available_days") is None and rec.get("fbm_available_days") is None:
        # 降级源无赛狐 availableDays（该字段仅属自定义报表/产品分析）→ 标记口径降级
        rec["_degraded_days"] = True
    return rec


# ---------------------------------------------------------------------------
# 组装结果（形状与旧版 HTTP 取数器完全一致，replenishment/render 零改动）
# ---------------------------------------------------------------------------
@dataclass
class FetchResult:
    records: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    coverage: List[str] = field(default_factory=list)   # 截断/无数据披露语句
    warnings: List[str] = field(default_factory=list)
    demo_fingerprint: Optional[str] = None
    report_id_used: Optional[str] = None
    degraded: bool = False
    degrade_reason: Optional[str] = None  # 口径降级的真实原因（未配置/发现被拒/主源失败），渲染层横幅用
    no_valid_data: bool = False  # 主源与降级源均不可用（records 全空）：横幅 + rc=2 信号，绝不静默空报告
    shipments: List[Dict[str, Any]] = field(default_factory=list)
    awd: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 落盘读取辅助（零网络：只读 out/raw/*.json）
# ---------------------------------------------------------------------------
def _load(raw_dir: Path, name: str) -> Optional[RawEnvelope]:
    p = raw_dir / (name if name.endswith(".json") else f"{name}.json")
    if not p.exists():
        return None
    try:
        return load_envelope(p)
    except Exception:
        return None


def _merge_pattern(raw_dir: Path, prefix: str) -> List[RawEnvelope]:
    """合并 msku_*.json / online_products_*.json 等批次文件（v3 并行落盘，文件名不冲突）。"""
    out: List[RawEnvelope] = []
    for p in sorted(raw_dir.glob(f"{prefix}_*.json")):
        try:
            out.append(load_envelope(p))
        except Exception:
            continue
    return out


def _cov_line(env: RawEnvelope, label: str, max_rows: int) -> str:
    """把信封 coverage 转成披露语句（语句风格与旧版 TruncationInfo.describe 一致）。"""
    cov = env.coverage or {}
    rows_n = len(env.rows("rows"))
    fetched = cov.get("fetched_count")
    shown = fetched if isinstance(fetched, int) else rows_n
    if cov.get("truncated"):
        total = cov.get("total_count")
        total_txt = f"，服务端报告约 {total} 行" if total else ""
        return (
            f"{label}: ⚠️ 本次仅覆盖 {shown} 行{total_txt}，"
            f"已达 max_rows={max_rows} 上限，其余未纳入统计。"
        )
    return f"{label}: 本次落盘 {rows_n} 行（未截断）。"


def _rows_from_env(env: RawEnvelope, *, label: str, max_rows: int, result: FetchResult) -> List[Dict[str, Any]]:
    """单个信封 → 行列表；status=failed / 空数据 / 截断的披露副作用写入 result。

    - ``status=failed`` → 该批不纳入统计，其余照常（数据说明记录 error_code）；
    - ``data:{}`` / 空 rows → 「无数据」，不写 0，不重试；
    - ``coverage.truncated`` → 披露语句（禁止静默截断）。
    """
    if env.status == "failed":
        result.warnings.append(
            f"{label} status=failed（error_code={env.error_code}）：该批未纳入统计，其余照常。"
        )
        return []
    rows = env.rows("rows")
    if is_no_data(env.data) or not rows:
        result.coverage.append(f"{label}: 无数据")
        return []
    result.coverage.append(_cov_line(env, label, max_rows))
    return rows


def _row_identity(rec: Dict[str, Any]) -> Any:
    """记录的 MSKU 身份：msku 退化到 sku（与计算层 replenishment.compute 的口径一致，
    降级源 get_online_product_page_list 常只有 sku）。None = 无法识别，不参与去重。"""
    return rec.get("msku") or rec.get("sku")


# ---------------------------------------------------------------------------
# 主流程：校验 out/raw/ 并组装 FetchResult（形状与旧版 fetch_all 一致）
# ---------------------------------------------------------------------------
def assemble_all(
    raw_dir: Path,
    *,
    report_id: Optional[str],
    skill_cfg: Dict[str, Any],
    result: Optional[FetchResult] = None,
) -> FetchResult:
    """四源落盘合并组装。report_id 为空 → 跳过宽表步骤，走在线产品降级并标「口径降级」。

    :param raw_dir: 落盘目录（通常是 ``<skill>/out/raw``）。
    :param report_id: 报表 ID（reports.yaml → inventory.id，或 --report-id 覆盖）。
    :param skill_cfg: ``config/inventory.yaml``（max_rows / 口径参数）。
    """
    result = result or FetchResult()
    raw_dir = Path(raw_dir)
    limits = skill_cfg.get("max_rows") or {}

    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        raise SystemExit(
            f"❌ 落盘目录为空或不存在：{raw_dir}。\n"
            "   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」\n"
            "   调用 mcp__Sellfox-MCP__{search,action} 并把信封落盘到 out/raw/，再重跑本脚本。"
        )

    scanned: List[Any] = []  # 全部信封原文，供演示指纹兜底扫描

    def _scan(obj: Any) -> None:
        scanned.append(obj)

    # 0) 店铺清单（演示指纹扫描 + 覆盖披露；不参与计算 —— 计算口径由落盘数据决定）
    env = _load(raw_dir, "shops")
    if env is None:
        result.warnings.append(
            "缺少 out/raw/shops.json（取数计划步骤 1）：店铺清单未校验，不影响主流程。"
        )
    else:
        _scan(env.obj)
        shop_rows = _rows_from_env(
            env, label="shops", max_rows=int(limits.get("shops", 40)), result=result
        )
        # 重复行防御：店铺清单是分批依据（shop_batch_size），上游重复（线上实测同 ID ×4）
        # 会放大批次数、放大重复取数 —— 去重（首个出现为准）并在覆盖范围披露。
        if shop_rows:
            seen_shop_ids: set = set()
            unique_shops = []
            for row in shop_rows:
                sid = pick(row, "id", "shopId", "shop_id")
                if sid in seen_shop_ids:
                    continue
                seen_shop_ids.add(sid)
                unique_shops.append(row)
            if len(unique_shops) < len(shop_rows):
                result.coverage.append(
                    f"shops: 落盘 {len(shop_rows)} 行含重复店铺（唯一 {len(unique_shops)} 个，"
                    "首个出现为准去重）；分批请按去重后的唯一店铺清单，避免重复批次。"
                )

    # 1) 宽表主源（每店铺批一文件；无 report_id → 整步跳过走降级）
    msku_envs = _merge_pattern(raw_dir, "msku")
    custom_records: List[Dict[str, Any]] = []
    custom_failed_codes: List[str] = []  # 主源 failed 的 error_code（降级原因/失败可见性用）
    max_cr = int(limits.get("custom_report", 200))
    if report_id and msku_envs:
        result.report_id_used = report_id
        result.sources.append("get_custom_report_page_list")
        truncated_any = False
        for env_ in msku_envs:
            _scan(env_.obj)
            batch = env_.path.stem  # msku_<shopId>
            if env_.status == "failed" and env_.error_code and env_.error_code not in custom_failed_codes:
                custom_failed_codes.append(env_.error_code)
            rows = _rows_from_env(
                env_, label=f"custom_report({batch})", max_rows=max_cr, result=result
            )
            truncated_any = truncated_any or bool((env_.coverage or {}).get("truncated"))
            for row in rows:
                custom_records.append(normalize_row(row, source="custom_report", currency="USD"))
        # 组装侧 max_rows 硬截断（服务端 20 条/页硬上限在取数侧用「单次 ≤1 页」规避）
        if len(custom_records) > max_cr:
            result.warnings.append(
                f"⚠️ 宽表合并 {len(custom_records)} 行，超出 max_rows={max_cr} 上限，"
                f"本次仅覆盖前 {max_cr} 行，其余未纳入统计。"
            )
            custom_records = custom_records[:max_cr]
            truncated_any = True
        if truncated_any:
            result.warnings.append(
                f"⚠️ 自定义报表取数触达 max_rows={max_cr} 上限，本次仅覆盖部分 MSKU，其余未纳入统计。"
            )
    elif report_id and not msku_envs:
        result.warnings.append(
            "宽表落盘缺失（out/raw/msku_*.json）：L2 明细层降级，改用在线产品降级源组装。"
        )
    elif not report_id and msku_envs:
        # report_id 为空时按取数计划不应调用宽表；即便有落盘也整步跳过，显式说明。
        # 失败可见性：已落盘的 failed 宽表信封（如发现验证读 RESULT_BUDGET_EXCEEDED）绝不静默 ——
        # error_code + 工具名进警告/数据说明（涉及工具），再走降级。
        any_failed = False
        for env_ in msku_envs:
            _scan(env_.obj)
            if env_.status == "failed":
                any_failed = True
                if env_.error_code and env_.error_code not in custom_failed_codes:
                    custom_failed_codes.append(env_.error_code)
                result.warnings.append(
                    f"custom_report({env_.path.stem}) status=failed（error_code={env_.error_code}）："
                    "get_custom_report_page_list 取数失败，report_id 未保存，该步未纳入统计，走降级路径。"
                )
        if any_failed:
            result.sources.append("get_custom_report_page_list(失败未采信)")
        result.warnings.append(
            "reports.yaml → inventory.id 为空：忽略已落盘的 msku_*.json，整步跳过走降级路径。"
        )

    # 2) 在线产品降级 / 补充源
    online_envs = _merge_pattern(raw_dir, "online_products")
    fallback: List[Dict[str, Any]] = []
    max_op = int(limits.get("online_product", 200))
    for env_ in online_envs:
        _scan(env_.obj)
        batch = env_.path.stem  # online_products_<batch>
        rows = _rows_from_env(
            env_, label=f"online_product({batch})", max_rows=max_op, result=result
        )
        for row in rows:
            fallback.append(_normalize_online_product(row))
    # 去重防御：上游 shop_id_list 过滤失效时（线上实测）不同批次会返回同一目录，
    # 同一 MSKU 按批次数重复计入会让建议单/核心结论成倍虚增 —— 按 MSKU
    # （msku 退化到 sku，与计算层口径一致）去重，首个出现为准（对齐补充路径做法），去重时披露。
    seen_msku: set = set()
    deduped_fallback: List[Dict[str, Any]] = []
    for rec in fallback:
        key = _row_identity(rec)
        if key is not None and key in seen_msku:
            continue
        if key is not None:
            seen_msku.add(key)
        deduped_fallback.append(rec)
    if len(deduped_fallback) < len(fallback):
        result.warnings.append(
            f"⚠️ 在线产品降级源跨批次重复 {len(fallback) - len(deduped_fallback)} 行"
            "（同一 MSKU，首个出现为准去重）：疑似上游 shop_id_list 过滤失效，建议核对店铺分批参数。"
        )
    fallback = deduped_fallback
    if len(fallback) > max_op:
        result.warnings.append(
            f"⚠️ 在线产品合并 {len(fallback)} 行，触达 max_rows={max_op} 上限，其余未纳入统计。"
        )
        fallback = fallback[:max_op]

    if not report_id:
        # report_id 为空 → 宽表整步跳过；存在 online_products_*.json 则组装降级数据
        result.degraded = True
        if custom_failed_codes:
            result.degrade_reason = (
                "报表发现验证失败（get_custom_report_page_list，error_code="
                + "/".join(custom_failed_codes) + "），report_id 未保存"
            )
        else:
            result.degrade_reason = "未配置报表 ID（reports.yaml → inventory.id 为空，尚未发现适用报表）"
        result.sources.append("get_online_product_page_list(降级)")
        result.warnings.append(
            f"⚠️ 口径降级：{result.degrade_reason}，改用在线产品 inventoryManage "
            "子对象；可售天数为自算口径，与赛狐 availableDays 不一致，尚未取得适用报表时先展示当前自算结果。"
        )
        result.records = fallback
        if not fallback:
            # 主源（宽表整步跳过/发现被拒）与降级源均不可用：显式警告 + 报告横幅 + rc=2 信号
            result.no_valid_data = True
            result.warnings.append(
                "⚠️ 主源与降级源均不可用，本报告无有效数据项：宽表整步未取（report_id 未取得），"
                "且降级源落盘缺失（out/raw/online_products_*.json）——全 0 结论不可采信，请核对取数落盘。"
            )
    elif custom_records:
        # 主源有数据：在线产品仅作补充（按 MSKU 去重合并，身份键与计算层一致），
        # 口径仍以宽表为准
        if fallback:
            result.sources.append("get_online_product_page_list(补充)")
        existing = {_row_identity(r) for r in custom_records if _row_identity(r) is not None}
        result.records = custom_records + [
            r for r in fallback if _row_identity(r) not in existing
        ]
    elif fallback:
        # 主源整批缺失（落盘缺失/failed/无数据）→ 实际口径已是降级源，显式标注
        result.degraded = True
        if custom_failed_codes:
            result.degrade_reason = (
                "主源取数失败（get_custom_report_page_list，error_code="
                + "/".join(custom_failed_codes) + "）"
            )
        elif not msku_envs:
            result.degrade_reason = "宽表落盘缺失（out/raw/msku_*.json）"
        else:
            result.degrade_reason = "主源整批无数据（custom_report 未取回任何行）"
        result.sources.append("get_online_product_page_list(降级)")
        result.records = fallback
    else:
        # 主源与降级源均不可用（稀疏 raw：主源 failed + online_products_*.json 缺失）：
        # 绝不静默产出全 0 项空报告 —— 显式警告 + 报告横幅 + rc=2 信号。
        result.records = []
        result.no_valid_data = True
        result.degraded = True
        if custom_failed_codes:
            result.degrade_reason = (
                "主源取数失败（get_custom_report_page_list，error_code="
                + "/".join(custom_failed_codes) + "）"
            )
        elif not msku_envs:
            result.degrade_reason = "宽表落盘缺失（out/raw/msku_*.json）"
        else:
            result.degrade_reason = "主源整批无数据（custom_report 未取回任何行）"
        result.warnings.append(
            f"⚠️ 主源与降级源均不可用，本报告无有效数据项（{result.degrade_reason}，"
            "且降级源落盘缺失 out/raw/online_products_*.json）——全 0 结论不可采信，请核对取数落盘。"
        )

    # 3) 在途货件 / 4) AWD 库存：可能为空（data:{} → 无数据），不视为失败
    env = _load(raw_dir, "fba_shipments")
    if env is None:
        result.warnings.append(
            "fba_shipment 落盘缺失（out/raw/fba_shipments.json）：在途货件未纳入，不影响主流程。"
        )
    else:
        _scan(env.obj)
        # 涉及工具与 coverage 区一致：该步真实调用过（含 failed/无数据）即列入
        result.sources.append("get_fba_shipment_page_list")
        result.shipments = _rows_from_env(
            env, label="fba_shipment", max_rows=int(limits.get("fba_shipment", 100)), result=result
        )

    env = _load(raw_dir, "awd_inventory")
    if env is None:
        result.warnings.append(
            "awd_inventory 落盘缺失（out/raw/awd_inventory.json）：AWD 库存未纳入，不影响主流程。"
        )
    else:
        _scan(env.obj)
        result.sources.append("get_awd_inventory_page_list")
        result.awd = _rows_from_env(
            env, label="awd_inventory", max_rows=int(limits.get("awd_inventory", 100)), result=result
        )

    # 演示指纹：记录内容优先（最接近结论层），其次任一落盘信封
    fp = detect_demo_environment(result.records)
    if not fp:
        for obj in scanned:
            fp = detect_demo_environment(obj)
            if fp:
                break
    result.demo_fingerprint = fp
    return result


def main(argv: Optional[List[str]] = None) -> int:
    """独立运行：校验 + 组装，输出摘要 JSON（不做判定与渲染，入口见 run_inventory_health.py）。"""
    ap = argparse.ArgumentParser(description="库存健康 - 落盘校验与组装（零网络）")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--report-id", default=None, help="模拟 reports.yaml 的 inventory.id（缺省按降级口径组装）")
    ap.add_argument("--out", default=None, help="把组装摘要写成 JSON（缺省打印到 stdout）")
    args = ap.parse_args(argv)

    here = Path(__file__).resolve().parent
    skill_root = here.parent
    raw_dir = Path(args.raw_dir).expanduser() if args.raw_dir else skill_root / "out" / "raw"

    from config_loader import load_yaml_file  # noqa: E402 （_shared 已在 sys.path）

    skill_cfg = load_yaml_file(skill_root / "config" / "inventory.yaml")
    result = assemble_all(raw_dir, report_id=args.report_id, skill_cfg=skill_cfg)

    summary = {
        "raw_dir": str(raw_dir),
        "report_id_used": result.report_id_used,
        "degraded": result.degraded,
        "degrade_reason": result.degrade_reason,
        "records": len(result.records),
        "shipments": len(result.shipments),
        "awd": len(result.awd),
        "sources": result.sources,
        "coverage": result.coverage,
        "warnings": result.warnings,
        "demo_fingerprint": result.demo_fingerprint,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = skill_root / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"✅ 组装摘要已写出：{out}")
    else:
        print(text)
    if result.demo_fingerprint:
        print(f"⚠️ 演示环境指纹：{result.demo_fingerprint}（数据非真实经营数据）")
    for w in result.warnings:
        print(f"⚠️ {w}")
    return 0 if result.records else 2


if __name__ == "__main__":
    raise SystemExit(main())
