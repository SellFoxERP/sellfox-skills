#!/usr/bin/env python3
"""每日经营日报 —— 落盘校验与组装层（fetch_data.py，v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：在会话里调 `mcp__Sellfox-MCP__{help,search,action}`，
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
    profit_shop.json           get_profit_report_by_shop   → reportList
    profit_shop_range.json     （可选，30 天基线切片）       → reportList
    msku_<shopId>.json         get_custom_report_page_list → rows（每店铺批一个文件）
    orders.json                get_order_page_list         → rows（含 PII，渲染前脱敏）
    campaigns_<shopId>.json    get_sp_campaign_list        → itemList（游标）
    multiplatform_<metric>.json get_multiplatform_sale_stats → rows（可选）
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


def _cfg(*keys: str, default: Any = None) -> Any:
    node: Any = _SKILL_CFG
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def _thresholds() -> Dict[str, Any]:
    return _LOADER.load("thresholds")


def _report_id() -> str:
    reports = (_LOADER.load("reports").get("reports") or {})
    return str((reports.get("daily_ops") or {}).get("id") or "").strip()


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
    """把网关 coverage 转成 bundle 的截断披露条目（截断必披露）。"""
    cov = env.coverage or {}
    if not cov.get("truncated"):
        return None
    return {
        "source": source,
        "scope": env.path.stem,
        "message": (
            f"⚠️ 本次仅覆盖 {cov.get('fetched_count')} 行"
            f"（服务端报告约 {cov.get('total_count')} 行），已达 max_rows 上限，其余未纳入统计。"
        ),
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
    """合并 msku_*.json / campaigns_*.json / multiplatform_*.json 等批次文件。"""
    out: List[RawEnvelope] = []
    for p in sorted(raw_dir.glob(f"{prefix}_*.json")):
        try:
            out.append(load_envelope(p))
        except Exception:
            continue
    return out


# --- 主流程：校验 + 组装 bundle（形状与旧版一致） ---------------------------
def run(date_arg: Optional[str] = None, raw_dir_arg: Optional[str] = None) -> Dict[str, Any]:
    rd = resolve_report_date(date_arg)
    raw_dir = Path(raw_dir_arg or (_SKILL_ROOT / "out" / "raw"))
    raw_dir = raw_dir if raw_dir.is_absolute() else _SKILL_ROOT / raw_dir

    trunc_store: List[Dict[str, Any]] = []
    human_check: List[str] = list(_cfg("human_check", default=[]))
    degradations: List[str] = []
    demo_fingerprint: Optional[str] = None

    bundle: Dict[str, Any] = {
        "report_date": rd,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "currency": _cfg("report", "currency", default="USD"),
            "profit_caliber_type": _cfg("report", "profit_caliber_type", default="settlement"),
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

    def _scan(obj: Any) -> None:
        nonlocal demo_fingerprint
        if isinstance(obj, dict):
            for warning in obj.get("warnings") or []:
                note = str(warning)
                if note not in degradations:
                    degradations.append(note)
            if obj.get("status") != "complete":
                degradations.append(f"数据响应状态 {obj.get('status', '缺失')}，不代表完整成功。")
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
            "   调用 mcp__Sellfox-MCP__{search,action} 并把信封落盘到 out/raw/，再重跑本脚本。"
        )

    # 1) 店铺清单
    env = _load(raw_dir, "shops")
    if env is None:
        raise SystemExit("❌ 缺少 out/raw/shops.json（取数计划步骤 1）。请先由 agent 落盘。")
    _scan(env.obj)
    t = _cov_truncation(env, "get_shop_page_list")
    if t:
        trunc_store.append(t)
    if is_no_data(env.data):
        degradations.append("店铺清单无数据：后续按店铺的步骤无法执行。")
        bundle["data"]["shops"] = []
    else:
        bundle["data"]["shops"] = env.rows("rows")

    shop_ids = [str(r.get("id")) for r in bundle["data"]["shops"] if r.get("id")]

    # 2) 店铺级利润（L1）
    env = _load(raw_dir, "profit_shop")
    if env is not None and not is_no_data(env.data):
        _scan(env.obj)
        t = _cov_truncation(env, "get_profit_report_by_shop")
        bundle["data"]["store_profit"] = {
            "reportList": env.rows("reportList"),
            "truncation": (t or {}).get("message", "未截断。"),
            "truncated": bool(t),
        }
        if t:
            trunc_store.append(t)
    else:
        bundle["data"]["store_profit"] = None
        degradations.append(
            "店铺级利润缺失或无数据（out/raw/profit_shop.json）：L1 核心指标不可用，需人工核查。"
        )

    # 3)（可选）30 天基线切片
    env = _load(raw_dir, "profit_shop_range")
    if env is not None and not is_no_data(env.data):
        _scan(env.obj)
        bundle["data"]["store_profit_range"] = {
            "reportList": env.rows("reportList"),
            "truncated": bool(_cov_truncation(env, "get_profit_report_by_shop(range)")),
        }

    # 4) 宽表（每店铺批一文件；无 report_id → 降级）
    report_id = _report_id()
    msku_envs = _merge_pattern(raw_dir, "msku")
    if not report_id:
        note = str(_cfg("degradation", "no_report_id_note", default="") or "")
        if not note or note.startswith((">", "|")):  # YAML 块标量被朴素解析时的兜底
            note = "尚未取得适用报表，L2 口径降级；助手可自动发现关联报表，当前先展示已有数据。"
        degradations.append(note)
        bundle["data"]["msku_report"] = None
    elif msku_envs:
        rows: List[Dict[str, Any]] = []
        for env_ in msku_envs:
            _scan(env_.obj)
            sid = env_.path.stem.replace("msku_", "")
            for r in env_.rows("rows"):
                r.setdefault("shopId", "" if sid == "ALL" else sid)
                rows.append(r)
            t = _cov_truncation(env_, "get_custom_report_page_list")
            if t:
                trunc_store.append(t)
        bundle["data"]["msku_report"] = {"rows": rows, "report_id": report_id, "batches": len(msku_envs)}
    else:
        bundle["data"]["msku_report"] = None
        degradations.append("宽表落盘缺失（out/raw/msku_*.json）：L2 明细层降级。")

    # 5) 订单异常样本（PII：渲染前脱敏由 render 层负责）
    env = _load(raw_dir, "orders")
    if env is not None and not is_no_data(env.data):
        _scan(env.obj)
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
        _scan(env_.obj)
        sid = env_.path.stem.replace("campaigns_", "")
        for it in env_.rows("itemList"):
            it.setdefault("shopId", sid)
            items.append(it)
    bundle["data"]["campaigns"] = {"itemList": items} if items else None
    if not camp_envs:
        degradations.append("广告活动结构落盘缺失（out/raw/campaigns_*.json）：STEP 03 降级。")

    # 7) 跨平台销量（可选，每指标一文件）
    multi_envs = _merge_pattern(raw_dir, "multiplatform")
    if multi_envs:
        metrics: Dict[str, List[Dict[str, Any]]] = {}
        for env_ in multi_envs:
            _scan(env_.obj)
            metric = env_.path.stem.replace("multiplatform_", "")
            metrics[metric] = env_.rows("rows")
        bundle["data"]["multiplatform"] = {"metrics": metrics}

    bundle["demo_fingerprint"] = demo_fingerprint
    bundle["meta"] = {
        "shops_total": len(shop_ids),
        "shops_covered": len(shop_ids),
        "msku_rows": len((bundle["data"].get("msku_report") or {}).get("rows") or []),
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
    args = ap.parse_args(argv)

    bundle = run(args.date, args.raw_dir)

    out = Path(args.out)
    if not out.is_absolute():
        out = _SKILL_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 数据包已写出：{out}（来源：{bundle['meta']['raw_dir']}）")
    if bundle.get("demo_fingerprint"):
        print(f"⚠️ 演示环境指纹：{bundle['demo_fingerprint']}（数据非真实经营数据）")
    for t in bundle.get("truncations", []):
        print(f"⚠️ 截断披露：{t['message']}（{t['source']} {t.get('scope', '')}）")
    for d in bundle.get("degradations", []):
        print(f"⚠️ 口径降级：{d}")
    if bundle["data"].get("store_profit") is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
