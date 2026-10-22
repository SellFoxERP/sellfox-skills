"""Step 6：定价与利润测算（含跨币种显式处理）。

数据来源与口径（字段名已核对 01/06 文档，勿凭记忆改）：

| 值 | 字段 | 所属工具 | 币种 |
|---|---|---|---|
| 售价 | ``listingPrice`` / ``standardPrice`` | get_online_product_page_list | 顶层 ``currency``（实测 USD） |
| ~~售价~~ 聚合金额 | ``salePrices``（线上实测为销售金额聚合，值=单价×整数倍数，**不可作单价**） | get_online_product_page_list | 顶层 ``currency`` |
| 采购成本 | ``purchaseCost`` | get_online_product_page_list | 成本口径（CNY） |
| 平台佣金 | ``referralFee`` | get_online_product_page_list | 售价口径 |
| FBA 费用 | ``fbaFees`` | get_online_product_page_list | 售价口径 |
| 变动结算费 | ``variableClosingFee`` | get_online_product_page_list | 售价口径 |
| 头程 | ``headTripCost``（商品行，唯一来源） | get_online_product_page_list | 成本口径 |

铁律：
- 售价（USD）与成本（CNY）**不得直接相减**；必须经 ``config/fx.yaml`` 换算并标注来源/日期。
- 汇率未配置 → 标「⚠️ 需人工核查」，**不臆造汇率、不给假数字**。
- 头程消费商品行 ``headTripCost``（线上实测返回；来源标注「商品行」，成本口径换算入账）；
  缺值需人工核查（v1.7.0 起自定义报表宽表 ``headTripPrice`` 补充源已移除，
  宽表口径需求转 custom-report-analysis 技能）。
"""

from __future__ import annotations

import argparse
import json
import math
from typing import Any, Dict, Optional

from _common import Notes, as_float, load_skill_yaml


def _convert(
    amount: Optional[float],
    from_ccy: Optional[str],
    to_ccy: str,
    fx: Dict[str, Any],
    notes: Notes,
    label: str,
) -> Optional[float]:
    """把 amount 从 from_ccy 换算到 to_ccy。跨币种缺汇率时返回 None 并标人工核查。"""
    if amount is None:
        return None
    if not from_ccy or not to_ccy:
        notes.need_manual(f"{label} 币种不明确，需人工核查")
        return None
    if from_ccy == to_ccy:
        return amount
    rate = fx.get("rate")
    base = fx.get("base")
    quote = fx.get("quote")
    if rate is None or not base or not quote:
        notes.need_manual(
            f"{label} 需要把 {from_ccy} 换算为 {to_ccy}，但 config/fx.yaml 未配置汇率，需人工核查"
        )
        return None
    try:
        valid_rate = float(rate)
    except (TypeError, ValueError):
        valid_rate = 0.0
    if not math.isfinite(valid_rate) or valid_rate <= 0:
        notes.need_manual(f"{label} 汇率须为正数")
        return None
    if not fx.get("source"):
        notes.need_manual(f"{label} 汇率须填写来源")
        return None
    if not fx.get("as_of"):
        notes.need_manual(f"{label} 汇率须填写日期")
        return None
    # 支持 base<->quote 两个方向。
    if from_ccy == quote and to_ccy == base:
        converted = amount / float(rate)
    elif from_ccy == base and to_ccy == quote:
        converted = amount * float(rate)
    else:
        notes.need_manual(
            f"{label} 的汇率方向未覆盖（{from_ccy}→{to_ccy}），请扩充 config/fx.yaml，需人工核查"
        )
        return None
    notes.note_source(f"fx:{label}", f"{fx.get('source')} @ {fx.get('as_of')}")
    return converted


def estimate_pricing(
    product: Dict[str, Any],
    *,
    notes: Optional[Notes] = None,
) -> Dict[str, Any]:
    """基于在线产品记录做定价与利润测算。

    :param product: ``fetch_baseline.summarize_product`` 的输出。
    """
    notes = notes or Notes()
    launch = load_skill_yaml("launch")
    pricing = launch.get("pricing") or {}
    fx = load_skill_yaml("fx")

    price_ccy = product.get("price_currency")
    cost_ccy = product.get("inventory_currency")

    # 售价链：listingPrice > standardPrice。第三级 salePrices 旧链把金额聚合当单价
    # （eval3-2026-10-22 数据源问题 N2：289.35 为销售金额聚合形态）——该字段已标注聚合
    # （见模块文档表），识别到此形态即拒绝用作单价并披露；缺单价走人工提示（fail-closed）。
    selling_price = product.get("listing_price") or product.get("standard_price")
    if selling_price is None and product.get("sale_prices") is not None:
        notes.need_manual(
            "售价：listingPrice/standardPrice 均缺；salePrices 为聚合金额"
            "（值=单价×整数倍数），不可作单价——单件售价需人工核查"
        )
    purchase_cost_raw = product.get("purchase_cost")
    referral = product.get("referral_fee")
    fba = product.get("fba_fees")
    vcs = product.get("variable_closing_fee")

    result: Dict[str, Any] = {
        "price_currency": price_ccy,
        "cost_currency": cost_ccy,
        "selling_price": selling_price,
        "lines": [],
        "manual_checks": [],
    }

    # 采购成本：成本口径 → 换算到售价口径。
    purchase_cost = _convert(purchase_cost_raw, cost_ccy, price_ccy, fx, notes, "采购成本")
    result["purchase_cost"] = purchase_cost
    result["purchase_cost_raw"] = purchase_cost_raw

    # 费用缺省回退（仅在无真实字段时）。
    if referral is None and pricing.get("default_referral_fee_pct") is not None and selling_price:
        referral = selling_price * float(pricing["default_referral_fee_pct"]) / 100.0
        result["lines"].append("referral_fee：用配置缺省佣金率估算")
    elif referral is None:
        notes.need_manual("平台佣金（referralFee）取不到且未配置缺省佣金率，需人工核查")
    if fba is None:
        if pricing.get("default_fba_fee") is not None:
            fba = float(pricing["default_fba_fee"])
            result["lines"].append("fba_fees：用配置缺省值估算")
        else:
            notes.need_manual("FBA 费用（fbaFees）取不到且未配置缺省值，需人工核查")

    # 头程：消费商品行 headTripCost（线上实测字段；成本口径 → 换算到售价口径），
    # 缺值需人工核查（v1.7.0 起宽表 headTripPrice 补充源已移除，转 custom-report-analysis 技能）。
    note_headtrip = pricing.get("headtrip_source")
    headtrip_raw = product.get("headtrip_cost")
    headtrip = _convert(headtrip_raw, cost_ccy, price_ccy, fx, notes, "头程成本")
    if headtrip_raw is None:
        notes.need_manual(f"头程成本：{note_headtrip}")
    else:
        notes.note_source("headtrip", "商品行 headTripCost")
        result["headtrip_source"] = "商品行 headTripCost"

    # 组装单件利润（全部换算到售价口径）。
    cost_components: Dict[str, Optional[float]] = {
        "purchase_cost": purchase_cost,
        "referral_fee": referral,
        "fba_fees": fba,
        "variable_closing_fee": vcs,
        "headtrip": headtrip,
    }
    result["cost_breakdown"] = dict(cost_components)

    # total_cost 判据只看与成本直接相关的必填项是否**实际缺值**（采购成本/头程/佣金/FBA 等），
    # 不看 notes.manual_checks——后者恒含字符限制等无关项，会把任何配置下的利润都拦掉。
    missing_costs = [k for k, v in cost_components.items() if v is None]
    total_cost: Optional[float] = None
    if not missing_costs:
        total_cost = sum(cost_components.values())

    est_profit: Optional[float] = None
    est_margin: Optional[float] = None
    if selling_price is not None and total_cost is not None:
        est_profit = selling_price - total_cost
        if selling_price:
            est_margin = round(est_profit / selling_price * 100.0, 2)

    result["total_cost"] = None if total_cost is None else round(total_cost, 2)
    result["estimated_profit"] = None if est_profit is None else round(est_profit, 2)
    result["estimated_margin_pct"] = est_margin
    result["cost_complete"] = total_cost is not None

    # 目标毛利倒推建议售价。
    target = pricing.get("target_gross_margin_pct")
    if target is not None and total_cost is not None:
        t = float(target) / 100.0
        if t < 1:
            result["target_margin_pct"] = target
            result["suggested_price"] = round(total_cost / (1 - t), 2)
    elif target is not None:
        notes.need_manual(
            f"成本不完整（缺 {'、'.join(missing_costs)}），无法倒推建议售价，需人工核查"
        )

    result["manual_checks"] = list(notes.manual_checks)
    if not result["cost_complete"]:
        notes.warn("利润测算不完整：存在需人工核查的费用项，估算值仅供参考，勿当作最终利润。")
    return result


def _main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description="定价与利润测算")
    ap.add_argument("--product-json", required=True, help="商品基线 JSON 文件")
    args = ap.parse_args()
    product = json.loads(open(args.product_json, encoding="utf-8").read())
    notes = Notes()
    result = estimate_pricing(product, notes=notes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())