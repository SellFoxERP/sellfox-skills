#!/usr/bin/env python3
"""每日经营日报 —— 落盘校验与组装层（fetch_data.py，v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：在会话里调 `mcp__Sellfox-MCP__{sf_discover,sf_describe,sf_execute}`，
    按 `references/workflow.md` 的**取数计划**把每步信封落盘到 `out/raw/<step>.json`。
  * **本脚本 = 计算面**：校验落盘文件（存在性/status/空数据/演示指纹/截断），
    并组装出与旧版完全相同形状的 `bundle.json` —— analyze.py / render_report.py 无需改动。

用法::

    # 1) agent 先按 workflow.md 取数计划落盘 out/raw/*.json
    # 2) 校验 + 组装：
    python fetch_data.py --date 2026-09-29 --out out/bundle.json
    python fetch_data.py                     # 默认「昨日」按配置口径

落盘步骤名（与取数计划一致）：
    shops.json                 get_shop_page_list          → rows
    profit_shop.json           get_profit_report_by_shop   → rows（标准化分页）
    profit_shop_range.json     （可选，30 天基线切片）       → rows（标准化分页）
    orders.json                get_order_page_list         → rows（含 PII，渲染前脱敏）
    campaigns_<shopId>.json    get_sp_campaign_list        → rows（标准化分页，游标）
    multiplatform_<metric>.json get_multiplatform_sale_stats → rows（可选）

v1.7.0 起**不再取自定义报表宽表**（get_custom_report_page_list / report_id 依赖整体移除）：
流量（sessions/CVR）、广告点击、库龄分桶、补货建议等宽表专属指标固定标
「⚠️ 需人工核查——由 custom-report-analysis 技能承接」（见 WIDE_TABLE_DEGRADATION）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- 定位 _shared（不写死 ../_shared/）--------------------------------------
_THIS = Path(__file__).resolve()
_SKILL_ROOT = _THIS.parent.parent
sys.path.insert(0, str(_THIS.parent))

from sharedpath import shared_root  # noqa: E402

_SHARED_ROOT = shared_root(_SKILL_ROOT)
if not _SHARED_ROOT.exists():
    raise SystemExit(
        f"_shared 路径不可用：{_SHARED_ROOT}。请在 config/paths.yaml 设置 shared_root，"
        "或设置环境变量 SELLFOX_SHARED_ROOT，并确保已一并放置 _shared/。"
    )
sys.path.insert(0, str(_SHARED_ROOT / "scripts"))

from config_loader import ConfigLoader, load_yaml_file  # noqa: E402
from sellfox_offline import (  # noqa: E402
    RawEnvelope,
    detect_demo_environment,
    detect_date_filter_unreliable,
    is_no_data,
    load_envelope,
)

# --- 配置 -------------------------------------------------------------------
_LOADER = ConfigLoader(shared_root=_SHARED_ROOT)
_SKILL_CFG = load_yaml_file(_SKILL_ROOT / "config" / "daily_report.yaml")

#: 宽表专属指标固定降级说明（v1.7.0 起本技能不取自定义报表，移交 custom-report-analysis）。
WIDE_TABLE_DEGRADATION = (
    "宽表（get_custom_report_page_list）不在本技能取数范围：流量（sessions/CVR）、"
    "广告点击、库龄分桶、补货建议等宽表专属指标由 custom-report-analysis 技能承接，"
    "本报告固定标「⚠️ 需人工核查」，不编造数值。"
)


def _cfg(*keys: str, default: Any = None) -> Any:
    node: Any = _SKILL_CFG
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def _thresholds() -> Dict[str, Any]:
    return _LOADER.load("thresholds")


# --- 日期口径（与旧版一致）---------------------------------------------------
def resolve_report_date(date_arg: Optional[str]) -> Dict[str, str]:
    if date_arg:
        iso = date_arg
    elif _cfg("report", "date_mode", default="bizday") == "local":
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(_cfg("report", "timezone", default="UTC"))
            now = datetime.now(tz)
        except Exception:
            now = datetime.now(timezone.utc)
        iso = (now.date() - timedelta(days=1)).isoformat()
    else:
        iso = (date.today() - timedelta(days=1)).isoformat()

    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return {
        "iso": iso,
        "compact": d.strftime("%Y%m%d"),
        "next_iso": (d + timedelta(days=1)).isoformat(),
    }


# --- 落盘读取辅助 -----------------------------------------------------------
def _cov_truncation(env: RawEnvelope, source: str) -> Optional[Dict[str, Any]]:
    """把网关 coverage 转成 bundle 的截断/覆盖披露条目（覆盖不全必披露）。

    判据（任一命中即披露）：``complete=false`` / ``truncated=true`` /
    ``fetched_count < total_count``；服务端总数未知时按「总数未知」披露，
    不因 truncated=false 而静默（实测存在 complete=false 且 truncated=false 的部分覆盖）。
    """
    cov = env.coverage or {}
    if not cov:
        return None
    fetched, total = cov.get("fetched_count"), cov.get("total_count")
    shortfall = isinstance(fetched, int) and isinstance(total, int) and total > fetched
    if cov.get("complete") is not False and not cov.get("truncated") and not shortfall:
        return None
    if isinstance(fetched, int) and isinstance(total, int):
        scope = f"本次仅覆盖 {fetched} 行（服务端报告约 {total} 行）"
    elif isinstance(fetched, int):
        scope = f"本次仅覆盖 {fetched} 行（服务端总数未知，覆盖是否完整无法确认）"
    else:
        scope = "覆盖行数未知（信封未返回 fetched_count）"
    reason = "已达 max_rows 上限" if cov.get("truncated") else "服务端 coverage 标记 complete=false"
    return {
        "source": source,
        "scope": env.path.stem,
        "message": f"⚠️ {scope}，{reason}，其余未纳入统计。",
    }


def _load(raw_dir: Path, name: str) -> Optional[RawEnvelope]:
    p = raw_dir / (name if name.endswith(".json") else f"{name}.json")
    if not p.exists():
        return None
    try:
        return load_envelope(p)
    except Exception:
        return None


def _merge_pattern(raw_dir: Path, prefix: str) -> List[RawEnvelope]:
    """合并 campaigns_*.json / multiplatform_*.json 等批次文件。"""
    out: List[RawEnvelope] = []
    for p in sorted(raw_dir.glob(f"{prefix}_*.json")):
        try:
            out.append(load_envelope(p))
        except Exception:
            continue
    return out


# --- 主流程：校验 + 组装 bundle（形状与旧版一致） ---------------------------
def run(
    date_arg: Optional[str] = None,
    raw_dir_arg: Optional[str] = None,
    caliber: Optional[str] = None,
    marketplace: Optional[str] = None,
) -> Dict[str, Any]:
    rd = resolve_report_date(date_arg)
    raw_dir = Path(raw_dir_arg or (_SKILL_ROOT / "out" / "raw"))
    raw_dir = raw_dir if raw_dir.is_absolute() else _SKILL_ROOT / raw_dir

    trunc_store: List[Dict[str, Any]] = []
    human_check: List[str] = list(_cfg("human_check", default=[]))
    degradations: List[str] = []
    demo_fingerprint: Optional[str] = None
    # 利润口径覆盖入口：用户点名 settlement/shipment 时用 --caliber 透传，
    # 与取数（get_profit_report_by_shop.profit_caliber_type）保持一致，缺省走配置。
    caliber = (caliber or "").strip() or None
    if caliber and caliber not in ("settlement", "shipment"):
        raise SystemExit(f"❌ 无效 --caliber：{caliber}（仅支持 settlement / shipment）。")
    marketplace_filter: List[str] = [
        m.strip().upper() for m in (marketplace or "").split(",") if m.strip()
    ]

    bundle: Dict[str, Any] = {
        "report_date": rd,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "currency": _cfg("report", "currency", default="USD"),
            "profit_caliber_type": caliber or _cfg("report", "profit_caliber_type", default="settlement"),
            "profit_caliber_source": "cli" if caliber else "config",
            "date_mode": _cfg("report", "date_mode", default="bizday"),
            "timezone": _cfg("report", "timezone", default=""),
            "baseline": _cfg("report", "baseline", default="window_field"),
        },
        "thresholds": _thresholds(),
        "truncations": trunc_store,
        "degradations": degradations,
        "human_check": human_check,
        "demo_fingerprint": None,
        "data": {},
    }

    def _scan(obj: Any, label: str = "") -> None:
        nonlocal demo_fingerprint
        if isinstance(obj, dict):
            for warning in obj.get("warnings") or []:
                note = str(warning)
                if note not in degradations:
                    degradations.append(note)
            if obj.get("status") != "complete":
                # 降级文案必须带 error_code 与步骤/工具名（workflow.md：数据说明记录 error_code）
                where = "、".join(
                    x for x in (label, f"工具 {obj.get('tool_id')}" if obj.get("tool_id") else "") if x
                )
                code = f"error_code={obj.get('error_code')}" if obj.get("error_code") else "信封未携带 error_code"
                head = f"{where}：" if where else ""
                degradations.append(
                    f"{head}步骤信封状态 {obj.get('status', '缺失')}（{code}），"
                    "不代表完整成功，该源按降级处理，相关项「⚠️ 需人工核查」。"
                )
        if isinstance(obj, dict) and detect_date_filter_unreliable(obj.get("data"), rd["iso"], rd["iso"]):
            note = "返回业务日期不在请求窗口内；以下数值仅展示返回样本，不能解释为该日经营结果。"
            if note not in degradations:
                degradations.append(note)
        fp = detect_demo_environment(obj)
        if fp and not demo_fingerprint:
            demo_fingerprint = fp

    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        raise SystemExit(
            f"❌ 落盘目录为空或不存在：{raw_dir}。\n"
            "   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」\n"
            "   调用 mcp__Sellfox-MCP__{sf_describe,sf_execute} 并把信封落盘到 out/raw/，再重跑本脚本。"
        )

    # 1) 店铺清单
    env = _load(raw_dir, "shops")
    if env is None:
        raise SystemExit("❌ 缺少 out/raw/shops.json（取数计划步骤 1）。请先由 agent 落盘。")
    _scan(env.obj, "步骤 1 店铺清单")
    t = _cov_truncation(env, "get_shop_page_list")
    if t:
        trunc_store.append(t)
    if is_no_data(env.data):
        degradations.append("店铺清单无数据：后续按店铺的步骤无法执行。")
        bundle["data"]["shops"] = []
    else:
        bundle["data"]["shops"] = env.rows("rows")

    # 店铺按唯一 ID 去重计数（上游可能返回重复行，如 5 店 ×4 重复 = 20 行）
    shop_ids: List[str] = []
    for sid in (str(r.get("id")) for r in bundle["data"]["shops"] if r.get("id")):
        if sid not in shop_ids:
            shop_ids.append(sid)
    if len(bundle["data"]["shops"]) != len(shop_ids):
        degradations.append(
            f"店铺清单上游返回重复行 {len(bundle['data']['shops'])}→{len(shop_ids)}（按唯一店铺 ID 去重后计数）。"
        )
    shop_regions = sorted({str(r.get("region")) for r in bundle["data"]["shops"] if r.get("region")})

    # 2) 店铺级利润（结论速览）
    env = _load(raw_dir, "profit_shop")
    if env is not None and not is_no_data(env.data):
        _scan(env.obj, "步骤 2 店铺级利润")
        t = _cov_truncation(env, "get_profit_report_by_shop")
        bundle["data"]["store_profit"] = {
            "reportList": env.rows(),
            "truncation": (t or {}).get("message", "未截断。"),
            "truncated": bool(t),
        }
        if t:
            trunc_store.append(t)
    else:
        bundle["data"]["store_profit"] = None
        degradations.append(
            "利润步骤缺失（out/raw/profit_shop.json 未落盘或无数据）："
            "结论速览的利润类指标（GMV/毛利/毛利率）不可用，标「⚠️ 需人工核查」；订单/广告等其余步骤照常。"
        )

    # 站点过滤核验：请求了 marketplace 过滤时，与上游实际返回的站点对比，
    # 上游忽略过滤（实测存在）必须显式披露，不能让「仅美国站」诉求被静默放行。
    profit_rows: List[Dict[str, Any]] = (bundle["data"].get("store_profit") or {}).get("reportList") or []
    sites_returned: List[str] = []
    for r in profit_rows:
        for si in r.get("shopInfos") or []:
            name = str((si or {}).get("marketplaceName") or "").strip()
            code = name.rsplit(" ", 1)[-1] if name.startswith("Amazon ") else ""
            if code and code not in sites_returned:
                sites_returned.append(code)
    sites_returned.sort()
    if marketplace_filter:
        extra_sites = [s for s in sites_returned if s not in marketplace_filter]
        if extra_sites or not sites_returned:
            got = "、".join(sites_returned) if sites_returned else "未知（利润行未带站点信息）"
            degradations.append(
                f"站点过滤未生效：请求仅 {'、'.join(marketplace_filter)} 站，"
                f"上游返回仍含 {got}（按返回样本口径展示，不代表请求范围）。"
            )

    # 3)（可选）30 天基线切片
    env = _load(raw_dir, "profit_shop_range")
    if env is not None and not is_no_data(env.data):
        _scan(env.obj, "步骤 2b 30 天基线")
        bundle["data"]["store_profit_range"] = {
            "reportList": env.rows(),
            "truncated": bool(_cov_truncation(env, "get_profit_report_by_shop(range)")),
        }

    # 4) 宽表步骤已移除（v1.7.0）：不再取 get_custom_report_page_list，
    #    宽表专属指标固定降级披露（由 custom-report-analysis 技能承接）；
    #    若落盘目录仍有历史 msku_*.json，一律忽略（不消费、不采信）。
    degradations.append(WIDE_TABLE_DEGRADATION)
    bundle["data"]["msku_report"] = None

    # 5) 订单异常样本（PII：渲染前脱敏由 render 层负责）
    env = _load(raw_dir, "orders")
    if env is not None and (env.status != "complete" or not is_no_data(env.data)):
        # failed/partial 信封必须扫出 error_code（失败可见性），不能静默当缺失
        _scan(env.obj, "步骤 3 订单样本")
    if env is not None and not is_no_data(env.data):
        t = _cov_truncation(env, "get_order_page_list")
        bundle["data"]["orders_sample"] = {
            "rows": env.rows("rows"),
            "truncation": (t or {}).get("message", "抽样完成。"),
            "truncated": bool(t),
            "is_sample": True,
        }
        if t:
            trunc_store.append(t)
    else:
        bundle["data"]["orders_sample"] = None

    # 6) SP 广告活动（每店铺一文件，游标）
    camp_envs = _merge_pattern(raw_dir, "campaigns")
    items: List[Dict[str, Any]] = []
    for env_ in camp_envs:
        sid = env_.path.stem.replace("campaigns_", "")
        _scan(env_.obj, f"步骤 4 广告 {sid}")
        t = _cov_truncation(env_, "get_sp_campaign_list")
        if t:
            trunc_store.append(t)
        for it in env_.rows():
            it.setdefault("shopId", sid)
            items.append(it)
    bundle["data"]["campaigns"] = {"itemList": items} if items else None
    if not camp_envs:
        degradations.append("广告活动结构落盘缺失（out/raw/campaigns_*.json）：STEP 03 降级。")

    # 7) 跨平台销量（可选，每指标一文件）
    multi_envs = _merge_pattern(raw_dir, "multiplatform")
    if multi_envs:
        metrics: Dict[str, List[Dict[str, Any]]] = {}
        multi_row_total = 0
        for env_ in multi_envs:
            metric = env_.path.stem.replace("multiplatform_", "")
            _scan(env_.obj, f"步骤 5 跨平台 {metric}")
            t = _cov_truncation(env_, "get_multiplatform_sale_stats")
            if t:
                trunc_store.append(t)
            metrics[metric] = env_.rows("rows")
            multi_row_total += len(metrics[metric])
        bundle["data"]["multiplatform"] = {"metrics": metrics}
        if not multi_row_total:
            degradations.append("跨平台销量缺失（已跳过）：步骤 5 信封无数据或取数失败。")

    bundle["demo_fingerprint"] = demo_fingerprint
    bundle["meta"] = {
        "shops_total": len(shop_ids),
        "shops_covered": len(shop_ids),
        "shop_regions": shop_regions,
        "marketplace_filter": marketplace_filter or None,
        "sites_returned": sites_returned,
        "profit_step_missing": bundle["data"].get("store_profit") is None,
        "campaign_rows": len(items),
        "demo_hit": demo_fingerprint,
        "raw_dir": str(raw_dir),
    }
    return bundle


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="每日经营日报 - 落盘校验与组装（零网络）")
    ap.add_argument("--date", help="报告日期 yyyy-MM-dd；缺省按配置口径取昨日")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--out", default="out/bundle.json", help="输出 bundle JSON 路径")
    ap.add_argument("--caliber", choices=["settlement", "shipment"],
                    help="利润口径覆盖（缺省读 config/daily_report.yaml；"
                         "用户点名口径时须与取数 profit_caliber_type 一致并透传本参数）")
    ap.add_argument("--marketplace", default=None,
                    help="请求的站点过滤（如 US 或 US,UK；用于核验上游过滤是否生效并回显请求范围）")
    args = ap.parse_args(argv)

    bundle = run(args.date, args.raw_dir, caliber=args.caliber, marketplace=args.marketplace)

    out = Path(args.out)
    if not out.is_absolute():
        out = _SKILL_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 数据包已写出：{out}（来源：{bundle['meta']['raw_dir']}）")
    if args.caliber:
        print(f"ℹ️ 利润口径覆盖：{args.caliber}（CLI --caliber，报告口径行按此标注）")
    if bundle.get("demo_fingerprint"):
        print(f"⚠️ 演示环境指纹：{bundle['demo_fingerprint']}（数据非真实经营数据）")
    for t in bundle.get("truncations", []):
        print(f"⚠️ 截断披露：{t['message']}（{t['source']} {t.get('scope', '')}）")
    for d in bundle.get("degradations", []):
        print(f"⚠️ 口径降级：{d}")
    if bundle["data"].get("store_profit") is None:
        # 最小集运行（如仅订单下钻）不再硬退：利润缺失走降级（横幅 + 需人工核查），其余步骤照常
        print("⚠️ 利润步骤缺失：结论速览的利润类指标不可用（已降级，不阻断）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
