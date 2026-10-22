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

def _load_raw(raw_dir: Path, name: str) -> Tuple[Optional[RawEnvelope], Optional[str]]:
    """读取一个落盘信封。返回 ``(信封, 问题)``：正常 = ``(env, None)``。

    拿不到信封的两种原因**必须区分**（排查方向相反）：
    - ``"missing"``：文件不存在 → agent 未按取数计划落盘；
    - ``"corrupt（…）"``：文件存在但解析失败（损坏，如截断/半写/非法 JSON）
      → 先查文件完整性，不要当成「没取数」盲目重取。
    """
    p = raw_dir / (name if name.endswith(".json") else f"{name}.json")
    if not p.exists():
        return None, "missing"
    try:
        return load_envelope(p), None
    except Exception as exc:
        return None, f"corrupt（{exc}）"


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


#: 落盘步骤 → 网关工具（error_details 里逐条标注，报告数据说明直接消费）。
_STEP_TOOLS = {
    "custom_current": "get_custom_report_page_list",
    "custom_previous": "get_custom_report_page_list",
    "online_products": "get_online_product_page_list",
}


def _check_envelope(
    env: RawEnvelope, name: str,
    errors: List[str], disclosures: List[str], warnings: List[str],
    error_details: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """单个落盘信封的确定性检查：status / partial / 空数据 / coverage 截断。

    failed 时除了人类可读的 errors 字符串，还记结构化 ``error_details``
    （步骤/工具/error_code/是否可重试），供 render_report 在「数据说明」逐条渲染 ——
    error_code 不允许只存在于此处而报告不可见。
    """
    warnings.extend(str(w) for w in env.warnings)
    if env.status not in ("complete", "partial"):
        errors.append(
            f"out/raw/{name}.json status={env.status} error_code={env.error_code} "
            f"{env.message or ''}（该源按无数据处理，需人工核查）"
        )
        if error_details is not None:
            error_details.append({
                "step": f"out/raw/{name}.json",
                "tool": _STEP_TOOLS.get(name, "未知"),
                "status": env.status,
                "error_code": env.error_code,
                "message": env.message,
                "retryable": env.obj.get("retryable"),
            })
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

        # 信号 3：好评率下滑（小样本不判 —— 与 rating_drop 对称，复用同一 min_rating_count 下界）
        if "review_rate_drop" in signals_enabled and rr_now is not None and rr_prev is not None:
            if rc_now is not None and rc_now >= min_rating_count and (rr_prev - rr_now) > 0:
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


#: 无 report_id 降级时的人工核查文案（三态，与 config/voc.yaml → degradation 的 banner 对应）。
#: 关键纪律：上游失败（发现失败 / 候选验证被拒）不得写成「先配置 report_id」。
_NO_REPORT_MANUAL = {
    "not_configured": (
        "lowReviewNum / reviewNum / reviewRate 需自定义报表；当前仅有 online 侧 rating/ratingCount，"
        "且无上一窗口基线（online 无历史），无法做环比 → 需人工核查或先配置 report_id。"
    ),
    "discovery_failed": (
        "自定义报表自动发现失败（error_code：{error_codes}）：lowReviewNum / reviewNum / reviewRate 不可用。"
        "这不是「未配置 report_id」—— 需人工核对发现链路（报表目录接口）后重跑发现，补配置解决不了。"
    ),
    "candidate_verification_rejected": (
        "报表候选已发现，但候选验证读取被上游拒绝（error_code：{error_codes}），report_id 未绑定："
        "需人工在赛狐网页端核对报表可用性 —— 不是未配置 report_id，重新配置无法解决。"
    ),
}


def _probe_no_report_reason(raw_dir: Path) -> Tuple[str, List[str]]:
    """无 report_id 时的降级原因归因（**只认落盘证据**，不臆测）。

    三态（与 ``config/voc.yaml → degradation`` 文案一一对应）：
    - ``candidate_verification_rejected``：发现已完成，custom_* / report_validate* 等
      宽表信封 status=failed 且带 error_code → 候选验证读取被上游拒，report_id 因此未绑定；
    - ``discovery_failed``：*index* 信封 status=failed → 报表自动发现本身失败；
    - ``not_configured``：无任何失败证据 → 真·未配置（现行文案）。
    """
    verify_codes: List[str] = []
    discovery_codes: List[str] = []
    for p in sorted(raw_dir.glob("*.json")):
        if p.name == "online_products.json":
            continue
        try:
            env = load_envelope(p)
        except Exception:
            continue  # 损坏文件不构成失败证据（主路径另有 corrupt 拦截）
        if env.status in ("complete", "partial") or not env.error_code:
            continue
        if "index" in p.name.lower():
            discovery_codes.append(str(env.error_code))
        else:
            verify_codes.append(str(env.error_code))
    if verify_codes:
        return "candidate_verification_rejected", verify_codes
    if discovery_codes:
        return "discovery_failed", discovery_codes
    return "not_configured", []


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
    error_details: List[Dict[str, Any]] = []
    degraded_reason: str = "none"
    degraded_error_codes: List[str] = []
    degraded_banner: Optional[str] = None

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

    def _scan_demo(envs: List[Optional[RawEnvelope]]) -> None:
        nonlocal demo_fp
        for env in envs:
            if demo_fp:
                return
            if env is None:
                continue
            fp = detect_demo_environment(env.obj)
            if fp:
                demo_fp = fp

    # 报表发现信封（index_discovery.json，get_custom_report_index_page_list）也纳入演示扫描
    # （eval3-2026-10-22 N2'）：上游可能只在报表名/指标名留「演示」标识（reportName 含
    # 「演示」即命中指纹）；发现信封缺失/损坏不阻断（发现不是必经步骤，只影响指纹覆盖面）。
    discovery_env, _discovery_problem = _load_raw(raw_dir, "index_discovery")

    if report_id:
        # 主路径：宽表两窗口环比（取数计划步骤 1/2）
        cur_env, cur_problem = _load_raw(raw_dir, "custom_current")
        prev_env, prev_problem = _load_raw(raw_dir, "custom_previous")
        problems = (("custom_current", cur_problem), ("custom_previous", prev_problem))
        missing = [n for n, pr in problems if pr == "missing"]
        corrupt = [n for n, pr in problems if pr is not None and pr.startswith("corrupt")]
        if missing:
            raise SystemExit(
                f"❌ 缺少取数计划落盘：{', '.join(f'out/raw/{m}.json' for m in missing)}（步骤 1/2，"
                f"get_custom_report_page_list 两窗口）。report_id 已配置（{report_id}），"
                "请由 agent 按取数计划落盘后重跑本脚本。"
            )
        if corrupt:
            # 与「文件缺失」区分：文件在但读不了 → 先查完整性，不是重走取数计划就能解决
            raise SystemExit(
                f"❌ 落盘文件存在但解析失败（损坏）：{', '.join(f'out/raw/{m}.json' for m in corrupt)} —— "
                + "；".join(pr for _, pr in problems if pr and pr.startswith("corrupt")) + "。\n"
                "   这不是「未落盘」：请先检查文件完整性（是否被截断/半写/非法 JSON），\n"
                "   修复或由 agent 重新完整落盘后重跑；不要盲目按「缺步骤」重取数。"
            )
        for name, env in (("custom_current", cur_env), ("custom_previous", prev_env)):
            _check_envelope(env, name, errors, disclosures, notes_warnings, error_details)
        _scan_demo([cur_env, prev_env, discovery_env])
        current = _trim(cur_env.rows("rows"))
        previous = _trim(prev_env.rows("rows"))
    else:
        # 降级路径：无 report_id → 只用 online 侧 rating/ratingCount（取数计划步骤 3）
        online_env, online_problem = _load_raw(raw_dir, "online_products")
        if online_problem == "missing":
            raise SystemExit(
                "❌ report_id 未配置，且缺少 out/raw/online_products.json（取数计划步骤 3，"
                "get_online_product_page_list）。请由 agent 按取数计划落盘后重跑本脚本，"
                "或由助手自动发现并验证适用报表后，再落盘宽表两窗口。"
            )
        if online_problem is not None:
            raise SystemExit(
                f"❌ out/raw/online_products.json 存在但解析失败（损坏）—— {online_problem}。\n"
                "   这不是「未落盘」：请先检查文件完整性（是否被截断/半写/非法 JSON），\n"
                "   修复或由 agent 重新完整落盘后重跑。"
            )
        _check_envelope(online_env, "online_products", errors, disclosures, notes_warnings, error_details)
        _scan_demo([online_env, discovery_env])
        current = _trim(online_env.rows("rows"), keep=_KEEP_ONLINE)
        previous = []
        degraded = "no_custom_report"
        # 降级原因三态归因（只认落盘证据）：「未配置」/「发现失败」/「候选验证被拒」文案不同，
        # 不能把上游故障误归因为用户没配置 report_id（见 config/voc.yaml → degradation）。
        degraded_reason, degraded_error_codes = _probe_no_report_reason(raw_dir)
        degraded_error_codes = list(dict.fromkeys(degraded_error_codes))  # 同码去重，保序
        deg_cfg = voc_cfg.get("degradation") or {}
        reason_key = {"not_configured": "no_custom_report"}.get(degraded_reason, degraded_reason)
        codes_text = "、".join(dict.fromkeys(degraded_error_codes)) or "未知"
        degraded_banner = str(
            (deg_cfg.get(reason_key) or {}).get("banner")
            or deg_cfg.get("no_custom_report", {}).get("banner")
            or "⚠️ 口径降级：缺少自定义报表 report_id。"
        ).replace("{error_codes}", codes_text)
        notes_warnings.append(degraded_banner)
        notes_manual.append(
            _NO_REPORT_MANUAL.get(degraded_reason, _NO_REPORT_MANUAL["not_configured"])
            .replace("{error_codes}", codes_text)
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
            # 降级原因三态（degraded=no_custom_report 时有效）：渲染层据此给如实文案
            "degraded_reason": degraded_reason,
            "degraded_error_codes": degraded_error_codes,
            "degraded_banner": degraded_banner,
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
        # 结构化取数失败（步骤/工具/error_code/是否可重试），render_report 在「数据说明」逐条渲染
        "error_details": error_details,
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
