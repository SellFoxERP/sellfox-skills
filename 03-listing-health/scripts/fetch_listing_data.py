#!/usr/bin/env python3
"""Listing 健康体检 —— 落盘校验与组装层（fetch_listing_data.py，v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：在会话里调 `mcp__Sellfox-MCP__{help,search,action}`，
    按 `references/workflow.md` 的**取数计划**把每步信封落盘到 `out/raw/<step>.json`。
  * **本脚本 = 计算面**：校验落盘文件（存在性/status/空数据/演示指纹/coverage 截断），
    并组装出与旧版完全相同形状的 `data.json` —— diagnose.py / render_report.py 无需改动。

用法::

    # 1) agent 先按 workflow.md 取数计划落盘 out/raw/*.json
    # 2) 校验 + 组装：
    python scripts/fetch_listing_data.py --shop-ids 10001 --out data.json

落盘步骤名（与取数计划一致）：
    online_<batch>.json         get_online_product_page_list → rows（按状态或店铺分批，一批一文件）
    custom_report_<label>.json  get_custom_report_page_list  → rows（依赖 report_id；缺 → 跳过并标口径降级）
    product_analyze.json        get_product_analyze_list     → rows（可选增强）
    labels.json                 get_product_by_label        → rows（仅配置 labels.required_groups 时）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import listing_common as lc

try:  # _shared/scripts 已由 listing_common 引导时挂载到 sys.path
    from sellfox_offline import (  # noqa: E402
        RawEnvelope,
        detect_demo_environment,
        is_no_data,
        load_envelope,
    )
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        f"无法加载 _shared 离线工具（shared_root={lc.SHARED_ROOT}）：{exc}\n"
        "请确认安装技能时一并放置 _shared/，并在 config/paths.yaml 设置 shared_root。"
    )

_SKILL_ROOT = lc.SKILL_DIR

# 宽表（489 字段）专属、online 表没有的字段 —— report_id 缺失时显式列出的不可用清单。
WIDE_TABLE_ONLY_FIELDS = (
    "sessions", "pageViews", "conversionRate", "buyBoxPercent",
    "lowReviewNum", "reviewNum", "bigCategoryRanking", "smallCategoryRanking",
    "availableDays",
)

# 组装层对宽表行的字段裁剪（与旧版一致：只留体检需要的列，避免 489 字段撑爆上下文）。
_TRAFFIC_KEEP = {
    "asin", "parentAsin", "msku", "sku", "shopId", "marketplaceId",
    "sessions", "pageViews", "conversionRate", "buyBoxPercent",
    "lowReviewNum", "reviewNum", "rating", "bigCategoryRanking", "smallCategoryRanking",
    "availableDays", "listingPrice", "currency",
}


# --- 落盘读取辅助 -----------------------------------------------------------
def _load(raw_dir: Path, name: str) -> Optional[RawEnvelope]:
    p = raw_dir / (name if name.endswith(".json") else f"{name}.json")
    if not p.exists():
        return None
    try:
        return load_envelope(p)
    except Exception:
        return None


def _merge_pattern(raw_dir: Path, prefix: str) -> List[RawEnvelope]:
    """合并 online_*.json / custom_report_*.json 等批次文件。"""
    out: List[RawEnvelope] = []
    for p in sorted(raw_dir.glob(f"{prefix}_*.json")):
        try:
            out.append(load_envelope(p))
        except Exception:
            continue
    return out


def _truncation(env: RawEnvelope, tool: str, batch: str) -> Optional[str]:
    """把网关 coverage 转成披露语句（截断必披露）。"""
    cov = env.coverage or {}
    if not cov.get("truncated"):
        return None
    return (
        f"[{tool}:{batch}] ⚠️ 本次仅覆盖 {cov.get('fetched_count')} 行"
        f"（服务端约 {cov.get('total_count')} 行），已达 max_rows 上限，其余未纳入统计。"
    )


def _rows_or_note(
    env: RawEnvelope,
    tool: str,
    batch: str,
    errors: List[str],
    disclosures: List[str],
) -> Optional[List[Dict[str, Any]]]:
    """校验单个信封：failed → 记 error；空 data → 无数据；partial/截断 → 披露。"""
    disclosures.extend(f"[{tool}:{batch}] {warning}" for warning in env.warnings)
    if env.status not in ("complete", "partial"):
        errors.append(
            f"{tool}[{batch}] status={env.status} error_code={env.error_code} {env.message or ''}"
        )
        return None
    if is_no_data(env.data):
        return None  # 无数据不是失败：落盘原样保留，不重试、不写 0
    if env.partial:
        disclosures.append(f"[{tool}:{batch}] status=partial：部分成功，可采信但须披露。")
    t = _truncation(env, tool, batch)
    if t:
        disclosures.append(t)
    return env.rows("rows")


# --- 主流程：校验 + 组装 data.json（形状与旧版一致） ------------------------
def run(args: argparse.Namespace) -> Dict[str, Any]:
    cfg = lc.load_skill_config("listing")
    windows = cfg.get("stat_windows") or {}
    short_days = args.short_days or int(windows.get("short_days", 7))
    long_days = args.long_days or int(windows.get("long_days", 30))

    statuses = [s.strip() for s in args.online_status.split(",") if s.strip()] or list(
        cfg.get("online_status_scan") or ["active", "inActive", "delete"]
    )
    shop_ids = [s.strip() for s in args.shop_ids.split(",") if s.strip()]
    label_ids = [s.strip() for s in args.label_ids.split(",") if s.strip()]

    # report_id 解析（与旧版一致：显式参数 > _shared/config/reports.yaml）
    report_id = args.report_id.strip()
    report_key = ((cfg.get("degradation") or {}).get("report_key")) or "daily_ops"
    if not report_id:
        reports = lc.load_shared_section("reports", "reports") or {}
        entry = reports.get(report_key) or {}
        report_id = str(entry.get("id") or "").strip()

    end = _dt.date.today()
    start = end - _dt.timedelta(days=long_days)

    raw_dir = Path(args.raw_dir or (_SKILL_ROOT / "out" / "raw"))
    raw_dir = raw_dir if raw_dir.is_absolute() else _SKILL_ROOT / raw_dir

    disclosures: List[str] = []
    errors: List[str] = []
    demo_fp: Optional[str] = None

    def _scan(obj: Any) -> None:
        nonlocal demo_fp
        fp = detect_demo_environment(obj)
        if fp and not demo_fp:
            demo_fp = fp

    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        raise SystemExit(
            f"❌ 落盘目录为空或不存在：{raw_dir}。\n"
            "   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」\n"
            "   调用 mcp__Sellfox-MCP__{search,action} 并把信封落盘到 out/raw/，再重跑本脚本。"
        )

    # 1) 在线产品（主数据源）：合并 online_<batch>.json，按 listingId（退化 shopId+asin）去重
    online_rows: List[Dict[str, Any]] = []
    seen: set = set()
    status_set = set(statuses)
    online_envs = _merge_pattern(raw_dir, "online")
    if not online_envs:
        errors.append("在线产品落盘缺失（out/raw/online_<batch>.json）：主数据源不可用，报告无法生成。")
    for env in online_envs:
        _scan(env.obj)
        batch = env.path.stem.replace("online_", "")
        rows = _rows_or_note(env, "get_online_product_page_list", batch, errors, disclosures)
        for rec in rows or []:
            rid = lc.record_id(rec)
            if rid in seen:
                continue
            seen.add(rid)
            rec = dict(rec)
            if batch in status_set:  # 按状态分批时保留扫描来源（与旧版 _scan_status 一致）
                rec["_scan_status"] = batch
            online_rows.append(rec)

    # 2) 宽表（依赖 report_id；缺 → 跳过整步并显式列出「宽表专属字段不可用」清单）
    traffic_rows: List[Dict[str, Any]] = []
    if not report_id:
        pass  # 口径降级：宽表整步跳过（不可用字段清单见 meta.wide_table_fields_unavailable）
    else:
        cr_envs = _merge_pattern(raw_dir, "custom_report")
        if not cr_envs:
            errors.append(
                "自定义报表落盘缺失（out/raw/custom_report_<label>.json）："
                "流量/CVR/购物车占比/类目排名/差评数不可用。"
            )
        for env in cr_envs:
            _scan(env.obj)
            batch = env.path.stem.replace("custom_report_", "")
            rows = _rows_or_note(env, "get_custom_report_page_list", batch, errors, disclosures)
            for rec in rows or []:
                traffic_rows.append({k: v for k, v in rec.items() if k in _TRAFFIC_KEEP})

    # 3) 产品分析（可选增强：文件缺失 = agent 未取，不算失败）
    analyze_rows: List[Dict[str, Any]] = []
    env = _load(raw_dir, "product_analyze")
    if env is not None:
        _scan(env.obj)
        analyze_rows = _rows_or_note(env, "get_product_analyze_list", "analyze", errors, disclosures) or []

    # 4) 标签产品（仅当配置了 labels.required_groups 或显式传了 --label-ids 才期望落盘）
    by_label_rows: List[Dict[str, Any]] = []
    required_groups = (cfg.get("labels") or {}).get("required_groups") or []
    if required_groups or label_ids:
        env = _load(raw_dir, "labels")
        if env is None:
            errors.append("标签产品落盘缺失（out/raw/labels.json）：标签覆盖校验降级，需人工核查。")
        else:
            _scan(env.obj)
            by_label_rows = _rows_or_note(env, "get_product_by_label", "labels", errors, disclosures) or []

    meta: Dict[str, Any] = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "shop_ids": shop_ids,
        "statuses_scanned": statuses,
        "window": {"short_days": short_days, "long_days": long_days,
                   "start": start.isoformat(), "end": end.isoformat(),
                   "timezone_note": "日期按服务器本地日；多站点 US/EU/JP 边界可能不同，需在报告注明"},
        "report_id": report_id,
        "report_configured": bool(report_id),
        "degraded": not bool(report_id),
        "demo_fingerprint": demo_fp,
        "disclosures": disclosures,
        "errors": errors,
    }
    if not report_id:
        meta["wide_table_fields_unavailable"] = list(WIDE_TABLE_ONLY_FIELDS)
    meta["raw_dir"] = str(raw_dir)

    return {
        "meta": meta,
        "online": online_rows,
        "traffic": traffic_rows,
        "analyze": analyze_rows,
        "by_label": by_label_rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Listing 健康体检 - 落盘校验与组装（零网络）")
    ap.add_argument("--shop-ids", default="",
                    help="逗号分隔；留空=不限店铺（仅记录范围，实际取数范围由 agent 落盘批次决定）")
    ap.add_argument("--report-id", default="",
                    help="自定义报表 ID；缺省读 _shared/config/reports.yaml → daily_ops.id")
    ap.add_argument("--online-status", default="",
                    help="逗号分隔；缺省读 config/listing.yaml（active/inActive/delete）")
    ap.add_argument("--label-ids", default="",
                    help="逗号分隔；提供或配置了 labels.required_groups 时期望 out/raw/labels.json")
    ap.add_argument("--short-days", type=int, default=0, help="覆盖近 N 天窗口（默认读配置）")
    ap.add_argument("--long-days", type=int, default=0, help="覆盖近 N 天窗口（默认读配置）")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--out", default="data.json", help="输出 data JSON 路径")
    args = ap.parse_args(argv)

    payload = run(args)
    lc.write_json(args.out, payload)

    meta = payload["meta"]
    print(f"✓ 在线产品 {len(payload['online'])} 条；流量宽表 {len(payload['traffic'])} 条"
          f" → {args.out}（来源：{meta['raw_dir']}）")
    if meta["demo_fingerprint"]:
        print(f"⚠️ 检测到演示环境指纹：{meta['demo_fingerprint']}（数据不可用于业务口径）")
    if meta["degraded"]:
        print("⚠️ 口径降级：未配置 report_id，宽表专属字段不可用 → "
              + "、".join(WIDE_TABLE_ONLY_FIELDS))
    for d in meta["disclosures"]:
        print(f"数据说明：{d}")
    for e in meta["errors"]:
        print(f"✗ {e}", file=sys.stderr)
    # 主数据源缺失（无 online 行且存在取数错误）→ 退出码 2，调用方可据此阻断后续诊断
    if not payload["online"] and meta["errors"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
