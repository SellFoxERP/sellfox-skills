#!/usr/bin/env python3
"""评论 VOC 归因 —— Step 1：用赛狐官方聚合指标定位问题 ASIN（v3：落盘校验+组装器，零网络）。

成本逻辑不变：这是**零外部依赖**的一步。先用便宜的官方聚合指标把范围收敛到少数 ASIN，
再去调（按次收费的）外部评论 MCP 取原文 —— 绝不先拉全量评论再筛。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：按 `references/workflow.md` 的**取数计划**调
    `mcp__Sellfox-MCP__{search,action}`，把每步完整信封落盘 `out/raw/`：
      custom_current.json    get_custom_report_page_list（当前窗口）→ rows
      custom_previous.json   get_custom_report_page_list（上一窗口）→ rows
      online_products.json   get_online_product_page_list（降级源） → rows
  * **本脚本 = 计算面**：校验落盘文件（存在性/status/空数据/演示指纹/coverage 截断），
    并组装出与旧版完全相同形状的 `signals.json` —— analyze_themes / render_report /
    build_optimization_brief 无需改动。

环比口径：对比 custom_current 与 custom_previous 两段落盘信封，逐 ASIN 相减
（报表按日期范围聚合，`rating` 等字段不是时间序列，只能两窗口相减）。
主键用 `shopId + asin`（同 ASIN 跨店是不同对象）。

无 `report_id`（或 custom 信封缺失）→ 降级用 online_products.json：
仅 rating/ratingCount，**无差评数、无环比基线** → 显式标注「无环比基线」，不报问题 ASIN。

用法::

    # 1) agent 先按 workflow.md 取数计划落盘 out/raw/*.json（脚本零 HTTP、零 Key）
    # 2) 校验 + 组装：
    python3 scripts/fetch_signals.py --days 7 --out out/signals.json
    python3 scripts/fetch_signals.py --report-id 123456 --out out/signals.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import voc_common as vc  # noqa: E402  (导入时即把 _shared/scripts 挂到 sys.path)

try:
    from sellfox_offline import (  # noqa: E402
        RawEnvelope,
        detect_demo_environment,
        is_no_data,
        load_envelope,
    )
except ImportError as exc:  # _shared 缺失：给出可操作的指引，而不是裸 ImportError
    raise SystemExit(
        f"无法加载 _shared/scripts/sellfox_offline.py（{exc}）。\n"
        "请确认安装技能时一并放置 _shared/，并在 config/paths.yaml 填入正确的 shared_root。"
    ) from exc


# 只保留需要的字段，避免 489 字段撑爆上下文。
_KEEP = {
    "asin", "parentAsin", "msku", "sku", "shopId", "marketplaceId", "title", "mainImage",
    "saleNum", "rating", "ratingCount", "lowReviewNum", "reviewNum", "reviewRate",
    "nowDate",
}

_KEEP_ONLINE = {
    "asin", "parentAsin", "sku", "shopId", "marketplaceId", "title",
    "mainImage", "saleNum", "rating", "ratingCount",
}


def _trim(rows: List[Dict[str, Any]], keep: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    keep = keep or _KEEP
    return [{k: v for k, v in r.items() if k in keep} for r in (rows or []) if isinstance(r, dict)]


def _window(end: _dt.date, days: int, offset: int) -> Tuple[_dt.date, _dt.date]:
    """返回 [start, end]；offset=0 当前窗口，offset=1 上一窗口。"""
    hi = end - _dt.timedelta(days=offset * days)
    lo = hi - _dt.timedelta(days=days - 1)
    return lo, hi


# --- 落盘读取与校验 ---------------------------------------------------------

def _load_raw(raw_dir: Path, name: str) -> Optional[RawEnvelope]:
    """读取一个落盘信封；不存在/损坏返回 None（损坏另记 errors 由调用方处理）。"""
    p = raw_dir / (name if name.endswith(".json") else f"{name}.json")
    if not p.exists():
        return None
    try:
        return load_envelope(p)
    except Exception:
        return None


def _cov_truncation(env: RawEnvelope, name: str) -> Optional[str]:
    """把网关 coverage 截断转成披露语句（截断必披露；文案含 render_report 识别标记）。"""
    cov = env.coverage or {}
    if not cov.get("truncated"):
        return None
    return (
        f"⚠️ out/raw/{name}.json 已达 max_rows 上限（coverage.truncated）："
        f"本次仅覆盖 {cov.get('fetched_count')} 行（服务端约 {cov.get('total_count')} 行），"
        f"其余未纳入统计。"
    )


def _check_envelope(
    env: RawEnvelope, name: str,
    errors: List[str], disclosures: List[str], warnings: List[str],
) -> None:
    """单个落盘信封的确定性检查：status / partial / 空数据 / coverage 截断。"""
    warnings.extend(str(w) for w in env.warnings)
    if env.status not in ("complete", "partial"):
        errors.append(
            f"out/raw/{name}.json status={env.status} error_code={env.error_code} "
            f"{env.message or ''}（该源按无数据处理，需人工核查）"
        )
    elif env.partial:
        disclosures.append(f"⚠️ out/raw/{name}.json status=partial：部分成功，可采信但必须在数据说明披露。")
    if is_no_data(env.data):
        warnings.append(
            f"⬜ out/raw/{name}.json 无数据（status={env.status or '未知'}）：该源为空，标「无数据」，不写 0。"
        )
    t = _cov_truncation(env, name)
    if t:
        disclosures.append(t)


def _index(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按 shopId+asin 建索引（同 ASIN 跨店是不同对象）。"""
    idx: Dict[str, Dict[str, Any]] = {}
    for r in rows or []:
        rid = vc.record_id(r)
        idx.setdefault(rid, r)
    return idx


def build_signals(
    current: List[Dict[str, Any]],
    previous: List[Dict[str, Any]],
    *,
    threshold_low: int,
    threshold_rating: float,
    min_rating_count: int,
    min_low_review_num: int,
    signals_enabled: List[str],
) -> List[Dict[str, Any]]:
    """逐 ASIN 计算环比并打标。返回候选问题 ASIN 列表（已附证据）。"""
    cur_idx = _index(current)
    prev_idx = _index(previous)
    out: List[Dict[str, Any]] = []

    for rid, cur in cur_idx.items():
        prev = prev_idx.get(rid) or {}
        asin = vc.asin_of(cur)
        if not asin:
            continue

        low_now = vc.to_float(cur.get("lowReviewNum"))
        low_prev = vc.to_float(prev.get("lowReviewNum"))
        rate_now = vc.to_float(cur.get("rating"))
        rate_prev = vc.to_float(prev.get("rating"))
        rr_now = vc.to_float(cur.get("reviewRate"))
        rr_prev = vc.to_float(prev.get("reviewRate"))
        rc_now = vc.to_float(cur.get("ratingCount"))

        flags: List[Dict[str, Any]] = []

        # 信号 1：差评数上升
        if "low_review_num_rise" in signals_enabled:
            low_delta = (low_now - low_prev) if (low_now is not None and low_prev is not None) else None
            if (low_delta is not None and low_delta > 0 and (low_now or 0) >= min_low_review_num) or (
                low_delta is None and low_now is not None and low_now >= min_low_review_num
            ):
                flags.append({
                    "signal": "low_review_num_rise",
                    "field": "lowReviewNum",
                    "source": "custom",
                    "now": low_now,
                    "prev": low_prev,
                    "threshold": f"环比上升≥{threshold_low}",
                })

        # 信号 2：评分下降（小样本不判）
        if "rating_drop" in signals_enabled:
            if rc_now is not None and rc_now >= min_rating_count and rate_now is not None and rate_prev is not None:
                drop = rate_prev - rate_now
                if drop >= threshold_rating:
                    flags.append({
                        "signal": "rating_drop",
                        "field": "rating",
                        "source": "custom",
                        "now": rate_now,
                        "prev": rate_prev,
                        "threshold": f"环比下降≥{threshold_rating}",
                    })

        # 信号 3：好评率下滑
        if "review_rate_drop" in signals_enabled and rr_now is not None and rr_prev is not None:
            if (rr_prev - rr_now) > 0:
                flags.append({
                    "signal": "review_rate_drop",
                    "field": "reviewRate",
                    "source": "custom",
                    "now": rr_now,
                    "prev": rr_prev,
                    "threshold": "环比下降",
                })

        if not flags:
            continue

        out.append({
            "asin": asin,
            "parent_asin": vc.key_of(cur.get("parentAsin")) or vc.key_of(prev.get("parentAsin")),
            "msku": vc.key_of(cur.get("msku")) or vc.key_of(prev.get("msku")),
            "sku": vc.key_of(cur.get("sku")) or vc.key_of(prev.get("sku")),
            "shop_id": vc.key_of(cur.get("shopId")) or vc.key_of(prev.get("shopId")),
            "marketplace_id": vc.key_of(cur.get("marketplaceId")) or vc.key_of(prev.get("marketplaceId")),
            "title": vc.key_of(cur.get("title")) or vc.key_of(prev.get("title")),
            "sale_num": vc.to_float(cur.get("saleNum")),
            "metrics": {
                "lowReviewNum": {"now": low_now, "prev": low_prev, "source": "custom"},
                "rating": {"now": rate_now, "prev": rate_prev, "source": "custom"},
                "reviewRate": {"now": rr_now, "prev": rr_prev, "source": "custom"},
                "ratingCount": {"now": rc_now, "source": "custom"},
            },
            "flags": flags,
            # 影响面排序权重：销量 × 信号强度（粗略，仅供排序，不是精确指标）
            "_impact": (vc.to_float(cur.get("saleNum")) or 0.0) * len(flags),
        })

    out.sort(key=lambda r: (r.get("_impact") or 0.0), reverse=True)
    return out


def run(args: argparse.Namespace) -> int:
    voc_cfg = vc.load_skill_config("voc")
    disc_cfg = voc_cfg.get("discovery") or {}
    th_after_sales = vc.load_shared_section("thresholds", "after_sales")

    end = _dt.date.fromisoformat(args.end_date) if args.end_date else _dt.date.today()
    days = int(args.days or (voc_cfg.get("stat_windows") or {}).get("short_days", 7))

    raw_dir = Path(args.raw_dir) if args.raw_dir else (vc.OUT_DIR / "raw")
    if not raw_dir.is_absolute():
        raw_dir = vc.SKILL_DIR / raw_dir

    # 阈值：本技能特有下界来自 config/voc.yaml → discovery；
    #      low_review 与 rating_drop_abs 的单一真源在 _shared/config/thresholds.yaml → after_sales。
    min_low = int(disc_cfg.get("min_low_review_num", 2))
    min_rating_count = int(disc_cfg.get("min_rating_count", 20))
    # rating_drop_abs 的单一真源是 _shared/config/thresholds.yaml → after_sales；
    # 若底座缺失，退化为兜底值并**显式标注来源**（不把兜底值混进真实数据）。
    if "rating_drop_abs" in th_after_sales:
        threshold_rating = float(th_after_sales["rating_drop_abs"])
        threshold_rating_source = "_shared/config/thresholds.yaml → after_sales.rating_drop_abs"
    else:
        threshold_rating = 0.2
        threshold_rating_source = "⚠️ 兜底默认值（阈值配置缺失，需人工核查）"
    threshold_low = 0
    signals_enabled = list(disc_cfg.get("signals") or ["low_review_num_rise", "rating_drop", "review_rate_drop"])
    max_asins = int(disc_cfg.get("max_flagged_asins", 20))
    report_key = disc_cfg.get("report_key", "daily_ops")

    notes_warnings: List[str] = []
    notes_manual: List[str] = []
    disclosures: List[str] = []
    errors: List[str] = []

    report_id = args.report_id or vc.resolve_report_id(report_key)
    degraded = "none"
    demo_fp: Optional[str] = None

    # --- 落盘目录前置检查（v3：取数是 agent 的责任）--------------------------
    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        raise SystemExit(
            f"❌ 落盘目录为空或不存在：{raw_dir}。\n"
            "   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」\n"
            "   调用 mcp__Sellfox-MCP__{search,action} 并把完整信封落盘到 out/raw/，再重跑本脚本。"
        )

    current: List[Dict[str, Any]] = []
    previous: List[Dict[str, Any]] = []

    def _scan_demo(envs: List[RawEnvelope]) -> None:
        nonlocal demo_fp
        for env in envs:
            if demo_fp:
                return
            fp = detect_demo_environment(env.obj)
            if fp:
                demo_fp = fp

    if report_id:
        # 主路径：宽表两窗口环比（取数计划步骤 1/2）
        cur_env = _load_raw(raw_dir, "custom_current")
        prev_env = _load_raw(raw_dir, "custom_previous")
        missing = [n for n, e in (("custom_current", cur_env), ("custom_previous", prev_env)) if e is None]
        if missing:
            raise SystemExit(
                f"❌ 缺少取数计划落盘：{', '.join(f'out/raw/{m}.json' for m in missing)}（步骤 1/2，"
                f"get_custom_report_page_list 两窗口）。report_id 已配置（{report_id}），"
                "请由 agent 按取数计划落盘后重跑本脚本。"
            )
        for name, env in (("custom_current", cur_env), ("custom_previous", prev_env)):
            _check_envelope(env, name, errors, disclosures, notes_warnings)
        _scan_demo([cur_env, prev_env])
        current = _trim(cur_env.rows("rows"))
        previous = _trim(prev_env.rows("rows"))
    else:
        # 降级路径：无 report_id → 只用 online 侧 rating/ratingCount（取数计划步骤 3）
        online_env = _load_raw(raw_dir, "online_products")
        if online_env is None:
            raise SystemExit(
                "❌ report_id 未配置，且缺少 out/raw/online_products.json（取数计划步骤 3，"
                "get_online_product_page_list）。请由 agent 按取数计划落盘后重跑本脚本，"
                "或由助手自动发现并验证适用报表后，再落盘宽表两窗口。"
            )
        _check_envelope(online_env, "online_products", errors, disclosures, notes_warnings)
        _scan_demo([online_env])
        current = _trim(online_env.rows("rows"), keep=_KEEP_ONLINE)
        previous = []
        degraded = "no_custom_report"
        notes_warnings.append(
            (voc_cfg.get("degradation") or {}).get("no_custom_report", {}).get(
                "banner", "⚠️ 口径降级：缺少自定义报表 report_id。"
            )
        )
        notes_manual.append(
            "lowReviewNum / reviewNum / reviewRate 需自定义报表；当前仅有 online 侧 rating/ratingCount，"
            "且无上一窗口基线（online 无历史），无法做环比 → 需人工核查或先配置 report_id。"
        )

    flagged = build_signals(
        current, previous,
        threshold_low=threshold_low,
        threshold_rating=threshold_rating,
        min_rating_count=min_rating_count,
        min_low_review_num=min_low,
        signals_enabled=signals_enabled,
    ) if report_id else []

    truncated = bool(flagged) and len(flagged) > max_asins
    if truncated:
        disclosures.append(f"⚠️ 命中 {len(flagged)} 个 ASIN，超过 max_flagged_asins={max_asins}，"
                           f"本次仅深挖 Top {max_asins}，其余未纳入（禁止静默截断）。")
        flagged = flagged[:max_asins]

    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "window": {
            "days": days,
            "end_date": end.isoformat(),
            "current": [d.isoformat() for d in _window(end, days, 0)],
            "previous": [d.isoformat() for d in _window(end, days, 1)],
        },
        "source": {
            "report_id": report_id,
            "report_key": report_key,
            "degraded": degraded,
            "fields": {
                "lowReviewNum": "get_custom_report_page_list",
                "reviewNum": "get_custom_report_page_list",
                "reviewRate": "get_custom_report_page_list",
                "rating": "get_custom_report_page_list / get_online_product_page_list",
                "ratingCount": "get_custom_report_page_list / get_online_product_page_list",
            },
        },
        "thresholds": {
            "rating_drop_abs": threshold_rating,
            "min_rating_count": min_rating_count,
            "min_low_review_num": min_low,
            "sources": {
                "rating_drop_abs": threshold_rating_source,
                "min_rating_count": "config/voc.yaml → discovery.min_rating_count",
                "min_low_review_num": "config/voc.yaml → discovery.min_low_review_num",
            },
        },
        "flagged": flagged,
        "observed": current,
        "counts": {"current_rows": len(current), "previous_rows": len(previous), "flagged": len(flagged)},
        "warnings": notes_warnings,
        "manual_checks": notes_manual,
        "disclosures": disclosures,
        "errors": errors,
        "demo_fingerprint": demo_fp,
    }

    vc.write_json(args.out, payload)

    # 人类可读摘要
    print(f"问题 ASIN：{len(flagged)} 个（窗口近 {days} 天，基线为前一窗口）")
    for r in flagged[:10]:
        sigs = ", ".join(f["signal"] for f in r.get("flags", []))
        print(f"  - {r['asin']}（{r.get('title') or '无标题'}）｜ {sigs}")
    if not flagged:
        if errors:
            print(f"  （未发现；但取数有 {len(errors)} 个错误，见 errors）")
        else:
            print("  （未发现达标问题 ASIN；若 report_id 未配置则为口径降级所致）")
    for w in payload["warnings"]:
        print(f"  {w}")
    for d in disclosures:
        print(f"  {d}")
    print(f"\n已写出 {args.out}（来源：{raw_dir}）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="校验落盘并组装问题 ASIN 信号（评论 VOC Step 1，零网络）")
    p.add_argument("--days", type=int, default=None, help="窗口天数（默认 config/voc.yaml→stat_windows.short_days）")
    p.add_argument("--end-date", default=None, help="窗口结束日 YYYY-MM-DD（默认今天）")
    p.add_argument("--report-id", default=None, help="自定义报表 ID（默认读 _shared/config/reports.yaml；须与 agent 落盘时所用一致）")
    p.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    p.add_argument("--out", default="out/signals.json", help="输出 JSON 路径（相对技能根）")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
