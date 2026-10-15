#!/usr/bin/env python3
"""利润异常诊断 —— 离线自检（v3：零网络、无需任何 MCP 凭证）。

用合成落盘信封（out/raw/*.json 形状）端到端跑通「agent 落盘 → 校验组装 → 归因 → 渲染」，验证：
  1. 双数据源字段归属正确（msku 报表 vs 自定义报表）。
  2. 归因符号（profit_sign）正确 —— 费用变多/成本变多都应判为「拉住毛利」。
  3. 缺失字段进入「需人工核查」，不编造、不写 0。
  4. 渲染出的报告含利润口径、来源工具列、延期结算区块。
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


def _mk_custom_row(*, purchase, headtrip, reimb, deferred, msku="MSKU-DEMO-1"):
    """一条 get_custom_report_page_list 原始行（成本为正数，实测口径）。"""
    return {
        "msku": [msku],
        "asin": [{"key": "B0DEMO0001"}],
        "shopId": [{"key": "演示店铺001"}],
        "purchaseCost": purchase,
        "headTripPrice": headtrip,
        "reimbursementAmount": reimb,
        "warehouseLostAmount": 8,
        "missingFromInboundAmount": 4.9,
        "deferredAmount": deferred,
    }


def _write(raw_dir: Path, name: str, payload: dict) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_happy_raw(raw_dir: Path) -> None:
    """双源 × 双窗口齐全的落盘（含 grossProfit 差异 + 赔偿空数据）。"""
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
    _write(raw_dir, "custom_current.json", _envelope(
        [_mk_custom_row(purchase=3600, headtrip=400, reimb=0, deferred=500),
         _mk_custom_row(purchase=1000, headtrip=200, reimb=5, deferred=0, msku="MSKU-DEMO-2")],
        list_key="rows",
    ))
    _write(raw_dir, "custom_base.json", _envelope(
        [_mk_custom_row(purchase=3000, headtrip=400, reimb=25, deferred=0),
         _mk_custom_row(purchase=1000, headtrip=200, reimb=5, deferred=0, msku="MSKU-DEMO-2")],
        list_key="rows",
    ))
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


def _assemble(raw_dir: Path, *, report_id="RID-DEMO", shop_ids=()):
    return assemble_all(
        report_id=report_id, shop_ids=list(shop_ids),
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
    checks.append(("演示信封被全部消费（mcp_calls>0）", result.mcp_calls == 7,
                   f"实际 mcp_calls={result.mcp_calls}（期望 7：msku×2 + custom×2 + shop×2 + reimbursement×1）"))
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

    # --- 归因（双源齐全）---
    # 本期毛利 800，基期 2000 → 跌 1200。制造 3 个诱因：销售额跌、采购成本涨、广告涨。
    report = diagnose(msku_rows=result.msku_rows, custom_rows=result.custom_rows, skill_cfg=cfg)
    d = next(m for m in report.mskus if m.msku == "MSKU-DEMO-1")
    by_key = {f.key: f for f in d.factors}
    checks.append(("毛利跌幅 = 1200", d.gross_profit_drop == 1200, f"实际={d.gross_profit_drop}"))

    # 贡献额符号：销售额跌 2000 → 贡献 2000；采购涨 600 → 贡献 600；广告涨 260 → 贡献 260
    checks.append(("销售额下滑贡献 +2000",
                   by_key.get("sales") and round(by_key["sales"].contribution) == 2000,
                   f"实际={by_key.get('sales') and by_key['sales'].contribution}"))
    checks.append(("采购成本上涨贡献 +600（自定义报表，符号须为正贡献）",
                   by_key.get("purchase_cost") and round(by_key["purchase_cost"].contribution) == 600,
                   f"实际={by_key.get('purchase_cost') and by_key['purchase_cost'].contribution}"))
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

    # --- 降级：自定义报表缺失（report_id 未配置的口径降级）---
    degraded = diagnose(msku_rows=result.msku_rows, custom_rows=[], skill_cfg=cfg)
    d0 = next(m for m in degraded.mskus if m.msku == "MSKU-DEMO-1")
    unavailable_keys = {f.key for f in d0.unavailable_factors}
    checks.append((
        "降级：自定义报表字段被标为不可用（purchase_cost/head_trip/deferred/reimbursement）",
        {"purchase_cost", "head_trip", "deferred", "reimbursement"} <= unavailable_keys,
        f"实际不可用={sorted(unavailable_keys)}",
    ))

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
            "degraded": result.degraded,
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
    checks.append(("报告含演示水印", "演示数据，非真实经营数据" in md, ""))
    checks.append(("报告含诚实边界", "本技能不做什么" in md, ""))
    checks.append(("报告披露赔偿「无数据」而非 0 条冒充", "无数据" in md, ""))

    # --- v3 边界：空落盘 / 零信封 / 截断披露 / 自定义报表两窗口缺失 ---
    checks.extend(_check_edges(cfg))

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
    """v3 边界回归：空落盘目录阻断、零信封告警、coverage 截断披露、四类归因缺失披露。"""
    checks: list[tuple[str, bool, str]] = []

    with tempfile.TemporaryDirectory(prefix="profit_edge_") as td:
        # 1) 空落盘目录 → SystemExit（绝不组装出「成功但空」的报告）。
        empty = Path(td) / "empty"
        empty.mkdir()
        try:
            assemble_all(report_id=None, shop_ids=[], skill_cfg=cfg, raw_dir=empty, **WINDOWS)
            blocked = False
        except SystemExit:
            blocked = True
        checks.append(("空落盘目录直接阻断（SystemExit，附行动指引）", blocked, ""))

        # 2) 只有无关文件 → mcp_calls=0 显式告警（区分「没取数」与「无数据」）。
        stray = Path(td) / "stray"
        _write(stray, "notes.json", {"status": "complete", "data": {"note": "与取数计划无关"},
                                     "coverage": {}, "pagination": {}})
        res = assemble_all(report_id="RID-DEMO", shop_ids=[], skill_cfg=cfg, raw_dir=stray, **WINDOWS)
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
        res_t = assemble_all(report_id=None, shop_ids=[], skill_cfg=cfg, raw_dir=trunc, **WINDOWS)
        checks.append(("coverage.truncated 转披露（仅覆盖 Top N，其余未纳入）",
                       any("仅覆盖" in c and "其余未纳入" in c for c in res_t.coverage),
                       f"coverage={res_t.coverage}"))

        # 4) report_id 已配置但 custom 两窗口均未落盘 → 四类归因不可用必须显式说明。
        noc = Path(td) / "no_custom"
        _write(noc, "profit_msku_current.json", _envelope(
            [_mk_msku_row(sales=7000, gross=800, commission=-700, fba=-600, cpc=-560,
                          storage=-40, refund_sales=-100)]))
        _write(noc, "profit_msku_base.json", _envelope(
            [_mk_msku_row(sales=9000, gross=2000, commission=-900, fba=-600, cpc=-300,
                          storage=-40, refund_sales=-100)]))
        res_n = assemble_all(report_id="RID-DEMO", shop_ids=[], skill_cfg=cfg, raw_dir=noc, **WINDOWS)
        checks.append(("custom 两窗口未落盘 → 四类归因不可用显式披露",
                       any("不可用" in w and "custom_current" in w for w in res_n.warnings),
                       f"warnings={[w[:40] for w in res_n.warnings]}"))

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
