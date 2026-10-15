#!/usr/bin/env python3
"""库存健康与补货 —— 计算层（纯函数，不触网）。

判定规则见 references/replenishment-rules.md：
  P0 来不及 / P1 有缺口 / P2 预警 / 健康
  滞销侧：库龄 271+ 天占比高 且 动销低 → 清货建议
  双向矩阵：① 紧急补货 ② 健康 ③ 滞销清理 ④ 低效观察

所有阈值来自 _shared/config/thresholds.yaml → inventory；
到货周期来自 _shared/config/lead_time.yaml（可被本次交互覆盖）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

URGENCY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "健康": 3, "未知": 4}

# 在途字段全部缺失 == 无数据，不能当 0
INBOUND_UNKNOWN = "⚠️ 在途无数据"


@dataclass
class PlanRow:
    msku: str
    sku: Optional[str] = None
    asin: Optional[str] = None
    title: Optional[str] = None
    shop_name: Optional[str] = None
    available_days: Optional[float] = None
    days_source: str = ""
    lead_days: Optional[float] = None
    lead_source: str = ""
    lead_layer: str = ""
    inbound_total: Optional[float] = None
    velocity: Optional[float] = None          # 日均销量
    velocity_source: str = ""
    gap: Optional[float] = None               # 缺口
    suggested: Optional[float] = None         # 建议补货量
    urgency: str = "未知"
    quadrant: str = ""
    dead_stock: bool = False
    age_271_pct: Optional[float] = None
    age_365_pct: Optional[float] = None
    turnover_days: Optional[float] = None
    isr: Optional[float] = None
    stock_cost: Optional[float] = None
    cost_currency: str = ""
    actions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_cell(self, key: str) -> str:
        value = getattr(self, key)
        if value is None:
            return "无数据"
        if isinstance(value, float):
            return f"{value:.1f}"
        return str(value)


@dataclass
class PlanResult:
    rows: List[PlanRow] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unresolved_lead_mskus: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)


def _velocity(rec: Dict[str, Any]) -> tuple[Optional[float], str]:
    """日均销量：优先赛狐月销速率，退化到窗口销量字段，绝不臆造。"""
    online = rec.get("_source") == "online_product"
    svm = rec.get("sales_velocity_month")
    if svm is not None:
        return svm / 30.0, "salesVelocityMonth/30"
    tsn = rec.get("thirty_sale_num")
    if tsn is not None:
        return tsn / 30.0, ("day30SaleNum/30" if online else "thirtySaleNum/30")
    ssn = rec.get("seven_sale_num")
    if ssn is not None:
        return ssn / 7.0, ("day7SaleNum/7" if online else "sevenSaleNum/7")
    return None, "无数据"


def _available_days(rec: Dict[str, Any], velocity: Optional[float]) -> tuple[Optional[float], str]:
    """可售天数：直接采信赛狐 availableDays（官方口径），降级时自算并标注。"""
    if rec.get("available_days") is not None:
        return rec["available_days"], "赛狐 availableDays"
    if rec.get("fbm_available_days") is not None:
        return rec["fbm_available_days"], "赛狐 fbmAvailableDays"
    if rec.get("_degraded_days") and velocity:
        # 降级源（get_online_product_page_list）无 availableDays，只能用其真实字段自算：
        # inventoryManage.available / 顶层 quantity / totalInventory（见 fetch_inventory
        # 的 ONLINE_PRODUCT_FIELDS），退化到自定义报表的多仓字段。
        stock = _coalesce(rec.get("online_available_qty"), rec.get("fba_inventory"))
        if stock is None and (
            rec.get("local_available_qty") is not None or rec.get("oversea_available_qty") is not None
        ):
            stock = (rec.get("local_available_qty") or 0.0) + (rec.get("oversea_available_qty") or 0.0)
        if stock is not None:
            # 真实的 0 库存 → 0 可售天数（= 已断货），不是「无数据」
            return stock / velocity, "自算(降级口径)"
    return None, "无数据"


def _coalesce(*values: Optional[float]) -> Optional[float]:
    for value in values:
        if value is not None:
            return value
    return None


def _age_pct(rec: Dict[str, Any], denominator_mode: str) -> tuple[Optional[float], Optional[float]]:
    buckets = [
        rec.get("inv_age_0_30"), rec.get("inv_age_31_60"), rec.get("inv_age_61_90"),
        rec.get("inv_age_91_180"), rec.get("inv_age_181_270"),
        rec.get("inv_age_271_365"), rec.get("inv_age_365_plus"),
    ]
    present = [b for b in buckets if b is not None]
    if denominator_mode == "fba_total" and rec.get("fba_total_inventory"):
        denom = rec["fba_total_inventory"]
    else:
        denom = sum(present) if present else rec.get("fba_total_inventory")
    if not denom:
        return None, None
    old = rec.get("inv_age_271_365")
    very_old = rec.get("inv_age_365_plus")
    pct271 = (old / denom * 100.0) if old is not None else None
    pct365 = (very_old / denom * 100.0) if very_old is not None else None
    return pct271, pct365


def compute(
    records: Sequence[Dict[str, Any]],
    *,
    loader: Any,
    skill_cfg: Dict[str, Any],
    on_date: Optional[date] = None,
    lead_time_override: Optional[Dict[str, Any]] = None,
) -> PlanResult:
    """对每个 MSKU 计算断货/滞销结论。

    :param loader: ConfigLoader 实例（读取 thresholds / lead_time）。
    :param lead_time_override: 本次交互填写的到货周期，形如 {"by_sku": {"A001": 20}}。
    """
    on_date = on_date or date.today()
    result = PlanResult()
    th = loader.section("thresholds", "inventory") or {}
    warn_days = float(th.get("available_days_min", 30))
    critical_days = float(th.get("available_days_critical", 15))
    age271_max = float(th.get("inv_age_271_365_pct_max", 10))
    age365_max = float(th.get("inv_age_365_plus_pct_max", 5))
    q = skill_cfg.get("quadrant") or {}
    high_vel = float(q.get("high_velocity_min", 10))
    ds = skill_cfg.get("dead_stock") or {}
    denom_mode = ds.get("age_denominator", "bucket_sum")
    cost_currency = (skill_cfg.get("currency") or {}).get("cost_currency", "CNY")
    supplier_map = skill_cfg.get("sku_supplier_map") or {}
    ship_map = skill_cfg.get("sku_shipping_method_map") or {}

    for rec in records:
        msku = str(rec.get("msku") or rec.get("sku") or "")
        if not msku:
            result.warnings.append("跳过一条无 msku/sku 的记录。")
            continue

        row = PlanRow(
            msku=msku, sku=rec.get("sku"), asin=rec.get("asin"),
            title=rec.get("title"), shop_name=rec.get("shop_name"),
            turnover_days=rec.get("turnover_days_month"), isr=rec.get("isr_month"),
        )

        velocity, v_src = _velocity(rec)
        row.velocity, row.velocity_source = velocity, v_src
        days, d_src = _available_days(rec, velocity)
        row.available_days, row.days_source = days, d_src
        row.inbound_total = rec.get("inbound_total")

        # 到货周期：三级解析 + 本次交互覆盖
        lead = loader.resolve_lead_time(
            msku=msku,
            supplier=supplier_map.get(msku),
            shipping_method=ship_map.get(msku),
            on_date=on_date,
            lead_time_override=lead_time_override,
        )
        row.lead_days, row.lead_source, row.lead_layer = lead.days, lead.source, lead.layer
        if lead.layer == "default" and lead.source != "本次填写":
            result.unresolved_lead_mskus.append(msku)

        # 资金占用（成本口径；币种假设来自配置，须与财务核对）
        cost_parts = [
            rec.get("fba_available_cost"), rec.get("oversea_stock_available_cost"),
            rec.get("domestic_stock_available_cost"),
        ]
        present_cost = [c for c in cost_parts if c is not None]
        if present_cost:
            row.stock_cost = sum(present_cost)
            row.cost_currency = cost_currency
            row.notes.append(f"资金占用为成本口径（假设币种 {cost_currency}，⚠️ 需与财务核对）")

        # ---- 断货判定 ----
        inb = row.inbound_total
        if days is None:
            row.urgency = "未知"
            row.notes.append("⚠️ 可售天数无数据，需人工核查")
        elif inb is None:
            row.urgency = "未知"
            row.notes.append(INBOUND_UNKNOWN + "，无法判断在途能否救急，需人工核查")
        else:
            if days < critical_days and inb == 0:
                row.urgency = "P0"
            elif days < lead.days and inb == 0:
                row.urgency = "P0"
            elif days < lead.days and inb > 0:
                row.urgency = "P1"
            elif days < warn_days:
                row.urgency = "P2"
            else:
                row.urgency = "健康"

        if row.urgency == "P0":
            row.actions.append("空运/海外仓调拨补急；暂停该 SKU 广告，避免白烧")
            if velocity is not None:
                row.suggested = rec.get("suggest_num_sum")
            if row.suggested is None:
                row.notes.append("suggestNumSum 无数据，建议量需人工核定")
        elif row.urgency == "P1":
            if velocity is not None:
                row.gap = velocity * (lead.days - days) - inb
                suggest = rec.get("suggest_num_sum")
                effective = max(row.gap, suggest) if suggest is not None else row.gap
                if effective <= 0:
                    # 在途已覆盖到货周期内需求 → 无缺口。绝不输出负数补货量，
                    # 也绝不把它继续标成「P1 有缺口」误导采购。
                    row.gap = 0.0
                    row.suggested = 0.0
                    row.urgency = "P2" if days < warn_days else "健康"
                    row.notes.append("在途已覆盖到货周期内缺口（缺口≤0），无补货缺口，建议量按 0 处理")
                    if row.urgency == "P2":
                        row.actions.append("进入备货观察，按 lead_time 提前下单")
                else:
                    row.suggested = effective
                    if suggest is None:
                        row.notes.append("suggestNumSum 无数据，建议量按缺口计算")
            else:
                row.notes.append("⚠️ 日均销量无数据，缺口无法计算，需人工核查")
        elif row.urgency == "P2":
            row.actions.append("进入备货观察，按 lead_time 提前下单")
        elif row.urgency == "健康":
            pass

        # ---- 滞销判定 ----
        pct271, pct365 = _age_pct(rec, denom_mode)
        row.age_271_pct, row.age_365_pct = pct271, pct365
        # 销量轴口径：high_velocity_min 是【月销速率】(件/月)，与赛狐 salesVelocityMonth 同单位；
        # 而 row.velocity 是日均，故乘 30 年化后再比较，避免单位错配。
        velocity_month = velocity * 30.0 if velocity is not None else None
        low_velocity = velocity_month is not None and velocity_month < high_vel
        age_hot = (pct271 is not None and pct271 > age271_max) or (pct365 is not None and pct365 > age365_max)
        if age_hot and low_velocity:
            row.dead_stock = True
            row.actions.append("清货：降价/促销/移除，规避长期仓储费")
            fee = (skill_cfg.get("long_term_storage") or {}).get("fee_per_cubic_foot")
            if fee:
                row.notes.append(f"长期仓储费按配置费率估算：{fee}")
            else:
                row.notes.append("⚠️ 长期仓储费需人工核查（未配置费率，不编造金额）")

        # ---- 双向矩阵 ----
        high_velocity = velocity_month is not None and velocity_month >= high_vel
        urgent = row.urgency in ("P0", "P1", "P2")
        if high_velocity and urgent:
            row.quadrant = "① 紧急补货"
        elif high_velocity:
            row.quadrant = "② 健康"
        elif row.dead_stock:
            row.quadrant = "③ 滞销清理"
        else:
            row.quadrant = "④ 低效观察"

        result.rows.append(row)

    result.rows.sort(key=lambda r: URGENCY_ORDER.get(r.urgency, 9))
    counts: Dict[str, int] = {}
    for r in result.rows:
        counts[r.urgency] = counts.get(r.urgency, 0) + 1
        counts[r.quadrant] = counts.get(r.quadrant, 0) + 1
    result.counts = counts
    if result.unresolved_lead_mskus:
        result.warnings.append(
            f"⚠️ 有 {len(result.unresolved_lead_mskus)} 个 MSKU 未命中到货周期配置，"
            "已按默认值计算（见「周期来源」列）。建议批量补全并回写 lead_time.yaml。"
        )
    return result


def build_lead_time_specs(result: PlanResult, default_days: float = 45) -> List[Any]:
    """为「解析不到」的 MSKU 生成一次性批量询问的 ParamSpec 列表。

    需要 shared/scripts/config_loader.py 的 ParamSpec。
    """
    from config_loader import ParamSpec  # 延迟导入，避免无配置时导入失败

    return [
        ParamSpec(
            name=msku, file="lead_time", section="by_sku", key=msku,
            label=f"{msku} 到货周期(天)", default=default_days,
        )
        for msku in result.unresolved_lead_mskus
    ]