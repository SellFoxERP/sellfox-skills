"""09-voc-insight 修复回归测试（2026-10-16 评测问题 P1-P8 / N1-N3；2026-10-22 去飞书/去自定义报表改写）。

直测修复的行为（零网络，只喂合成落盘信封）：
  1. [P5] signals.errors/error_details 渲染进「0. 数据说明」：步骤/工具/error_code/是否可重试逐条可见；
  2. [固定口径] 宽表信号（lowReviewNum/reviewNum/reviewRate 及环比）由 custom-report-analysis 承接，
     报告置顶固定横幅；残留宽表落盘不消费并披露；
  3. [P6] online 快照取数失败时第 1 节给「取数失败（error_code：X）」；
     observed 为空不再打印「以下展示已取到的评分快照」；
  4. [P2] 快照低分筛选补 min_rating_count 小样本保护（评分数不足不入选）；
  5. [P3] 成本闸：显式 --adapter-file 视为启用外部调用意图（不再按 enabled=false 判 HOLD），
     HOLD 基于「将发生外部调用」；--reviews-json 本地回灌不属于外部调用（无闸门 + 标注）；
  6. [N2] flagged=0 时跳过「将对 0 个 ASIN 发起外部调用」确认，直接输出无调用结论；
  7. [P4] 编排入口 --end-date 透传 fetch_signals，窗口固定；
  8. [P7] 损坏 JSON 与文件缺失区分报错（corrupt ≠ missing，排查方向不同）；
  9. [N3] 人工核查清单语义重复条目去重 +「⚠️ ⚠️」双前缀修单；
 10. [P1] SKILL.md 的 validate_raw 示例命令带仓库根运行前置。
2026-10-22：自定义报表两窗口步骤已删，online_products.json 为唯一必选主路径；
  --report-id / --feishu 入口已移除。
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "09-voc-insight"
SCRIPTS = SKILL / "scripts"

# 平名 import（voc_common）必须解析到本技能版本：先注册再 exec，加载完恢复避免污染。
_SAVED = sys.modules.get("voc_common")
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"fix09_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"fix09_{name}"] = module
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


try:
    fetch_signals = _load("fetch_signals")
    render_report = _load("render_report")
    fetch_reviews = _load("fetch_reviews")
    voc_insight = _load("voc_insight")
finally:
    if _SAVED is None:
        sys.modules.pop("voc_common", None)
    else:
        sys.modules["voc_common"] = _SAVED


# ---------------------------------------------------------------- 合成信封 --

def _ok_envelope(rows):
    return {"status": "complete", "data": {"rows": rows},
            "coverage": {"complete": True}, "trace_id": "t-ok"}


def _failed_envelope(code, message="upstream rejected", retryable=False):
    return {"status": "failed", "error_code": code, "message": message,
            "retryable": retryable, "data": None, "trace_id": "t-fail"}


def _row(asin, **kw):
    base = {"asin": asin, "shopId": 10001, "title": f"item-{asin}", "saleNum": 100,
            "rating": 4.6, "ratingCount": 200, "lowReviewNum": 1, "reviewNum": 10,
            "reviewRate": 0.95}
    base.update(kw)
    return base


class _TmpRawMixin:
    def _raw(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raw = Path(tmp.name) / "raw"
        raw.mkdir()
        for name, obj in files.items():
            p = raw / name
            if isinstance(obj, str):
                p.write_text(obj, encoding="utf-8")
            else:
                p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return raw

    def _signals(self, raw, days=7):
        out = raw.parent / "signals.json"
        ns = argparse.Namespace(days=days, end_date=None,
                                raw_dir=str(raw), out=str(out))
        rc = fetch_signals.run(ns)
        return rc, json.loads(out.read_text(encoding="utf-8"))


# --------------------------------------------------- Fix 1 / 3：失败可见性 --

class TestErrorsRenderedInHeader(unittest.TestCase, _TmpRawMixin):
    def test_error_details_rendered_with_step_tool_code_retryable(self):
        raw = self._raw({
            "online_products.json": _failed_envelope("CONTRACT_VERSION_MISMATCH", "cv mismatch", None),
        })
        _, signals = self._signals(raw)
        md = render_report.render(signals, {}, None)
        self.assertIn("取数失败（该源已按无数据处理，需人工核查）", md)
        self.assertIn("步骤 out/raw/online_products.json（工具 get_online_product_page_list）", md)
        self.assertIn("error_code=CONTRACT_VERSION_MISMATCH", md)
        self.assertIn("是否可重试：未知", md)  # retryable 缺省（离线合成信封形态）
        # 第 1 节：取数失败 ≠ 无数据；observed 为空不打快照引导语
        self.assertIn("取数失败（error_code：CONTRACT_VERSION_MISMATCH），原因见「0. 数据说明」", md)
        self.assertNotIn("以下展示已取到的评分快照", md)

    def test_retryable_true_false_words(self):
        raw_f = self._raw({"online_products.json": _failed_envelope("RESULT_BUDGET_EXCEEDED", "budget", False)})
        _, signals_f = self._signals(raw_f)
        md_f = render_report.render(signals_f, {}, None)
        self.assertIn("是否可重试：否", md_f)
        raw_t = self._raw({"online_products.json": _failed_envelope("RATE_LIMITED", "slow down", True)})
        _, signals_t = self._signals(raw_t)
        md_t = render_report.render(signals_t, {}, None)
        self.assertIn("是否可重试：是", md_t)

    def test_legacy_errors_strings_still_visible(self):
        # 旧版 signals（无 error_details）：errors 字符串兜底渲染，error_code 仍全文可见
        signals = {"window": {"days": 7, "current": ["a", "b"], "previous": ["c", "d"]},
                   "source": {"degraded": "none"},
                   "counts": {"previous_rows": 0}, "flagged": [], "observed": [],
                   "errors": ["out/raw/online_products.json status=failed error_code=X_CODE boom"]}
        md = render_report.render(signals, {}, None)
        self.assertIn("error_code=X_CODE", md)
        self.assertIn("取数失败（error_code：X_CODE）", md)


# ------------------------------------------------- Fix 2：固定口径（宽表承接）--

class TestFixedHandoff(unittest.TestCase, _TmpRawMixin):
    def test_handoff_banner_top_and_manual(self):
        raw = self._raw({"online_products.json": _ok_envelope([_row("B0X1")])})
        _, signals = self._signals(raw)
        src = signals["source"]
        self.assertEqual(src["degraded"], "none")
        self.assertIn("custom-report-analysis", src["handoff_banner"])
        self.assertIn("固定口径", src["handoff_banner"])
        self.assertTrue(any("custom-report-analysis" in w for w in signals["warnings"]))
        self.assertTrue(any("custom-report-analysis" in m for m in signals["manual_checks"]))
        md = render_report.render(signals, {}, None)
        head = md.split("## 1.")[0]
        self.assertIn("custom-report-analysis", head)
        self.assertIn("固定口径", head)

    def test_stale_custom_raw_not_consumed_but_disclosed(self):
        raw = self._raw({
            "online_products.json": _ok_envelope([_row("B0X1")]),
            "custom_current.json": _ok_envelope([_row("B0X2")]),
            "custom_previous.json": _ok_envelope([_row("B0X2")]),
        })
        _, signals = self._signals(raw)
        # 快照行只来自 online；宽表行未混入
        self.assertEqual(len(signals["observed"]), 1)
        self.assertEqual(signals["counts"]["current_rows"], 1)
        self.assertTrue(any("custom_current" in d and "custom-report-analysis" in d
                            for d in signals["disclosures"]), signals["disclosures"])

    def test_snapshot_flags_low_rating_rows(self):
        raw = self._raw({"online_products.json": _ok_envelope([
            _row("B0LOW1", rating=3.6, ratingCount=300, saleNum=50),
            _row("B0HIGH", rating=4.6, ratingCount=300, saleNum=999),
        ])})
        _, signals = self._signals(raw)
        self.assertEqual([f["asin"] for f in signals["flagged"]], ["B0LOW1"])
        self.assertEqual(signals["flagged"][0]["flags"][0]["signal"], "low_rating_snapshot")


# ------------------------------------------------- Fix 3：observed 空引导语 --

class TestSnapshotIntroOnlyWhenObserved(unittest.TestCase, _TmpRawMixin):
    def test_snapshot_table_rendered_when_flagged_empty(self):
        raw = self._raw({"online_products.json": _ok_envelope([_row("B0Y1", rating=4.5)])})
        _, signals = self._signals(raw)
        md = render_report.render(signals, {}, None)
        sec1 = md.split("## 1.")[1].split("## 2.")[0]
        self.assertIn("快照内无", sec1)
        self.assertIn("以下展示已取到的评分快照", sec1)
        self.assertIn("| B0Y1", sec1)
        self.assertIn("无法判断评分是否下降", sec1)
        self.assertIn("custom-report-analysis", sec1)


# ------------------------------------------------ Fix 4：小样本保护对称性 --

class TestSmallSampleProtection(unittest.TestCase):
    def _flagged(self, rating_count):
        rows = [_row("B0S1", rating=3.5, ratingCount=rating_count)]
        out = fetch_signals.build_low_rating_rows(
            rows, rating_low=4.0, min_rating_count=20,
        )
        return out

    def test_small_sample_not_flagged(self):
        # ratingCount=12 < min_rating_count=20：评分 3.5 也不入选（小样本不判）
        self.assertEqual(self._flagged(12), [])

    def test_large_sample_flagged(self):
        flagged = self._flagged(200)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["asin"], "B0S1")

    def test_missing_rating_not_flagged(self):
        self.assertEqual(fetch_signals.build_low_rating_rows(
            [_row("B0S2", rating=None)], rating_low=4.0, min_rating_count=20), [])


# ------------------------------------------------- Fix 5 / 6：成本闸语义 --

class TestCostGate(unittest.TestCase, _TmpRawMixin):
    ADAPTER = str(SKILL / "adapters" / "example_voc_reviews.md")

    def _run_gate(self, signals_obj, argv):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        spath = Path(tmp.name) / "signals.json"
        spath.write_text(json.dumps(signals_obj, ensure_ascii=False), encoding="utf-8")
        out = Path(tmp.name) / "reviews.json"
        opts = {"--adapter-file": "adapter_file", "--reviews-json": "reviews_json",
                "--confirm-external": "confirm_external"}
        kw = {"adapter_file": None, "reviews_json": None, "confirm_external": False}
        it = iter(argv)
        for a in it:
            if a == "--confirm-external":
                kw["confirm_external"] = True
            else:
                kw[opts[a]] = next(it)
        ns = argparse.Namespace(signals=str(spath), out=str(out), **kw)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fetch_reviews.run(ns)
        return rc, buf.getvalue(), json.loads(out.read_text(encoding="utf-8"))

    @staticmethod
    def _signals(flagged_count):
        flagged = [{"asin": f"B0G{i}", "flags": [{"signal": "low_rating_snapshot"}]}
                   for i in range(flagged_count)]
        return {"flagged": flagged, "counts": {"flagged": flagged_count}}

    def test_flagged_zero_skips_confirmation(self):
        _, console, payload = self._run_gate(self._signals(0), [])
        self.assertIn("问题 ASIN 为 0 → 本次不发起外部调用，无需确认", console)
        self.assertNotIn("待确认", console)

    def test_explicit_adapter_file_overrides_enabled_false(self):
        # 显式 --adapter-file = 启用外部调用意图：HOLD 理由不含 enabled=false，只剩待确认
        _, console, payload = self._run_gate(self._signals(3), ["--adapter-file", self.ADAPTER])
        self.assertIn("闸门状态        : HOLD", console)
        self.assertNotIn("enabled=false", console)
        self.assertTrue(any("待确认：将对 3 个 ASIN" in r for r in payload["gate"]["reasons"]))
        self.assertTrue(payload["gate"]["pending_external_call"])

    def test_no_intent_still_hold_with_enabled_reason(self):
        _, console, payload = self._run_gate(self._signals(3), [])
        self.assertIn("enabled=false", console)
        self.assertEqual(payload["gate"]["action"], "HOLD")
        self.assertFalse(payload["gate"]["pending_external_call"])

    def test_local_replay_no_gate_but_annotated(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        reviews_path = Path(tmp.name) / "raw_reviews.json"
        reviews_path.write_text(json.dumps([
            {"id": "R1", "asin": "B0G0", "rating": 2, "text": "too small",
             "reviewer_name": "JohnSmith42", "reviewer_hash": "h1"},
        ], ensure_ascii=False), encoding="utf-8")
        _, console, payload = self._run_gate(
            self._signals(3), ["--adapter-file", self.ADAPTER, "--confirm-external",
                               "--reviews-json", str(reviews_path)])
        self.assertIn("N/A（本地回灌）", console)
        self.assertIn("本地回灌样本，未发生外部调用", console)
        self.assertTrue(payload["gate"]["local_replay"])
        self.assertFalse(payload["gate"]["pending_external_call"])
        self.assertTrue(any("本地回灌样本，未发生外部调用" in w for w in payload["warnings"]))
        self.assertEqual(len(payload["reviews"]), 1)  # 回灌照常规范化（掩码后仍 1 条）
        self.assertEqual(payload["reviews"][0]["reviewer_name"], "J" + "*" * 9 + "2")


# --------------------------------------------------- Fix 7：--end-date 透传 --

class TestEndDatePassthrough(unittest.TestCase):
    def test_parser_and_run_forward_end_date(self):
        argv = ["voc_insight.py", "--days", "7", "--end-date", "2026-10-15"]
        saved_argv, sys.argv = sys.argv, argv
        captured = {}
        real_run, real_check = voc_insight._run, voc_insight.check_raw

        def fake_run(script, sargv):
            captured.setdefault(script, []).append(list(sargv))
            return 0

        voc_insight._run = fake_run
        voc_insight.check_raw = lambda: 0  # 跳过落盘检查（本测试只验证透传）
        try:
            with redirect_stdout(io.StringIO()):
                rc = voc_insight.main()
        finally:
            voc_insight._run = real_run
            voc_insight.check_raw = real_check
            sys.argv = saved_argv
        self.assertEqual(rc, 0)
        fs_argv = captured["fetch_signals.py"][0]
        self.assertIn("--end-date", fs_argv)
        self.assertEqual(fs_argv[fs_argv.index("--end-date") + 1], "2026-10-15")

    def test_parser_no_feishu_or_report_id_flags(self):
        """去飞书/去自定义报表后：编排入口不再有 --feishu / --report-id 参数。"""
        argv = ["voc_insight.py", "--help"]
        saved_argv, sys.argv = sys.argv, argv
        try:
            with self.assertRaises(SystemExit) as cm:
                with redirect_stdout(io.StringIO()) as buf:
                    voc_insight.main()
        finally:
            sys.argv = saved_argv
        self.assertEqual(cm.exception.code, 0)
        self.assertNotIn("--feishu", buf.getvalue())
        self.assertNotIn("--report-id", buf.getvalue())

    def test_raw_steps_only_online(self):
        """落盘检查只关注 online_products.json（宽表两窗口已删）。"""
        self.assertEqual([name for name, _ in voc_insight.RAW_STEPS],
                         ["online_products.json"])


# ------------------------------------------------- Fix 8：corrupt ≠ missing --

class TestCorruptVsMissing(unittest.TestCase, _TmpRawMixin):
    def test_missing_reports_missing(self):
        raw = self._raw({"stray_step.json": _ok_envelope([_row("B0M1")])})
        with self.assertRaises(SystemExit) as cm:
            self._signals(raw)
        msg = str(cm.exception)
        self.assertIn("缺少 out/raw/online_products.json", msg)
        self.assertNotIn("解析失败", msg)

    def test_corrupt_reports_corrupt_not_missing(self):
        raw = self._raw({"online_products.json": '{"status": "complete", "data": {"rows": [ TRUNC'})
        with self.assertRaises(SystemExit) as cm:
            self._signals(raw)
        msg = str(cm.exception)
        self.assertIn("存在但解析失败（损坏）", msg)
        self.assertIn("online_products.json", msg)
        self.assertNotIn("缺少 out/raw/online_products.json", msg)


# --------------------------------------------- Fix 9：清单去重 + 前缀修单 --

class TestManualSectionDedup(unittest.TestCase):
    def _signals(self):
        return {
            "window": {"days": 7, "current": ["a", "b"], "previous": ["c", "d"]},
            "source": {"degraded": "none",
                       "handoff_banner": ("⚠️ 固定口径：自定义报表（489 宽表）已移出本技能，"
                                          "lowReviewNum / reviewNum / reviewRate 及其环比不可用；"
                                          "差评数与宽表分析由 custom-report-analysis 技能承接。"
                                          "本报告基于 online 评分快照（rating/ratingCount，无历史基线，不做评分环比）。")},
            "counts": {"previous_rows": 0}, "flagged": [], "observed": [],
            "warnings": ["⚠️ 固定口径：自定义报表（489 宽表）已移出本技能，lowReviewNum / reviewNum / reviewRate 及其环比不可用；差评数与宽表分析由 custom-report-analysis 技能承接。本报告基于 online 评分快照（rating/ratingCount，无历史基线，不做评分环比）。"],
            "manual_checks": ["lowReviewNum / reviewNum / reviewRate：需自定义报表宽表 → 由 custom-report-analysis 技能承接；评分环比基线（online 无历史）无法构造，需人工比对历史或改用宽表。"],
        }

    def _section(self, signals):
        md = render_report.render(signals, {}, None)
        return md.split("## 4. 需人工核查清单")[1].split("## 5.")[0]

    def test_no_double_prefix(self):
        self.assertNotIn("⚠️ ⚠️", self._section(self._signals()))

    def test_no_duplicate_low_review_item(self):
        sec = self._section(self._signals())
        # manual_checks 长条一次；横幅原文在「0. 数据说明」置顶、清单不再复读（去重生效）
        self.assertEqual(sec.count("lowReviewNum"), 1)

    def test_exact_duplicate_items_collapsed(self):
        signals = self._signals()
        signals["manual_checks"].append("评论原文的完整性与代表性（外部源抽样可能偏倚）：需人工判断。")
        sec = self._section(signals)
        self.assertEqual(sec.count("评论原文的完整性与代表性"), 1)


# ------------------------------------------------------ Fix 10：文档前置 --

class TestSkillDocCommand(unittest.TestCase):
    def test_validate_raw_command_has_repo_root_prerequisite(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        block = text.split("_shared/scripts/validate_raw.py")[0].split("```bash")[-1]
        self.assertIn("仓库根", block)  # 示例命令块内说明须从仓库根运行

    def test_validate_raw_step_is_online_only(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--step online_products:rows", text)


if __name__ == "__main__":
    unittest.main()
