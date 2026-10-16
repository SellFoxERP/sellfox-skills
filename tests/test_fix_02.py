"""02-inventory-health 修复回归测试（合成信封直测，零网络）。

覆盖 2026-10-16 评测问题清单的修复：
  1. 降级路径跨批次 MSKU 去重（上游 shop_id_list 过滤失效时建议不虚增）；
  2. report_id 未保存时 failed 宽表信封不静默（error_code + 工具名进 warnings/数据说明）；
  3. 口径降级横幅按真实原因（degrade_reason：未配置 / 发现被拒 / 主源失败）；
  4. 店铺清单重复行去重 + 披露；
  5. 「涉及工具」补列 get_fba_shipment_page_list / get_awd_inventory_page_list。
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "02-inventory-health" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # py3.9 dataclass 解析 cls.__module__ 需要
    spec.loader.exec_module(module)
    return module


fetch_inventory = _load("fetch_inventory")
render = _load("render")


def envelope(rows, *, status="complete", error_code=None, truncated=False,
             total=None, list_key="rows"):
    """构造与线上同形状的完整信封。"""
    cov = {"complete": not truncated, "fetched_count": len(rows) if rows else 0,
           "total_count": total if total is not None else (len(rows) if rows else 0),
           "truncated": truncated}
    obj = {"tool_id": "synthetic", "status": status, "coverage": cov,
           "warnings": [], "trace_id": "synthetic-trace"}
    if status == "failed":
        obj["error_code"] = error_code or "UPSTREAM_CONTRACT_ERROR"
        obj["data"] = None
    else:
        obj["data"] = {list_key: rows} if rows is not None else {}
    return obj


def online_row(sku, *, title="", qty=100, day30=300, available=50, shop_id=10001):
    return {
        "sku": sku, "shopId": shop_id, "title": title, "quantity": qty,
        "day30SaleNum": day30,
        "inventoryManage": {"available": available, "currency": "CNY",
                            "inboundShipped": 0},
    }


def custom_row(msku, *, days=20, velocity=6, inbound=0):
    return {"msku": [msku], "asin": [{"key": "B0SYN"}], "shopId": [{"key": "10001"}],
            "availableDays": days, "salesVelocityMonth": velocity,
            "inboundShipped": inbound, "suggestNumSum": 80}


class AssembleTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name)
        self.cfg = {"max_rows": {"custom_report": 200, "online_product": 200,
                                 "fba_shipment": 100, "awd_inventory": 100}}

    def put(self, name, obj):
        self.raw.joinpath(f"{name}.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    # ---- 修复 1：降级路径跨批次 MSKU 去重 ----
    def test_fallback_dedup_cross_batch(self):
        """三个批次返回同一目录（shop_id_list 过滤失效）→ 每个 MSKU 只计一次。"""
        self.put("shops", envelope([{"id": f"AD-1000{i}"} for i in (1, 2, 3)]))
        for batch in ("b1", "b2", "b3"):
            self.put(f"online_products_{batch}",
                     envelope([online_row("SKU-D1", title="first"),
                               online_row("SKU-D2", day30=90, available=900)]))
        result = fetch_inventory.assemble_all(self.raw, report_id=None, skill_cfg=self.cfg)
        self.assertTrue(result.degraded)
        skus = [r.get("msku") or r.get("sku") for r in result.records]
        self.assertEqual(sorted(skus), ["SKU-D1", "SKU-D2"])  # 不再 ×3 虚增
        # 首个出现为准：保留 b1 的 title
        d1 = next(r for r in result.records if r.get("sku") == "SKU-D1")
        self.assertEqual(d1.get("title"), "first")
        # 去重披露
        self.assertTrue(any("跨批次重复 4 行" in w for w in result.warnings), result.warnings)

    def test_fallback_keeps_rows_without_msku_or_sku(self):
        """无法识别 MSKU 的行不去重（不能因去重丢「数据不足」行）。"""
        self.put("online_products_b1", envelope([
            {"shopId": 10001, "quantity": 10}, {"shopId": 10001, "quantity": 20}]))
        result = fetch_inventory.assemble_all(self.raw, report_id=None, skill_cfg=self.cfg)
        self.assertEqual(len(result.records), 2)

    def test_supplement_merge_still_dedupes(self):
        """主源成功时的补充路径合并去重行为保持不变。"""
        self.put("msku_7001", envelope([custom_row("SKU-A")]))
        self.put("online_products_b1", envelope([online_row("SKU-A"),
                                                 online_row("SKU-B")]))
        result = fetch_inventory.assemble_all(
            self.raw, report_id="RPT-1", skill_cfg=self.cfg)
        self.assertFalse(result.degraded)
        keys = sorted((r.get("msku") or r.get("sku")) for r in result.records)
        self.assertEqual(keys, ["SKU-A", "SKU-B"])
        self.assertIn("get_online_product_page_list(补充)", result.sources)

    # ---- 修复 2：failed 宽表信封不静默（report_id 未保存） ----
    def test_failed_msku_envelope_visible_without_report_id(self):
        self.put("msku_AD-10001", envelope(None, status="failed",
                                           error_code="RESULT_BUDGET_EXCEEDED"))
        self.put("online_products_b1", envelope([online_row("SKU-F1")]))
        result = fetch_inventory.assemble_all(self.raw, report_id=None, skill_cfg=self.cfg)
        joined = "\n".join(result.warnings)
        self.assertIn("error_code=RESULT_BUDGET_EXCEEDED", joined)
        self.assertIn("get_custom_report_page_list", joined)
        self.assertIn("custom_report(msku_AD-10001) status=failed", joined)
        self.assertIn("get_custom_report_page_list(失败未采信)", result.sources)
        self.assertTrue(result.degraded)
        self.assertIn("报表发现验证失败", result.degrade_reason)
        self.assertIn("RESULT_BUDGET_EXCEEDED", result.degrade_reason)

    def test_degrade_reason_main_source_failed_with_report_id(self):
        self.put("msku_8001", envelope(None, status="failed",
                                       error_code="CONTRACT_VERSION_MISMATCH"))
        self.put("online_products_1", envelope([online_row("SKU-F2")]))
        result = fetch_inventory.assemble_all(
            self.raw, report_id="RPT-INV-2026-001", skill_cfg=self.cfg)
        self.assertTrue(result.degraded)
        self.assertIn("主源取数失败", result.degrade_reason)
        self.assertIn("CONTRACT_VERSION_MISMATCH", result.degrade_reason)
        self.assertEqual([r.get("sku") for r in result.records], ["SKU-F2"])

    def test_degrade_reason_unconfigured(self):
        self.put("online_products_1", envelope([online_row("SKU-U1")]))
        result = fetch_inventory.assemble_all(self.raw, report_id=None, skill_cfg=self.cfg)
        self.assertTrue(result.degraded)
        self.assertIn("未配置报表 ID", result.degrade_reason)
        self.assertNotIn("error_code", result.degrade_reason)

    # ---- 修复 4：店铺清单重复行去重 + 披露 ----
    def test_shops_duplicate_disclosure(self):
        rows = []
        for i in range(1, 6):
            rows.extend([{"id": f"AD-1000{i}", "name": f"演示店{i}"}] * 4)
        self.put("shops", envelope(rows))
        self.put("online_products_b1", envelope([online_row("SKU-S1")]))
        result = fetch_inventory.assemble_all(self.raw, report_id=None, skill_cfg=self.cfg)
        joined = "\n".join(result.coverage)
        self.assertIn("落盘 20 行含重复店铺（唯一 5 个", joined)
        self.assertIn("首个出现为准去重", joined)

    def test_shops_no_duplicate_no_disclosure(self):
        self.put("shops", envelope([{"id": "AD-20001"}, {"id": "AD-20002"}]))
        self.put("online_products_b1", envelope([online_row("SKU-S2")]))
        result = fetch_inventory.assemble_all(self.raw, report_id=None, skill_cfg=self.cfg)
        self.assertFalse(any("含重复店铺" in c for c in result.coverage))

    # ---- 修复 5：涉及工具补列 fba / awd（与 coverage 区一致，含 failed / 无数据） ----
    def test_sources_include_fba_awd_tools(self):
        self.put("msku_7001", envelope([custom_row("SKU-T1")]))
        self.put("fba_shipments", envelope(None, status="failed",
                                           error_code="UPSTREAM_CONTRACT_ERROR"))
        self.put("awd_inventory", envelope(None))  # data:{} → 无数据
        result = fetch_inventory.assemble_all(
            self.raw, report_id="RPT-1", skill_cfg=self.cfg)
        self.assertIn("get_fba_shipment_page_list", result.sources)
        self.assertIn("get_awd_inventory_page_list", result.sources)
        self.assertIn("awd_inventory: 无数据", result.coverage)
        self.assertTrue(any("error_code=UPSTREAM_CONTRACT_ERROR" in w
                            for w in result.warnings))
        # 未落盘的步骤不虚列工具
        self.assertNotIn("get_online_product_page_list(降级)", result.sources)

    def test_sources_exclude_fba_awd_when_not_fetched(self):
        self.put("online_products_1", envelope([online_row("SKU-T2")]))
        result = fetch_inventory.assemble_all(self.raw, report_id=None, skill_cfg=self.cfg)
        self.assertNotIn("get_fba_shipment_page_list", result.sources)
        self.assertNotIn("get_awd_inventory_page_list", result.sources)


class RenderBannerTestCase(unittest.TestCase):
    """修复 3：口径降级横幅按真实原因，不硬编码「未配置报表 ID」。"""

    def _banner(self, meta):
        plan = render.PlanResult(counts={})
        text = render.render_report(plan, fetch_meta=meta)
        for line in text.splitlines():
            if "口径降级" in line:
                return line
        return ""

    def test_banner_reports_discovery_rejected(self):
        line = self._banner({"degraded": True, "sources": [],
                             "degrade_reason": "报表发现验证失败"
                             "（get_custom_report_page_list，error_code=RESULT_BUDGET_EXCEEDED），"
                             "report_id 未保存"})
        self.assertIn("报表发现验证失败", line)
        self.assertIn("RESULT_BUDGET_EXCEEDED", line)
        self.assertNotIn("未配置报表 ID", line)

    def test_banner_reports_main_source_failure(self):
        line = self._banner({"degraded": True, "sources": [],
                             "degrade_reason": "主源取数失败"
                             "（get_custom_report_page_list，error_code=CONTRACT_VERSION_MISMATCH）"})
        self.assertIn("主源取数失败", line)
        self.assertIn("CONTRACT_VERSION_MISMATCH", line)

    def test_banner_unconfigured_without_reason_falls_back(self):
        line = self._banner({"degraded": True, "sources": []})
        self.assertIn("未配置报表 ID", line)


if __name__ == "__main__":
    unittest.main()
