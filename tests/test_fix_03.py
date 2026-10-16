"""03-listing-health 评测问题修复的回归测试（合成数据直测，零网络）。

覆盖：
- 基线快照自持：写入/读取/meta.baseline 生成/陈旧记录清除/BSR 环比规则可达
- 宽表状态三态：enabled / fetch_failed(error_code) / not_configured + discovery_failed
- diagnose.py:147 文案：区分「未配置」「取数失败」「已启用但无该 ASIN 匹配行」
- 断货检查排除 P0 已删除/不可售记录
- 空数据（status=complete）落「无数据」披露进 bundle 并在报告可见
- 标签命中清单披露（by_label 不编造合并）
- 请求店铺范围与返回行 shopId 不符时披露
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "03-listing-health" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fetch_mod = _load("lhf_fetch", SCRIPTS / "fetch_listing_data.py")
diagnose_mod = _load("lhf_diagnose", SCRIPTS / "diagnose.py")
render_mod = _load("lhf_render", SCRIPTS / "render_report.py")


def _args(raw_dir: Path, **kw) -> Namespace:
    base = dict(
        shop_ids=kw.pop("shop_ids", "555001"),
        label_ids=kw.pop("label_ids", ""),
        report_id=kw.pop("report_id", ""),
        online_status=kw.pop("online_status", ""),
        short_days=0,
        long_days=0,
        raw_dir=str(raw_dir),
        baseline_snapshot=kw.pop("baseline_snapshot", None),
    )
    base.update(kw)
    return Namespace(**base)


def _online_row(n: int, **kw) -> dict:
    row = {
        "id": 900000 + n,
        "shopId": 555001,
        "asin": f"B0SYNTH{n:03d}",
        "listingId": f"SYN-LID-{n:03d}",
        "title": f"Synthetic {n}",
        "onlineStatus": "Active",
        "quantity": 10,
        "rating": "4.5",
        "bsrRank": [{"rank": 1000 + n, "bigCategory": False,
                     "categoryID": "1", "categoryName": "Synth", "order": 1}],
        "day7SaleNum": 100,
        "day30SaleNum": 400,
        "buyBoxWinner": True,
    }
    row.update(kw)
    return row


class _Env:
    """合成信封落盘器（形态对齐 out/raw/*.json）。"""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.raw = out_dir / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)

    def envelope(self, name: str, rows=None, status: str = "complete",
                 error_code=None, message=None, tool_id="get_online_product_page_list",
                 data_none=False):
        obj = {
            "tool_id": tool_id, "status": status, "contract_version": "synthetic-cv",
            "warnings": [], "trace_id": f"synthetic-{name}", "coverage": {},
        }
        if status in ("complete", "partial") and not data_none:
            obj["data"] = {"pageNo": 1, "pageSize": 20,
                           "totalSize": len(rows or []), "rows": rows or []}
        else:
            obj["data"] = None
        if error_code:
            obj["error_code"] = error_code
        if message:
            obj["message"] = message
        (self.raw / f"{name}.json").write_text(
            json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return obj


class BaselineSnapshotTest(unittest.TestCase):
    """修复 1：BSR/评分基线快照自持。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = _Env(Path(self.temp.name) / "out")

    def test_first_run_writes_snapshot_second_run_matches_baseline(self):
        self.env.envelope("online_active", [_online_row(1, bsrRank=[{"rank": 4200}],
                                                       rating="4.2"),
                                            _online_row(2, bsrRank=[{"rank": 150}],
                                                        rating="4.7")])
        # 第一次运行：无历史快照 → 不生成 meta.baseline，但落盘快照
        p1 = fetch_mod.run(_args(self.env.raw))
        self.assertNotIn("baseline", p1["meta"])
        snap_path = self.env.out_dir / "baseline-snapshot.json"
        self.assertTrue(snap_path.exists())
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        self.assertEqual(set(snap), {"lid:SYN-LID-001", "lid:SYN-LID-002"})
        self.assertEqual(snap["lid:SYN-LID-001"]["bsrRank"], 4200)
        self.assertEqual(snap["lid:SYN-LID-001"]["rating"], 4.2)
        self.assertTrue(snap["lid:SYN-LID-001"]["captured_at"])
        self.assertTrue(any("无历史快照" in d for d in p1["meta"]["disclosures"]))

        # 第二次运行：读入上次快照 → meta.baseline 按记录 ID 匹配
        p2 = fetch_mod.run(_args(self.env.raw))
        baseline = p2["meta"]["baseline"]
        self.assertEqual(baseline["lid:SYN-LID-001"]["bsrRank"], 4200)
        self.assertEqual(baseline["lid:SYN-LID-001"]["rating"], 4.2)
        self.assertTrue(any("环比基线来自上次运行快照" in d
                            for d in p2["meta"]["disclosures"]))

    def test_second_run_bsr_deterioration_rule_reachable(self):
        self.env.envelope("online_active", [_online_row(1, bsrRank=[{"rank": 4200}])])
        fetch_mod.run(_args(self.env.raw))  # 建快照
        snap_path = self.env.out_dir / "baseline-snapshot.json"
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        snap["lid:SYN-LID-001"]["bsrRank"] = 2500  # 模拟上次名次更好
        snap_path.write_text(json.dumps(snap), encoding="utf-8")
        p = fetch_mod.run(_args(self.env.raw))
        result = diagnose_mod.diagnose(p)
        bsr = [x for x in result["problems"] if x["check"] == "BSR 恶化"]
        self.assertEqual(len(bsr), 1)
        self.assertIn("2500 → 4200", bsr[0]["evidence"])
        self.assertEqual(bsr[0]["severity"], "P2")

    def test_snapshot_rebuilt_stale_records_removed(self):
        self.env.envelope("online_active", [_online_row(1), _online_row(2)])
        fetch_mod.run(_args(self.env.raw))
        snap_path = self.env.out_dir / "baseline-snapshot.json"
        self.assertEqual(len(json.loads(snap_path.read_text(encoding="utf-8"))), 2)
        # 下次运行仅剩 1 条记录 → 快照全量重建，陈旧记录清除
        self.env.envelope("online_active", [_online_row(1)])
        fetch_mod.run(_args(self.env.raw))
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        self.assertEqual(set(snap), {"lid:SYN-LID-001"})


class WideTableThreeStateTest(unittest.TestCase):
    """修复 2：宽表状态三态如实（启用成功/取数失败/未配置或发现失败）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = _Env(Path(self.temp.name) / "out")
        self.env.envelope("online_active", [_online_row(1)])

    def test_enabled(self):
        self.env.envelope("custom_report_daily",
                          [{"asin": "B0SYNTH001", "sessions": 10, "conversionRate": 2.0}],
                          tool_id="get_custom_report_page_list")
        p = fetch_mod.run(_args(self.env.raw, report_id="RPT-1"))
        self.assertEqual(p["meta"]["wide_table"]["status"], "enabled")
        self.assertEqual(p["meta"]["wide_table"]["rows"], 1)
        md = render_mod.render({"meta": p["meta"], "problems": [],
                                "manual_verification": []})
        self.assertIn("宽表）已启用", md)
        self.assertNotIn("宽表取数失败", md)

    def test_fetch_failed_disclosed_not_enabled(self):
        self.env.envelope("custom_report_daily", status="failed",
                          error_code="RESULT_BUDGET_EXCEEDED",
                          message="Result exceeds budget",
                          tool_id="get_custom_report_page_list")
        p = fetch_mod.run(_args(self.env.raw, report_id="RPT-6126"))
        wt = p["meta"]["wide_table"]
        self.assertEqual(wt["status"], "fetch_failed")
        self.assertIn("RESULT_BUDGET_EXCEEDED", wt["error_codes"])
        self.assertEqual(p["traffic"], [])
        result = diagnose_mod.diagnose(p)
        md = render_mod.render(result)
        self.assertIn("宽表取数失败", md)
        self.assertIn("RESULT_BUDGET_EXCEEDED", md)
        self.assertNotIn("已启用", md)
        # 修复 5：report_id 已配置 + 取数失败 → 不再误写「缺 report_id」
        manual_txt = "\n".join(result["manual_verification"])
        self.assertIn("宽表取数失败", manual_txt)
        self.assertNotIn("无流量检查不可用（缺 report_id）", manual_txt)

    def test_discovery_failed_envelope_is_read_and_disclosed(self):
        # report_id 为空（发现失败回落简版）：已落盘 failed 信封必须被读取并披露
        self.env.envelope("custom_report_daily", status="failed",
                          error_code="RESULT_BUDGET_EXCEEDED",
                          message="Result exceeds budget",
                          tool_id="get_custom_report_page_list")
        p = fetch_mod.run(_args(self.env.raw, report_id=""))
        wt = p["meta"]["wide_table"]
        self.assertEqual(wt["status"], "discovery_failed")
        self.assertIn("RESULT_BUDGET_EXCEEDED", wt["error_codes"])
        self.assertEqual(p["traffic"], [])
        self.assertTrue(any("报表发现失败" in e and "RESULT_BUDGET_EXCEEDED" in e
                            for e in p["meta"]["errors"]))
        result = diagnose_mod.diagnose(p)
        md = render_mod.render(result)
        self.assertIn("报表发现失败", md)
        self.assertIn("RESULT_BUDGET_EXCEEDED", md)
        self.assertNotIn("已启用", md)


class DiagnoseWordingTest(unittest.TestCase):
    """修复 5：diagnose.py:147 区分「已配置但无该记录行」与「未配置」。"""

    def test_configured_no_row_vs_not_configured(self):
        base_online = [_online_row(1)]
        with tempfile.TemporaryDirectory() as td:
            env = _Env(Path(td) / "a")
            env.envelope("online_active", base_online)
            env.envelope("custom_report_daily",
                         [{"asin": "B0OTHER999", "sessions": 5}],
                         tool_id="get_custom_report_page_list")
            p = fetch_mod.run(_args(env.raw, report_id="RPT-1"))
            result = diagnose_mod.diagnose(p)
            txt = "\n".join(result["manual_verification"])
            self.assertIn("无流量检查不可用（宽表已启用但无该 ASIN 匹配行）", txt)
            self.assertNotIn("缺 report_id）\n", txt)
        with tempfile.TemporaryDirectory() as td:
            env = _Env(Path(td) / "b")
            env.envelope("online_active", base_online)
            p = fetch_mod.run(_args(env.raw, report_id=""))
            result = diagnose_mod.diagnose(p)
            txt = "\n".join(result["manual_verification"])
            self.assertIn("无流量检查不可用（缺 report_id）", txt)


class StockoutExcludesP0Test(unittest.TestCase):
    """修复 6：断货检查排除 P0 已删除/不可售记录。"""

    def test_deleted_and_unsellable_skipped(self):
        data = {
            "meta": {"report_configured": False, "degraded": True,
                     "wide_table": {"status": "not_configured", "error_codes": [], "rows": 0}},
            "online": [
                _online_row(1, onlineStatus="delete", quantity=0),
                _online_row(2, onlineStatus="inActive", quantity=0),
                _online_row(3, quantity=0),  # Active + qty=0 → 保留断货提示
                _online_row(4, quantity=5),
            ],
            "traffic": [],
        }
        result = diagnose_mod.diagnose(data)
        stock = [x for x in result["problems"] if x["check"] == "断货拖累排名"]
        self.assertEqual(len(stock), 1)
        self.assertEqual(stock[0]["asin"], "B0SYNTH003")
        p0 = [x for x in result["problems"] if x["severity"] == "P0"]
        self.assertEqual(len(p0), 2)  # delete / inActive 的 P0 判定不受影响


class EmptyDataDisclosureTest(unittest.TestCase):
    """修复 4：空 data（status=complete）落「无数据」标注进 bundle，报告可见。"""

    def test_no_data_disclosed(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(Path(td) / "out")
            env.envelope("online_active", [_online_row(1)])
            env.envelope("labels", rows=None, data_none=False,
                         tool_id="get_product_by_label")
            # data={} 形态：get_product_by_label 返回空 dict
            (env.raw / "labels.json").write_text(json.dumps({
                "tool_id": "get_product_by_label", "status": "complete",
                "warnings": [], "trace_id": "t", "coverage": {}, "data": {},
            }, ensure_ascii=False), encoding="utf-8")
            p = fetch_mod.run(_args(env.raw, label_ids="清仓"))
            self.assertTrue(any("无数据" in d for d in p["meta"]["disclosures"]))
            self.assertEqual(p["by_label"], [])
            self.assertEqual(p["meta"]["by_label"]["rows"], 0)
            result = diagnose_mod.diagnose(p)
            md = render_mod.render(result)
            self.assertIn("无数据", md)
            self.assertIn("标签命中", md)


class LabelHitsDisclosureTest(unittest.TestCase):
    """修复 9：标签命中清单披露，不编造合并。"""

    def test_hits_listed_with_join_key_note(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(Path(td) / "out")
            env.envelope("online_active", [_online_row(1)])
            env.envelope("labels", [
                {"shopId": "shop_x", "labelId": "label_9", "labelName": "清仓",
                 "labelType": "商品标签", "product": "合成商品A"},
                {"shopId": "shop_x", "labelId": "label_9", "labelName": "清仓",
                 "labelType": "商品标签", "product": "合成商品B"},
            ], tool_id="get_product_by_label")
            p = fetch_mod.run(_args(env.raw, label_ids="清仓"))
            bl = p["meta"]["by_label"]
            self.assertEqual(bl["rows"], 2)
            self.assertEqual(bl["hits"][0]["count"], 2)
            self.assertFalse(bl["join_key_present"])
            self.assertIn("缺少 ASIN/SKU 关联键", bl["note"])
            self.assertFalse(bl["merged_into_diagnosis"])
            self.assertTrue(any("标签命中 2 行" in d for d in p["meta"]["disclosures"]))
            result = diagnose_mod.diagnose(p)
            md = render_mod.render(result)
            self.assertIn("标签命中（get_product_by_label）", md)
            self.assertIn("清仓（label_9）", md)
            self.assertIn("缺少 ASIN/SKU 关联键，未并入分诊", md)


class ShopScopeCheckTest(unittest.TestCase):
    """修复 3：请求店铺范围与返回行 shopId 不符时披露。"""

    def test_mismatch_disclosed_data_kept(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(Path(td) / "out")
            env.envelope("online_active", [_online_row(1, shopId=10001)])
            p = fetch_mod.run(_args(env.raw, shop_ids="23901"))
            self.assertEqual(len(p["online"]), 1)  # 数据照用
            self.assertTrue(any("返回集店铺与请求范围不符" in d
                                and "23901" in d and "10001" in d
                                for d in p["meta"]["disclosures"]))
            result = diagnose_mod.diagnose(p)
            md = render_mod.render(result)
            self.assertIn("返回集店铺与请求范围不符", md)

    def test_matching_scope_no_noise(self):
        with tempfile.TemporaryDirectory() as td:
            env = _Env(Path(td) / "out")
            env.envelope("online_active", [_online_row(1, shopId=23901)])
            p = fetch_mod.run(_args(env.raw, shop_ids="23901"))
            self.assertFalse(any("返回集店铺与请求范围不符" in d
                                 for d in p["meta"]["disclosures"]))


if __name__ == "__main__":
    unittest.main()
