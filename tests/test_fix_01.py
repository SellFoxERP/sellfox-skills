"""01-daily-ops-report 修复回归测试（2026-10-16 评测问题）。

用最小合成信封直接测 fetch/analyze/render 的修复行为：
  * refundStatus="0"（字符串枚举，非 "0" 才是退款）不再误标「退款」；
  * 截断/覆盖披露判据放宽：complete=false / truncated=true / fetched<total 任一即披露（total 未知按未知披露）；
  * 店铺清单按唯一店铺 ID 去重计数，重复行有披露；
  * failed 信封降级文案含 error_code 与步骤/工具名；
  * 缺 profit_shop 不再 exit 2，继续组装（顶部横幅 + 需人工核查）；
  * 跨平台数据被消费（按币种分组，不跨币种合计）；
  * 红线标题用 thresholds.redline.max_items；
  * 数据说明输出店铺/站点覆盖范围。
零网络：只喂合成 JSON。
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "01-daily-ops-report" / "scripts"

fetch = analyze = render = None


def _load(name):
    spec = importlib.util.spec_from_file_location(f"fix01_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def setUpModule():
    global fetch, analyze, render
    fetch = _load("fetch_data")
    analyze = _load("analyze")
    render = _load("render_report")


DAY = "2026-10-15"


def envelope(tool_id, data, *, status="complete", coverage=None, error_code=None):
    n = 0
    if isinstance(data, dict):
        for k in ("rows", "reportList", "itemList"):
            if isinstance(data.get(k), list):
                n = len(data[k])
    if coverage is None:
        coverage = {"complete": True, "fetched_count": n, "total_count": n, "truncated": False}
    obj = {"status": status, "warnings": [], "tool_id": tool_id, "data": data,
           "period": {"start": DAY, "end": DAY}, "coverage": coverage}
    if status != "complete":
        obj["error_code"] = error_code
        obj["coverage"] = None
    return obj


def write_raw(tmp, name, obj):
    raw = Path(tmp) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return raw


def shop(sid, region="US"):
    return {"id": sid, "name": f"店铺{sid}", "region": region, "status": "active"}


def profit_row(sid, sales, currency="USD"):
    return {"shopId": sid, "shopName": f"店铺{sid}", "currency": currency,
            "productSales": sales, "salesNum": 10, "grossProfit": sales * 0.2}


def order(oid, *, ret="0", refund="0", cancel="0"):
    return {"amazonOrderId": oid, "shopId": "S1", "shopName": "店铺S1",
            "orderTotalAmount": "12.34", "orderTotalCurrency": "USD",
            "isReturnOrder": ret, "refundStatus": refund, "isBuyerRequestedCancel": cancel,
            "buyerEmail": None, "buyerName": None}


def minimal_analysis(**over):
    a = {"report_date": {"iso": DAY}, "demo_fingerprint": None,
         "config": {"currency": "USD", "profit_caliber_type": "settlement",
                    "date_mode": "bizday", "timezone": "UTC"},
         "l1": {"currency": "USD"}, "steps": {}, "redlines": [], "redline_max_items": 5,
         "truncations": [], "degradations": [], "no_data_sources": [], "human_check": []}
    a.update(over)
    return a


class RefundStatusTest(unittest.TestCase):
    """[P1] refundStatus 为字符串枚举："0"=非退款，非 "0"=退款（勿按真值判断）。"""

    def _render_orders(self, rows):
        bundle = {"data": {"orders_sample": {"rows": rows}}}
        return render.render_markdown(minimal_analysis(), bundle)

    def test_zero_string_not_flagged_as_refund(self):
        md = self._render_orders([order("A1", ret="1", refund="0")])
        self.assertIn("标记：退货", md)
        self.assertNotIn("退货/退款", md)

    def test_nonzero_string_flagged_as_refund(self):
        md = self._render_orders([order("A2", refund="2")])
        self.assertIn("标记：退款", md)

    def test_plain_order_omitted_from_sample(self):
        md = self._render_orders([order("A3")])
        self.assertNotIn("A3", md)
        self.assertIn("样本中未发现退款/退货/取消标记的订单", md)


class TruncationCriteriaTest(unittest.TestCase):
    """[截断防御] complete=false / truncated=true / fetched<total 任一即披露。"""

    def _cov(self, coverage):
        from sellfox_offline import RawEnvelope  # noqa: F401  (经由 fetch 已入 sys.path)

        obj = {"status": "complete", "warnings": [], "tool_id": "t", "data": {"rows": []},
               "coverage": coverage}
        env = fetch.RawEnvelope(Path("orders.json"), obj)
        return fetch._cov_truncation(env, "get_order_page_list")

    def test_complete_and_full_coverage_silent(self):
        self.assertIsNone(self._cov({"complete": True, "fetched_count": 2, "total_count": 2, "truncated": False}))

    def test_truncated_flag_disclosed(self):
        t = self._cov({"complete": False, "fetched_count": 6, "total_count": 128, "truncated": True})
        self.assertIsNotNone(t)
        self.assertIn("6 行", t["message"])
        self.assertIn("128 行", t["message"])
        self.assertIn("已达 max_rows 上限", t["message"])

    def test_incomplete_without_truncated_flag_disclosed(self):
        t = self._cov({"complete": False, "fetched_count": 20, "total_count": 28491, "truncated": False})
        self.assertIsNotNone(t)
        self.assertIn("20 行", t["message"])
        self.assertIn("complete=false", t["message"])

    def test_fetched_less_than_total_disclosed(self):
        t = self._cov({"complete": True, "fetched_count": 3, "total_count": 9, "truncated": False})
        self.assertIsNotNone(t)
        self.assertIn("3 行", t["message"])

    def test_unknown_total_disclosed_as_unknown(self):
        t = self._cov({"complete": False, "fetched_count": 100, "total_count": None, "truncated": False})
        self.assertIsNotNone(t)
        self.assertIn("总数未知", t["message"])


class FetchBundleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _run(self, raw_dir):
        return fetch.run(DAY, str(raw_dir))

    def test_shop_rows_deduped_for_meta(self):
        rows = [shop(sid) for sid in ["S1", "S2", "S3", "S4", "S5"] for _ in range(4)]  # 20 行 = 5 店 ×4
        raw = write_raw(self.root, "shops.json", envelope("get_shop_page_list", {"rows": rows}))
        bundle = self._run(raw)
        self.assertEqual(bundle["meta"]["shops_total"], 5)
        self.assertEqual(bundle["meta"]["shops_covered"], 5)
        self.assertTrue(any("重复行 20→5" in d for d in bundle["degradations"]))

    def test_failed_envelope_discloses_error_code_and_step(self):
        raw = write_raw(self.root, "shops.json", envelope("get_shop_page_list", {"rows": [shop("S1")]}))
        write_raw(self.root, "profit_shop.json", envelope("get_profit_report_by_shop",
                                                          {"reportList": [profit_row("S1", 100.0)]}))
        write_raw(self.root, "msku_S1.json", envelope("get_custom_report_page_list",
                                                      {"rows": [{"msku": "M", "saleNum": 1}]},
                                                      status="failed", error_code="CONTRACT_VERSION_MISMATCH"))
        bundle = self._run(raw)
        hits = [d for d in bundle["degradations"]
                if "error_code=CONTRACT_VERSION_MISMATCH" in d
                and "步骤 3 宽表 S1" in d and "get_custom_report_page_list" in d]
        self.assertTrue(hits, msg=str(bundle["degradations"]))
        self.assertIn("⚠️ 需人工核查", hits[0])

    def test_missing_profit_continues_with_degradation(self):
        raw = write_raw(self.root, "shops.json", envelope("get_shop_page_list", {"rows": [shop("S1")]}))
        write_raw(self.root, "orders.json", envelope("get_order_page_list", {"rows": [order("A1", refund="2")]}))
        bundle = self._run(raw)
        self.assertIsNone(bundle["data"]["store_profit"])
        self.assertTrue(bundle["meta"]["profit_step_missing"])
        self.assertTrue(any("利润步骤缺失" in d for d in bundle["degradations"]))
        # 订单等其余步骤照常组装
        self.assertEqual(len(bundle["data"]["orders_sample"]["rows"]), 1)
        # main() 不再 exit 2
        out = self.root / "bundle.json"
        rc = fetch.main(["--date", DAY, "--raw-dir", str(raw), "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())

    def test_orders_coverage_incomplete_becomes_truncation(self):
        raw = write_raw(self.root, "shops.json", envelope("get_shop_page_list", {"rows": [shop("S1")]}))
        write_raw(self.root, "profit_shop.json", envelope("get_profit_report_by_shop",
                                                          {"reportList": [profit_row("S1", 100.0)]}))
        write_raw(self.root, "orders.json", envelope("get_order_page_list", {"rows": [order("A1")]},
                                                     coverage={"complete": False, "fetched_count": 20,
                                                               "total_count": 28491, "truncated": False}))
        bundle = self._run(raw)
        self.assertTrue(any(t["source"] == "get_order_page_list" and "20 行" in t["message"]
                            for t in bundle["truncations"]))


class MissingProfitPipelineTest(unittest.TestCase):
    """缺 profit_shop：analyze/render 容错 + 顶部降级横幅。"""

    def test_analyze_and_render_tolerate_empty_profit(self):
        bundle = {"report_date": {"iso": DAY, "compact": "20261015"},
                  "config": {"currency": "USD", "profit_caliber_type": "settlement",
                             "baseline": "window_field"},
                  "thresholds": {}, "truncations": [], "degradations": ["利润步骤缺失（out/raw/profit_shop.json 未落盘或无数据）"],
                  "human_check": [], "demo_fingerprint": None,
                  "data": {"shops": [], "store_profit": None, "orders_sample": None,
                           "campaigns": None, "msku_report": None}}
        a = analyze.analyze(bundle)
        self.assertIsNone(a["l1"]["gmv"])
        self.assertEqual(a["l1"]["currency"], "USD")  # 无币种组时回退配置币种，不误标「多币种」
        md = render.render_markdown(a, bundle)
        self.assertIn("利润步骤缺失", md)
        self.assertIn("⚠️ 需人工核查", md)
        self.assertIn("GMV（销售额） | 无数据", md)


class MultiplatformTest(unittest.TestCase):
    """跨平台数据被消费：按币种分组展示，不跨币种合计。"""

    ROWS = [
        {"platformId": 6, "platformName": "TikTok Shop", "statDate": DAY,
         "salePrice": 100.0, "orderNum": 10, "currency": "USD"},
        {"platformId": 6, "platformName": "TikTok Shop", "statDate": DAY,
         "salePrice": 50.0, "orderNum": 5, "currency": "USD"},
        {"platformId": 4, "platformName": "Walmart", "statDate": DAY,
         "salePrice": 70.0, "orderNum": 7, "currency": "GBP"},
    ]

    def test_flat_rows_grouped_by_currency(self):
        a = analyze.analyze({"data": {"multiplatform": {"metrics": {"salePrice": self.ROWS}}},
                             "thresholds": {}, "report_date": {"iso": DAY, "compact": "20261015"}})
        multi = a["multiplatform"]
        self.assertTrue(multi["available"])
        self.assertEqual(set(multi["platforms"]), {"TikTok Shop", "Walmart"})
        g = {(x["platform"], x["currency"]): x for x in multi["groups"]}
        self.assertEqual(g[("TikTok Shop", "USD")]["salePrice"], 150.0)
        self.assertEqual(g[("TikTok Shop", "USD")]["orderNum"], 15)
        self.assertEqual(g[("Walmart", "GBP")]["salePrice"], 70.0)
        md = render.render_markdown(minimal_analysis(multiplatform=multi), {})
        self.assertIn("【跨平台销量】", md)
        self.assertIn("TikTok Shop", md)
        self.assertIn("150.00", md)
        self.assertIn("不跨币种直接合计", md)

    def test_nested_live_shape_supported(self):
        rows = [{"platformName": ["亚马逊"],
                 "productSaleDayOpenVo": [{"day": DAY, "currency": "USD",
                                           "salePrice": "19.99", "orderNum": "8"}]}]
        a = analyze.analyze({"data": {"multiplatform": {"metrics": {"salePrice": rows}}},
                             "thresholds": {}, "report_date": {"iso": DAY, "compact": "20261015"}})
        groups = a["multiplatform"]["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["platform"], "亚马逊")
        self.assertEqual(groups[0]["salePrice"], 19.99)

    def test_absent_multiplatform_renders_no_section(self):
        md = render.render_markdown(minimal_analysis(), {"data": {}})
        self.assertNotIn("【跨平台销量】", md)


class RedlineAndCoverageRenderTest(unittest.TestCase):
    def test_redline_title_uses_configured_max_items(self):
        a = minimal_analysis(redline_max_items=5, redlines=[{"name": "x"}])
        md = render.render_markdown(a, {})
        self.assertIn("今日红线（≤5 条", md)

    def test_shop_and_site_coverage_line(self):
        bundle = {"meta": {"shops_total": 1, "shops_covered": 1, "shop_regions": ["US"]}}
        md = render.render_markdown(minimal_analysis(), bundle)
        self.assertIn("店铺/站点覆盖范围：1 家店铺；站点：US", md)


if __name__ == "__main__":
    unittest.main()
