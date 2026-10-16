"""08-listing-launch 修复回归测试（2026-10-16 评测问题）。

直测九条修复的行为（零网络，只喂合成落盘信封/合成商品行）：
  1. [范围防御] analysis 行与目标 ASIN 本地关联：白名单保留 asinList（线上列表形），
     无交集的行不进基线，剔除数量按「analysis 行 N 条中 M 条…已剔除」披露；
  2. [失败可见性] 两步信封级 warnings 汇总去重进 notes（渲染层落「数据说明与警示」）；
  3. [悬空引用] 上架包不再出现「见附录」（包内本无附录）；
  4. [中文违禁词] compliance.yaml 词表中英双语，「全网最低价，永久保修」命中高风险表述；
  5. [模板修复] A+ 提示词不再出现 {marketplace} 字面量，站点实插值；
  6. [判据解耦] total_cost 只看成本项（采购/头程/佣金/FBA/结算费）实际缺值，
     不被字符限制等无关 manual 项牵连——费用齐全时利润/倒推售价可计算；
  7. [措辞] 汇率提示区分「须为正数 / 须填写来源 / 须填写日期」三种；
  8. [头程] 优先消费商品行 headTripCost（来源标注「商品行」），缺值才人工核查；
  9. [文档口径] workflow.md 取数参数补 anyOf 必填、降级措辞与实际渲染对齐；
     四处文档（SKILL.md / workflow.md / estimate_pricing.py / launch.yaml）头程口径同步。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "08-listing-launch"
SCRIPTS = SKILL / "scripts"

# 先注册裸名再 exec：技能脚本之间的平名 import（from _common import ...）必须解析到
# 本技能版本；加载完恢复原值避免污染（与其他技能同名脚本隔离）。
_PLAIN_NAMES = ("_common", "fetch_baseline", "build_asset_brief", "check_compliance",
                "estimate_pricing", "render_package")
_SAVED = {n: sys.modules.get(n) for n in _PLAIN_NAMES}
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"fix08_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"fix08_{name}"] = module
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


try:
    _common = _load("_common")
    fetch_baseline = _load("fetch_baseline")
    build_asset_brief = _load("build_asset_brief")
    check_compliance = _load("check_compliance")
    estimate_pricing = _load("estimate_pricing")
    render_package = _load("render_package")
finally:
    for _n, _m in _SAVED.items():
        if _m is None:
            sys.modules.pop(_n, None)
        else:
            sys.modules[_n] = _m

Notes = _common.Notes


# ---------------------------------------------------------------------------
# 合成信封（形状对齐 out/eval-make-envelopes.py；避开演示指纹字符串）
# ---------------------------------------------------------------------------

def envelope(tool_id, rows, *, status="complete", warnings=None):
    return {
        "status": status, "warnings": warnings or [], "tool_id": tool_id,
        "contract_version": "cv-fixed-0001", "data": {"rows": rows},
        "scope": {}, "period": {},
        "coverage": {"complete": status == "complete", "fetched_count": len(rows),
                     "total_count": len(rows), "truncated": False},
        "pagination": {}, "trace_id": "trace-fixed-0001",
        "output_validation": {"field_errors": []},
    }


def online_row(asin="B0FIX0001", **overrides):
    row = {"asin": asin, "title": "Fixed Test Product", "currency": "USD",
           "listingPrice": 29.99, "purchaseCost": 85, "referralFee": 4.5,
           "fbaFees": 6.2, "variableClosingFee": 0.39, "headTripCost": None,
           "inventoryManage": {"currency": "CNY"}}
    row.update(overrides)
    return row


def analyze_row(asin=None, asin_list=None):
    row = {"spu": "SPU-FIX-001", "title": "Analyze Row",
           "categoryNameList": ["Home & Kitchen"], "openDate": "2026-10-12",
           "currency": "USD", "bsrRank": [{"rank": 15234}]}
    if asin is not None:
        row["asin"] = asin
    if asin_list is not None:
        row["asinList"] = asin_list
    return row


def write_raw(tmp, online=None, analyze=None, *, online_warnings=None, analyze_warnings=None):
    raw = Path(tmp) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    if online is not None:
        (raw / "online_product.json").write_text(
            json.dumps(envelope("get_online_product_page_list", online,
                                warnings=online_warnings), ensure_ascii=False),
            encoding="utf-8")
    if analyze is not None:
        (raw / "product_analyze.json").write_text(
            json.dumps(envelope("get_product_analyze_list", analyze,
                                warnings=analyze_warnings), ensure_ascii=False),
            encoding="utf-8")
    return raw


class TestAnalysisAsinAssociation(unittest.TestCase):
    """修复 1：analysis 行按 asinList 与目标 ASIN 求交，无交集剔除并披露。"""

    def test_unrelated_rows_dropped_and_disclosed(self):
        rows = [
            analyze_row(asin_list=["B0FIX0001"]),                      # 线上列表形，命中
            analyze_row(asin_list=["B0OTHER009"]),                     # 线上列表形，无关
            analyze_row(asin="B0FIX0001"),                             # 离线标量形，命中
            analyze_row(asin_list=[]),                                 # 无 ASIN 可核验 → 剔除
        ]
        with tempfile.TemporaryDirectory() as tmp:
            raw = write_raw(tmp, online=[online_row()], analyze=rows)
            notes = Notes()
            base = fetch_baseline.build_baseline(
                raw_dir=raw, notes=notes, target_asins=["B0FIX0001"])
        self.assertEqual(len(base["analysis"]), 2)
        self.assertTrue(any(
            "analysis 行 4 条中 2 条与目标 ASIN 无关联（上游过滤疑似未生效），已剔除"
            in w for w in notes.warnings))
        # 白名单保留 asinList（线上实测列表形键不再被裁掉，对应关系可核验）。
        self.assertIn("asinList", base["analysis"][0])
        self.assertEqual(base["analysis"][0]["asinList"], ["B0FIX0001"])

    def test_no_target_keeps_all_rows(self):
        rows = [analyze_row(asin_list=["B0OTHER009"]), analyze_row(asin="B0FIX0001")]
        with tempfile.TemporaryDirectory() as tmp:
            raw = write_raw(tmp, online=[online_row()], analyze=rows)
            notes = Notes()
            base = fetch_baseline.build_baseline(raw_dir=raw, notes=notes)
        self.assertEqual(len(base["analysis"]), 2)
        self.assertFalse(any("已剔除" in w for w in notes.warnings))

    def test_live_shape_row_without_scalar_asin_matches_via_list(self):
        # 线上真实行只有 asinList（标量 asin 被上游裁掉），仍须可关联。
        rows = [analyze_row(asin_list=["B0OTHER001"]),
                analyze_row(asin_list=["B0FIX0001", "B0FIX0002"])]
        with tempfile.TemporaryDirectory() as tmp:
            raw = write_raw(tmp, online=[online_row()], analyze=rows)
            base = fetch_baseline.build_baseline(
                raw_dir=raw, notes=Notes(), target_asins=["B0FIX0002"])
        self.assertEqual(len(base["analysis"]), 1)
        self.assertEqual(base["analysis"][0]["asinList"], ["B0FIX0001", "B0FIX0002"])


class TestEnvelopeWarningsMerged(unittest.TestCase):
    """修复 2：两步信封级 warnings 汇总去重，进 notes（渲染落数据说明）。"""

    def test_merged_deduped_with_step_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = write_raw(
                tmp, online=[online_row()], analyze=[analyze_row(asin="B0FIX0001")],
                online_warnings=["口径未认证提示", "字段与未验证文档不一致，原值保留"],
                analyze_warnings=["口径未认证提示"])
            notes = Notes()
            fetch_baseline.build_baseline(raw_dir=raw, notes=notes)
        joined = "\n".join(notes.warnings)
        self.assertIn("取数信封 warnings（online_product、product_analyze）：口径未认证提示", joined)
        self.assertIn("取数信封 warnings（online_product）：字段与未验证文档不一致，原值保留", joined)
        # 去重：同一条 warning 只出现一次。
        self.assertEqual(joined.count("口径未认证提示"), 1)

    def test_rendered_into_data_notes_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = write_raw(
                tmp, online=[online_row()], analyze=[analyze_row(asin="B0FIX0001")],
                online_warnings=["字段表现与未验证文档不一致，原值保留。分析前查看 output_validation.field_errors。"],
                analyze_warnings=["输出契约未完成业务口径认证；指标含义以契约说明为准。"])
            notes = Notes()
            base = fetch_baseline.build_baseline(raw_dir=raw, notes=notes)
        md = render_package.render_package(
            product_name="测试商品", marketplace="US",
            baseline=base, notes=notes, date_str="2026-10-16")
        self.assertIn("### 数据说明与警示", md)
        self.assertIn("取数信封 warnings（online_product）："
                      "字段表现与未验证文档不一致，原值保留。分析前查看 output_validation.field_errors。", md)
        self.assertIn("取数信封 warnings（product_analyze）："
                      "输出契约未完成业务口径认证；指标含义以契约说明为准。", md)


class TestNoDanglingAppendixRef(unittest.TestCase):
    """修复 3：区块 1 不再「见附录」（包内无附录，引用悬空）。"""

    def test_block1_has_no_appendix_reference(self):
        md = render_package.render_package(
            product_name="测试商品", marketplace="US",
            baseline={"analysis": [analyze_row(asin="B0FIX0001")], "demo_fingerprint": None},
            notes=Notes(), date_str="2026-10-16")
        self.assertIn("赛狐商品分析记录：1 条（含类目/BSR/上架时间）", md)
        self.assertNotIn("见附录", md)
        self.assertNotIn("附录", md)


class TestChineseBannedWords(unittest.TestCase):
    """修复 4：中文违禁词进词表，「全网最低价，永久保修」命中高风险表述。"""

    def test_user_phrase_hits_banned_words(self):
        copy = {"title": "折叠凳 全网最低价，永久保修",
                "bullets": ["Sturdy and collapsible"],
                "description": "全网最低价，永久保修，绝对值得买"}
        result = check_compliance.check_compliance(copy=copy, notes=Notes())
        words = {(h["field"], h["word"]) for h in result["banned_word_hits"]}
        self.assertIn(("title", "全网最低价"), words)
        self.assertIn(("title", "永久保修"), words)
        self.assertIn(("description", "绝对"), words)
        self.assertTrue(all(h["risk"] == "禁用/高风险表述"
                            for h in result["banned_word_hits"]))
        self.assertTrue(result["advisory_only"])

    def test_english_words_still_hit(self):
        result = check_compliance.check_compliance(
            copy={"title": "100% safe guaranteed cure"}, notes=Notes())
        words = {h["word"] for h in result["banned_word_hits"]}
        self.assertIn("guaranteed", words)
        self.assertIn("100% safe", words)

    def test_yaml_keeps_both_languages(self):
        cfg = _common.load_skill_yaml("compliance")
        words = [str(w) for w in (cfg.get("banned_words") or [])]
        for zh in ("全网最低价", "最低价", "永久保修", "终身免费", "最佳", "第一",
                   "顶级", "绝对", "100%", "国家级", "秒杀全网", "史上最"):
            self.assertIn(zh, words)
        for en in ("best seller", "guaranteed", "free shipping"):
            self.assertIn(en, words)


class TestAplusPromptInterpolation(unittest.TestCase):
    """修复 5：A+ 提示词 {marketplace} 字面量 → 实插值。"""

    def test_aplus_prompt_uses_real_marketplace(self):
        brief = build_asset_brief.build_brief(
            product_name="测试商品", marketplace="UK", notes=Notes())
        aplus = [a for a in brief["assets"] if a["id"].startswith("aplus-")]
        self.assertTrue(aplus)
        for a in aplus:
            self.assertNotIn("{marketplace}", a["prompt"])
            self.assertIn("适配 UK 站点审美", a["prompt"])


def _full_cost_product(**overrides):
    """同币种、成本项齐全的商品行（避开 fx 配置，直测判据解耦）。

    键名对齐 ``fetch_baseline.summarize_product`` 的输出（含 inventory_currency）。
    """
    product = {
        "price_currency": "USD", "inventory_currency": "USD",
        "listing_price": 30.0, "purchase_cost": 10.0,
        "referral_fee": 4.5, "fba_fees": 6.2, "variable_closing_fee": 0.39,
        "headtrip_cost": 2.0,
    }
    product.update(overrides)
    return product


class TestTotalCostCriterion(unittest.TestCase):
    """修复 6：total_cost 判据只看成本项实际缺值，不看无关 manual 项。"""

    def test_profit_computed_despite_unrelated_manual_checks(self):
        notes = Notes()
        # 无关 manual 项（字符限制未配置）——旧判据会因此把利润恒置 None。
        notes.need_manual("title 的字符限制（title_max_chars）未配置，需人工核查")
        result = estimate_pricing.estimate_pricing(_full_cost_product(), notes=notes)
        self.assertEqual(result["total_cost"], 23.09)      # 10+4.5+6.2+0.39+2.0
        self.assertEqual(result["estimated_profit"], 6.91)  # 30-23.09
        self.assertEqual(result["estimated_margin_pct"], 23.03)
        self.assertTrue(result["cost_complete"])
        # launch.yaml 默认 target_gross_margin_pct=25 → 倒推建议售价可计算。
        self.assertEqual(result["suggested_price"], 30.79)  # 23.09/0.75
        # 头程来源标注（修复 8）。
        self.assertEqual(result["headtrip_source"], "商品行 headTripCost")
        self.assertEqual(result["cost_breakdown"]["headtrip"], 2.0)
        self.assertEqual(notes.sources.get("headtrip"), "商品行 headTripCost")
        self.assertFalse(any("头程成本" in m for m in result["manual_checks"]))

    def test_missing_headtrip_blocks_total(self):
        result = estimate_pricing.estimate_pricing(
            _full_cost_product(headtrip_cost=None), notes=Notes())
        self.assertIsNone(result["total_cost"])
        self.assertIsNone(result["estimated_profit"])
        self.assertFalse(result["cost_complete"])
        self.assertTrue(any(m.startswith("头程成本：") for m in result["manual_checks"]))
        self.assertIn("headtrip", result["cost_breakdown"])
        self.assertIsNone(result["cost_breakdown"]["headtrip"])

    def test_missing_purchase_cost_blocks_total(self):
        result = estimate_pricing.estimate_pricing(
            _full_cost_product(purchase_cost=None), notes=Notes())
        self.assertIsNone(result["total_cost"])
        self.assertFalse(result["cost_complete"])
        # 倒推售价的人工提示点名实际缺的成本项。
        self.assertTrue(any("缺 purchase_cost" in m for m in result["manual_checks"]))


class TestFxMessageSplit(unittest.TestCase):
    """修复 7：rate 非正 / 缺来源 / 缺日期三种提示分开。"""

    FX_OK = {"base": "USD", "quote": "CNY", "rate": 7.1,
             "source": "中国银行中间价", "as_of": "2026-10-15"}

    def _run(self, fx):
        real = estimate_pricing.load_skill_yaml
        estimate_pricing.load_skill_yaml = (
            lambda name: dict(fx) if name == "fx" else real(name))
        try:
            product = {"price_currency": "USD", "inventory_currency": "CNY",
                       "listing_price": 150.0, "purchase_cost": 710.0,
                       "referral_fee": 15.0, "fba_fees": 20.0,
                       "variable_closing_fee": 0.0, "headtrip_cost": 71.0}
            notes = Notes()
            return estimate_pricing.estimate_pricing(product, notes=notes), notes
        finally:
            estimate_pricing.load_skill_yaml = real

    def test_valid_fx_converts_headtrip_from_product_row(self):
        result, notes = self._run(self.FX_OK)
        # 710 CNY / 7.1 = 100 USD；头程 71 CNY → 10 USD（商品行字段换算入账）。
        self.assertAlmostEqual(result["purchase_cost"], 100.0, places=6)
        self.assertEqual(result["headtrip_source"], "商品行 headTripCost")
        self.assertAlmostEqual(result["cost_breakdown"]["headtrip"], 10.0, places=6)
        self.assertAlmostEqual(result["total_cost"], 145.0, places=6)
        self.assertEqual(notes.sources.get("fx:采购成本"), "中国银行中间价 @ 2026-10-15")

    def test_nonpositive_rate_message(self):
        _, notes = self._run(dict(self.FX_OK, rate=0))
        self.assertTrue(any("汇率须为正数" in m for m in notes.manual_checks))
        self.assertFalse(any("须填写来源" in m for m in notes.manual_checks))

    def test_missing_source_message(self):
        _, notes = self._run(dict(self.FX_OK, source=None))
        self.assertTrue(any(m.endswith("汇率须填写来源") for m in notes.manual_checks))
        self.assertFalse(any("须为正数" in m for m in notes.manual_checks))

    def test_missing_asof_message(self):
        _, notes = self._run(dict(self.FX_OK, as_of=None))
        self.assertTrue(any(m.endswith("汇率须填写日期") for m in notes.manual_checks))
        self.assertFalse(any("须为正数" in m for m in notes.manual_checks))


class TestHeadtripRenderAndSummarize(unittest.TestCase):
    """修复 8（渲染/组装面）：头程从商品行取值并在区块 6 标注来源。"""

    def test_summarize_product_maps_headtrip(self):
        p = fetch_baseline.summarize_product(online_row(headTripCost=5.6))
        self.assertEqual(p["headtrip_cost"], 5.6)
        p2 = fetch_baseline.summarize_product(online_row(headTripCost=None))
        self.assertIsNone(p2["headtrip_cost"])

    def test_pricing_block_shows_headtrip_source(self):
        pricing = estimate_pricing.estimate_pricing(_full_cost_product(), notes=Notes())
        md = render_package.render_package(
            product_name="测试商品", marketplace="US", pricing=pricing,
            notes=Notes(), date_str="2026-10-16")
        self.assertIn("- 头程：2.0（来源：商品行 headTripCost）", md)

    def test_honest_boundary_mentions_product_row_first(self):
        md = render_package.render_package(
            product_name="测试商品", marketplace="US", notes=Notes(), date_str="2026-10-16")
        self.assertIn("优先取商品行 `headTripCost`", md)


class TestDocsAligned(unittest.TestCase):
    """修复 8/9 文档口径：四处头程声明 + anyOf 必填 + 降级措辞对齐。"""

    def test_headtrip_docs_prefer_product_row(self):
        for rel in ("SKILL.md", "references/workflow.md",
                    "scripts/estimate_pricing.py", "config/launch.yaml"):
            text = (SKILL / rel).read_text(encoding="utf-8")
            self.assertIn("headTripCost", text, rel)
            self.assertNotIn("仅存在于 get_custom_report_page_list", text, rel)
            # 旧口径「头程仅宽表可得」的声明不得复现（佣金/FBA 的「取不到」与头程无关）。
            self.assertFalse(any("头程" in ln and "取不到" in ln
                                 for ln in text.splitlines()), rel)

    def test_workflow_fetch_plan_has_anyof_requirement(self):
        text = (SKILL / "references" / "workflow.md").read_text(encoding="utf-8")
        row = [ln for ln in text.splitlines()
               if ln.startswith("| 2 |") and "product_analyze.json" in ln][0]
        self.assertIn("fields", row)
        self.assertIn("field_modules", row)
        self.assertIn("至少一项非空", row)
        self.assertIn("anyOf", row)

    def test_workflow_degradation_wording_matches_render(self):
        text = (SKILL / "references" / "workflow.md").read_text(encoding="utf-8")
        self.assertNotIn("商品分析缺失（已降级）", text)
        row = [ln for ln in text.splitlines() if "product_analyze` 缺失/失败" in ln][0]
        self.assertIn("竞品/关键词情报需外部 MCP", row)
        self.assertIn("error_code", row)

    def test_template_no_stale_headtrip_claim(self):
        text = (SKILL / "references" / "listing-package-template.md").read_text(encoding="utf-8")
        self.assertNotIn("仅存在于 get_custom_report_page_list", text)


if __name__ == "__main__":
    unittest.main()
