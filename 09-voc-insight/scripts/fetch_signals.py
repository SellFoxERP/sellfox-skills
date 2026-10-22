#!/usr/bin/env python3
"""评论 VOC 归因 —— Step 1：用赛狐官方聚合指标定位问题 ASIN（v3：落盘校验+组装器，零网络）。

成本逻辑不变：这是**零外部依赖**的一步。先用便宜的官方聚合指标把范围收敛到少数 ASIN，
再去调（按次收费的）外部评论 MCP 取原文 —— 绝不先拉全量评论再筛。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：按 `references/workflow.md` 的**取数计划**调
    `mcp__Sellfox-MCP__{sf_describe,sf_execute}`，把每步完整信封落盘 `out/raw/`：
      online_products.json   get_online_product_page_list（主路径，评分快照）→ rows
  * **本脚本 = 计算面**：校验落盘文件（存在性/status/空数据/演示指纹/coverage 截断），
    并组装出与旧版相同形状的 `signals.json` —— analyze_themes / render_report /
    build_optimization_brief 无需改动。

口径（2026-10-22 起，自定义报表已移出本技能）：
  * 主路径 = `get_online_product_page_list` **评分快照**（rating / ratingCount）。
  * online 侧**无差评数字段**（lowReviewNum / reviewNum / reviewRate 只在 489 宽表）、
    **无历史基线**（online 无历史），因此无法做评分环比 —— 这是固定口径，不是临时降级；
    宽表分析（差评数环比、好评率环比等）由 `custom-report-analysis` 技能承接。
  * 快照按 `shopId + asin` 去重（同 ASIN 跨店是不同对象），按销量降序给出
    低分 ASIN 候选清单（`rating` 低于阈值的行），供人工/外部评论源抽查。

用法::

    # 1) agent 先按 workflow.md 取数计划落盘 out/raw/*.json（脚本零 HTTP、零 Key）
    # 2) 校验 + 组装：
    python3 scripts/fetch_signals.py --days 7 --out out/signals.json
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


# 只保留需要的字段，避免 79 字段全量撑爆上下文。
_KEEP_ONLINE = {
    "asin", "parentAsin", "sku", "shopId", "marketplaceId", "title",
    "mainImage", "saleNum", "rating", "ratingCount",
}


def _trim(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: v for k, v in r.items() if k in _KEEP_ONLINE}
            for r in (rows or []) if isinstance(r, dict)]


def _window(end: _dt.date, days: int, offset: int) -> Tuple[_dt.date, _dt.date]:
    """返回 [start, end]；offset=0 当前窗口，offset=1 上一窗口（快照路径不消费上一窗口）。"""
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


#: 快照候选清单里「低分」的判定阈值（小样本不判，与旧环比口径的小样本保护对称）。
_RATING_LOW_DEFAULT = 4.0


def build_low_rating_rows(
    observed: List[Dict[str, Any]],
    *,
    rating_low: float,
    min_rating_count: int,
) -> List[Dict[str, Any]]:
    """从快照里挑出「评分偏低且样本足够」的 ASIN，按销量降序（影响面粗排）。

    这不是环比信号（online 无历史基线），只是快照筛选：帮人把注意力集中到
    最可能出问题的少数 ASIN 上。空数据/样本不足的行**不列入**，也不写 0。
    """
    out: List[Dict[str, Any]] = []
    for r in observed or []:
        asin = vc.asin_of(r)
        if not asin:
            continue
        rating = vc.to_float(r.get("rating"))
        count = vc.to_float(r.get("ratingCount"))
        if rating is None or count is None or count < min_rating_count:
            continue
        if rating > rating_low:
            continue
        out.append({
            "asin": asin,
            "parent_asin": vc.key_of(r.get("parentAsin")),
            "sku": vc.key_of(r.get("sku")),
            "shop_id": vc.key_of(r.get("shopId")),
            "marketplace_id": vc.key_of(r.get("marketplaceId")),
            "title": vc.key_of(r.get("title")),
            "sale_num": vc.to_float(r.get("saleNum")),
            "metrics": {
                "rating": {"now": rating, "prev": None, "source": "online"},
                "ratingCount": {"now": count, "source": "online"},
            },
            "flags": [{"signal": "low_rating_snapshot",
                       "field": "rating",
                       "source": "online",
                       "now": rating,
                       "prev": None,
                       "threshold": f"快照评分≤{rating_low} 且评分数≥{min_rating_count}"}],
            "_impact": (vc.to_float(r.get("saleNum")) or 0.0),
        })
    out.sort(key=lambda r: (r.get("_impact") or 0.0), reverse=True)
    return out


def run(args: argparse.Namespace) -> int:
    voc_cfg = vc.load_skill_config("voc")
    disc_cfg = voc_cfg.get("discovery") or {}

    end = _dt.date.fromisoformat(args.end_date) if args.end_date else _dt.date.today()
    days = int(args.days or (voc_cfg.get("stat_windows") or {}).get("short_days", 7))

    raw_dir = Path(args.raw_dir) if args.raw_dir else (vc.OUT_DIR / "raw")
    if not raw_dir.is_absolute():
        raw_dir = vc.SKILL_DIR / raw_dir

    # 快照「低分」阈值与小样本下界：单一真源在 config/voc.yaml → discovery。
    min_rating_count = int(disc_cfg.get("min_rating_count", 20))
    rating_low = float(disc_cfg.get("snapshot_rating_low", _RATING_LOW_DEFAULT))
    max_asins = int(disc_cfg.get("max_flagged_asins", 20))

    notes_warnings: List[str] = []
    notes_manual: List[str] = []
    disclosures: List[str] = []
    errors: List[str] = []
    error_details: List[Dict[str, Any]] = []
    demo_fp: Optional[str] = None

    # 固定口径披露（不是临时降级）：宽表信号由 custom-report-analysis 承接。
    # 横幅文案单一真源在 config/voc.yaml → degradation.no_custom_report.banner。
    handoff_banner = str(
        ((voc_cfg.get("degradation") or {}).get("no_custom_report") or {}).get("banner")
        or ("⚠️ 固定口径：自定义报表（489 宽表）已移出本技能，"
            "lowReviewNum / reviewNum / reviewRate 及其环比不可用；"
            "差评数与宽表分析由 custom-report-analysis 技能承接。")
    )
    notes_warnings.append(handoff_banner)
    notes_manual.append(
        "lowReviewNum / reviewNum / reviewRate：需自定义报表宽表 → 由 custom-report-analysis 技能承接；"
        "评分环比基线（online 无历史）无法构造，需人工比对历史或改用宽表。"
    )

    # --- 落盘目录前置检查（v3：取数是 agent 的责任）--------------------------
    if not raw_dir.exists() or not any(raw_dir.glob("*.json")):
        raise SystemExit(
            f"❌ 落盘目录为空或不存在：{raw_dir}。\n"
            "   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」\n"
            "   调用 mcp__Sellfox-MCP__{sf_describe,sf_execute} 并把完整信封落盘到 out/raw/，再重跑本脚本。"
        )

    current: List[Dict[str, Any]] = []

    # 主路径：online 评分快照（取数计划唯一必选步骤）
    online_env, online_problem = _load_raw(raw_dir, "online_products")
    if online_problem == "missing":
        raise SystemExit(
            "❌ 缺少 out/raw/online_products.json（取数计划唯一必选步骤，"
            "get_online_product_page_list）。请由 agent 按取数计划落盘后重跑本脚本。"
        )
    if online_problem is not None:
        raise SystemExit(
            f"❌ out/raw/online_products.json 存在但解析失败（损坏）—— {online_problem}。\n"
            "   这不是「未落盘」：请先检查文件完整性（是否被截断/半写/非法 JSON），\n"
            "   修复或由 agent 重新完整落盘后重跑。"
        )
    _check_envelope(online_env, "online_products", errors, disclosures, notes_warnings, error_details)
    fp = detect_demo_environment(online_env.obj)
    if fp:
        demo_fp = fp
    current = _trim(online_env.rows("rows"))

    # 残留宽表落盘（custom_current/custom_previous 等）不消费，只披露承接说明。
    stale_customs = sorted(
        [p.name for p in raw_dir.glob("custom_current*.json")]
        + [p.name for p in raw_dir.glob("custom_previous*.json")]
    )
    if stale_customs:
        disclosures.append(
            f"⚠️ 宽表落盘（{', '.join(stale_customs)}）已不在本技能取数范围，未消费；"
            "差评数/好评率等宽表信号由 custom-report-analysis 技能承接。"
        )

    flagged = build_low_rating_rows(
        current,
        rating_low=rating_low,
        min_rating_count=min_rating_count,
    )

    truncated = bool(flagged) and len(flagged) > max_asins
    if truncated:
        disclosures.append(f"⚠️ 快照低分 ASIN 命中 {len(flagged)} 个，超过 max_flagged_asins={max_asins}，"
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
            "degraded": "none",
            # 固定口径（2026-10-22）：宽表信号由 custom-report-analysis 承接
            "handoff_banner": handoff_banner,
            "fields": {
                "rating": "get_online_product_page_list（快照，无历史基线）",
                "ratingCount": "get_online_product_page_list（快照，无历史基线）",
                "lowReviewNum": "custom-report-analysis 技能承接（489 宽表）",
                "reviewNum": "custom-report-analysis 技能承接（489 宽表）",
                "reviewRate": "custom-report-analysis 技能承接（489 宽表）",
            },
        },
        "thresholds": {
            "snapshot_rating_low": rating_low,
            "min_rating_count": min_rating_count,
            "sources": {
                "snapshot_rating_low": "config/voc.yaml → discovery.snapshot_rating_low",
                "min_rating_count": "config/voc.yaml → discovery.min_rating_count",
            },
        },
        "flagged": flagged,
        "observed": current,
        "counts": {"current_rows": len(current), "previous_rows": 0, "flagged": len(flagged)},
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
    print(f"快照低分 ASIN 候选：{len(flagged)} 个（online 评分快照，无环比基线）")
    for r in flagged[:10]:
        print(f"  - {r['asin']}（{r.get('title') or '无标题'}）｜ rating {r['metrics']['rating']['now']}")
    if not flagged:
        if errors:
            print(f"  （未发现；但取数有 {len(errors)} 个错误，见 errors）")
        else:
            print("  （快照内无「评分低且样本足够」的 ASIN；评分环比与差评数由 custom-report-analysis 承接）")
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
    p.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    p.add_argument("--out", default="out/signals.json", help="输出 JSON 路径（相对技能根）")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
