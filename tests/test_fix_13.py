"""13-sales-profit-analysis 修复回归测试（2026-10-16 线上评测 N1-N14）。

直测 14 条修复的行为（零网络，只喂合成/归档信封）：
  1. [N1] int bizDay 全链路不崩：日期键归一为 str，排序/剔除/并集不再 TypeError；
  2. [N2] 贷项按主符号方向判定：正号存储（线上形态）下负值日=冲回检出、正常费用日零假阳性；
     负号存储回归（与主符号相反的正值日=冲回）；
  3. [N6] 榜提取兼容嵌套 analyze 行（asinList/mskuList + fieldsMap.profitPrice/saleNum）
     与扁平行（asin/msku/grossProfit）；reportList/rows 两种 list_key；
  4. [N7] 聚合行按业务键（店铺/MSKU/日期）去重，dedup N→M 披露；
  5. [N8] 单窗口快照（base=None）：口径行不拼「vs None」、结论给本期快照数；
  6. [N10] 渲染兜底脱敏用 mask_pii_in_text（str 恒等的 mask_pii 不再使用）；
  7. [N3] cross_check_mismatch 渲染进 ⑤（日分段≠聚合行逐项可见）；
  8. [N4] daily_current/daily_base 信封（含 failed）都进 coverage；
  9. [N5] rank 信封（含 failed）始终进 coverage，榜缺失时渲染「榜单不可得」；
 10. [N9] 演示指纹扫描含 order_*.json / shops.json（订单侧指纹触发水印）；
 11. [N11] 桥接字段全缺 → 「无数据（非 0）」非 0.00；币种从行 currency 集合生成；
 12. [N12] 停投断点 days 按去重日期数计；单量字段全缺时跳过并标注；
 13. [N13/N14] 文档契约口径（workflow.md/SKILL.md 不再有 options.product_analysis 与 status 1·2）；
 14. selfcheck 独立可跑（三场景全绿）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "13-sales-profit-analysis"
SCRIPTS = SKILL / "scripts"
EVAL = SKILL / "out" / "eval-live"

sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"fix13_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"fix13_{name}"] = module
    spec.loader.exec_module(module)
    return module


run_analysis = _load("run_analysis")
render_report = _load("render_report")


def write_env(path: Path, rows, list_key="reportList", status="complete", **extra):
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {"status": status, "data": {list_key: rows},
           "coverage": {"complete": True, "truncated": False},
           "warnings": [], "trace_id": "t"}
    env.update(extra)
    path.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")


PLAN_TWO = {"caliber": "settlement", "granularity": "msku", "cost_layer": "batch",
            "current": {"start": "20261015", "end": "20261021"},
            "base": {"start": "20260924", "end": "20261007"}}


def positive_rows(days_cpc, credit_day=None, credit_value=-273.6):
    """正号存储（线上实测形态）合成行：成本费用全为正值。"""
    base = {"productSales": "1000", "salesNum": "10", "currency": "USD",
            "orderIncome": "1000", "orderExpend": "300", "serviceFeeExpend": "100",
            "refund": "50", "purchasePcFeeBatch": "200", "headTripPcFeeBatch": "80",
            "grossProfit": "150"}
    rows = [dict(base, bizDay=d, cpcCost=v) for d, v in days_cpc]
    if credit_day is not None:
        rows.append(dict(base, bizDay=credit_day, cpcCost=credit_value, grossProfit="543.6"))
    return rows


class Fix13AnalysisTests(unittest.TestCase):
    """run_analysis.py：N1/N2/N4/N5/N6/N7/N9/N11/N12。"""

    def test_n1_int_bizday_no_crash_and_str_keys(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            cur = positive_rows([(20261015, "120")])
            base = positive_rows([(20261001, "120"), (20261003, "120")],
                                 credit_day=20261008)
            write_env(raw / "agg_current.json", cur)
            write_env(raw / "agg_base.json", base)
            write_env(raw / "daily_current" / "seg1.json", cur)
            write_env(raw / "daily_base" / "seg1.json", base)
            (raw / "analysis_plan.json").write_text(json.dumps(PLAN_TWO), encoding="utf-8")
            result = run_analysis.build_analysis(raw, PLAN_TWO)  # 修复前此处 TypeError
            dc = result["daily_checks"]
            self.assertTrue(all(isinstance(d, str) for d in dc["credit_days_union"]))
            self.assertEqual(dc["credit_days_union"], ["20261008"])

    def test_n2_positive_storage_dominant_sign(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            cur = positive_rows([(20261015, "120")])
            base = positive_rows([(20261001, "120"), (20261003, "120")],
                                 credit_day=20261008)
            write_env(raw / "agg_current.json", cur)
            write_env(raw / "agg_base.json", base)
            write_env(raw / "daily_current" / "seg1.json", cur)
            write_env(raw / "daily_base" / "seg1.json", base)
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            dc = result["daily_checks"]
            # 主符号=+（正号存储）；正常费用日零假阳性
            self.assertEqual(dc["credit_signs"]["cpcCost"], 1)
            self.assertEqual(dc["credit_days"], [])
            # 真实冲回（负值日）检出并进剔后 Δ
            ex = dc["base_ex_credit_bridge"]
            self.assertEqual(ex["excluded_days"], ["20261008"])
            self.assertEqual(ex["base_gp_ex_credit"], 300.0)
            self.assertEqual(ex["delta_gp_ex_credit"], 150.0 - 300.0)

    def test_n2_negative_storage_regression(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            day = {"productSales": "1000", "grossProfit": "200", "orderIncome": "1000",
                   "orderExpend": "-300", "serviceFeeExpend": "-100", "refund": "-50",
                   "purchasePcFeeBatch": "-200", "headTripPcFeeBatch": "-80"}
            cur = [dict(day, bizDay="20261015", cpcCost="-120")]
            base = [dict(day, bizDay="20261001", cpcCost="-70", grossProfit="200"),
                    dict(day, bizDay="20261003", cpcCost="60", grossProfit="330")]
            write_env(raw / "agg_current.json", cur)
            write_env(raw / "agg_base.json", base)
            write_env(raw / "daily_current" / "seg1.json", cur)
            write_env(raw / "daily_base" / "seg1.json", base)
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            dc = result["daily_checks"]
            self.assertEqual(dc["credit_signs"]["cpcCost"], -1)   # 负号存储主符号
            self.assertEqual(dc["credit_days"], [])               # 正常负值日不误报
            self.assertEqual(dc["base_ex_credit_bridge"]["excluded_days"], ["20261003"])

    def test_n6_nested_and_flat_rank_rows(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            write_env(raw / "agg_current.json", positive_rows([(20261015, "0")]))
            nested = [
                {"asinList": ["B0TOP9"], "mskuList": ["M-9"],
                 "fieldsMap": {"profitPrice": "812.45", "saleNum": "300"}},
                {"asinList": ["B0LOSS"], "mskuList": ["M-L"],
                 "fieldsMap": {"profitPrice": "-273.6", "saleNum": "90"}},
            ]
            write_env(raw / "rank_current.json", nested, list_key="rows")
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            rk = result["rankings"]["current"]
            self.assertEqual(rk["top"][0]["asin"], "B0TOP9")
            self.assertEqual(float(rk["top"][0]["grossProfit"]), 812.45)
            self.assertEqual(rk["bottom"][0]["asin"], "B0LOSS")
            self.assertEqual(rk["skipped_zero_sales"], 0)

    def test_n6_reportlist_list_key_rank_rows(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            write_env(raw / "agg_current.json", positive_rows([(20261015, "0")]))
            flat = [{"asin": "B0F1", "msku": "M-F1", "productSales": "500",
                     "grossProfit": "800", "salesNum": "50"},
                    {"asin": "B0F2", "msku": "M-F2", "productSales": "300",
                     "grossProfit": "-350", "salesNum": "9"}]
            write_env(raw / "rank_current.json", flat, list_key="reportList")
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            rk = result["rankings"]["current"]
            self.assertEqual(rk["top"][0]["asin"], "B0F1")
            self.assertEqual(float(rk["bottom"][0]["grossProfit"]), -350.0)

    def test_n5_failed_rank_notes_always_in_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            write_env(raw / "agg_current.json", positive_rows([(20261015, "0")]))
            write_env(raw / "rank_current.json", [], list_key="rows",
                      status="failed", error_code="RESULT_BUDGET_EXCEEDED")
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            notes = [n for n in result["coverage"] if n["step"] == "rank_current"]
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]["error_code"], "RESULT_BUDGET_EXCEEDED")
            self.assertEqual(notes[0]["status"], "failed")

    def test_n4_daily_current_failed_in_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            write_env(raw / "agg_current.json", positive_rows([(20261015, "0")]))
            write_env(raw / "daily_current" / "seg0.json", [], status="failed",
                      error_code="RESULT_BUDGET_EXCEEDED")
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            steps = {(n["step"], n.get("error_code")) for n in result["coverage"]}
            self.assertIn(("daily_current", "RESULT_BUDGET_EXCEEDED"), steps)

    def test_n7_dedup_by_business_key(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            row = {"bizDay": 20261015, "productSales": "100", "grossProfit": "30",
                   "shopInfos": [{"shopId": 704382}], "mskuList": ["M-1"]}
            other = dict(row, mskuList=["M-2"], grossProfit="10")
            write_env(raw / "agg_current.json", [row, dict(row), other])  # 1 条重复行
            write_env(raw / "agg_base.json", [dict(other, bizDay=20260901)])
            (raw / "analysis_plan.json").write_text(json.dumps(PLAN_TWO), encoding="utf-8")
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            self.assertEqual(result["periods"]["current"]["dedup"],
                             {"before": 3, "after": 2})
            self.assertEqual(result["periods"]["current"]["rows"], 2)
            self.assertAlmostEqual(result["periods"]["current"]["gp"], 40.0)

    def test_n7_no_key_rows_not_deduped(self):
        rows = [{"bizDay": 20261015, "productSales": "100", "grossProfit": "30"},
                {"bizDay": 20261015, "productSales": "80", "grossProfit": "5"}]
        out, dd = run_analysis.dedup_rows(rows)
        self.assertEqual(len(out), 2)   # 无店铺/MSKU 业务键 → 不冒险去重
        self.assertEqual(dd, {"before": 2, "after": 2})

    def test_n9_order_and_shops_demo_scan(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            write_env(raw / "agg_current.json", positive_rows([(20261015, "0")]))
            write_env(raw / "order_current.json",
                      [{"amazonOrderId": "ORDER-DEMO-1001", "shopName": "演示店铺1001",
                        "buyerName": "张三丰", "buyerEmail": "zhangsanfeng@example.com"}])
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            self.assertIsNotNone(result["demo_fingerprint"])  # 订单侧指纹触发水印
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            write_env(raw / "agg_current.json", positive_rows([(20261015, "0")]))
            write_env(raw / "shops.json", [{"shopName": "演示店铺1001"}], list_key="rows")
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            self.assertIsNotNone(result["demo_fingerprint"])

    def test_n11_bridge_missing_is_none_and_currency(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            rows = [{"bizDay": 20261015, "productSales": "1000", "grossProfit": "230",
                     "currency": "USD", "cpcCost": "0"}]
            write_env(raw / "agg_current.json", rows)
            write_env(raw / "agg_base.json", [dict(rows[0], bizDay=20260901)])
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            cur = result["periods"]["current"]
            self.assertIsNone(cur["bridge"]["orderIncome"])   # 全缺 → None（无数据非 0）
            self.assertEqual(cur["bridge"]["cpcCost"], 0.0)   # 真值 0 仍是 0
            self.assertIsNone(result["delta"]["bridge"]["orderIncome"])
            self.assertEqual(result["currencies"], "USD")
        # 多币种 → 「X/Y 分开未折算」；无行 → 未知
        rows = [{"productSales": "1", "grossProfit": "1", "currency": "USD"},
                {"productSales": "1", "grossProfit": "1", "currency": "CAD"}]
        self.assertEqual(run_analysis._currency_desc(rows), "CAD/USD 分开未折算")
        self.assertEqual(run_analysis._currency_desc([]), "未知")

    def test_n12_stall_days_by_distinct_dates(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            def day_rows(d, mskus=2):
                return [{"bizDay": f"2026101{d}", "mskuList": [f"M-{i}"],
                         "productSales": "50", "cpcCost": "0",
                         "adSpOrderNum": "0", "cpcSalesNum": "0"} for i in range(mskus)]
            daily = day_rows(5) + day_rows(6) + day_rows(7)   # 3 天 × 2 行 = 6 行
            write_env(raw / "agg_current.json", positive_rows([(20261015, "10")]))
            write_env(raw / "daily_current" / "seg1.json", daily)
            write_env(raw / "daily_base" / "seg1.json",
                      positive_rows([(20260901, "10")]))
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            stalls = result["daily_checks"]["zero_run_stalls"]
            self.assertEqual(stalls, [{"field": "cpcCost", "from": "20261015", "days": 3}])
            self.assertEqual(result["daily_checks"]["distinct_days"], 3)

    def test_n12_missing_ad_order_fields_skips_stall(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td)
            daily = [{"bizDay": f"2026101{d}", "productSales": "50", "cpcCost": "0"}
                     for d in (5, 6, 7)]   # 无 adSpOrderNum/cpcSalesNum
            write_env(raw / "agg_current.json", positive_rows([(20261015, "10")]))
            write_env(raw / "daily_current" / "seg1.json", daily)
            write_env(raw / "daily_base" / "seg1.json", positive_rows([(20260901, "10")]))
            result = run_analysis.build_analysis(raw, PLAN_TWO)
            dc = result["daily_checks"]
            self.assertEqual(dc["zero_run_stalls"], [])
            self.assertIn("停投断点不可判", dc["stall_check_note"])
            self.assertIn("adSpOrderNum", dc["stall_check_note"])

    def test_n1_int_and_str_mixed_day_keys_sort(self):
        rows = [{"bizDay": 20261002, "productSales": "1", "grossProfit": "1"},
                {"bizDay": "20261001", "productSales": "1", "grossProfit": "1"}]
        keys = sorted({run_analysis._day_key(r) for r in rows})  # 修复前 int/str 混排 TypeError
        self.assertEqual(keys, ["20261001", "20261002"])


class Fix13RenderTests(unittest.TestCase):
    """render_report.py：N3/N5/N8/N10/N11。"""

    def _analysis(self, **over):
        a = {
            "meta": {"caliber": "settlement", "granularity": "msku", "cost_layer": "batch",
                     "current": {"start": "20261015", "end": "20261021"},
                     "base": {"start": "20260924", "end": "20261007"}},
            "demo_fingerprint": None, "coverage": [],
            "currencies": "USD",
            "no_data": {"current": False, "base": False},
            "periods": {
                "current": {"rows": 1, "gp": 100.0, "sale": 500.0, "margin": 0.2,
                            "bridge": {"orderIncome": 500.0, "orderExpend": -200.0,
                                       "serviceFeeExpend": None, "refund": None,
                                       "purchasePcFeeBatch": None,
                                       "headTripPcFeeBatch": None, "cpcCost": 0.0},
                            "bridge_total": 300.0, "residual": -200.0,
                            "skipped_zero_sales": 0, "dedup": {"before": 3, "after": 2}},
                "base": {"rows": 1, "gp": 80.0, "sale": 400.0, "margin": 0.2,
                         "bridge": {"orderIncome": 400.0, "orderExpend": -180.0,
                                    "serviceFeeExpend": None, "refund": None,
                                    "purchasePcFeeBatch": None,
                                    "headTripPcFeeBatch": None, "cpcCost": 0.0},
                         "bridge_total": 220.0, "residual": -140.0,
                         "skipped_zero_sales": 0, "dedup": {"before": 2, "after": 2}},
            },
            "delta": {"bridge": {"orderIncome": 100.0, "orderExpend": -20.0,
                                 "serviceFeeExpend": None, "refund": None,
                                 "purchasePcFeeBatch": None,
                                 "headTripPcFeeBatch": None, "cpcCost": 0.0},
                      "gp": 20.0, "margin_pp": 0.0},
            "selfchecks": {"sentinel_excluded": {"current": 0, "base": 0}},
        }
        a.update(over)
        return a

    def test_n3_cross_check_mismatch_rendered(self):
        a = self._analysis(daily_checks={
            "cross_check_mismatch": [
                {"field": "orderIncome", "daily_sum": 1000.0, "agg": 500.0}],
        })
        text = render_report.render(a)
        self.assertIn("日分段加总 ≠ 聚合行", text)
        self.assertIn("orderIncome 日分段 1,000.00 ≠ 聚合 500.00", text)
        self.assertIn("先修取数再谈结论", text)

    def test_n5_rank_failure_rendered_when_rankings_missing(self):
        a = self._analysis(coverage=[
            {"step": "rank_current", "file": "rank_current_desc.json",
             "status": "failed", "error_code": "RESULT_BUDGET_EXCEEDED", "rows": 0}])
        text = render_report.render(a)
        self.assertIn("排名工具取数失败（error_code=RESULT_BUDGET_EXCEEDED），榜单不可得", text)

    def test_n5_rank_failure_rendered_with_empty_rankings(self):
        a = self._analysis(coverage=[
            {"step": "rank_current", "file": "rank_current_desc.json",
             "status": "failed", "error_code": "RESULT_BUDGET_EXCEEDED", "rows": 0}],
            rankings={"current": {"top": [], "bottom": [], "skipped_zero_sales": 0}})
        text = render_report.render(a)
        self.assertIn("排名工具取数失败（error_code=RESULT_BUDGET_EXCEEDED），该次榜单不可得", text)

    def test_n8_snapshot_no_vs_none(self):
        a = self._analysis()
        a["meta"]["base"] = None
        a["periods"]["base"] = {"rows": 0, "gp": 0.0, "sale": 0.0, "margin": None,
                                "bridge": {k: None for k in
                                           ("orderIncome", "orderExpend", "serviceFeeExpend",
                                            "refund", "purchasePcFeeBatch",
                                            "headTripPcFeeBatch", "cpcCost")},
                                "bridge_total": None, "residual": None,
                                "skipped_zero_sales": 0, "dedup": {"before": 0, "after": 0}}
        text = render_report.render(a)
        self.assertNotIn("vs None", text)
        self.assertNotIn("无可用数据", text)
        self.assertIn("单窗口快照，无对比基期", text)
        self.assertIn("本期毛利 100.00", text)
        self.assertIn("20.0%", text)          # 快照毛利率可见

    def test_n8_two_period_still_vs(self):
        text = render_report.render(self._analysis())
        self.assertIn("vs 20260924~20261007", text)
        self.assertIn("② 结论：毛利上升", text)

    def test_n11_bridge_none_renders_no_data_not_zero(self):
        text = render_report.render(self._analysis())
        self.assertIn("| serviceFeeExpend | 无数据（非 0） | 无数据（非 0） | 无数据（非 0） |", text)

    def test_n7_dedup_disclosed(self):
        text = render_report.render(self._analysis())
        self.assertIn("上游重复行 本期：3→2", text)

    def test_n10_mask_pii_in_text_applied(self):
        # 修复点：兜底脱敏必须是文本级 mask_pii_in_text（mask_pii 对 str 恒等）
        masked = render_report.mask_pii_in_text(
            "buyerEmail: zhangsanfeng@example.com, buyerName: 张三丰")
        self.assertNotIn("zhangsanfeng@example.com", masked)
        self.assertNotIn("张三丰", masked)
        # main() 落盘路径确实过这层
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            leaky = self._analysis()
            (td / "analysis.json").write_text(json.dumps(leaky), encoding="utf-8")
            out_path = td / "report.md"
            original_render = render_report.render

            def render_with_leak(a):
                return original_render(a) + "buyerEmail: leak@example.com"

            render_report.render = render_with_leak
            saved_argv = sys.argv
            sys.argv = ["render_report.py", "--analysis", str(td / "analysis.json"),
                        "--out", str(out_path)]
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = render_report.main()
            finally:
                render_report.render = original_render
                sys.argv = saved_argv
            self.assertEqual(rc, 0)
            out = out_path.read_text(encoding="utf-8")
            self.assertNotIn("leak@example.com", out)
            self.assertIn("le***@example.com", out)

    def test_n2_credit_line_with_sign_convention(self):
        a = self._analysis(daily_checks={
            "credit_signs": {"cpcCost": 1},
            "credit_days": [],
            "base_ex_credit_bridge": {"excluded_days": ["20261008"],
                                      "delta_gp_ex_credit": -150.0},
        })
        text = render_report.render(a)
        self.assertIn("cpcCost主符号=+", text)
        self.assertIn("基期剔除日 ['20261008']", text)

    def test_n12_stall_note_rendered(self):
        a = self._analysis(daily_checks={
            "stall_check_note": "单量字段缺失（adSpOrderNum/cpcSalesNum 全缺），停投断点不可判"})
        text = render_report.render(a)
        self.assertIn("停投断点不可判", text)


class Fix13SelfcheckAndDocsTests(unittest.TestCase):
    """selfcheck 三场景 + 文档契约口径（N13/N14）。"""

    def test_selfcheck_passes(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(run_analysis.selfcheck(), 0)
        self.assertIn("int日期/正号存储贷项/嵌套analyze行", buf.getvalue())

    def test_n13_docs_no_stale_options_contract(self):
        workflow = (SKILL / "references" / "workflow.md").read_text(encoding="utf-8")
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for doc, name in ((workflow, "workflow.md"), (skill, "SKILL.md")):
            self.assertNotIn("options.product_analysis", doc,
                             f"{name} 仍有过期 options.product_analysis 契约（N13）")
            self.assertNotIn("status 1·2", doc,
                             f"{name} 仍有 status 1·2 数字码口径（N14）")
        self.assertIn("`fields`", workflow)
        self.assertIn("`field_modules`", workflow)
        self.assertIn("anyOf 至少一项非空", workflow)
        self.assertIn("profitPrice", workflow)
        self.assertIn("active", workflow)   # N14：枚举以实测为准

    def test_n2_fieldmap_sign_paragraph(self):
        fm = (SKILL / "references" / "field-map.md").read_text(encoding="utf-8")
        self.assertIn("符号以实测为准", fm)          # 不再断言「成本费用类负」
        self.assertIn("主符号", fm)
        self.assertNotIn("成本费用类负：", fm)


class Fix13ArchivedReplayTests(unittest.TestCase):
    """归档信封复放（评测证据重跑，离线只读）。"""

    def _replay(self, src: Path, name: str):
        import shutil
        out_dir = SKILL / "out" / "replay" / name
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)
        for item in sorted(src.iterdir()):
            if item.is_file():
                shutil.copy(item, out_dir / item.name)
            else:
                shutil.copytree(item, out_dir / item.name)
        return out_dir

    def test_replay_case3_crash_scene_rc0(self):
        """case-3 raw.orig（int bizDay 崩溃原证）→ rc=0。"""
        src = self._replay(EVAL / "case-3" / "raw.orig", "t-case3")
        result = run_analysis.build_analysis(src, json.loads(
            (src / "analysis_plan.json").read_text(encoding="utf-8")))
        dc = result["daily_checks"]
        self.assertEqual(dc["credit_signs"]["cpcCost"], 1)     # 正号存储识别
        self.assertEqual(dc["credit_days"], [])                # 200 条假阳性消失
        self.assertEqual(dc["base_ex_credit_bridge"]["excluded_days"], [])
        self.assertEqual(len(dc["cross_check_mismatch"]), 7)

    def test_replay_offline_realshape_rankings_appear(self):
        """case-2 offline-realshape（嵌套 analyze 行）→ 榜单出现。"""
        src = self._replay(EVAL / "case-2" / "offline-realshape" / "raw", "t-realshape")
        result = run_analysis.build_analysis(src, json.loads(
            (src / "analysis_plan.json").read_text(encoding="utf-8")))
        rk = result["rankings"]["current"]
        self.assertEqual([r["asin"] for r in rk["top"]], ["B0REAL0", "B0REAL3"])
        self.assertEqual([r["asin"] for r in rk["bottom"]],
                         ["B0REAL1", "B0REAL2", "B0REAL4"])

    def test_replay_case9_credit_detected(self):
        """case-9（正号存储 + 基期单日 -273.6 冲回注入）→ 检出且剔后 Δ 重算。"""
        src = self._replay(EVAL / "case-9" / "raw", "t-case9")
        result = run_analysis.build_analysis(src, json.loads(
            (src / "analysis_plan.json").read_text(encoding="utf-8")))
        ex = result["daily_checks"]["base_ex_credit_bridge"]
        self.assertEqual(ex["excluded_days"], ["20261008"])
        self.assertNotEqual(ex["delta_gp_ex_credit"], result["delta"]["gp"])

    def test_replay_case11_budget_exceeded_disclosed(self):
        """case-11（注入 seg0 失败段）→ error_code 进 coverage。"""
        src = self._replay(EVAL / "case-11" / "raw", "t-case11")
        result = run_analysis.build_analysis(src, json.loads(
            (src / "analysis_plan.json").read_text(encoding="utf-8")))
        failed = [n for n in result["coverage"]
                  if n.get("error_code") == "RESULT_BUDGET_EXCEEDED"]
        self.assertTrue(failed)
        self.assertEqual(failed[0]["step"], "daily_current")
        text = render_report.render(result)
        self.assertIn("RESULT_BUDGET_EXCEEDED", text)

    def test_replay_case1_snapshot_render(self):
        """case-1（单窗口 by_shop 快照）→ 快照结论可见、无「vs None」。"""
        src = self._replay(EVAL / "case-1" / "raw", "t-case1")
        plan = json.loads((src / "analysis_plan.json").read_text(encoding="utf-8"))
        result = run_analysis.build_analysis(src, plan)
        text = render_report.render(result)
        self.assertIn("单窗口快照，无对比基期", text)
        self.assertNotIn("vs None", text)
        self.assertNotIn("无可用数据", text)
        self.assertIn("本期毛利", text)
        self.assertIn("无数据（非 0）", text)     # by_shop 桥接缺 → 非全 0.00


if __name__ == "__main__":
    unittest.main()
