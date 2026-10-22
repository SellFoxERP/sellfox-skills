"""02-inventory-health 修复回归测试（合成信封直测，零网络）。

覆盖 2026-10-16 评测问题清单的修复（v1.7.0 随宽表步骤移除改写）：
  1. 跨批次 MSKU 去重（上游 shop_id_list 过滤失效时建议不虚增）；
  2. 宽表（get_custom_report_page_list）步骤已移除：assemble_all 不再收 report_id，
     固定降级原因「未接入自定义报表（custom-report-analysis 承接）」，残留 msku_*.json 忽略；
  3. 口径降级横幅按固定原因渲染；
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
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
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
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        self.assertEqual(len(result.records), 2)

    def test_supplement_merge_still_dedupes(self):
        """主源合并去重行为保持：跨批次同一 MSKU 只计一次。"""
        self.put("online_products_b1", envelope([online_row("SKU-A"),
                                                 online_row("SKU-B")]))
        self.put("online_products_b2", envelope([online_row("SKU-A", day30=999)]))
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        self.assertTrue(result.degraded)  # 固定口径降级（未接入自定义报表）
        keys = sorted((r.get("msku") or r.get("sku")) for r in result.records)
        self.assertEqual(keys, ["SKU-A", "SKU-B"])
        self.assertIn("get_online_product_page_list", result.sources)
        self.assertNotIn("get_online_product_page_list(补充)", result.sources)

    # ---- v1.7.0：宽表步骤移除，残留 msku_*.json 忽略 + 固定降级原因 ----
    def test_msku_envelopes_ignored_fixed_degrade_reason(self):
        self.put("msku_AD-10001", envelope(None, status="failed",
                                           error_code="RESULT_BUDGET_EXCEEDED"))
        self.put("online_products_b1", envelope([online_row("SKU-F1")]))
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        self.assertTrue(result.degraded)
        self.assertIn("未接入自定义报表", result.degrade_reason)
        self.assertIn("custom-report-analysis", result.degrade_reason)
        self.assertEqual([r.get("sku") for r in result.records], ["SKU-F1"])
        self.assertNotIn("get_custom_report_page_list", result.sources)
        joined = "\n".join(result.warnings)
        self.assertNotIn("RESULT_BUDGET_EXCEEDED", joined)  # 残留宽表信封不消费

    def test_degrade_reason_fixed_when_online_missing(self):
        """在线产品源缺失：no_valid_data 信号（run 层 rc=2），降级原因仍是固定文案。"""
        self.put("shops", envelope([{"id": "AD-1"}]))
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        self.assertTrue(result.degraded)
        self.assertTrue(result.no_valid_data)
        self.assertEqual(result.records, [])
        self.assertIn("未接入自定义报表", result.degrade_reason)
        self.assertTrue(any("无有效数据项" in w for w in result.warnings))

    def test_degrade_reason_unconfigured(self):
        self.put("online_products_1", envelope([online_row("SKU-U1")]))
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        self.assertTrue(result.degraded)
        self.assertIn("未接入自定义报表（由 custom-report-analysis 技能承接），按在线产品口径自算",
                      result.degrade_reason)
        self.assertNotIn("error_code", result.degrade_reason)

    # ---- 修复 4：店铺清单重复行去重 + 披露 ----
    def test_shops_duplicate_disclosure(self):
        rows = []
        for i in range(1, 6):
            rows.extend([{"id": f"AD-1000{i}", "name": f"演示店{i}"}] * 4)
        self.put("shops", envelope(rows))
        self.put("online_products_b1", envelope([online_row("SKU-S1")]))
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        joined = "\n".join(result.coverage)
        self.assertIn("落盘 20 行含重复店铺（唯一 5 个", joined)
        self.assertIn("首个出现为准去重", joined)

    def test_shops_no_duplicate_no_disclosure(self):
        self.put("shops", envelope([{"id": "AD-20001"}, {"id": "AD-20002"}]))
        self.put("online_products_b1", envelope([online_row("SKU-S2")]))
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        self.assertFalse(any("含重复店铺" in c for c in result.coverage))

    # ---- 修复 5：涉及工具补列 fba / awd（与 coverage 区一致，含 failed / 无数据） ----
    def test_sources_include_fba_awd_tools(self):
        self.put("online_products_b1", envelope([online_row("SKU-T1")]))
        self.put("fba_shipments", envelope(None, status="failed",
                                           error_code="UPSTREAM_CONTRACT_ERROR"))
        self.put("awd_inventory", envelope(None))  # data:{} → 无数据
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        self.assertIn("get_fba_shipment_page_list", result.sources)
        self.assertIn("get_awd_inventory_page_list", result.sources)
        self.assertIn("awd_inventory: 无数据", result.coverage)
        self.assertTrue(any("error_code=UPSTREAM_CONTRACT_ERROR" in w
                            for w in result.warnings))
        # 未落盘的步骤不虚列工具
        self.assertNotIn("get_custom_report_page_list", result.sources)

    def test_sources_exclude_fba_awd_when_not_fetched(self):
        self.put("online_products_1", envelope([online_row("SKU-T2")]))
        result = fetch_inventory.assemble_all(self.raw, skill_cfg=self.cfg)
        self.assertNotIn("get_fba_shipment_page_list", result.sources)
        self.assertNotIn("get_awd_inventory_page_list", result.sources)


class RenderBannerTestCase(unittest.TestCase):
    """口径降级横幅（v1.7.0 固定文案）：未接入自定义报表（custom-report-analysis 承接）。"""

    def _banner(self, meta):
        plan = render.PlanResult(counts={})
        text = render.render_report(plan, fetch_meta=meta)
        for line in text.splitlines():
            if "口径降级" in line:
                return line
        return ""

    def test_banner_reports_fixed_handoff(self):
        line = self._banner({"degraded": True, "sources": [],
                             "degrade_reason": "未接入自定义报表（由 custom-report-analysis 技能承接），按在线产品口径自算"})
        self.assertIn("未接入自定义报表", line)
        self.assertIn("custom-report-analysis", line)
        self.assertIn("自算口径", line)
        self.assertNotIn("未配置报表 ID", line)

    def test_banner_no_valid_data(self):
        text = render.render_report(render.PlanResult(counts={}),
                                    fetch_meta={"no_valid_data": True, "sources": [],
                                                "degrade_reason": "未接入自定义报表（由 custom-report-analysis 技能承接），按在线产品口径自算"})
        self.assertIn("主源不可用，本报告无有效数据项", text)
        self.assertIn("custom-report-analysis", text)

    def test_banner_unconfigured_without_reason_falls_back(self):
        line = self._banner({"degraded": True, "sources": []})
        self.assertIn("未接入自定义报表", line)


if __name__ == "__main__":
    unittest.main()
