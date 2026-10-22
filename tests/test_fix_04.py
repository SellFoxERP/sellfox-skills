"""04-weekly-review 评测问题修复的回归测试（合成数据直测，零网络）。

覆盖（2026-10-16 离线/线上评测问题清单）：
1. [P2] 月报上期窗口：上一完整自然月（上月 1 日 ~ 月末），不再丢月末一日（08-31）；
   周报上期仍是等长前一周，不受影响。
2. [失败语义] 本期 profit failed → 核心结论写明「取数失败（error_code：X），异常判定不可用」，
   绝不输出「本期未触发配置阈值内的异常项」。
3. [失败语义] failed 时核心指标「本期」列标「⚠️ 需人工核查」而非「不适用」；
   fetch rc=2 是信号非中断（weekly_data.json 照写，compute/render 可继续）。
4. [字段名] 广告花费环比列取 deltas 的 ad_cost_rate_pct（compute 实际产出键）。
5. [格式] 退款率按小数原值换算百分比并带单位（0.031 → 3.1%）。
6. [格式] 币种汇总表金额走千分位/两位小数（15224.099999999999 → 15,224.10）。
7. [币种] MSKU 榜加币种列；榜单跨币种未折算在数据说明披露。
8. [披露] 多币种时已取得的 charData 被弃用 → 趋势注记 + 数据说明披露「已获取、未采用」。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "04-weekly-review" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# fetch_weekly_data 内部 `import period` → 先以同名注册，保证共享同一模块实例
period_mod = _load("period", SCRIPTS / "period.py")
fetch_mod = _load("wrf_fetch", SCRIPTS / "fetch_weekly_data.py")
compute_mod = _load("wrf_compute", SCRIPTS / "compute_metrics.py")
render_mod = _load("wrf_render", SCRIPTS / "render_report.py")

ANCHOR = "2026-10-16"
WEEK_START, WEEK_END = "2026-10-05", "2026-10-11"


def envelope(tool, rows=None, *, status="complete", list_key="reportList",
             error_code=None, message="", data=None):
    rows = rows or []
    obj = {"status": status, "tool_id": tool, "warnings": [],
           "period": {"start_date": WEEK_START, "end_date": WEEK_END},
           "coverage": {"complete": True, "fetched_count": len(rows),
                        "total_count": len(rows), "truncated": False},
           "data": {list_key: rows} if data is None else data}
    if status != "complete":
        obj["error_code"] = error_code
        obj["message"] = message
        obj["coverage"] = None
    return obj


def shop_row(shop, sales, *, currency="USD", num=1000, gp_rate=0.2,
             ad_rate=0.12, refund=0.031):
    """单行店铺利润：cpcCost 为负数费用（weekly.yaml 口径），refundRate 为小数原值。"""
    return {"shopId": shop, "currency": currency, "productSales": sales,
            "salesNum": num, "grossProfit": round(sales * gp_rate, 3),
            "cpcCost": round(-sales * ad_rate, 3), "refundRate": refund}


def msku_row(tag, sales, gp=None, currency="USD"):
    return {"mskuList": f"MSKU-T4-{tag}", "asinList": f"B0T4{tag}",
            "currency": currency, "productSales": sales,
            "salesNum": max(1, int(sales / 10)),
            "grossProfit": gp if gp is not None else round(sales * 0.2, 3),
            "cpcCost": round(-(gp if gp is not None else sales * 0.2) * 0.5, 3)}


def write_raw(raw: Path, name: str, obj) -> Path:
    raw.mkdir(parents=True, exist_ok=True)
    p = raw / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def run_pipeline(raw: Path):
    """fetch_all → compute → render（零网络，全在进程内）。"""
    weekly = fetch_mod.fetch_all("week", ANCHOR, "settlement", None, "",
                                 raw_dir_arg=str(raw))
    computed = compute_mod.compute(weekly)
    md = render_mod.render(computed, include_mermaid=False)
    return weekly, computed, md


def table_row(md: str, label: str):
    """取 Markdown 表格中「| label | ...」一行的单元格列表（去首尾空单元格）。"""
    for line in md.splitlines():
        if line.startswith(f"| {label} |"):
            return [c.strip() for c in line.strip("|").split("|")]
    raise AssertionError(f"表格行未找到：{label}\n{md}")


class RawDirMixin:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name) / "raw"


class MonthPrevWindowTest(unittest.TestCase):
    """[P2] 月报上期窗口 = 上一完整自然月（含月末一日）；周报逻辑不动。"""

    def test_september_prev_is_full_august(self):
        w = period_mod.build_plan("month", "2026-10-16")["windows"]
        self.assertEqual(w["prev"]["start_iso"], "2026-08-01")
        self.assertEqual(w["prev"]["end_iso"], "2026-08-31")
        self.assertEqual(w["prev"]["start_profit"], "20260801")
        self.assertEqual(w["prev"]["end_profit"], "20260831")

    def test_month_prev_month_same_period_equals_prev(self):
        w = period_mod.build_plan("month", "2026-10-16")["windows"]
        self.assertEqual(w["prev_month_same_period"]["start_iso"], "2026-08-01")
        self.assertEqual(w["prev_month_same_period"]["end_iso"], "2026-08-31")

    def test_leap_february_prev_keeps_january_end(self):
        w = period_mod.build_plan("month", "2024-03-01")["windows"]
        self.assertEqual((w["current"]["start_iso"], w["current"]["end_iso"]),
                         ("2024-02-01", "2024-02-29"))
        self.assertEqual((w["prev"]["start_iso"], w["prev"]["end_iso"]),
                         ("2024-01-01", "2024-01-31"))

    def test_december_prev_crosses_year(self):
        w = period_mod.build_plan("month", "2026-01-31")["windows"]
        self.assertEqual((w["prev"]["start_iso"], w["prev"]["end_iso"]),
                         ("2025-11-01", "2025-11-30"))

    def test_week_prev_unchanged_equal_length(self):
        w = period_mod.build_plan("week", ANCHOR)["windows"]
        self.assertEqual((w["current"]["start_iso"], w["current"]["end_iso"]),
                         (WEEK_START, WEEK_END))
        self.assertEqual((w["prev"]["start_iso"], w["prev"]["end_iso"]),
                         ("2026-09-28", "2026-10-04"))
        # 上月同期仍是「整体前移一个自然月」（日期同号）
        self.assertEqual((w["prev_month_same_period"]["start_iso"],
                          w["prev_month_same_period"]["end_iso"]),
                         ("2026-09-05", "2026-09-11"))


class FailedSemanticsTest(RawDirMixin, unittest.TestCase):
    """[失败语义] 本期 profit failed：rc=2 信号非中断 + 核心结论/单元格语义。"""

    def _raw(self):
        write_raw(self.raw, "profit_shop_current.json", envelope(
            "get_profit_report_by_shop", status="failed",
            error_code="CONTRACT_VERSION_MISMATCH",
            message="Contract changed; no business request was sent."))
        write_raw(self.raw, "profit_shop_prev.json", envelope(
            "get_profit_report_by_shop", [shop_row("SHOP-T4-1", 10400.04, ad_rate=0.13)]))
        write_raw(self.raw, "profit_msku.json", envelope(
            "get_profit_report_by_msku", [msku_row(f"{i:04d}", 1000 * (10 - i)) for i in range(10)]))

    def test_fetch_exit2_but_writes_weekly_data(self):
        self._raw()
        out = Path(self.temp.name) / "weekly_data.json"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "fetch_weekly_data.py"),
             "--period", "week", "--anchor", ANCHOR, "--profit-caliber", "settlement",
             "--raw-dir", str(self.raw), "--out", str(out)],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 2)                      # rc=2 = 信号
        self.assertTrue(out.exists())                             # 中间产物照写 → 可继续
        self.assertIn("缺失或失败", proc.stdout)

    def test_failed_source_carries_error_code(self):
        self._raw()
        weekly = fetch_mod.fetch_all("week", ANCHOR, "settlement", None, "",
                                     raw_dir_arg=str(self.raw))
        src = weekly["sources"]["profit_current"]
        self.assertEqual(src["status"], "error")
        self.assertEqual(src["error_code"], "CONTRACT_VERSION_MISMATCH")

    def test_core_conclusion_and_cells(self):
        self._raw()
        _, computed, md = run_pipeline(self.raw)
        # 状态结构化透传（compute → render）
        self.assertEqual(computed["source_status"]["profit_current"]["error_code"],
                         "CONTRACT_VERSION_MISMATCH")
        # 核心结论：明确取数失败 + 异常判定不可用，绝不说「未触发异常项」
        self.assertIn("本期利润数据取数失败（error_code：CONTRACT_VERSION_MISMATCH）", md)
        self.assertIn("异常判定不可用", md)
        self.assertNotIn("未触发配置阈值内的异常项", md)
        # 本期列升级为「⚠️ 需人工核查」，其余窗口列不受影响
        self.assertEqual(table_row(md, "GMV")[1], "⚠️ 需人工核查")
        self.assertEqual(table_row(md, "毛利率")[1], "⚠️ 需人工核查")
        self.assertEqual(table_row(md, "退款率")[1], "⚠️ 需人工核查")
        self.assertEqual(table_row(md, "GMV")[2], "10,400.04")    # 上期照常
        # 数据说明仍记录契约失败事件
        self.assertIn("error_code=CONTRACT_VERSION_MISMATCH", md)


class HealthyWeekRenderTest(RawDirMixin, unittest.TestCase):
    """[字段名/格式] 单币种正常链路：广告花费环比、退款率百分比。"""

    def setUp(self):
        super().setUp()
        write_raw(self.raw, "profit_shop_current.json", envelope(
            "get_profit_report_by_shop",
            [shop_row("SHOP-T4-1", 8000.00, ad_rate=0.12, refund=0.031),
             shop_row("SHOP-T4-2", 3999.96, ad_rate=0.12, refund=0.031)]))
        write_raw(self.raw, "profit_shop_prev.json", envelope(
            "get_profit_report_by_shop",
            [shop_row("SHOP-T4-1", 6933.36, ad_rate=0.13, refund=0.028)]))
        write_raw(self.raw, "profit_msku.json", envelope(
            "get_profit_report_by_msku", [msku_row(f"{i:04d}", 900 * (10 - i)) for i in range(10)]))
        self.weekly, self.computed, self.md = run_pipeline(self.raw)

    def test_ad_cost_delta_uses_ad_cost_rate_pct(self):
        # compute 只产 ad_cost_rate_pct（占比口径），渲染不得取不存在的 ad_cost_pct
        self.assertNotIn("ad_cost_pct", self.computed["deltas"]["prev"])
        self.assertAlmostEqual(self.computed["deltas"]["prev"]["ad_cost_rate_pct"],
                               (12.0 - 13.0) / 13.0 * 100, places=1)
        row = table_row(self.md, "广告花费")
        self.assertEqual(row[3], "-7.7%")                         # 环比列有值，不再是「不适用」

    def test_refund_rate_shown_as_percent(self):
        row = table_row(self.md, "退款率")
        self.assertEqual(row[1], "3.1%")                          # 0.031 → 3.1%
        self.assertEqual(row[2], "2.8%")                          # 0.028 → 2.8%
        self.assertNotIn("| 退款率 | 0.03", self.md)              # 不再裸显小数原值

    def test_healthy_report_keeps_no_anomaly_line(self):
        self.assertIn("本期未触发配置阈值内的异常项", self.md)
        self.assertNotIn("取数失败", self.md)


class MultiCurrencyRenderTest(RawDirMixin, unittest.TestCase):
    """[币种/披露] 多币种：汇总表格式化、榜单币种列、charData 弃用披露。"""

    def setUp(self):
        super().setUp()
        write_raw(self.raw, "profit_shop_current.json", envelope(
            "get_profit_report_by_shop",
            [shop_row("SHOP-T4-US", 5120.80, currency="USD"),
             shop_row("SHOP-T4-DE", 15224.099999999999, currency="EUR")]))
        write_raw(self.raw, "profit_shop_prev.json", envelope(
            "get_profit_report_by_shop", [shop_row("SHOP-T4-US", 4600.0, currency="USD")]))
        write_raw(self.raw, "profit_msku.json", envelope(
            "get_profit_report_by_msku",
            [msku_row("9001", 9000.0, 1800.0, currency="USD"),
             msku_row("9002", 8000.0, 1200.0, currency="EUR"),
             msku_row("9003", 6000.0, 900.0, currency="USD")] * 2))
        write_raw(self.raw, "trend.json", envelope(
            "get_product_analyze_detail", data={"charData": [{"day": "2026-10-05", "gmv": 1.0}]}))
        self.weekly, self.computed, self.md = run_pipeline(self.raw)

    def test_currency_summary_amounts_formatted(self):
        self.assertIn("| EUR | 15,224.10 |", self.md)             # 千分位/两位小数
        self.assertNotIn("15224.099999999999", self.md)          # 浮点尾数不外露
        self.assertIn("| USD | 5,120.80 |", self.md)

    def test_ranking_has_currency_column_and_disclosure(self):
        # 标题列全缺时整列隐藏（商家视角清理空「不适用」列）
        self.assertIn("| MSKU | ASIN | 币种 |", self.md)
        self.assertNotIn("| 标题 |", self.md)
        self.assertIn("⚠️ 榜单跨币种未折算", self.md)
        self.assertIn("各行金额为原币种", self.md)
        eur = [ln for ln in self.md.splitlines() if ln.startswith("| MSKU-T4-9002 ")][0]
        self.assertIn("| EUR |", eur)                             # EUR 行带原币种标注

    def test_chardata_discarded_with_disclosure(self):
        # 趋势：多币种不拼一条线，但已取得的 charData 须披露「已获取、未采用」
        self.assertEqual(self.computed["trend"]["status"], "mixed_currency")
        self.assertIn("已获取 charData（单币种序列）但多币种不可加总，未采用",
                      self.computed["trend"]["note"])
        self.assertIn("charData 已获取（单币种序列），但本期利润行多币种、不可加总，未采用",
                      self.md)                                    # 数据说明补披露

    def test_mixed_currency_still_no_cross_sum(self):
        self.assertIn("禁止跨币种直接相加", self.md)
        self.assertEqual(table_row(self.md, "GMV")[1], "不适用")  # 顶层金额不跨币种合计


class RankingsUnitTest(unittest.TestCase):
    """[币种] rankings() 每行带 currency（06 已确认字段名）。"""

    def test_rows_carry_currency(self):
        fm = {"gmv": "productSales", "sales_num": "salesNum", "gross_profit": "grossProfit",
              "gross_profit_rate": "grossProfitRate", "ad_cost": "cpcCost"}
        rows = [msku_row("8001", 100.0, 20.0, currency="EUR"),
                msku_row("8002", 200.0, 40.0, currency="USD")]
        res = compute_mod.rankings(rows, fm, 5, {})
        self.assertEqual([r["currency"] for r in res["rows"]], ["EUR", "USD"])


if __name__ == "__main__":
    unittest.main()
