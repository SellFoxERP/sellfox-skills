#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周报 / 月报 —— 落盘校验与组装层（fetch_weekly_data.py，v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：在会话里调客户端元工具 `mcp__Sellfox-MCP__{help,search,action}`，
    按 `references/workflow.md` 的**取数计划**把每步**完整信封**
    （status/data/coverage/pagination/warnings）落盘到 `out/raw/<step>.json`
    （多窗口，文件名带窗口标签）。旧直连客户端 `sellfox_client.py` 已废弃，本模块不再 import。
  * **本脚本 = 计算面**：零网络，只读落盘文件——校验（存在性 / status / 空数据 /
    演示指纹 / coverage 截断），并组装出与旧版**完全相同形状**的 `weekly_data.json`，
    compute_metrics.py / render_report.py / push_feishu.py 无需改动。

落盘文件 → 输出键映射（与取数计划一致；窗口值由 `scripts/period.py` 固定，不手算）：
    profit_shop_current.json     本期（最近完整自然周/月） → data.profit_current
    profit_shop_prev.json        上期                      → data.profit_prev
    profit_shop_prev_month.json  上月同期                  → data.profit_prev_month_same_period
    profit_shop_prev_year.json   去年同期（同比开启时）     → data.profit_yoy
    profit_shop_mtd.json         本月累计（达成率 basis）   → data.profit_month_to_date
    profit_msku.json             MSKU 利润（本期，单文件）  → data.rank_top / data.rank_bottom
                                 （组装层本地排序：Top 按销售额降序 / Bottom 按毛利升序）
    custom_current.json          自定义宽表（本期）         → data.custom_report
    custom_prev*.json            宽表历史窗口（可选，当前计算面未消费）
    trend.json                   商品分析 charData（可选）  → data.char_data
    shops.json                   店铺清单（可选，计算面不依赖，仅保持形状）

口径纪律（不变）：
* 利润口径 `profit_caliber_type`（settlement/shipment）必须显式，写入 meta 并进报告「数据说明」；
* 空数据（status=complete 且 data 空）= 「无数据」，**不写 0、不重试**；
* 截断必披露：信封 `coverage.truncated=true` → 转成披露语句写入 sources（渲染层落到「数据说明」）；
* 演示指纹（`MOCK-` / `SHOP-DEMO-` / 值含「演示」等）→ meta.mock=True，报告顶部水印。

用法::

    # 1) agent 先按 references/workflow.md 取数计划落盘 out/raw/*.json
    # 2) 校验 + 组装：
    python fetch_weekly_data.py --period week --anchor 2026-10-12 --out weekly_data.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import skill_paths

SHARED_ROOT = skill_paths.bootstrap()

from sellfox_offline import (  # type: ignore  # noqa: E402
    RawEnvelope,
    detect_demo_environment,
    detect_date_filter_unreliable,
    is_no_data,
    load_envelope,
)
from config_loader import ConfigLoader  # type: ignore  # noqa: E402

import period as period_mod  # noqa: E402


# ---------------------------------------------------------------------------
# 窗口 → 落盘文件映射（多窗口，文件名带窗口标签；顺序与旧版输出键一致）
# (输出键, 落盘步骤名, 窗口标签)
# ---------------------------------------------------------------------------
PROFIT_WINDOW_STEPS: Tuple[Tuple[str, str, str], ...] = (
    ("profit_current", "profit_shop_current", "本期"),
    ("profit_prev", "profit_shop_prev", "上期"),
    ("profit_prev_month_same_period", "profit_shop_prev_month", "上月同期"),
    ("profit_month_to_date", "profit_shop_mtd", "本月累计"),
    ("profit_yoy", "profit_shop_prev_year", "去年同期"),
)

TOOL_PROFIT_SHOP = "get_profit_report_by_shop"
TOOL_PROFIT_MSU = "get_profit_report_by_msku"
TOOL_CUSTOM = "get_custom_report_page_list"
TOOL_TREND = "get_product_analyze_detail"
TOOL_SHOPS = "get_shop_page_list"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _make_source(status: str, tool: str, note: str = "", truncation: str = "",
                 error_code: str = "") -> Dict[str, Any]:
    """与旧版 sources 条目同构：{status, tool, note, truncation}；failed 时附 error_code。"""
    out = {"status": status, "tool": tool, "note": note, "truncation": truncation}
    if error_code:
        out["error_code"] = error_code
    return out


def _num(value: Any) -> Optional[float]:
    """把赛狐返回的数值（可能是字符串）安全转 float；转不了返回 None。"""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cov_notes(env: RawEnvelope) -> str:
    """把信封 coverage 截断 / partial 状态转成披露语句（截断与覆盖不完整必披露）。

    覆盖真相只看 ``coverage{complete, fetched_count, total_count, truncated}``，
    **不信** ``pagination.has_more``（实测与 coverage 矛盾，见 SELLFOX_CLIENT.md §5）。
    """
    parts: List[str] = []
    cov = env.coverage or {}
    if cov.get("truncated"):
        parts.append(
            f"⚠️ 本次仅覆盖 {cov.get('fetched_count')} 行"
            f"（服务端报告约 {cov.get('total_count')} 行），已达 max_rows 上限，其余未纳入统计。"
        )
    if env.partial:
        parts.append("⚠️ status=partial：部分成功，已采信但覆盖不完整（须在数据说明披露）。")
    return "；".join(parts)


def _load(raw_dir: Path, name: str) -> Tuple[Optional[RawEnvelope], str]:
    """读取一个落盘信封；返回 (信封或 None, 失败原因)。"""
    p = raw_dir / (name if name.endswith(".json") else f"{name}.json")
    if not p.exists():
        return None, f"缺少落盘 {p}（取数计划步骤未完成）"
    try:
        return load_envelope(p), ""
    except Exception as exc:  # noqa: BLE001 —— JSON 损坏要给出可读原因
        return None, f"{p.name} 无法解析：{exc}"


def _consume_envelope(
    env: Optional[RawEnvelope], err: str, tool: str, label: str, list_key: str
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """按 v3 状态语义消费一个信封：missing/corrupt → degraded/error；failed → error；
    空数据 → no_data；否则取 list_key 行，状态 ok。返回 (source, rows)。"""
    if env is None:
        status = "error" if "无法解析" in err else "degraded"
        return _make_source(status, tool, f"{err}；{label}对应指标将标「不适用/无数据」"), []
    if env.status == "failed":
        code = env.error_code or "未知"
        note = f"status=failed error_code={code} {env.message or ''}".strip()
        return _make_source("error", tool, note, error_code=code), []
    notes = _cov_notes(env)
    if is_no_data(env.data):
        return _make_source("no_data", tool, "无数据", notes), []
    rows = env.rows(list_key)
    if not rows:
        return _make_source(
            "no_data", tool,
            f"无数据（data 非空但 {list_key} 为空——核对列表字段名：{tool} 的列表字段是 {list_key}）",
            notes,
        ), []
    return _make_source("ok", tool, truncation=notes), rows


# ---------------------------------------------------------------------------
# 各步骤组装
# ---------------------------------------------------------------------------

def assemble_profit_window(
    raw_dir: Path, step: str, label: str
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[RawEnvelope]]:
    """校验并组装一个店铺级利润窗口（reportList）。"""
    env, err = _load(raw_dir, step)
    source, rows = _consume_envelope(env, err, TOOL_PROFIT_SHOP, label, "reportList")
    return source, rows, env


def assemble_rankings(
    raw_dir: Path, fm: Dict[str, str]
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Optional[RawEnvelope]]:
    """MSKU 榜单：单一 profit_msku.json 供 Top / Bottom 两个榜单（组装层本地排序）。

    取数计划不再按 order_by 分两次调用；若该文件因 max_rows 截断，
    Bottom（毛利升序）可能未覆盖真实最差项 —— 截断披露会随 source 带出。
    """
    env, err = _load(raw_dir, "profit_msku")
    source, rows = _consume_envelope(env, err, TOOL_PROFIT_MSU, "Top/Bottom 榜单", "reportList")
    # 覆盖量化：榜单「仅覆盖 N 行」必须带上服务端全量总数（无则标「总数未知」），
    # 否则 Top K 不可达（全量 < K）时用户无从判断头部集中度结论的覆盖上限。
    if env is not None:
        cov_total = (env.coverage or {}).get("total_count")
        if isinstance(cov_total, int):
            source["rank_rows_total"] = cov_total

    def _key(row: Dict[str, Any], field: str) -> float:
        v = _num(row.get(field))
        return v if v is not None else 0.0

    if rows:
        extra = "由 profit_msku.json 本地排序：Top 按销售额降序 / Bottom 按毛利升序"
        src_top = dict(source, note=extra)
        src_bottom = dict(source, note=extra + "（若文件被截断，最差项可能未覆盖）")
        top = sorted(rows, key=lambda r: _key(r, fm["gmv"]), reverse=True)
        bottom = sorted(
            rows,
            key=lambda r: (1 if _num(r.get(fm["gross_profit"])) is None else 0,
                           _key(r, fm["gross_profit"])),
        )
        return src_top, src_bottom, top, bottom, env
    return source, dict(source), [], [], env


def assemble_custom(
    raw_dir: Path, report_id: str
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[RawEnvelope]]:
    """自定义宽表（489 字段，本期窗口）。缺 report_id / 缺落盘 → 降级（不崩）。"""
    if not report_id:
        note = ("未配置 reports.yaml → daily_ops.id，流量/广告/退款等 489 字段不可用；"
                "助手可自动发现适用报表；当前按可得数据展示（口径降级）")
        return _make_source("degraded", TOOL_CUSTOM, note), [], None
    env, err = _load(raw_dir, "custom_current")
    source, rows = _consume_envelope(env, err, TOOL_CUSTOM, "宽表（本期）", "rows")
    return source, rows, env


def assemble_trend(raw_dir: Path, trend_cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Any]:
    """趋势图（可选）：信封 data 里的 charData；缺失时计算层自动降级为 bizDay 日粒度。"""
    if not trend_cfg.get("enabled", True):
        return _make_source("skipped", TOOL_TREND, "趋势图已关闭"), None
    env, err = _load(raw_dir, "trend")
    if env is None:
        return _make_source(
            "skipped", TOOL_TREND,
            f"{err}；趋势图降级为利润报表 bizDay 日粒度（计算层自动处理）"), None
    if env.status == "failed":
        note = f"status=failed error_code={env.error_code or '未知'} {env.message or ''}".strip()
        return _make_source("error", TOOL_TREND, note), None
    data = env.data if isinstance(env.data, dict) else {}
    char_data = data.get("charData") or data.get("char_data")
    if char_data:
        return _make_source("ok", TOOL_TREND, truncation=_cov_notes(env)), char_data
    return _make_source("no_data", TOOL_TREND, "信封未返回 charData（趋势图降级为 bizDay 日粒度）"), None


def assemble_shops(raw_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[RawEnvelope]]:
    """店铺清单（可选）：本技能计算面不依赖，仅为保持输出形状与告警路由备用。"""
    env, err = _load(raw_dir, "shops")
    if env is None:
        return _make_source(
            "skipped", TOOL_SHOPS,
            f"{err}；本技能计算面不依赖店铺清单（如需告警路由请由 agent 落盘）"), [], None
    source, rows = _consume_envelope(env, err, TOOL_SHOPS, "店铺清单", "rows")
    return source, rows, env


# ---------------------------------------------------------------------------
# 主流程：校验落盘 + 组装（形状与旧版一致）
# ---------------------------------------------------------------------------

def fetch_all(
    period: str,
    anchor: Optional[str],
    caliber: str,
    shop_ids: Optional[List[str]],
    currency: str,
    shared_root: Optional[str] = None,
    raw_dir_arg: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = skill_paths.load_skill_config("weekly")
    loader = ConfigLoader(shared_root)
    reports_cfg = loader.load("reports")
    fm = cfg["profit_report_fields"]

    plan = period_mod.build_plan(
        period=period, anchor=anchor,
        week_start=(cfg.get("period", {}) or {}).get("week_start", "monday"),
        yoy=(cfg.get("period", {}).get("compare", {}) or {}).get("yoy", True),
    )

    daily_report_id = str(((reports_cfg.get("reports") or {}).get("daily_ops") or {}).get("id") or "").strip()
    agg_currency = currency or (cfg.get("currency", {}) or {}).get("aggregate_to", "") or ""

    raw_dir = Path(raw_dir_arg) if raw_dir_arg else (skill_paths.SKILL_ROOT / "out" / "raw")
    if not raw_dir.is_absolute():
        raw_dir = skill_paths.SKILL_ROOT / raw_dir
    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        raise SystemExit(
            f"❌ 落盘目录为空或不存在：{raw_dir}。\n"
            "   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」\n"
            "   调用 mcp__Sellfox-MCP__{search,action} 并把每步信封落盘到 out/raw/，再重跑本脚本。"
        )

    sources: Dict[str, Any] = {}
    data: Dict[str, Any] = {}
    envs: List[Optional[RawEnvelope]] = []

    # 1) 店铺清单（可选）
    shops_src, shops_rows, env = assemble_shops(raw_dir)
    sources["shops"] = shops_src
    data["shops"] = shops_rows
    envs.append(env)

    # 2) 店铺级利润多窗口（本期 / 上期 / 上月同期 / 本月累计 / 去年同期）
    # 失真披露措辞按 period 类型（周报→该周；月报→该月），不写死「该周」。
    period_word = "该月" if plan.get("period") == "month" else "该周"
    for out_key, step, label in PROFIT_WINDOW_STEPS:
        if out_key == "profit_yoy" and "yoy" not in plan["windows"]:
            continue  # 同比关闭时不输出该键（与旧版一致）
        src, rows, env = assemble_profit_window(raw_dir, step, label)
        if env is not None:
            env_period = env.obj.get("period") or {}
            start, end = env_period.get("start_date"), env_period.get("end_date")
            # 同比基期按月 yyyyMM（超 6 个月窗口按契约改按月）：整月口径标记，
            # compute 层据此在数据说明标注「与本期窗口非等长，仅作方向参考」。
            if out_key == "profit_yoy" and any(
                isinstance(v, str) and len(v) == 6 and v.isdigit() for v in (start, end)
            ):
                src["yoy_month_granularity"] = True
            if start and end and detect_date_filter_unreliable(env.data, str(start), str(end)):
                src["note"] = (f"返回业务日期与请求窗口不符；数值仅展示返回样本，"
                               f"不能解释为{period_word}经营结果。")
                src["truncation"] = (src.get("truncation") or "") + " " + src["note"]
        sources[out_key] = src
        data[out_key] = rows
        envs.append(env)

    # 3) MSKU 榜单（Top / Bottom 共用一个落盘文件）
    top_src, bottom_src, top_rows, bottom_rows, env = assemble_rankings(raw_dir, fm)
    sources["rank_top"] = top_src
    sources["rank_bottom"] = bottom_src
    data["rank_top"] = top_rows
    data["rank_bottom"] = bottom_rows
    envs.append(env)

    # 4) 自定义宽表（本期；缺 report_id → 降级）+ 历史窗口（可选，暂未消费）
    custom_src, custom_rows, env = assemble_custom(raw_dir, daily_report_id)
    sources["custom_report"] = custom_src
    data["custom_report"] = custom_rows
    envs.append(env)
    prev_customs = sorted(p.name for p in raw_dir.glob("custom_prev*.json"))
    if prev_customs:
        sources["custom_prev"] = _make_source(
            "skipped", TOOL_CUSTOM,
            f"宽表历史窗口已落盘 {len(prev_customs)} 份（{', '.join(prev_customs)}），"
            "当前计算面只消费 custom_current")

    # 5) 趋势图（可选）
    trend_src, char_data = assemble_trend(raw_dir, cfg.get("trend") or {})
    sources["trend"] = trend_src
    data["char_data"] = char_data
    data["analyze_list"] = []  # v3 不再落盘 analyze_list；计算面仅消费 char_data

    # 6) 演示环境识别（运行期，不靠人记；扫全部已落盘信封）
    mock_fingerprint: Optional[str] = None
    for env in envs:
        if env is None:
            continue
        fp = detect_demo_environment(env.obj)
        if fp:
            mock_fingerprint = fp
            break

    meta = {
        "skill": "04-weekly-review",
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "period": plan,
        "profit_caliber_type": caliber,
        "currency": agg_currency or "原币种（未折算，禁止跨币种直接相加）",
        "report_id_present": bool(daily_report_id),
        "mock": bool(mock_fingerprint),
        "demo_fingerprint": mock_fingerprint,
        "shop_filter": shop_ids or [],
        "raw_dir": str(raw_dir),
    }
    return {"meta": meta, "sources": sources, "data": data}


def _main() -> None:
    ap = argparse.ArgumentParser(description="周报/月报取数（只读）")
    ap.add_argument("--period", choices=["week", "month"], default="week")
    ap.add_argument("--anchor", default=None, help="参考日期 YYYY-MM-DD")
    ap.add_argument("--profit-caliber", choices=["settlement", "shipment"], default="settlement")
    ap.add_argument("--shop-ids", nargs="*", default=None, help="限定店铺，留空=全部")
    ap.add_argument("--currency", default="", help="折算币种，留空=原币种")
    ap.add_argument("--shared-root", default=None, help="覆盖 _shared/ 绝对路径")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--out", default="weekly_data.json")
    args = ap.parse_args()

    result = fetch_all(
        period=args.period, anchor=args.anchor, caliber=args.profit_caliber,
        shop_ids=args.shop_ids, currency=args.currency, shared_root=args.shared_root,
        raw_dir_arg=args.raw_dir,
    )
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                              encoding="utf-8")
    print(f"✅ 校验+组装完成 → {args.out}（来源落盘：{result['meta']['raw_dir']}）")
    for name, src in result["sources"].items():
        flag = "" if src["status"] == "ok" else f"  [{src['status']}]"
        print(f"  - {name}: {src['tool']}{flag}")
        if src.get("note"):
            print(f"      {src['note']}")
        if src.get("truncation"):
            print(f"      {src['truncation']}")
    if result["meta"]["mock"]:
        print(f"⚠️ 演示数据，非真实经营数据（指纹：{result['meta']['demo_fingerprint']}）")

    cur_src = result["sources"].get("profit_current") or {}
    if cur_src.get("status") in ("error", "degraded"):
        print("❌ 本期窗口（profit_shop_current.json）缺失或失败，核心指标不可用。"
              "请先按取数计划落盘后重跑。")
        raise SystemExit(2)


if __name__ == "__main__":
    _main()
