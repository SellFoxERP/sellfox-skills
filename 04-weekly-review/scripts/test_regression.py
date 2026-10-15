#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试（周报 / 月报计算层）。无第三方依赖，直接 ``python scripts/test_regression.py`` 运行。

覆盖两个曾出错的点（均为本次修复的回归护栏）：
  1. 达成率 ``month_key`` 必须与 basis 同月 —— anchor 落在月初时，本期窗口会落在上个月，
     若取「本期窗口起始月」会把本月累计去比上个月目标（数值与评级皆错）。
  2. 榜单标识列字段名未核实 —— 取不到时必须标「需人工核查」，不得臆造字段名或静默留空。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compute_metrics as cm  # noqa: E402
import period as p  # noqa: E402


def test_achievement_month_key_aligns_to_anchor_month() -> None:
    """月初 anchor 的周报达成率，month_key 必须等于 anchor 所在月（= month_to_date 月）。"""
    targets = {"monthly": {"2026-10": {"gmv": 2000}},
               "achievement": {"excellent_min": 100, "warning_min": 80}}
    mtd = {"gmv": 1800, "gross_profit": 300, "gross_profit_rate": 0.17, "ad_cost_rate": 14.0}
    cur = {"gmv": 1500, "gross_profit": 280, "gross_profit_rate": 0.18, "ad_cost_rate": 13.3}
    for anchor in ("2026-10-01", "2026-10-05", "2026-10-15"):
        plan = p.build_plan("week", anchor)
        out = cm.achievement(plan, targets, mtd, cur)
        assert out["status"] == "ok", out
        assert out["month_key"] == "2026-10", f"anchor={anchor} month_key={out['month_key']}"
        assert out["basis"] == "本月累计", out["basis"]


def test_rank_identifier_never_fabricated() -> None:
    """get_profit_report_by_msku 行里没有可确认的标识字段 → 标识列留空并计数，不臆造。"""
    rows = [{"productSales": 100, "salesNum": 1, "grossProfit": 10, "cpcCost": -1}]
    fm = {"gmv": "productSales", "sales_num": "salesNum", "gross_profit": "grossProfit",
          "gross_profit_rate": "grossProfitRate", "ad_cost": "cpcCost"}
    res = cm.rankings(rows, fm, 5, {"msku_candidates": ["msku", "spu"],
                                    "asin_candidates": ["asin"], "title_candidates": ["title"]})
    assert res["identifier_unresolved"] == 1
    assert res["rows"][0]["msku"] is None and res["rows"][0]["asin"] is None


def main() -> int:
    tests = [test_achievement_month_key_aligns_to_anchor_month, test_rank_identifier_never_fabricated]
    for t in tests:
        t()
        print(f"✅ {t.__name__}")
    print("ALL REGRESSION TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())