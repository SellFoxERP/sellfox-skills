"""06-ad-optimizer 修复回归测试（2026-10-16 评测问题），合成/归档数据直测，零网络。

覆盖：
1. 下载任务 reportState 中文失败态告警（生成失败/失败 → failed；已生成 → 成功；
   英文 FAIL/ERROR 仍认）——ad_fetch._report_state_class + 落盘信封直测 + 归档 case5b 复放；
2. 商品投放（sp_product_targeting）维度覆盖披露小节：有数据出现、无数据不出现 +
   归档 case6（真实 targetings 信封）复放；
3. verify 判定表形态 {"items":[…]} 不再被误判为信封形态（比对分支可达、actual 回填）；
4. verify 未执行项语义：只比对 record 日志里有 success/failed 记录的项，
   未执行项标「未执行（不在本次批准范围）」不报不一致 + 归档 case9 复放；
5. 提案混入不支持的写工具 → execute 干净报错（exit 5、无 traceback、无执行清单），
   fail-closed 语义不变；
6. 累计闸跨周拦截：上周 +30%、今天再 +30% 依赖 _shared 默认 7 天回看窗
   （net_change_lookback_hours=168）被拦；06 侧 thresholds/optimizer 无旧键冲突。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "06-ad-optimizer"
SCRIPTS = SKILL / "scripts"
LIVE = SKILL / "out" / "eval-live-2026-10-16"

# 先注册裸名再 exec：optimize.py 内部 `import _bootstrap/ad_fetch/diagnose`
# 必须解析到本技能版本（与其他技能同名脚本隔离）；加载完恢复原值避免污染。
_PLAIN_NAMES = ("_bootstrap", "ad_fetch", "diagnose")
_SAVED = {n: sys.modules.get(n) for n in _PLAIN_NAMES}
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"fix06_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"fix06_{name}"] = module
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


try:
    _bootstrap = _load("_bootstrap")
    ad_fetch = _load("ad_fetch")
    diagnose = _load("diagnose")
    optimize = _load("optimize")
finally:
    for _n, _m in _SAVED.items():
        if _m is None:
            sys.modules.pop(_n, None)
        else:
            sys.modules[_n] = _m

from approval import AccumulatorGate, ChangeItem  # noqa: E402  （_bootstrap 已挂 _shared/scripts）


def envelope(list_key: str, rows, *, status: str = "complete", tool_id: str = "synthetic"):
    return {
        "tool_id": tool_id, "status": status, "contract_version": "synthetic-cv",
        "warnings": [], "trace_id": "synthetic", "coverage": {},
        "data": {list_key: rows},
    }


def _write_raw(tmp: str, shop_id: str, *, campaigns=None, keywords=None,
               targetings=None, download_rows=None):
    raw = Path(tmp) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / f"campaigns_{shop_id}.json").write_text(
        json.dumps(envelope("itemList", campaigns or []), ensure_ascii=False), encoding="utf-8")
    if keywords is not None:
        (raw / f"keywords_{shop_id}.json").write_text(
            json.dumps(envelope("itemList", keywords), ensure_ascii=False), encoding="utf-8")
    if targetings is not None:
        (raw / f"targetings_{shop_id}.json").write_text(
            json.dumps(envelope("itemList", targetings), ensure_ascii=False), encoding="utf-8")
    if download_rows is not None:
        (raw / "download_tasks.json").write_text(
            json.dumps(envelope("rows", download_rows,
                                tool_id="get_ad_download_task_page_list"),
                       ensure_ascii=False), encoding="utf-8")
    return raw


OPT_CFG = _bootstrap.load_optimizer()
THRESHOLDS = _bootstrap.load_thresholds()


def _run_cli(*args, cwd=SKILL):
    return subprocess.run([sys.executable, str(SCRIPTS / "optimize.py"), *args],
                          cwd=str(cwd), capture_output=True, text=True)


# ---------------------------------------------------------------------------
# 1. reportState 中文失败态
# ---------------------------------------------------------------------------


class ReportStateClassTest(unittest.TestCase):
    def test_mapping_cn_and_en(self):
        f = ad_fetch._report_state_class
        for s in ("生成失败", "失败", "生成失败（超时）"):
            self.assertEqual(f(s), "failed", s)
        for s in ("FAILED", "FAIL", "HAS_ERROR", "Error"):
            self.assertEqual(f(s), "failed", s)
        for s in ("已生成", "生成完成", "SUCCESS", "COMPLETED", "GENERATED"):
            self.assertEqual(f(s), "success", s)
        for s in ("生成中", "排队中", "", None):
            self.assertEqual(f(s), "unknown", s)

    def test_chinese_failed_state_triggers_warning(self):
        with tempfile.TemporaryDirectory() as td:
            raw = _write_raw(td, "10001", campaigns=[{"campaignId": "C1", "state": "enabled"}],
                             download_rows=[{"reportState": "已生成"}, {"reportState": "生成失败"}])
            data = ad_fetch.load_from_raw(raw, "10001", "2026-10-08", "2026-10-14", OPT_CFG)
            self.assertEqual(data.download_tasks["states"], {"已生成": 1, "生成失败": 1})
            hit = [w for w in data.warnings if "下载任务存在失败态" in w]
            self.assertEqual(len(hit), 1)
            self.assertIn("生成失败", hit[0])
            self.assertIn("不可信", hit[0])

    def test_english_failed_and_no_false_positive(self):
        with tempfile.TemporaryDirectory() as td:
            raw = _write_raw(td, "10001", campaigns=[{"campaignId": "C1", "state": "enabled"}],
                             download_rows=[{"reportState": "FAILED"}])
            data = ad_fetch.load_from_raw(raw, "10001", "2026-10-08", "2026-10-14", OPT_CFG)
            self.assertTrue(any("下载任务存在失败态" in w for w in data.warnings))
        with tempfile.TemporaryDirectory() as td:
            raw = _write_raw(td, "10001", campaigns=[{"campaignId": "C1", "state": "enabled"}],
                             download_rows=[{"reportState": "已生成"}, {"reportState": "生成中"}])
            data = ad_fetch.load_from_raw(raw, "10001", "2026-10-08", "2026-10-14", OPT_CFG)
            self.assertFalse(any("下载任务存在失败态" in w for w in data.warnings))


class ReplayCase5bTest(unittest.TestCase):
    """复放线上归档 case5b（中文失败态信封）：告警必须触发。"""

    def test_archived_chinese_failed_envelope_warns(self):
        raw = LIVE / "case5b" / "raw"
        if not (raw / "download_tasks.json").exists():
            self.skipTest("归档 case5b 不存在")
        data = ad_fetch.load_from_raw(raw, "AD-10001", "2026-09-01", "2026-09-30", OPT_CFG)
        self.assertEqual(data.download_tasks["states"].get("生成失败"), 1)
        hit = [w for w in data.warnings if "下载任务存在失败态" in w]
        self.assertEqual(len(hit), 1, f"中文失败态告警未触发：{data.warnings}")
        self.assertIn("生成失败", hit[0])

    def test_cli_ad_fetch_reports_failed_state(self):
        raw = LIVE / "case5b" / "raw"
        if not (raw / "download_tasks.json").exists():
            self.skipTest("归档 case5b 不存在")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "ad_fetch.py"), "--shop-id", "AD-10001",
             "--start", "2026-09-01", "--end", "2026-09-30", "--raw-dir", str(raw)],
            cwd=str(SKILL), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("下载任务存在失败态", proc.stdout)
        self.assertIn("生成失败", proc.stdout)


# ---------------------------------------------------------------------------
# 2. 商品投放维度覆盖披露
# ---------------------------------------------------------------------------


class ProductTargetingDisclosureTest(unittest.TestCase):

    @staticmethod
    def _render(product_targetings):
        data = ad_fetch.AdData(shop_id="SHOP-T1", campaigns=[{"campaignId": "C1"}],
                               product_targetings=product_targetings)
        findings = diagnose.diagnose(data, THRESHOLDS, OPT_CFG)
        proposal_items = diagnose.build_change_items(findings, OPT_CFG)
        from approval import Proposal
        proposal = Proposal(skill="ad-optimizer", title="t", items=proposal_items)
        return optimize.render_report(data, findings, proposal, optimize._sources_markdown(),
                                      start_date="2026-10-08", end_date="2026-10-14")

    def test_section_present_when_data(self):
        md = self._render([{"targetId": "T1", "state": "enabled", "bid": 0.5},
                           {"targetId": "T2", "state": "enabled", "bid": 0.4}])
        self.assertIn("维度覆盖：商品投放", md)
        self.assertIn("商品投放（sp_product_targeting）数据已获取 2 条", md)
        self.assertIn("本版未实现该维度诊断规则，未纳入诊断结论", md)

    def test_section_absent_when_no_data(self):
        md = self._render([])
        self.assertNotIn("维度覆盖：商品投放", md)


class ReplayCase6Test(unittest.TestCase):
    """复放线上归档 case6（真实 targetings 信封）：披露小节出现。"""

    def test_report_discloses_targeting_dimension(self):
        raw = LIVE / "case6" / "raw"
        if not (raw / "targetings_AD-10001.json").exists():
            self.skipTest("归档 case6 不存在")
        data = ad_fetch.load_from_raw(raw, "AD-10001", "2026-10-05", "2026-10-11", OPT_CFG)
        self.assertTrue(data.product_targetings, "归档 targetings 信封应被组装")
        findings = diagnose.diagnose(data, THRESHOLDS, OPT_CFG)
        items = diagnose.build_change_items(findings, OPT_CFG)
        from approval import Proposal
        proposal = Proposal(skill="ad-optimizer", title="t", items=items)
        md = optimize.render_report(data, findings, proposal, optimize._sources_markdown(),
                                    start_date="2026-10-05", end_date="2026-10-11")
        n = len(data.product_targetings)
        self.assertIn("维度覆盖：商品投放", md)
        self.assertIn(f"数据已获取 {n} 条", md)


# ---------------------------------------------------------------------------
# 3+4. verify：判定表形态 & 未执行项语义
# ---------------------------------------------------------------------------


class RowsFromActualShapeTest(unittest.TestCase):

    def test_verdict_table_without_data_key(self):
        rows, shape = optimize._rows_from_actual(
            {"items": [{"index": 1, "target_id": "C1", "field": "budget", "actual": 60}]})
        self.assertEqual(shape, "verdict")
        self.assertEqual(rows[0]["actual"], 60)

    def test_envelope_shapes(self):
        rows, shape = optimize._rows_from_actual(
            {"status": "complete", "data": {"itemList": [{"campaignId": "C1", "budget": 60}]}})
        self.assertEqual(shape, "envelope")
        self.assertEqual(rows[0]["campaignId"], "C1")
        _, shape2 = optimize._rows_from_actual({"status": "failed", "data": None})
        self.assertEqual(shape2, "envelope")
        _, shape3 = optimize._rows_from_actual({"foo": "bar"})
        self.assertEqual(shape3, "unknown")


def _proposal_payload(items):
    return {"meta": {"shop_id": "SHOP-T1"}, "items": items}


class VerifyExecutedSemanticsTest(unittest.TestCase):

    ITEMS = [
        {"index": 1, "tool": "edit_sp_campaign", "target_type": "campaign",
         "target_id": "C1", "field": "budget", "before": 100, "after": 120},
        {"index": 2, "tool": "edit_sp_campaign", "target_type": "campaign",
         "target_id": "C2", "field": "budget", "before": 50, "after": 60},
    ]

    ENV = {"status": "complete", "data": {"itemList": [
        {"campaignId": "C1", "budget": 120}, {"campaignId": "C2", "budget": 60}]}}

    def test_verdict_branch_reachable_and_backfills_actual(self):
        res = optimize.verify_proposal(
            _proposal_payload(self.ITEMS),
            {"items": [{"index": 1, "target_id": "C1", "field": "budget", "actual": 100}]})
        r1 = res["rows"][0]
        self.assertEqual(r1["actual"], 100)
        self.assertEqual(r1["verdict"], "回读与期望不一致")
        self.assertNotIn("信封中未找到", r1["verdict"])

    def test_unexecuted_items_marked_not_reported_as_mismatch(self):
        res = optimize.verify_proposal(_proposal_payload(self.ITEMS), self.ENV, executed={1})
        by_idx = {r["index"]: r for r in res["rows"]}
        self.assertTrue(by_idx[1]["executed"])
        self.assertTrue(by_idx[1]["ok"])
        self.assertFalse(by_idx[2]["executed"])
        self.assertIn("未执行（不在本次批准范围）", by_idx[2]["verdict"])
        self.assertTrue(res["ok"], "未执行项不得让整体报不一致")

    def test_unexecuted_item_missing_from_actual_is_not_mismatch(self):
        # 子集批准后全量 verify：未执行项不在回读里也不报「不一致」
        res = optimize.verify_proposal(
            _proposal_payload(self.ITEMS),
            {"status": "complete", "data": {"itemList": [{"campaignId": "C1", "budget": 120}]}},
            executed={1})
        r2 = res["rows"][1]
        self.assertFalse(r2["executed"])
        self.assertIn("未执行", r2["verdict"])
        self.assertTrue(res["ok"])

    def test_all_unexecuted_and_pair_fallback(self):
        # 全部未执行：无已比对项 → ok=False（CLI 层给「均未执行」特殊提示）
        res = optimize.verify_proposal(_proposal_payload(self.ITEMS), self.ENV,
                                       executed=set(), executed_pairs=set())
        self.assertTrue(all(not r["executed"] and "未执行" in r["verdict"] for r in res["rows"]))
        self.assertFalse(res["ok"])
        # 缺 index 的记录按 (target_id, field) 兜底命中
        res2 = optimize.verify_proposal(_proposal_payload(self.ITEMS), self.ENV,
                                        executed=set(), executed_pairs={("C2", "budget")})
        by_idx = {r["index"]: r for r in res2["rows"]}
        self.assertFalse(by_idx[1]["executed"])
        self.assertTrue(by_idx[2]["executed"])  # (target_id, field) 兜底命中
        self.assertTrue(res2["ok"])

    def test_envelope_branch_still_compares(self):
        res = optimize.verify_proposal(_proposal_payload(self.ITEMS), self.ENV)
        self.assertTrue(res["ok"])
        self.assertTrue(all(r["verdict"] == "一致" for r in res["rows"]))

    def test_executed_from_log(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "ad_changes.jsonl"
            recs = [
                {"index": 1, "target_id": "C1", "field": "budget", "status": "success"},
                {"target_id": "C9", "field": "state", "status": "failed"},  # 缺 index → pair 兜底
                {"index": 3, "target_id": "C3", "field": "budget", "status": "skipped"},  # 未执行
                "not-json",
            ]
            log.write_text("\n".join(json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r
                                     for r in recs) + "\n", encoding="utf-8")
            executed, pairs = optimize._executed_from_log(log)
            self.assertEqual(executed, {1})
            self.assertIn(("C9", "state"), pairs)
            self.assertNotIn(3, executed)
            # 日志不存在 = 什么都没执行
            self.assertEqual(optimize._executed_from_log(Path(td) / "none.jsonl"), (set(), set()))


class ReplayCase9VerifyTest(unittest.TestCase):
    """复放线上归档 case9：判定表比对可达 + 未执行项不再误报。"""

    CASE = LIVE / "case9"

    def test_verdict_shape_and_unexecuted_semantics(self):
        proposal = self.CASE / "ad_proposal_2026-10-05_2026-10-11.json"
        actual = self.CASE / "readback_verdict.json"
        log = self.CASE / "ad_changes.jsonl"
        if not (proposal.exists() and actual.exists() and log.exists()):
            self.skipTest("归档 case9 不存在")
        payload = json.loads(proposal.read_text(encoding="utf-8"))
        executed, pairs = optimize._executed_from_log(log)
        self.assertIn(1, executed)   # #1 执行过（failed 记录）
        self.assertNotIn(2, executed)
        res = optimize.verify_proposal(payload, json.loads(actual.read_text(encoding="utf-8")),
                                       executed=executed, executed_pairs=pairs)
        by_idx = {r["index"]: r for r in res["rows"]}
        self.assertEqual(by_idx[1]["actual"], 10.0)
        self.assertEqual(by_idx[1]["verdict"], "回读与期望不一致")  # 不再是「回读信封中未找到」
        self.assertIn("未执行（不在本次批准范围）", by_idx[2]["verdict"])
        self.assertFalse(res["ok"])

    def test_cli_verify_exit_2_with_clean_table(self):
        proposal = self.CASE / "ad_proposal_2026-10-05_2026-10-11.json"
        actual = self.CASE / "readback_verdict.json"
        log = self.CASE / "ad_changes.jsonl"
        if not (proposal.exists() and actual.exists() and log.exists()):
            self.skipTest("归档 case9 不存在")
        proc = _run_cli("verify", "--proposal", str(proposal), "--actual", str(actual),
                        "--log", str(log))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("回读与期望不一致", proc.stdout)
        self.assertIn("未执行（不在本次批准范围）", proc.stdout)
        self.assertIn("1 项未执行", proc.stdout)
        self.assertNotIn("回读信封中未找到", proc.stdout)


# ---------------------------------------------------------------------------
# 5. 不支持的写工具 → 干净报错 fail-closed
# ---------------------------------------------------------------------------


def _mixed_proposal(path: Path):
    payload = {
        "meta": {"skill": "ad-optimizer", "title": "混入域外工具的提案",
                 "shop_id": "SHOP-T1", "demo_fingerprint": None,
                 "start_date": "2026-10-09", "end_date": "2026-10-15"},
        "items": [
            {"index": 1, "tool": "edit_sp_campaign", "target_type": "campaign",
             "target_id": "900000000000301", "field": "budget", "before": 100, "after": 120,
             "reason": "test", "action": "set_budget", "risk": "L2", "extra": {}},
            {"index": 2, "tool": "edit_listing_title", "target_type": "listing",
             "target_id": "B0TEST0001", "field": "title", "before": "旧标题", "after": "新标题",
             "reason": "域外请求", "action": "", "risk": "L4", "extra": {}},
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class UnsupportedToolFailClosedTest(unittest.TestCase):

    def test_write_args_still_raises(self):
        from approval import ChangeItem
        bad = ChangeItem(tool="edit_listing_title", target_type="listing",
                         target_id="B0X", field="title", before="a", after="b")
        with self.assertRaises(ValueError):
            optimize._write_args(bad, "SHOP-T1")

    def test_execute_clean_error_exit_5_no_traceback_no_checklist(self):
        with tempfile.TemporaryDirectory() as td:
            proposal = _mixed_proposal(Path(td) / "mixed.json")
            before = set(SKILL.glob("out/ad_execute_*"))
            proc = _run_cli("execute", "--proposal", str(proposal), "--approve", "批准全部")
            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 5, combined)
            self.assertIn("提案项 #2", combined)
            self.assertIn("edit_listing_title", combined)
            self.assertIn("拒绝执行整批", combined)
            self.assertNotIn("Traceback", combined)
            self.assertEqual(set(SKILL.glob("out/ad_execute_*")), before,
                             "fail-closed：不得生成任何执行清单")


# ---------------------------------------------------------------------------
# 6. 累计闸跨周拦截（依赖 _shared 默认 7 天回看窗）
# ---------------------------------------------------------------------------


def _ts(hours_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - hours_ago * 3600))


class AccumulatorGateLookbackTest(unittest.TestCase):

    @staticmethod
    def _gate(log: Path):
        return AccumulatorGate(str(log), limits=THRESHOLDS.get("approval"))

    def test_config_has_no_stale_lookback_key(self):
        # 06 读的 thresholds approval 段没有 net_change_lookback_hours 旧键 → 用 _shared 默认 168h
        approval = THRESHOLDS.get("approval") or {}
        self.assertNotIn("net_change_lookback_hours", approval)
        self.assertNotIn("net_change_lookback", json.dumps(OPT_CFG))
        self.assertEqual(self._gate(Path("/nonexistent")).limits["net_change_lookback_hours"], 168)

    def test_last_week_plus30_then_today_plus30_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "ad_changes.jsonl"
            rec = {"tool": "edit_sp_campaign", "target_type": "campaign", "target_id": "C700",
                   "field": "budget", "before": 100, "after": 130, "status": "success",
                   "ts": _ts(6 * 24)}  # 上周（6 天前，在默认 7 天回看窗内）
            log.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
            item = ChangeItem(tool="edit_sp_campaign", target_type="campaign", target_id="C700",
                              field="budget", before=130, after=169, reason="再 +30%")
            self.assertEqual(item.delta_pct, 30.0)  # 单步合规
            violations = self._gate(log).check([item])
            net = [v for v in violations if "净变化" in v]
            self.assertTrue(net, f"跨周累计净变化应被拦：{violations}")
            self.assertIn("+69.0%", net[0])
            # 拦截必须来自回看窗净变化，而非 24h 次数/今日去重（历史不在 24h 内）
            self.assertFalse([v for v in violations if "24h 内已调整" in v or "今日已变更" in v])

    def test_history_outside_lookback_not_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "ad_changes.jsonl"
            rec = {"tool": "edit_sp_campaign", "target_type": "campaign", "target_id": "C700",
                   "field": "budget", "before": 100, "after": 130, "status": "success",
                   "ts": _ts(9 * 24)}  # 9 天前，超出默认回看窗
            log.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
            item = ChangeItem(tool="edit_sp_campaign", target_type="campaign", target_id="C700",
                              field="budget", before=130, after=169, reason="窗口外")
            self.assertEqual(self._gate(log).check([item]), [])


class MerchantCopyTest(unittest.TestCase):
    """[2026-10-22 商家视角] 空提案直说 + 绩效措辞/规则名中文（工程词不出现在成品报告）。"""

    @staticmethod
    def _no_perf_report():
        from approval import Proposal
        data = ad_fetch.AdData(shop_id="AD-10001",
                               campaigns=[{"campaignId": "C1", "name": "演示活动", "state": "paused", "budget": 10}])
        findings = diagnose.diagnose(data, THRESHOLDS, OPT_CFG)
        proposal = Proposal(skill="ad-optimizer", title="广告优化提案 2026-10-05~2026-10-11",
                            items=[], risk_level="L2")
        return optimize.render_report(data, findings, proposal, optimize._sources_markdown(),
                                      start_date="2026-10-05", end_date="2026-10-11")

    def test_empty_proposal_says_no_changes_upfront(self):
        md = self._no_perf_report()
        self.assertIn("本次不产生变更提案——绩效数据缺失，无法判断投放好坏", md)
        self.assertIn("下一步：先解决绩效文件获取（见诊断明细 P2 项），再重跑", md)

    def test_empty_proposal_omits_empty_table_and_rollback_keeps_confirmation(self):
        md = self._no_perf_report()
        self.assertNotIn("## 变更前后对比", md)
        self.assertNotIn("## 回滚方案", md)
        self.assertIn("## 确认方式", md)
        self.assertIn("批准 1,3,5", md)
        self.assertIn("变更条数：0", md)

    def test_perf_none_wording_translated(self):
        md = self._no_perf_report()
        self.assertIn("绩效数据：缺失", md)
        self.assertNotIn("绩效来源", md)
        self.assertNotIn("scope=none", md)

    def test_insufficient_data_rule_shown_in_chinese(self):
        md = self._no_perf_report()
        self.assertIn("| P2 | 数据不足 |", md)
        self.assertNotIn("insufficient_data", md)

    def test_perf_present_wording_translated(self):
        from approval import Proposal
        data = ad_fetch.AdData(shop_id="SHOP-T2", campaigns=[{"campaignId": "C1"}],
                               perf_campaign=[{"campaignId": "C1", "cost": "1", "orders": "1"}],
                               perf_source="download_task", perf_scope="campaign")
        findings = diagnose.diagnose(data, THRESHOLDS, OPT_CFG)
        md = optimize.render_report(data, findings, Proposal(skill="ad-optimizer", title="t", items=[]),
                                    optimize._sources_markdown(),
                                    start_date="2026-10-05", end_date="2026-10-11")
        self.assertIn("绩效数据：下载任务报表（活动级）", md)
        self.assertNotIn("绩效来源", md)
        # 有绩效但 0 条提案：直说未触发规则，不渲染空表
        self.assertIn("本次不产生变更提案——已取到的绩效未触发自动优化规则", md)
        self.assertNotIn("## 变更前后对比", md)

    def test_nonempty_proposal_keeps_full_table(self):
        from approval import Proposal, ChangeItem
        data = ad_fetch.AdData(shop_id="SHOP-T3", campaigns=[{"campaignId": "C1"}],
                               perf_source="download_task", perf_scope="campaign")
        proposal = Proposal(skill="ad-optimizer", title="t", items=[
            ChangeItem(tool="edit_sp_campaign", target_type="campaign", target_id="C1",
                       field="budget", before=10, after=13, reason="r")])
        md = optimize.render_report(data, [], proposal, optimize._sources_markdown(),
                                    start_date="2026-10-05", end_date="2026-10-11")
        self.assertIn("## 变更前后对比", md)
        self.assertIn("## 回滚方案", md)
        self.assertNotIn("本次不产生变更提案", md)


class SelftestRunTest(unittest.TestCase):

    def test_selftest_green_with_upgraded_assertions(self):
        proc = _run_cli("selftest")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("自检通过", proc.stdout)
        self.assertIn("record/verify helper 往返一致", proc.stdout)


class V17DeFeishuDeCustomReportTest(unittest.TestCase):
    """[v1.7.0] 去飞书 + 去自定义报表：审批=会话内人工确认；custom_report 兜底移除。"""

    DOCS = ("SKILL.md", "references/workflow.md", "references/output-templates.md",
            "references/field-dictionary.md", "references/diagnosis-rules.md")

    def test_docs_have_no_feishu_approval_chain(self):
        for rel in self.DOCS:
            text = (SKILL / rel).read_text(encoding="utf-8")
            self.assertNotIn("push_proposal", text, rel)
            self.assertNotIn("## 五、飞书审批", text, rel)   # 旧章节头不得复现
            self.assertNotIn("飞书审批卡片", text, rel)       # output-templates 旧节
            self.assertNotIn("写飞书", text, rel)
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("会话内人工确认", skill)
        self.assertNotIn("推送飞书审批", skill)
        wf = (SKILL / "references" / "workflow.md").read_text(encoding="utf-8")
        self.assertIn("五、人工确认（会话内）", wf)

    def test_no_get_custom_report_page_list_anywhere(self):
        for path in list(SKILL.rglob("*")):
            if "__pycache__" in path.parts or "out" in path.parts or not path.is_file():
                continue
            if path.suffix in (".py", ".md", ".json", ".yaml"):
                self.assertNotIn("get_custom_report_page_list",
                                 path.read_text(encoding="utf-8"), str(path))

    def test_custom_report_fallback_removed_in_loader(self):
        # perf_primary=custom_report（旧配置残留）不再加载 perf_custom_<shopId>.json：
        # 落盘存在也按「非法值/移除」处理，绩效保持缺失并告警（fail-closed 不编造绩效）。
        cfg = json.loads(json.dumps(OPT_CFG))
        cfg["data_source"] = {"perf_primary": "custom_report"}
        with tempfile.TemporaryDirectory() as td:
            raw = _write_raw(td, "10001", campaigns=[{"campaignId": "C1", "state": "enabled"}])
            (raw / "perf_custom_10001.json").write_text(
                json.dumps(envelope("rows", [{"adCost": "9.9", "msku": "M1"}]),
                           ensure_ascii=False), encoding="utf-8")
            data = ad_fetch.load_from_raw(raw, "10001", "2026-10-08", "2026-10-14", cfg)
        self.assertEqual(data.perf_source, "none")
        self.assertEqual(data.perf_campaign, [])
        self.assertTrue(any("custom_report" in w and "移除" in w
                            for w in data.warnings), data.warnings)

    def test_scripts_have_no_feishu_wording(self):
        for name in ("optimize.py", "ad_fetch.py", "diagnose.py", "_bootstrap.py"):
            src = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertNotIn("飞书", src, name)
            self.assertNotIn("feishu", src, name)
            self.assertNotIn("perf_custom", src, name)

    def test_evals_approval_wording_updated(self):
        evals = json.loads((SKILL / "evals" / "evals.json").read_text(encoding="utf-8"))
        joined = json.dumps(evals, ensure_ascii=False)
        self.assertNotIn("push_proposal", joined)
        self.assertNotIn("custom_report 兜底并标注", joined)
        self.assertIn("会话内人工确认", joined)


if __name__ == "__main__":
    unittest.main()
