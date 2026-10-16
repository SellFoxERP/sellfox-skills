"""09-voc-insight 修复回归测试（2026-10-16 评测问题 P1-P8 / N1-N3）。

直测十条修复的行为（零网络，只喂合成落盘信封）：
  1. [P5] signals.errors/error_details 渲染进「0. 数据说明」：步骤/工具/error_code/是否可重试逐条可见；
  2. [N1] 降级原因三态区分：未配置 / 发现失败（error_code）/ 候选验证被拒（error_code），各自文案如实，
     不把上游故障误归因为用户没配置 report_id；
  3. [P6] 两窗口均 failed 时第 1 节给「取数失败（error_code：X）」而非「缺少历史基线」；
     observed 为空不再打印「以下展示已取到的评分快照」；
  4. [P2] review_rate_drop 补 min_rating_count 小样本保护（与 rating_drop 对称）；
  5. [P3] 成本闸：显式 --adapter-file 视为启用外部调用意图（不再按 enabled=false 判 HOLD），
     HOLD 基于「将发生外部调用」；--reviews-json 本地回灌不属于外部调用（无闸门 + 标注）；
  6. [N2] flagged=0 时跳过「将对 0 个 ASIN 发起外部调用」确认，直接输出无调用结论；
  7. [P4] 编排入口 --end-date 透传 fetch_signals，环比两窗口固定；
  8. [P7] 损坏 JSON 与文件缺失区分报错（corrupt ≠ missing，排查方向不同）；
  9. [N3] 人工核查清单语义重复条目去重 +「⚠️ ⚠️」双前缀修单；上游失败态不再补「需配置 report_id」；
 10. [P1] SKILL.md 的 validate_raw 示例命令带仓库根运行前置。
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

    def _signals(self, raw, report_id="880099", days=7):
        out = raw.parent / "signals.json"
        ns = argparse.Namespace(days=days, end_date=None, report_id=report_id,
                                raw_dir=str(raw), out=str(out))
        rc = fetch_signals.run(ns)
        return rc, json.loads(out.read_text(encoding="utf-8"))


# --------------------------------------------------- Fix 1 / 3：失败可见性 --

class TestErrorsRenderedInHeader(unittest.TestCase, _TmpRawMixin):
    def test_error_details_rendered_with_step_tool_code_retryable(self):
        raw = self._raw({
            "custom_current.json": _failed_envelope("CONTRACT_VERSION_MISMATCH", "cv mismatch", None),
            "custom_previous.json": _failed_envelope("CONTRACT_VERSION_MISMATCH", "cv mismatch", None),
        })
        _, signals = self._signals(raw)
        md = render_report.render(signals, {}, None)
        self.assertIn("取数失败（该源已按无数据处理，需人工核查）", md)
        self.assertIn("步骤 out/raw/custom_current.json（工具 get_custom_report_page_list）", md)
        self.assertIn("error_code=CONTRACT_VERSION_MISMATCH", md)
        self.assertIn("是否可重试：未知", md)  # retryable 缺省（离线合成信封形态）
        # 第 1 节：取数失败 ≠ 缺历史基线；observed 为空不打快照引导语
        self.assertIn("取数失败（error_code：CONTRACT_VERSION_MISMATCH），原因见「0. 数据说明」", md)
        self.assertNotIn("缺少历史基线", md)
        self.assertNotIn("以下展示已取到的评分快照", md)

    def test_retryable_true_false_words(self):
        raw = self._raw({
            "custom_current.json": _failed_envelope("RESULT_BUDGET_EXCEEDED", "budget", False),
            "custom_previous.json": _failed_envelope("RATE_LIMITED", "slow down", True),
        })
        _, signals = self._signals(raw)
        md = render_report.render(signals, {}, None)
        self.assertIn("是否可重试：否", md)
        self.assertIn("是否可重试：是", md)

    def test_legacy_errors_strings_still_visible(self):
        # 旧版 signals（无 error_details）：errors 字符串兜底渲染，error_code 仍全文可见
        signals = {"window": {"days": 7, "current": ["a", "b"], "previous": ["c", "d"]},
                   "source": {"report_id": "x", "degraded": "none"},
                   "counts": {"previous_rows": 0}, "flagged": [], "observed": [],
                   "errors": ["out/raw/custom_current.json status=failed error_code=X_CODE boom"]}
        md = render_report.render(signals, {}, None)
        self.assertIn("error_code=X_CODE", md)
        self.assertIn("取数失败（error_code：X_CODE）", md)


# ------------------------------------------------------ Fix 2：降级三态区分 --

class TestDegradationReason(unittest.TestCase, _TmpRawMixin):
    def _reason(self, files):
        files.setdefault("online_products.json", _ok_envelope([_row("B0X1")]))
        raw = self._raw(files)
        _, signals = self._signals(raw, report_id=None)
        return signals

    def test_unconfigured_keeps_current_banner(self):
        signals = self._reason({})
        src = signals["source"]
        self.assertEqual(src["degraded"], "no_custom_report")
        self.assertEqual(src["degraded_reason"], "not_configured")
        self.assertIn("缺少自定义报表 report_id", src["degraded_banner"])
        self.assertIn("先配置 report_id", signals["manual_checks"][0])

    def test_candidate_verification_rejected(self):
        signals = self._reason({
            "custom_current.json": _failed_envelope("RESULT_BUDGET_EXCEEDED"),
            "custom_previous.json": _failed_envelope("RESULT_BUDGET_EXCEEDED"),
        })
        src = signals["source"]
        self.assertEqual(src["degraded_reason"], "candidate_verification_rejected")
        self.assertEqual(src["degraded_error_codes"], ["RESULT_BUDGET_EXCEEDED"])
        banner = src["degraded_banner"]
        self.assertIn("RESULT_BUDGET_EXCEEDED", banner)
        self.assertIn("不是未配置 report_id", banner)
        self.assertIn("上游读取被拒", banner)
        self.assertNotIn("先配置 report_id", signals["manual_checks"][0])
        self.assertIn("在赛狐网页端核对", signals["manual_checks"][0])

    def test_discovery_failed(self):
        signals = self._reason({"report_index_p1.json": _failed_envelope("PERMISSION_DENIED")})
        src = signals["source"]
        self.assertEqual(src["degraded_reason"], "discovery_failed")
        self.assertEqual(src["degraded_error_codes"], ["PERMISSION_DENIED"])
        self.assertIn("发现失败 ≠ 未配置", src["degraded_banner"])
        self.assertIn("补配置解决不了", signals["manual_checks"][0])

    def test_verification_takes_priority_over_discovery(self):
        # 宽表失败信封在场 → 一定是「发现已完成、验证被拒」（index 失败只是伴生证据）
        signals = self._reason({
            "report_index_p1.json": _failed_envelope("PERMISSION_DENIED"),
            "custom_current.json": _failed_envelope("RESULT_BUDGET_EXCEEDED"),
        })
        self.assertEqual(signals["source"]["degraded_reason"], "candidate_verification_rejected")

    def test_banner_rendered_in_header_even_with_other_warnings(self):
        signals = self._reason({"custom_current.json": _failed_envelope("RESULT_BUD_EXCEEDED")})
        signals["warnings"].insert(0, "输出契约未完成业务口径认证；指标含义以契约说明为准。")
        md = render_report.render(signals, {}, None)
        head = md.split("## 1.")[0]
        self.assertIn("候选验证读取被上游拒绝（error_code：RESULT_BUD_EXCEEDED）", head)
        self.assertIn("不是未配置 report_id", head)


# ------------------------------------------------- Fix 3：observed 空引导语 --

class TestSnapshotIntroOnlyWhenObserved(unittest.TestCase, _TmpRawMixin):
    def test_no_baseline_with_observed_keeps_intro_and_table(self):
        raw = self._raw({"online_products.json": _ok_envelope([_row("B0Y1")])})
        _, signals = self._signals(raw, report_id=None)
        md = render_report.render(signals, {}, None)
        sec1 = md.split("## 1.")[1].split("## 2.")[0]
        self.assertIn("缺少历史基线", sec1)
        self.assertIn("以下展示已取到的评分快照", sec1)
        self.assertIn("| B0Y1", sec1)


# ------------------------------------------------ Fix 4：小样本保护对称性 --

class TestReviewRateDropSmallSample(unittest.TestCase):
    def _flags(self, rating_count):
        rows_cur = [_row("B0S1", ratingCount=rating_count, reviewRate=0.80)]
        rows_prev = [_row("B0S1", ratingCount=rating_count, reviewRate=0.95, rating=4.6)]
        out = fetch_signals.build_signals(
            rows_cur, rows_prev,
            threshold_low=0, threshold_rating=0.2, min_rating_count=20,
            min_low_review_num=2,
            signals_enabled=["low_review_num_rise", "rating_drop", "review_rate_drop"],
        )
        return [f["signal"] for f in out[0]["flags"]] if out else []

    def test_small_sample_not_flagged(self):
        # ratingCount=12 < min_rating_count=20：reviewRate 下滑也不判（与 rating_drop 对称）
        self.assertNotIn("review_rate_drop", self._flags(12))

    def test_large_sample_flagged(self):
        self.assertIn("review_rate_drop", self._flags(200))


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
        flagged = [{"asin": f"B0G{i}", "flags": [{"signal": "rating_drop"}]} for i in range(flagged_count)]
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


# ------------------------------------------------- Fix 8：corrupt ≠ missing --

class TestCorruptVsMissing(unittest.TestCase, _TmpRawMixin):
    def test_missing_reports_missing(self):
        raw = self._raw({"custom_current.json": _ok_envelope([_row("B0M1")])})
        with self.assertRaises(SystemExit) as cm:
            self._signals(raw)
        msg = str(cm.exception)
        self.assertIn("缺少取数计划落盘", msg)
        self.assertIn("custom_previous.json", msg)
        self.assertNotIn("解析失败", msg)

    def test_corrupt_reports_corrupt_not_missing(self):
        raw = self._raw({
            "custom_current.json": _ok_envelope([_row("B0M1")]),
            "custom_previous.json": '{"status": "complete", "data": {"rows": [ TRUNC',
        })
        with self.assertRaises(SystemExit) as cm:
            self._signals(raw)
        msg = str(cm.exception)
        self.assertIn("存在但解析失败（损坏）", msg)
        self.assertIn("custom_previous.json", msg)
        self.assertNotIn("缺少取数计划落盘", msg)

    def test_online_corrupt_vs_missing(self):
        # 缺 online 时 raw 里须另有落盘文件才能越过「落盘目录为空」前置检查
        for files, needle in (
            ({"stray_step.json": _ok_envelope([_row("B0M1")])}, "缺少 out/raw/online_products.json"),
            ({"online_products.json": "{ NOT JSON"}, "存在但解析失败（损坏）"),
        ):
            raw = self._raw(files)
            with self.assertRaises(SystemExit) as cm:
                self._signals(raw, report_id=None)
            self.assertIn(needle, str(cm.exception))


# --------------------------------------------- Fix 9：清单去重 + 前缀修单 --

class TestManualSectionDedup(unittest.TestCase):
    def _signals(self, reason="not_configured"):
        return {
            "window": {"days": 7, "current": ["a", "b"], "previous": ["c", "d"]},
            "source": {"report_id": None, "degraded": "no_custom_report",
                       "degraded_reason": reason},
            "counts": {"previous_rows": 0}, "flagged": [], "observed": [],
            "warnings": ["⚠️ 口径降级：缺少自定义报表 report_id，lowReviewNum/reviewNum/reviewRate 不可用；仅能用 online 侧 rating/ratingCount（且无环比基线，需历史快照）。"],
            "manual_checks": ["lowReviewNum / reviewNum / reviewRate 需自定义报表；当前仅有 online 侧 rating/ratingCount，"
                              "且无上一窗口基线（online 无历史），无法做环比 → 需人工核查或先配置 report_id。"],
        }

    def _section(self, signals):
        md = render_report.render(signals, {}, None)
        return md.split("## 4. 需人工核查清单")[1].split("## 5.")[0]

    def test_no_double_prefix(self):
        self.assertNotIn("⚠️ ⚠️", self._section(self._signals()))

    def test_no_duplicate_low_review_item(self):
        sec = self._section(self._signals())
        self.assertEqual(sec.count("lowReviewNum"), 2)  # manual_checks 长条 + 降级横幅各一次，无第三条静态重复

    def test_upstream_failure_reason_gets_no_configure_hint(self):
        sec = self._section(self._signals(reason="candidate_verification_rejected"))
        self.assertNotIn("需自定义报表 report_id", sec)

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


if __name__ == "__main__":
    unittest.main()
