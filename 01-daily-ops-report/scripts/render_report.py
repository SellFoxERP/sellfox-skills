#!/usr/bin/env python3
"""每日经营日报 —— 渲染层（render_report.py）。

输入：analysis.json（+ 可选 bundle.json 以取订单样本与店铺名）
输出：Markdown 日报（本地文件），可选写入飞书。

要点：
  * 严格使用 references/report-template.md 的结构：L1 结论 / L2 七步 / L3 附录 / 数据说明。
  * 若命中演示环境指纹 → 顶部加水印，且**默认阻断写飞书**（可 --override-demo 显式覆盖）。
  * 写飞书前对订单 PII（buyerEmail/buyerName）脱敏并标注「已脱敏」。
  * 截断披露、口径降级、无数据、需人工核查项一律显式输出（禁止静默省略）。

用法::

    python render_report.py --analysis out/analysis.json --bundle out/bundle.json --out out/daily.md
    python render_report.py --analysis out/analysis.json --feishu
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_THIS = Path(__file__).resolve()
_SKILL_ROOT = _THIS.parent.parent
sys.path.insert(0, str(_THIS.parent))
from sharedpath import shared_root as _resolve_shared_root  # noqa: E402

_SHARED_ROOT = _resolve_shared_root(_SKILL_ROOT)
if not _SHARED_ROOT.exists():
    raise SystemExit(f"_shared 路径不可用：{_SHARED_ROOT}。请设置 config/paths.yaml 的 shared_root。")
sys.path.insert(0, str(_SHARED_ROOT / "scripts"))
from config_loader import load_yaml_file  # noqa: E402

_SKILL_CFG = load_yaml_file(_SKILL_ROOT / "config" / "daily_report.yaml")


def _fmt(v: Any, digits: int = 2, unit: str = "") -> str:
    if v is None:
        return "无数据"
    if isinstance(v, str):
        return v
    try:
        return f"{float(v):,.{digits}f}{unit}"
    except (TypeError, ValueError):
        return str(v)


def render_markdown(analysis: Dict[str, Any], bundle: Optional[Dict[str, Any]] = None) -> str:
    bundle = bundle or {}
    rd = analysis.get("report_date") or {}
    iso = rd.get("iso", "?")
    l1 = analysis.get("l1") or {}
    cur = l1.get("currency") or (analysis.get("config") or {}).get("currency", "USD")
    L: List[str] = []

    if analysis.get("demo_fingerprint"):
        L.append("> ⚠️ **演示数据，非真实经营数据**（指纹：%s）。数值不可用于业务判断。" % analysis["demo_fingerprint"])
        L.append("")

    L.append(f"# 📊 每日经营日报 | {iso}")
    L.append("")

    # ---------- L1 结论层 ----------
    L.append("## 【L1 结论层】管理层看这里")
    L.append("")
    L.append("### 🔴 今日红线（≤%d 条，含归因线索）" % len(analysis.get("redlines") or []))
    L.append("")
    reds = analysis.get("redlines") or []
    if not reds:
        L.append("- 无触发红线的异常项（按当前阈值）。")
    for a in reds:
        dev = a.get("deviation_pct")
        dev_s = f"，偏离 {dev:+.1f}%" if isinstance(dev, (int, float)) else ""
        L.append(
            f"- **{a.get('name')}**：{_fmt(a.get('current'), 2, a.get('unit',''))}"
            f"（基线 {_fmt(a.get('baseline'), 2, a.get('unit',''))}{dev_s}）"
        )
        if a.get("clue"):
            L.append(f"  - 归因线索：{a['clue']}")
    L.append("")
    L.append("### 核心指标")
    L.append("")
    L.append("| 指标 | 昨日 | 币种/口径 |")
    L.append("|---|---|---|")
    L.append(f"| GMV（销售额） | {_fmt(l1.get('gmv'))} | {cur} |")
    L.append(f"| 销量（件） | {_fmt(l1.get('units'), 0)} | — |")
    L.append(f"| 毛利 | {_fmt(l1.get('gross_profit'))} | {cur} |")
    L.append(f"| 毛利率 | {_fmt(l1.get('gross_profit_rate'), 2, '%')} | 利润口径 {analysis.get('config', {}).get('profit_caliber_type','settlement')} |")
    if l1.get("by_currency"):
        for currency, values in l1["by_currency"].items():
            L.append(f"| 销售额 / 毛利（{currency}） | {_fmt(values.get('gmv'))} / {_fmt(values.get('gross_profit'))} | {currency} |")
    L.append(f"| ACOS | {_fmt(l1.get('acos'), 2, '%')} | 报表口径（adCost/adSalePrice） |")
    L.append(f"| CVR | {_fmt(l1.get('cvr'), 2, '%')} | orderNum/sessions |")
    L.append("")
    L.append(f"> ⚠️ 一句话结论：（自动生成占位，需运营复核）核心指标见上；红线 {len(reds)} 条，详见下方七步明细。")
    L.append("")

    # ---------- L2 明细层 ----------
    L.append("## 【L2 明细层】运营看这里")
    L.append("")
    steps = analysis.get("steps") or {}

    def _step_header(no: str, title: str, p0: bool = False) -> None:
        tag = " 🔴 P0" if p0 else ""
        L.append(f"### STEP {no} {title}{tag}")
        L.append("")

    _step_header("01", "账户 / 订单（覆盖最低，多项需人工）", p0=True)
    s1 = steps.get("01_account") or {}
    L.append(f"- 退款数：{_fmt(s1.get('refund_num'), 0)} ｜ 退款金额：{_fmt(s1.get('refund_amount'))} ｜ 退货数：{_fmt(s1.get('return_num'), 0)}")
    L.append(f"- 订单下钻样本：{_fmt(s1.get('anomaly_orders_sampled'), 0)} 条（抽样，非全量）")
    for m in s1.get("manual") or []:
        L.append(f"- {m}")
    L.append("")

    _step_header("02", "销售 / 流量（P0）", p0=True)
    s2 = steps.get("02_sales") or {}
    L.append(f"- GMV：{_fmt(s2.get('gmv'))} ｜ 销量：{_fmt(s2.get('units'), 0)} ｜ 基线方法：{s2.get('baseline_method')}")
    if not s2.get("store_baseline_available"):
        L.append(f"- ⚠️ {s2.get('note')}")
    msku_an = s2.get("msku_anomalies") or []
    if msku_an:
        L.append("- 异常 MSKU（销量环比）：")
        for a in msku_an[:10]:
            L.append(f"  - {a.get('msku')}（店铺 {a.get('shopId')}）：{_fmt(a.get('current'),0)} vs 近7日均 {_fmt(a.get('baseline'),2)}，偏离 {a.get('deviation_pct'):+.1f}%")
    else:
        L.append("- 无 MSKU 销量异常（或宽表无数据）。")
    L.append("")

    _step_header("03", "广告")
    s3 = steps.get("03_ads") or {}
    L.append(f"- 广告花费：{_fmt(s3.get('ad_cost'))} ｜ 广告销售额：{_fmt(s3.get('ad_sales'))} ｜ ACOS：{_fmt(s3.get('acos'), 2, '%')}")
    cs = s3.get("campaign_states")
    if isinstance(cs, dict):
        L.append(f"- 活动状态：enabled {cs.get('enabled',0)} ｜ paused {cs.get('paused',0)} ｜ archived {cs.get('archived',0)} ｜ unknown {cs.get('unknown',0)}")
    else:
        L.append(f"- 活动状态：{cs}")
    for z in s3.get("high_cost_zero_order") or []:
        L.append(f"- ⚠️ 高花费零转化：{z.get('msku')}（花费 {_fmt(z.get('adCost'))}）")
    L.append(f"- 说明：{s3.get('note')}")
    L.append("")

    _step_header("04", "库存")
    s4 = steps.get("04_inventory") or {}
    warns = s4.get("warnings")
    if isinstance(warns, str):
        L.append(f"- {warns}")
    else:
        L.append(f"- 断货预警 {s4.get('warn_count',0)} 项（紧急 {s4.get('critical_count',0)} 项）")
        for w in (warns or [])[:20]:
            ib = w.get("inbound") or {}
            L.append(
                f"  - [{w.get('level')}] {w.get('msku')}（店铺 {w.get('shopId')}）：可售 {_fmt(w.get('availableDays'),0,'天')}，"
                f"在途 working {_fmt(ib.get('inboundWorking'),0)} / shipped {_fmt(ib.get('inboundShipped'),0)} / transit {_fmt(ib.get('inTransit'),0)}"
            )
    L.append("")

    _step_header("05", "转化")
    s5 = steps.get("05_conversion") or {}
    L.append(f"- Sessions：{_fmt(s5.get('sessions'), 0)} ｜ 订单：{_fmt(s5.get('orders'), 0)} ｜ CVR：{_fmt(s5.get('cvr'), 2, '%')}")
    for b in s5.get("buybox_below") or []:
        L.append(f"  - ⚠️ 购物车占比偏低：{b.get('msku')} → {_fmt(b.get('buyBoxPercent'), 2, '%')}")
    L.append(f"- 说明：{s5.get('note')}")
    L.append("")

    _step_header("06", "售后")
    s6 = steps.get("06_after_sales") or {}
    L.append(f"- 退款率（均值）：{_fmt(s6.get('refund_rate'), 2, '%')} ｜ 退款数：{_fmt(s6.get('refund_num'),0)} ｜ 退货数：{_fmt(s6.get('return_num'),0)}")
    L.append(f"- 新增差评数：{_fmt(s6.get('low_review_num'), 0)}")
    for m in s6.get("manual") or []:
        L.append(f"- {m}")
    L.append("")

    _step_header("07", "政策（无法自动化）")
    s7 = steps.get("07_policy") or {}
    L.append(f"- 状态：{s7.get('status')}")
    for m in s7.get("manual") or []:
        L.append(f"- {m}")
    L.append("")

    # ---------- L3 附录 ----------
    L.append("## 【L3 附录】")
    L.append("")
    L.append("### 订单下钻样本（已脱敏）")
    L.append("")
    orders = ((bundle.get("data") or {}).get("orders_sample") or {}).get("rows") or []
    if not orders:
        L.append("- 无数据")
    else:
        try:
            from feishu_writer import mask_pii_in_text

            _mask = mask_pii_in_text
        except Exception:  # 兜底：无 _shared 时用简单掩码
            def _mask(t: str) -> str:  # type: ignore
                return t
        shown = 0
        for o in orders:
            if shown >= 10:
                break
            flag = []
            if str(o.get("isReturnOrder")) in ("1", "true", "True"):
                flag.append("退货")
            if o.get("refundStatus"):
                flag.append("退款")
            if str(o.get("isBuyerRequestedCancel")) in ("1", "true", "True"):
                flag.append("买家请求取消")
            if not flag:
                continue
            line = (f"- {o.get('amazonOrderId')}（{o.get('shopName') or o.get('shopId')}）"
                    f" 标记：{'/'.join(flag)} 金额：{_fmt(o.get('orderTotalAmount'))} {o.get('orderTotalCurrency','')}"
                    f" buyerEmail: {o.get('buyerEmail','')} buyerName: {o.get('buyerName','')}")
            L.append(_mask(line))
            shown += 1
        if shown == 0:
            L.append("- 样本中未发现退款/退货/取消标记的订单（注意：本项为抽样，非全量）")
        L.append("")
        L.append("> 已脱敏：buyerEmail / buyerName 已掩码。")
    L.append("")

    # ---------- 数据说明（必填）----------
    L.append("---")
    L.append("")
    L.append("### 数据说明（口径 / 覆盖范围）")
    L.append("")
    cfg = analysis.get("config") or {}
    L.append(f"- 利润口径：{cfg.get('profit_caliber_type','settlement')}（结算/发货口径二者结果不同）")
    L.append(f"- 币种：聚合币种 {cur}（以返回行币种核验；请求参数不代表折算已成功）")
    L.append(f"- 业务日边界：{cfg.get('date_mode')}（时区参考 {cfg.get('timezone','—')}）；数据截止 {iso} 23:59")
    L.append("- 数据源：赛狐 ERP（get_profit_report_by_shop / get_custom_report_page_list / get_order_page_list / get_sp_campaign_list）")
    L.append("")

    deg = analysis.get("degradations") or []
    if deg:
        L.append("### ⚠️ 口径降级")
        L.append("")
        for d in deg:
            L.append(f"- {d}")
        L.append("")

    trunc = analysis.get("truncations") or []
    if trunc:
        L.append("### ⚠️ 取数覆盖范围（截断披露，非静默）")
        L.append("")
        for t in trunc:
            L.append(f"- {t.get('message')}（来源：{t.get('source')} {t.get('scope','')}）")
        L.append("")

    nd = analysis.get("no_data_sources") or []
    if nd:
        L.append("### 无数据")
        L.append("")
        for s in nd:
            L.append(f"- {s}：无数据（0 与「没拿到」是两回事，此处不写 0）")
        L.append("")

    L.append("### ⚠️ 本日报未能覆盖（赛狐 MCP 无对应数据源）")
    L.append("")
    for m in analysis.get("human_check") or []:
        L.append(f"- {m} — {HUMAN_TEXT}")
    L.append("")

    L.append("### 参数来源")
    L.append("")
    L.append("| 参数 | 取值 | 来源 |")
    L.append("|---|---|---|")
    L.append(f"| 预警阈值 | thresholds.yaml | 配置命中 |")
    L.append(f"| 利润口径 | {cfg.get('profit_caliber_type','settlement')} | 配置命中 |")
    rid = ((bundle.get("data") or {}).get("msku_report") or {}).get("report_id")
    L.append(f"| daily_ops report_id | {rid or '缺失（已降级）'} | {'配置命中' if rid else '默认值 ⚠️'} |")
    L.append("")

    return "\n".join(L)


HUMAN_TEXT = "⚠️ 需人工核查"


def push_feishu(markdown: str, analysis: Dict[str, Any], bundle: Dict[str, Any], *, override_demo: bool = False) -> str:
    """写入飞书文档。演示环境默认阻断；写前 PII 脱敏（markdown_to_blocks 内自动处理）。"""
    from feishu_writer import FeishuWriter

    folder = (os.environ.get("FEISHU_FOLDER_TOKEN")
              or _SKILL_CFG.get("output", {}).get("feishu_folder_token") or None)
    fw = FeishuWriter(folder_token=folder)

    # 演示环境闸门：对所有取数结果扫描指纹
    fw.guard_demo(bundle.get("data") or {}, override=override_demo)

    iso = (analysis.get("report_date") or {}).get("iso", "?")
    doc_id = fw.create_doc(f"每日经营日报 {iso}")
    fw.write_blocks(doc_id, fw.markdown_to_blocks(markdown))
    return doc_id


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="每日经营日报 - 渲染/写入")
    ap.add_argument("--analysis", default="out/analysis.json")
    ap.add_argument("--bundle", default="out/bundle.json")
    ap.add_argument("--out", default="out/daily_report.md")
    ap.add_argument("--feishu", action="store_true", help="写入飞书文档")
    ap.add_argument("--override-demo", action="store_true", help="演示环境也强制写入飞书（仍需水印）")
    args = ap.parse_args(argv)

    def _path(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else _SKILL_ROOT / q

    a_path = _path(args.analysis)
    if not a_path.exists():
        print(f"❌ 找不到分析文件：{a_path}（先运行 analyze.py）", file=sys.stderr)
        return 2
    analysis = json.loads(a_path.read_text(encoding="utf-8"))
    bundle = json.loads(_path(args.bundle).read_text(encoding="utf-8")) if _path(args.bundle).exists() else {}

    md = render_markdown(analysis, bundle)
    out = _path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"✅ 日报已渲染：{out}")

    if args.feishu:
        try:
            doc_id = push_feishu(md, analysis, bundle, override_demo=args.override_demo)
            print(f"✅ 已写入飞书文档：{doc_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"❌ 写飞书失败：{exc}", file=sys.stderr)
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())