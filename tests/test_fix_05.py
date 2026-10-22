"""05-profit-diagnosis 修复回归测试（2026-10-16 评测问题；2026-10-22 去飞书/去自定义报表改写）。

直测九条修复的行为（零网络，只喂合成落盘信封）：
  1. [P1] reimbursement 消费后注册进 sources；「本次记录数/状态」按真实落盘信封
     （有信封=已取数 N 批 M 行 / data 空=无数据 / failed=失败带 error_code / 缺文件=未取数）；
  2. [P1] 归因符号运行时判定：费用类因子按基期行中位值判定实际存储符号，正号存储（线上
     msku 报表实测口径）时修正贡献方向并在报告披露；收入类因子（productSales）不校准；
  3. failed 信封在 coverage 行标「取数失败（error_code：X）」，不再混同「无数据」；
  4. 核心结论按真实状态给文案（失败→「取数失败，原因见数据说明」；无数据→现行文案）；
  5. 延期区块改为固定分工披露（宽表因子由 custom-report-analysis 承接）+ 赔偿条数核对；
  6. 请求 ASIN 0 命中披露：信封 scope.search_contents 提取 + --asin CLI + 渲染层显式披露
     且不下任何归因结论；
  7. 落盘信封 period 与 resolve_windows 口径窗口不一致 → warnings 告警；
  9. 死配置 caliber.compare_both 从 diagnosis.yaml / attribution-rules.md 删除。
2026-10-22：自定义报表步骤已删（宽表因子归因由 custom-report-analysis 承接），
  残留宽表落盘不消费并披露；--report-id / --feishu 入口已移除。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "05-profit-diagnosis"
SCRIPTS = SKILL / "scripts"

# 先注册裸名再 exec：fetch_profit/diagnose/render 之间的平名 import 必须解析到本技能版本
# （与其他技能同名脚本隔离）；加载完恢复原值避免污染。
_PLAIN_NAMES = ("_bootstrap", "fetch_profit", "diagnose", "render", "run_profit_diagnosis")
_SAVED = {n: sys.modules.get(n) for n in _PLAIN_NAMES}
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"fix05_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"fix05_{name}"] = module
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


try:
    _bootstrap = _load("_bootstrap")
    fetch_profit = _load("fetch_profit")
    diagnose_mod = _load("diagnose")
    render_mod = _load("render")
    run_mod = _load("run_profit_diagnosis")
finally:
    for _n, _m in _SAVED.items():
        if _m is None:
            sys.modules.pop(_n, None)
        else:
            sys.modules[_n] = _m


WINDOWS = dict(
    cur_start=date(2026, 9, 16), cur_end=date(2026, 10, 15),
    base_start=date(2026, 8, 17), base_end=date(2026, 9, 15),
)
# 与 WINDOWS 对齐的信封 period（msku/shop 报表用 yyyyMMdd；yyyy-MM-dd 形态亦被归一化识别）
P_CUR = {"start_date": "20260916", "end_date": "20261015", "date_type": 2}
P_BASE = {"start_date": "20260817", "end_date": "20260915", "date_type": 2}


# ---------------------------------------------------------------------------
# 合成信封（形状对齐 out/_eval/gen.py）
# ---------------------------------------------------------------------------
def envelope(rows, *, list_key="reportList", period=None, status="complete", coverage=None,
             scope=None, error_code=None, message=None, tool_id="get_profit_report_by_msku"):
    data = None if status == "failed" else ({list_key: rows} if rows is not None else {})
    obj = {
        "status": status, "warnings": [], "tool_id": tool_id,
        "contract_version": "cv-fix05", "data": data, "scope": scope or {},
        "period": period or {}, "currency": "USD",
        "coverage": coverage or {"complete": True, "fetched_count": len(rows or []),
                                 "total_count": len(rows or []), "truncated": False},
        "pagination": {"has_more": False, "rows": list_key}, "trace_id": "fix05",
    }
    if status == "failed":
        obj.update(error_code=error_code or "UPSTREAM_ERROR", message=message or "")
    return obj


def msku_row(msku="MSKU-A001", asin="B0TESTA001", *, sales, gross, commission, fba, cpc,
             storage, refund, rate=7.15):
    return {
        "mskuList": [msku], "msku": [msku],
        "asinList": [{"key": asin}], "asin": [{"key": asin}],
        "title": [{"key": msku + " 商品标题"}],
        "currency": "USD", "currencyList": ["USD"], "rate": rate,
        "salesNum": int(sales / 30), "productSales": sales,
        "grossProfit": gross, "grossProfitRate": round(gross / sales, 4) if sales else None,
        "commission": commission, "fbaFulfillmentFee": fba, "cpcCost": cpc,
        "storageFee": storage, "refundSales": refund,
        "compensationFee": 0, "purchasePcFee": 0, "headTripPcFee": 0,
    }


def reimb_rows(msku="MSKU-A001", amounts=(25.0, 15.0)):
    return [{"caseId": f"R-F{i}", "msku": msku, "amount": a, "status": "已到账",
             "reason": "WAREHOUSE_LOST"} for i, a in enumerate(amounts, 1)]


def write(raw: Path, name: str, obj: dict) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def msku_pair(raw: Path, *, cur: dict, base: dict, cur_period=None, base_period=None,
              cur_scope=None, base_scope=None, asin="B0TESTA001", msku="MSKU-A001"):
    write(raw, "profit_msku_current.json",
          envelope([msku_row(msku, asin, **cur)], period=cur_period or P_CUR, scope=cur_scope))
    write(raw, "profit_msku_base.json",
          envelope([msku_row(msku, asin, **base)], period=base_period or P_BASE, scope=base_scope))


# 线下合成口径（费用负数）：基期毛利 3000 → 本期 1200，跌 1800
NEG = dict(cur=dict(sales=7000, gross=1200, commission=-700, fba=-1380, cpc=-1250,
                    storage=-200, refund=-260),
           base=dict(sales=9000, gross=3000, commission=-900, fba=-1200, cpc=-900,
                     storage=-200, refund=-180))
# 线上实测口径（msku 费用正数）：同样跌 1800，费用因子方向必须与负号口径一致
POS = dict(cur=dict(sales=7000, gross=1200, commission=950, fba=1380, cpc=1250,
                    storage=200, refund=260),
           base=dict(sales=9000, gross=3000, commission=900, fba=1200, cpc=900,
                     storage=200, refund=180))

CFG = _bootstrap.load_diagnosis()


def assemble(raw: Path):
    return fetch_profit.assemble_all(
        shop_ids=[], skill_cfg=CFG, raw_dir=raw, **WINDOWS)


def render_md(report, result, **meta_over):
    meta = {
        "sources": result.sources, "coverage": result.coverage, "warnings": result.warnings,
        "demo_fingerprint": result.demo_fingerprint,
        "caliber": result.caliber, "report_currency": "USD", "cost_currency": "CNY",
        "top_n": 20, "include_deferred_section": True,
        "reimbursement_count": result.reimbursement_status, "mcp_calls": result.mcp_calls,
    }
    meta.update(meta_over)
    return render_mod.render_report(report, fetch_meta=meta, **WINDOWS)


def diagnose_result(result):
    return diagnose_mod.diagnose(msku_rows=result.msku_rows, skill_cfg=CFG)


class TempRawMixin:
    def raw(self, tag="raw"):
        d = tempfile.TemporaryDirectory(prefix=f"fix05_{tag}_")
        self.addCleanup(d.cleanup)
        return Path(d.name) / "raw"


# ---------------------------------------------------------------------------
# 修复#1：赔偿源注册 + 计数/状态按真实落盘信封
# ---------------------------------------------------------------------------
class ReimbursementDisclosureTest(TempRawMixin, unittest.TestCase):

    def test_three_rows_registered_and_counted(self):
        raw = self.raw()
        msku_pair(raw, **NEG)
        write(raw, "reimbursement.json",
              envelope(reimb_rows(amounts=(25.0, 15.0, 5.0)), list_key="rows",
                       period={"start_time": "2026-08-17 00:00:00", "end_time": "2026-10-15 23:59:59"},
                       tool_id="get_amazon_reimbursement_report"))
        result = assemble(raw)
        self.assertIn("get_amazon_reimbursement_report", result.sources)
        self.assertEqual(len(result.reimbursement_rows), 3)
        self.assertEqual(result.reimbursement_status, "已取数 1 批 / 3 行")

    def test_no_data_status(self):
        raw = self.raw()
        msku_pair(raw, **NEG)
        write(raw, "reimbursement.json",
              envelope(None, list_key="rows", tool_id="get_amazon_reimbursement_report"))
        result = assemble(raw)
        self.assertIn("get_amazon_reimbursement_report", result.sources)
        self.assertEqual(result.reimbursement_rows, [])
        self.assertEqual(result.reimbursement_status, "无数据")

    def test_failed_status_with_error_code(self):
        raw = self.raw()
        msku_pair(raw, **NEG)
        write(raw, "reimbursement.json", {
            "status": "failed", "warnings": [], "tool_id": "get_amazon_reimbursement_report",
            "error_code": "UPSTREAM_CONTRACT_ERROR", "message": "null payload", "data": None,
            "coverage": {}, "pagination": {}, "trace_id": "fix05-reimb-f",
        })
        result = assemble(raw)
        # 已发起调用 → sources 仍注册（失败≠没取）；状态带 error_code；coverage 行区分「取数失败」
        self.assertIn("get_amazon_reimbursement_report", result.sources)
        self.assertEqual(result.reimbursement_status, "取数失败（error_code：UPSTREAM_CONTRACT_ERROR）")
        self.assertTrue(any("reimbursement: 取数失败（error_code：UPSTREAM_CONTRACT_ERROR）" in c
                            for c in result.coverage), result.coverage)
        self.assertFalse(any(c.startswith("reimbursement: 无数据") for c in result.coverage))
        self.assertIn("reimbursement（error_code=UPSTREAM_CONTRACT_ERROR）", result.failed_steps)

    def test_missing_file_stays_unfetched(self):
        raw = self.raw()
        msku_pair(raw, **NEG)
        result = assemble(raw)
        self.assertEqual(result.reimbursement_status, "未取数")
        self.assertNotIn("get_amazon_reimbursement_report", result.sources)

    def test_rendered_footnote_shows_real_status(self):
        raw = self.raw()
        msku_pair(raw, **NEG)
        write(raw, "reimbursement.json",
              envelope(reimb_rows(), list_key="rows", tool_id="get_amazon_reimbursement_report"))
        result = assemble(raw)
        md = render_md(diagnose_result(result), result)
        self.assertIn("（本次记录数 / 状态：已取数 1 批 / 2 行）", md)
        self.assertNotIn("（本次记录数 / 状态：未取数）", md)
        self.assertIn("get_amazon_reimbursement_report", md)  # 数据源清单不再漏该工具


# ---------------------------------------------------------------------------
# 修复#2：归因符号运行时判定（正号费用存储 → 修正贡献方向 + 披露）
# ---------------------------------------------------------------------------
class RuntimeSignCalibrationTest(TempRawMixin, unittest.TestCase):

    def _factors(self, pair):
        raw = self.raw()
        msku_pair(raw, **pair)
        result = assemble(raw)
        report = diagnose_result(result)
        diag = next(m for m in report.mskus if m.msku == "MSKU-A001")
        return result, report, {f.key: f for f in diag.factors}

    def test_positive_fee_contribution_corrected(self):
        """线上口径（费用正数）：广告 900→1250 须判为拉住毛利 +350（不校准则整体反号 -350）。"""
        _, _, fk = self._factors(POS)
        self.assertTrue(fk["ad_cost"].sign_calibrated)
        self.assertEqual(round(fk["ad_cost"].contribution), 350)
        self.assertEqual(round(fk["fba_fulfillment"].contribution), 180)
        self.assertEqual(round(fk["commission"].contribution), 50)
        # 负号口径（线下合成）与正号口径（线上实测）贡献方向一致
        _, _, fk_neg = self._factors(NEG)
        self.assertEqual(round(fk_neg["ad_cost"].contribution), 350)
        self.assertFalse(fk_neg["ad_cost"].sign_calibrated)
        self.assertEqual(round(fk_neg["fba_fulfillment"].contribution), 180)

    def test_revenue_like_factors_never_calibrated(self):
        """收入类因子（productSales）恒正存储，不做符号校准；宽表收入类因子已删除。"""
        raw = self.raw()
        msku_pair(raw, **POS)
        result = assemble(raw)
        report = diagnose_result(result)
        diag = next(m for m in report.mskus if m.msku == "MSKU-A001")
        fk = {f.key: f for f in diag.factors}
        self.assertEqual(round(fk["sales"].contribution), 2000)
        self.assertFalse(fk["sales"].sign_calibrated)
        cal = diagnose_mod.storage_sign_calibration(
            result.msku_rows, CFG["attribution"]["factors"])
        self.assertNotIn("product_sales", cal)
        self.assertIn("cpc_cost", cal)  # 费用类因子仍被校准
        # 宽表收入类因子（reimbursementAmount）已不在因子表 —— 不参与校准也不参与归因
        self.assertNotIn("reimbursement_amount", diagnose_mod.REVENUE_LIKE_FIELDS)
        self.assertNotIn("reimbursement", {f.get("key") for f in CFG["attribution"]["factors"]})

    def test_calibration_skips_zero_median(self):
        """基期中位值恰为 0（无法判定存储符号）→ 保持配置缺省，不校准。"""
        raw = self.raw()
        msku_pair(raw, cur=POS["cur"],
                  base=dict(sales=9000, gross=3000, commission=900, fba=1200,
                            cpc=0, storage=200, refund=180))
        result = assemble(raw)
        cal = diagnose_mod.storage_sign_calibration(
            result.msku_rows, CFG["attribution"]["factors"])
        self.assertNotIn("cpc_cost", cal)

    def test_disclosure_rendered_under_attribution_table(self):
        raw = self.raw()
        msku_pair(raw, **POS)
        result = assemble(raw)
        md = render_md(diagnose_result(result), result)
        self.assertIn("符号校准披露", md)
        self.assertIn("实测为正号存储", md)
        self.assertIn("已按正号口径归因", md)
        self.assertIn("cpcCost", md)


# ---------------------------------------------------------------------------
# 修复#3：failed ≠ 无数据（coverage 行）
# ---------------------------------------------------------------------------
class FailedVsNoDataTest(TempRawMixin, unittest.TestCase):

    def test_failed_msku_coverage_line_distinguished(self):
        raw = self.raw()
        write(raw, "profit_msku_current.json", envelope(
            None, status="failed", error_code="CONTRACT_VERSION_MISMATCH",
            message="Contract version mismatch", period=P_CUR))
        write(raw, "profit_msku_base.json", envelope([msku_row(**NEG["base"])], period=P_BASE))
        result = assemble(raw)
        self.assertTrue(any("MSKU 利润报表本期: 取数失败（error_code：CONTRACT_VERSION_MISMATCH）" in c
                            for c in result.coverage), result.coverage)
        self.assertFalse(any(c.startswith("MSKU 利润报表本期: 无数据") for c in result.coverage))
        self.assertIn("MSKU 利润报表本期（error_code=CONTRACT_VERSION_MISMATCH）", result.failed_steps)


# ---------------------------------------------------------------------------
# 修复#4：核心结论按真实状态给文案
# ---------------------------------------------------------------------------
class CoreConclusionFailureTextTest(TempRawMixin, unittest.TestCase):

    def _render_empty(self, **meta_over):
        result = fetch_profit.FetchResult()
        meta = {"mcp_calls": 7, "sources": [], "coverage": [], "warnings": [],
                "reimbursement_count": "未取数"}
        meta.update(meta_over)
        return render_mod.render_report(diagnose_mod.DiagnosisReport(), fetch_meta=meta, **WINDOWS)

    def test_failed_wording(self):
        md = self._render_empty(msku_failed=["MSKU 利润报表本期（error_code=CONTRACT_VERSION_MISMATCH）"])
        self.assertIn("取数失败，原因见数据说明", md)
        self.assertNotIn("可能为「无数据」，而非失败", md)

    def test_no_data_wording_preserved(self):
        md = self._render_empty()
        self.assertIn("可能为「无数据」，而非失败", md)
        self.assertNotIn("取数失败，原因见数据说明", md)


# ---------------------------------------------------------------------------
# 修复#5：延期区块改为固定分工披露（宽表因子由 custom-report-analysis 承接）
# ---------------------------------------------------------------------------
class DeferredSectionHandoffTest(TempRawMixin, unittest.TestCase):

    def test_fixed_handoff_disclosed(self):
        raw = self.raw()
        msku_pair(raw, **NEG)
        result = assemble(raw)
        md = render_md(diagnose_result(result), result)
        self.assertIn("延期结算与赔偿", md)
        self.assertIn("custom-report-analysis", md)
        self.assertIn("本报告不取值、不编造", md)
        # 固定分工披露在「利润口径」区也出现（不是条件性口径降级）
        self.assertIn("口径分工（固定）", md)
        self.assertNotIn("口径降级", md)

    def test_reimbursement_count_still_reported(self):
        raw = self.raw()
        msku_pair(raw, **NEG)
        write(raw, "reimbursement.json",
              envelope(reimb_rows(), list_key="rows", tool_id="get_amazon_reimbursement_report"))
        result = assemble(raw)
        md = render_md(diagnose_result(result), result)
        self.assertIn("get_amazon_reimbursement_report", md)
        self.assertIn("（本次记录数 / 状态：已取数 1 批 / 2 行）", md)

    def test_stale_custom_raw_not_consumed_but_disclosed(self):
        """残留宽表落盘不消费，coverage 披露承接说明（mcp_calls 只计 msku 两份）。"""
        raw = self.raw()
        msku_pair(raw, **NEG)
        write(raw, "custom_current.json",
              envelope([{"msku": ["MSKU-A001"], "purchaseCost": 9999}], list_key="rows",
                       tool_id="get_custom_report_page_list"))
        result = assemble(raw)
        self.assertEqual(result.mcp_calls, 2)
        self.assertTrue(any("custom-report-analysis" in c and "未消费" in c
                            for c in result.coverage), result.coverage)


# ---------------------------------------------------------------------------
# 修复#6：ASIN 0 命中披露（scope 提取 / 渲染披露 / CLI 入口）
# ---------------------------------------------------------------------------
class AsinMissDisclosureTest(TempRawMixin, unittest.TestCase):

    def test_scope_search_contents_extracted(self):
        raw = self.raw()
        msku_pair(raw, **NEG,
                  cur_scope={"search_type": "asin", "search_contents": ["B0MOCKA001"]},
                  base_scope={"search_type": "asin", "search_contents": ["B0MOCKA001"]})
        result = assemble(raw)
        self.assertEqual(result.requested_asins, ["B0MOCKA001"])

    def test_non_asin_search_ignored(self):
        raw = self.raw()
        msku_pair(raw, **NEG, cur_scope={"search_type": "msku", "search_contents": ["MSKU-X"]})
        result = assemble(raw)
        self.assertEqual(result.requested_asins, [])

    def test_render_discloses_and_withholds_attribution(self):
        raw = self.raw()
        msku_pair(raw, **NEG, asin="B0DEMO1001", msku="MSKU-DEMO-1001")
        result = assemble(raw)
        md = render_md(diagnose_result(result), result, requested_asins=["B0MOCKA001"])
        self.assertIn("请求的 ASIN `B0MOCKA001` 未命中任何 MSKU 行", md)
        self.assertIn("上游 search_type/search_contents 过滤可能未生效", md)
        self.assertIn("不下任何归因结论", md)
        self.assertIn("跳过归因结论", md)
        self.assertNotIn("贡献额", md)  # 归因表整节被门控

    def test_render_hit_keeps_attribution(self):
        raw = self.raw()
        msku_pair(raw, **NEG, asin="B0DEMO1001", msku="MSKU-DEMO-1001")
        result = assemble(raw)
        md = render_md(diagnose_result(result), result, requested_asins=["B0DEMO1001"])
        self.assertNotIn("未命中任何 MSKU 行", md)
        self.assertIn("贡献额", md)

    def test_cli_end_to_end(self):
        """run_profit_diagnosis --asin 0 命中：报告显式披露且不下归因结论；赔偿计数如实。"""
        raw = self.raw()
        msku_pair(raw, **NEG, asin="B0DEMO1001", msku="MSKU-DEMO-1001")
        write(raw, "reimbursement.json",
              envelope(reimb_rows(amounts=(25.0, 15.0)), list_key="rows",
                       tool_id="get_amazon_reimbursement_report"))
        with tempfile.TemporaryDirectory(prefix="fix05_cli_") as td:
            out = Path(td) / "report.md"
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "run_profit_diagnosis.py"),
                 "--end", "2026-10-15",
                 "--raw-dir", str(raw), "--asin", "B0MOCKA001", "--out", str(out),
                 "--keep-md"],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            # 默认仅 .html；--keep-md 时两者（HTML 即报告，md 为调试/测试载荷）
            self.assertTrue(out.with_suffix(".html").is_file())
            md = out.read_text(encoding="utf-8")
            self.assertIn("请求的 ASIN `B0MOCKA001` 未命中任何 MSKU 行", md)
            self.assertIn("不下任何归因结论", md)
            self.assertNotIn("贡献额", md)
            self.assertIn("（本次记录数 / 状态：已取数 1 批 / 2 行）", md)

    def test_cli_no_feishu_or_report_id_flags(self):
        """去飞书/去自定义报表后：CLI 不再有 --feishu / --report-id 入口。"""
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "run_profit_diagnosis.py"), "--help"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("--feishu", proc.stdout)
        self.assertNotIn("--report-id", proc.stdout)


# ---------------------------------------------------------------------------
# 修复#7：落盘信封 period 与口径窗口一致性校验
# ---------------------------------------------------------------------------
class WindowConsistencyTest(TempRawMixin, unittest.TestCase):

    def test_off_by_one_day_warned(self):
        raw = self.raw()
        msku_pair(raw, **NEG, base_period={"start_date": "20260818", "end_date": "20260915",
                                           "date_type": 2})
        result = assemble(raw)
        hit = [w for w in result.warnings if "落盘信封请求窗口" in w]
        self.assertEqual(len(hit), 1, result.warnings)
        self.assertIn("2026-08-18~2026-09-15", hit[0])
        self.assertIn("2026-08-17~2026-09-15", hit[0])  # 口径窗口并列呈现，可人工核对

    def test_iso_period_mismatch_warned_and_match_silent(self):
        """yyyy-MM-dd 形态的 period 失配要告警；口径一致时不告警。"""
        raw = self.raw()
        msku_pair(raw, **NEG,
                  cur_period={"start_date": "2026-09-16 00:00:00", "end_date": "2026-10-14 23:59:59"},
                  base_period=P_BASE)
        result = assemble(raw)
        hits = [w for w in result.warnings if "落盘信封请求窗口" in w]
        self.assertEqual(len(hits), 1, result.warnings)
        self.assertIn("2026-09-16~2026-10-14", hits[0])
        # base 窗口（yyyyMMdd 形态）与口径一致 → 不告警
        self.assertFalse(any("2026-08-17~2026-09-15" in w and "落盘信封请求窗口" in w
                             for w in result.warnings))

    def test_matching_windows_silent(self):
        raw = self.raw()
        msku_pair(raw, **NEG)
        write(raw, "reimbursement.json",
              envelope(reimb_rows(), list_key="rows", tool_id="get_amazon_reimbursement_report"))
        result = assemble(raw)
        self.assertFalse([w for w in result.warnings if "落盘信封请求窗口" in w], result.warnings)


# ---------------------------------------------------------------------------
# 修复#9：死配置 compare_both 清理
# ---------------------------------------------------------------------------
class DeadConfigCleanupTest(unittest.TestCase):

    def test_compare_both_removed_from_config(self):
        text = (SKILL / "config" / "diagnosis.yaml").read_text(encoding="utf-8")
        self.assertNotIn("compare_both", text)
        self.assertEqual(CFG["caliber"]["default"], "settlement")

    def test_compare_both_removed_from_docs(self):
        rules = (SKILL / "references" / "attribution-rules.md").read_text(encoding="utf-8")
        self.assertNotIn("compare_both", rules)
        self.assertIn("--caliber", rules)  # 两口径对比改为显式跑两次的说法


if __name__ == "__main__":
    unittest.main()
