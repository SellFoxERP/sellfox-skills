#!/usr/bin/env python3
"""利润异常诊断 —— 离线自检（v3：零网络、无需任何 MCP 凭证）。

用合成落盘信封（out/raw/*.json 形状）端到端跑通「agent 落盘 → 校验组装 → 归因 → 渲染」，验证：
  1. 归因字段归属正确（全部因子来自 msku 报表；宽表因子已由 custom-report-analysis 承接）。
  2. 归因符号（profit_sign）正确 —— 费用变多应判为「拉住毛利」。
  3. 缺失字段进入「需人工核查」，不编造、不写 0。
  4. 渲染出的报告含利润口径、来源工具列、延期结算区块（宽表分工固定披露）。
  5. v3 落盘纪律：failed 信封不可采信、空 data 标「无数据」、coverage 截断必披露、
     零落盘信封（mcp_calls=0）显式告警、空落盘目录直接阻断。
  6. 环比窗口夹取后必须合法且等长，并返回披露告警。

运行：python scripts/selfcheck.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import load_diagnosis  # noqa: E402

from diagnose import diagnose  # noqa: E402
from fetch_profit import assemble_all, resolve_windows  # noqa: E402
from render import render_report  # noqa: E402


# ---------------------------------------------------------------------------
# 合成信封构造（与 agent 落盘的 out/raw/*.json 形状一致）
# ---------------------------------------------------------------------------
def _envelope(rows, *, list_key="reportList", status="complete", coverage=None, trace="demo-trace"):
    """构造一份 action 完整信封（status/data/coverage/pagination/trace_id）。"""
    return {
        "status": status,
        "data": {list_key: rows} if rows is not None else {},
        "coverage": coverage
        if coverage is not None
        else {"complete": True, "fetched_count": len(rows or []), "total_count": len(rows or []), "truncated": False},
        "pagination": {"has_more": False, "rows": list_key},
        "trace_id": trace,
    }


def _mk_msku_row(*, sales, gross, commission, fba, cpc, storage, refund_sales, msku="MSKU-DEMO-1"):
    """一条 get_profit_report_by_msku 原始行（费用为负数，实测口径）。"""
    return {
        "msku": [msku],
        "asin": [{"key": "B0DEMO0001"}],
        "title": [{"key": "演示商品"}],
        "currency": "USD",
        "rate": 7.15,
        "productSales": sales,
        "grossProfit": gross,
        "grossProfitRate": (gross / sales) if sales else None,
        "commission": commission,
        "fbaFulfillmentFee": fba,
        "cpcCost": cpc,
        "storageFee": storage,
        "refundSales": refund_sales,
    }


def _write(raw_dir: Path, name: str, payload: dict) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_happy_raw(raw_dir: Path) -> None:
    """主源 × 双窗口齐全的落盘（含 grossProfit 差异 + 赔偿空数据）。"""
    _write(raw_dir, "profit_msku_current.json", _envelope([
        _mk_msku_row(sales=7000, gross=800, commission=-700, fba=-600, cpc=-560, storage=-40, refund_sales=-100),
        _mk_msku_row(sales=5000, gross=500, commission=-500, fba=-300, cpc=-200, storage=-30, refund_sales=-50,
                     msku="MSKU-DEMO-2"),
    ]))
    _write(raw_dir, "profit_msku_base.json", _envelope([
        _mk_msku_row(sales=9000, gross=2000, commission=-900, fba=-600, cpc=-300, storage=-40, refund_sales=-100),
        _mk_msku_row(sales=5000, gross=500, commission=-500, fba=-300, cpc=-200, storage=-30, refund_sales=-50,
                     msku="MSKU-DEMO-2"),
    ]))
    _write(raw_dir, "profit_shop_current.json", _envelope(
        [{"shopName": "演示店铺001", "grossProfit": 1300, "productSales": 12000}], trace="demo-trace-shop",
    ))
    # 背景源基期：failed 信封 → 不可采信，只披露不产出该窗口行。
    _write(raw_dir, "profit_shop_base.json", {
        "status": "failed", "error_code": "UPSTREAM_ERROR",
        "message": "演示注入：店铺级基期取数失败", "data": None,
        "coverage": {}, "pagination": {}, "trace_id": "demo-trace-shop-base",
    })
    # 赔偿报告：可能 data:{} → 「无数据」，不是失败，不重试。
    _write(raw_dir, "reimbursement.json", _envelope(None, list_key="rows"))


WINDOWS = dict(
    cur_start=date(2026, 9, 1), cur_end=date(2026, 9, 30),
    base_start=date(2026, 8, 2), base_end=date(2026, 8, 31),
)


def _assemble(raw_dir: Path, *, shop_ids=()):
    return assemble_all(
        shop_ids=list(shop_ids),
        skill_cfg=load_diagnosis(), raw_dir=raw_dir, **WINDOWS,
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    cfg = load_diagnosis()

    with tempfile.TemporaryDirectory(prefix="profit_selfcheck_") as td:
        raw_dir = Path(td) / "raw"
        _build_happy_raw(raw_dir)
        result = _assemble(raw_dir)

    checks: list[tuple[str, bool, str]] = []

    # --- v3 落盘校验层 ---
    checks.append(("演示信封被全部消费（mcp_calls>0）", result.mcp_calls == 5,
                   f"实际 mcp_calls={result.mcp_calls}（期望 5：msku×2 + shop×2 + reimbursement×1）"))
    checks.append(("空 shop_ids 披露「全部店铺」覆盖语",
                   any("全部店铺" in c for c in result.coverage), f"coverage={result.coverage[:2]}"))
    checks.append(("双窗口 MSKU 行带 _window 标记（current/baseline 各 2 行）",
                   len([r for r in result.msku_rows if r["_window"] == "current"]) == 2
                   and len([r for r in result.msku_rows if r["_window"] == "baseline"]) == 2,
                   f"msku_rows={len(result.msku_rows)}"))
    checks.append(("reimbursement data:{} 标「无数据」（不写 0、不报错）",
                   any("reimbursement: 无数据" in c for c in result.coverage)
                   and result.reimbursement_rows == [],
                   f"coverage={[c for c in result.coverage if 'reimbursement' in c]}"))
    checks.append(("failed 信封披露取数失败且不产出该窗口行",
                   any("取数失败" in w for w in result.warnings)
                   and all(r["_window"] == "current" for r in result.shop_rows),
                   f"shop_rows={[r.get('_window') for r in result.shop_rows]}"))
    checks.append(("演示指纹从落盘信封中识别", result.demo_fingerprint is not None,
                   f"指纹={result.demo_fingerprint}"))
    checks.append(("数据源去重（get_profit_report_by_msku 只记一次）",
                   result.sources.count("get_profit_report_by_msku") == 1, f"sources={result.sources}"))
    checks.append(("赔偿 data:{} → 注册进数据源清单且状态「无数据」（修复#1）",
                   "get_amazon_reimbursement_report" in result.sources
                   and result.reimbursement_status == "无数据",
                   f"状态={result.reimbursement_status}"))
    # 宽表固定分工：custom 落盘存在也不再消费，coverage 披露承接说明
    checks.append(("残留宽表落盘不消费并披露 custom-report-analysis 承接", True,
                   "（宽表步骤已删：custom_current/custom_base 不在取数计划；渲染层固定披露）"))

    # --- 归因（主源齐全）---
    # 本期毛利 800，基期 2000 → 跌 1200。制造 2 个诱因：销售额跌、广告涨。
    report = diagnose(msku_rows=result.msku_rows, skill_cfg=cfg)
    d = next(m for m in report.mskus if m.msku == "MSKU-DEMO-1")
    by_key = {f.key: f for f in d.factors}
    checks.append(("毛利跌幅 = 1200", d.gross_profit_drop == 1200, f"实际={d.gross_profit_drop}"))

    # 贡献额符号：销售额跌 2000 → 贡献 2000；广告涨 260 → 贡献 260
    checks.append(("销售额下滑贡献 +2000",
                   by_key.get("sales") and round(by_key["sales"].contribution) == 2000,
                   f"实际={by_key.get('sales') and by_key['sales'].contribution}"))
    checks.append(("广告花费上涨贡献 +260（msku 费用为负，符号须为正贡献）",
                   by_key.get("ad_cost") and round(by_key["ad_cost"].contribution) == 260,
                   f"实际={by_key.get('ad_cost') and by_key['ad_cost'].contribution}"))
    checks.append(("佣金随销售额下降不判为诱因",
                   by_key.get("commission") is None or by_key["commission"].contribution <= 0,
                   f"实际={by_key.get('commission') and by_key['commission'].contribution}"))
    checks.append(("无跌幅的 MSKU（MSKU-DEMO-2）不进异常清单",
                   next(m for m in report.mskus if m.msku == "MSKU-DEMO-2").gross_profit_drop == 0,
                   ""))
    checks.append((f"归因条数 ≤ {cfg['attribution']['max_items']}",
                   len(d.driver_factors) <= int(cfg["attribution"]["max_items"]),
                   f"实际={len(d.driver_factors)}"))
    checks.append(("每条归因都带来源工具",
                   all(f.tool for f in d.driver_factors),
                   f"={[f.tool for f in d.driver_factors]}"))
    checks.append(("归因因子不再含自定义报表工具",
                   all(f.tool == "get_profit_report_by_msku" for f in d.factors + d.unavailable_factors),
                   f"tools={sorted({f.tool for f in d.factors + d.unavailable_factors})}"))

    # --- 宽表因子固定态：配置已删，报告固定披露承接 ---
    factor_keys = {f.get("key") for f in cfg["attribution"]["factors"]}
    checks.append(("配置因子表不含宽表因子（purchase_cost/head_trip/deferred/reimbursement）",
                   not ({"purchase_cost", "head_trip", "deferred", "reimbursement"} & factor_keys),
                   f"实际 keys={sorted(factor_keys)}"))

    # --- 渲染 ---
    cur_start, cur_end, base_start, base_end, _win_warn = resolve_windows(
        date(2026, 9, 30), 30, max_lookback_days=180
    )
    md = render_report(
        report,
        fetch_meta={
            "sources": result.sources,
            "coverage": result.coverage,
            "warnings": result.warnings,
            "demo_fingerprint": result.demo_fingerprint,
            "caliber": result.caliber,
            "report_currency": "USD",
            "cost_currency": "CNY",
            "top_n": 20,
            "include_deferred_section": True,
            "reimbursement_count": len(result.reimbursement_rows),
            "mcp_calls": result.mcp_calls,
        },
        cur_start=cur_start, cur_end=cur_end, base_start=base_start, base_end=base_end,
    )
    checks.append(("报告标注利润口径 settlement", "settlement（结算口径" in md, ""))
    checks.append(("报告含来源工具列", "来源工具" in md, ""))
    checks.append(("报告含延期结算区块", "延期结算与赔偿" in md, ""))
    checks.append(("报告固定披露宽表归因由 custom-report-analysis 承接",
                   "custom-report-analysis" in md and "口径分工（固定）" in md, ""))
    checks.append(("报告含演示水印", "演示数据，非真实经营数据" in md, ""))
    checks.append(("报告含诚实边界", "本技能不做什么" in md, ""))
    checks.append(("报告披露赔偿「无数据」而非 0 条冒充", "无数据" in md, ""))

    # --- v3 边界：空落盘 / 零信封 / 截断披露 ---
    checks.extend(_check_edges(cfg))

    # --- 评测修复（2026-10-16）：赔偿三态计数 / 正号费用符号校准 ---
    checks.extend(_check_fixes(cfg))

    # --- 环比窗口夹取：合法且等长 + 披露 ---
    checks.extend(_check_windows())

    width = max(len(name) for name, _, _ in checks)
    failed = 0
    for name, ok, detail in checks:
        mark = "✅" if ok else "❌"
        if not ok:
            failed += 1
        print(f"{mark} {name:<{width}}  {detail}")

    print()
    print("=" * 74)
    print(md[:1200])
    print("..." if len(md) > 1200 else "")
    print("=" * 74)
    print(f"\n{'✅ 全部通过' if failed == 0 else f'❌ {failed} 项失败'}")
    return 1 if failed else 0


def _check_edges(cfg) -> list[tuple[str, bool, str]]:
    """v3 边界回归：空落盘目录阻断、零信封告警、coverage 截断披露。"""
    checks: list[tuple[str, bool, str]] = []

    with tempfile.TemporaryDirectory(prefix="profit_edge_") as td:
        # 1) 空落盘目录 → SystemExit（绝不组装出「成功但空」的报告）。
        empty = Path(td) / "empty"
        empty.mkdir()
        try:
            assemble_all(shop_ids=[], skill_cfg=cfg, raw_dir=empty, **WINDOWS)
            blocked = False
        except SystemExit:
            blocked = True
        checks.append(("空落盘目录直接阻断（SystemExit，附行动指引）", blocked, ""))

        # 2) 只有无关文件 → mcp_calls=0 显式告警（区分「没取数」与「无数据」）。
        stray = Path(td) / "stray"
        _write(stray, "notes.json", {"status": "complete", "data": {"note": "与取数计划无关"},
                                     "coverage": {}, "pagination": {}})
        res = assemble_all(shop_ids=[], skill_cfg=cfg, raw_dir=stray, **WINDOWS)
        checks.append(("零落盘信封（mcp_calls=0）触发显式告警",
                       res.mcp_calls == 0 and any("mcp_calls=0" in w for w in res.warnings),
                       f"mcp_calls={res.mcp_calls}"))

        # 3) coverage.truncated → 必须转成披露语句（不信 has_more）。
        trunc = Path(td) / "trunc"
        _write(trunc, "profit_msku_current.json", _envelope(
            [_mk_msku_row(sales=7000, gross=800, commission=-700, fba=-600, cpc=-560,
                          storage=-40, refund_sales=-100)],
            coverage={"complete": False, "fetched_count": 20, "total_count": 99, "truncated": True},
        ))
        res_t = assemble_all(shop_ids=[], skill_cfg=cfg, raw_dir=trunc, **WINDOWS)
        checks.append(("coverage.truncated 转披露（仅覆盖 Top N，其余未纳入）",
                       any("仅覆盖" in c and "其余未纳入" in c for c in res_t.coverage),
                       f"coverage={res_t.coverage}"))

        # 4) 残留宽表落盘（custom_current/custom_base）→ 不消费并披露承接说明。
        stale = Path(td) / "stale_custom"
        _write(stale, "profit_msku_current.json", _envelope(
            [_mk_msku_row(sales=7000, gross=800, commission=-700, fba=-600, cpc=-560,
                          storage=-40, refund_sales=-100)]))
        _write(stale, "profit_msku_base.json", _envelope(
            [_mk_msku_row(sales=9000, gross=2000, commission=-900, fba=-600, cpc=-300,
                          storage=-40, refund_sales=-100)]))
        _write(stale, "custom_current.json", _envelope(
            [{"msku": ["MSKU-DEMO-1"], "purchaseCost": 3600}], list_key="rows"))
        res_s = assemble_all(shop_ids=[], skill_cfg=cfg, raw_dir=stale, **WINDOWS)
        checks.append(("残留宽表落盘不消费 + coverage 披露 custom-report-analysis 承接",
                       res_s.mcp_calls == 2
                       and any("custom-report-analysis" in c and "未消费" in c for c in res_s.coverage),
                       f"mcp_calls={res_s.mcp_calls} coverage={[c[:40] for c in res_s.coverage]}"))

    return checks


def _check_fixes(cfg) -> list[tuple[str, bool, str]]:
    """评测修复（2026-10-16）回归：

    * 修复#1 —— 赔偿源注册进 sources + 计数/状态按真实落盘信封（已取数 N 批 M 行 /
      无数据 / 取数失败带 error_code），不再恒「未取数」；
    * 修复#2 —— msku 报表费用为**正数**存储（线上实测口径）时，费用因子贡献方向按
      实测符号修正并在报告披露（收入类因子不校准）。
    """
    checks: list[tuple[str, bool, str]] = []

    def _msku_pair(raw_dir: Path, *, cur: dict, base: dict) -> None:
        _write(raw_dir, "profit_msku_current.json", _envelope([_mk_msku_row(**cur)]))
        _write(raw_dir, "profit_msku_base.json", _envelope([_mk_msku_row(**base)]))

    with tempfile.TemporaryDirectory(prefix="profit_fix_") as td:
        # 1) 赔偿信封含 3 行 → 「已取数 1 批 / 3 行」且数据源清单注册该工具。
        raw3 = Path(td) / "reimb3"
        _msku_pair(raw3,
                   cur=dict(sales=7000, gross=800, commission=-700, fba=-600, cpc=-560,
                            storage=-40, refund_sales=-100),
                   base=dict(sales=9000, gross=2000, commission=-900, fba=-600, cpc=-300,
                             storage=-40, refund_sales=-100))
        _write(raw3, "reimbursement.json", _envelope(
            [{"caseId": f"R-FIX{i}", "msku": "MSKU-DEMO-1", "amount": 10.0 * i} for i in (1, 2, 3)],
            list_key="rows"))
        res3 = assemble_all(shop_ids=[], skill_cfg=cfg, raw_dir=raw3, **WINDOWS)
        checks.append(("赔偿 3 行 → 已取数 1 批 / 3 行 且注册进数据源（修复#1）",
                       res3.reimbursement_status == "已取数 1 批 / 3 行"
                       and len(res3.reimbursement_rows) == 3
                       and "get_amazon_reimbursement_report" in res3.sources,
                       f"状态={res3.reimbursement_status}"))

        # 2) 赔偿信封 failed → 状态/coverage 行区分「取数失败（error_code）」与「无数据」。
        rawf = Path(td) / "reimb_failed"
        _msku_pair(rawf,
                   cur=dict(sales=7000, gross=800, commission=-700, fba=-600, cpc=-560,
                            storage=-40, refund_sales=-100),
                   base=dict(sales=9000, gross=2000, commission=-900, fba=-600, cpc=-300,
                             storage=-40, refund_sales=-100))
        _write(rawf, "reimbursement.json", {
            "status": "failed", "error_code": "UPSTREAM_TIMEOUT",
            "message": "演示注入：赔偿取数失败", "data": None,
            "coverage": {}, "pagination": {}, "trace_id": "fix-reimb-failed",
        })
        resf = assemble_all(shop_ids=[], skill_cfg=cfg, raw_dir=rawf, **WINDOWS)
        checks.append(("赔偿 failed → 状态/coverage 带 error_code 的「取数失败」（修复#1/#3）",
                       resf.reimbursement_status == "取数失败（error_code：UPSTREAM_TIMEOUT）"
                       and any("reimbursement: 取数失败（error_code：UPSTREAM_TIMEOUT）" in c
                               for c in resf.coverage)
                       and not any(c.startswith("reimbursement: 无数据") for c in resf.coverage)
                       and "get_amazon_reimbursement_report" in resf.sources,
                       f"状态={resf.reimbursement_status}"))

        # 3) 正号费用（线上 msku 报表实测口径）：广告 900→1250 贡献须修正为 +350（不校准则 -350），
        #    销售额（收入类）不校准仍 +2000；并在报告披露「实测为正号存储」。
        rawp = Path(td) / "positive_fee"
        _msku_pair(rawp,
                   cur=dict(sales=7000, gross=1200, commission=950, fba=1380, cpc=1250,
                            storage=200, refund_sales=260),
                   base=dict(sales=9000, gross=3000, commission=900, fba=1200, cpc=900,
                             storage=200, refund_sales=180))
        resp = assemble_all(shop_ids=[], skill_cfg=cfg, raw_dir=rawp, **WINDOWS)
        rep = diagnose(msku_rows=resp.msku_rows, skill_cfg=cfg)
        dp = next(m for m in rep.mskus if m.msku == "MSKU-DEMO-1")
        pk = {f.key: f for f in dp.factors}
        checks.append(("正号费用：广告涨 900→1250 贡献修正为 +350 且 sign_calibrated（修复#2）",
                       pk["ad_cost"].sign_calibrated and round(pk["ad_cost"].contribution) == 350
                       and round(pk["sales"].contribution) == 2000
                       and not pk["sales"].sign_calibrated,
                       f"ad={pk['ad_cost'].contribution} sales={pk['sales'].contribution}"))
        md_p = render_report(
            rep,
            fetch_meta={
                "sources": resp.sources, "coverage": resp.coverage, "warnings": resp.warnings,
                "demo_fingerprint": resp.demo_fingerprint,
                "caliber": resp.caliber, "report_currency": "USD", "cost_currency": "CNY",
                "top_n": 20, "include_deferred_section": True,
                "reimbursement_count": resp.reimbursement_status, "mcp_calls": resp.mcp_calls,
            },
            **WINDOWS,
        )
        checks.append(("正号费用：报告披露「实测为正号存储」符号校准（修复#2）",
                       "符号校准披露" in md_p and "实测为正号存储" in md_p
                       and "已按正号口径归因" in md_p,
                       ""))

    return checks


def _check_windows() -> list[tuple[str, bool, str]]:
    """环比窗口回归：夹取后必须合法且等长，并返回披露告警（不静默夹取）。"""
    checks: list[tuple[str, bool, str]] = []
    for p in (30, 90, 120, 180, 200):
        cs, ce, bs, be, warns = resolve_windows(date(2026, 9, 30), p, max_lookback_days=180)
        ok = (bs <= be) and ((ce - cs).days == (be - bs).days)
        checks.append((
            f"resolve_windows(period_days={p}) 基期合法且等长",
            ok,
            f"本期 {cs}~{ce}（{(ce - cs).days}天） 基期 {bs}~{be}（{(be - bs).days}天） warns={len(warns)}",
        ))
    # 超限时必须有告警披露
    _, _, _, _, warn_over = resolve_windows(date(2026, 9, 30), 120, max_lookback_days=180)
    checks.append(("period_days 超限时返回截短告警（不静默夹取）", len(warn_over) == 1, f"warnings={warn_over}"))
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
