#!/usr/bin/env python3
"""利润异常诊断 —— 归因引擎。

把两个宽表（msku 171 字段 + 自定义 489 字段）**收敛成 3~5 条归因**。

方法论（为什么这样设计）：
  1. 不做「字段排行榜」。171 个字段里绝大多数是别的字段的比率/占比，直接排序会得出一堆同源结论。
     所以先把因子收敛成**互相独立、金额可加**的十来条（见 config/diagnosis.yaml → attribution.factors）。
  2. 每条因子只算一个数：**它对毛利跌幅的贡献额**（contribution_to_drop）。贡献额可加，
     因此能回答「毛利少赚的这 8 万，分别是谁吃掉的」—— 这才是归因，而不是罗列。
  3. 符号按配置显式声明（profit_sign），因为赛狐报表里费用字段的存储符号不统一
     （msku 侧与自定义报表侧不同；线上实测 msku 侧费用为正数）。靠猜必反向。
     ⚠️ profit_sign 只是**缺省假设**：归因前对每个费用类因子取基期行的中位数值做
     运行时符号校准（`storage_sign_calibration`），实测存储符号与假设不符时按实测符号
     修正贡献方向并在报告披露；收入类因子（收入恒正）不校准。

边界：本模块只做**算术归因**，不做因果推断。因子间相关（如销售额跌会连带佣金跌）会在
输出里提示，交由人判断。

依赖：仅标准库 + 上层 fetch_profit。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # 作为脚本直接运行时（python scripts/diagnose.py）兜底
    from .fetch_profit import canonical_for, source_tool_for
except ImportError:  # pragma: no cover
    from fetch_profit import canonical_for, source_tool_for  # type: ignore


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class FactorResult:
    """单条归因因子。"""

    key: str
    label: str
    tool: str                # ⚠️ 所属工具（归因表必须标注）
    raw_field: str           # 原始 API 字段名
    canonical: str           # 规范化键
    current: Optional[float]
    baseline: Optional[float]
    contribution: Optional[float]   # 对毛利跌幅的贡献额（正 = 拉住毛利）
    share_pct: Optional[float]      # 贡献额 / 毛利跌幅
    available: bool                 # 数据是否可取（字段在两表中都拿不到 → False）
    reason: str = ""                # 不可用 / 需人工核查的原因
    hint: str = ""
    sign_calibrated: bool = False   # 运行时符号校准触发（实测存储符号与配置假设不符）
    sign_note: str = ""             # 校准披露文案（进报告归因表脚注）

    def to_row(self) -> str:
        cur = "无数据" if self.current is None else f"{self.current:,.2f}"
        base = "无数据" if self.baseline is None else f"{self.baseline:,.2f}"
        contrib = "—" if self.contribution is None else f"{self.contribution:+,.2f}"
        share = "—" if self.share_pct is None else f"{self.share_pct:.0f}%"
        return f"| {self.label} | {self.tool} | `{self.raw_field}` | {base} | {cur} | {contrib} | {share} |"


@dataclass
class MskuDiagnosis:
    msku: str
    asin: str
    title: str
    current_gross_profit: Optional[float]
    baseline_gross_profit: Optional[float]
    gross_profit_delta: Optional[float]      # 本期 − 基期（负 = 毛利下滑）
    gross_profit_drop: Optional[float]       # 跌幅绝对值（基期 − 本期，正 = 下滑额）
    gross_profit_rate_current: Optional[float]
    gross_profit_rate_baseline: Optional[float]
    factors: List[FactorResult] = field(default_factory=list)
    driver_factors: List[FactorResult] = field(default_factory=list)   # 通过门槛的 3~5 条
    unavailable_factors: List[FactorResult] = field(default_factory=list)  # 需人工核查
    data_gaps: List[str] = field(default_factory=list)
    cross_check: List[str] = field(default_factory=list)
    severe: bool = False

    @property
    def has_anomaly(self) -> bool:
        return self.gross_profit_drop is not None and self.gross_profit_drop > 0


@dataclass
class DiagnosisReport:
    mskus: List[MskuDiagnosis] = field(default_factory=list)
    totals: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 取值
# ---------------------------------------------------------------------------
def _by_msku(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = (row.get("msku") or "").strip()
        if key:
            out[key] = row
    return out


def _by_window(rows: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    cur = _by_msku([r for r in rows if r.get("_window") == "current"])
    base = _by_msku([r for r in rows if r.get("_window") == "baseline"])
    return cur, base


def _delta(cur: Optional[float], base: Optional[float]) -> Optional[float]:
    if cur is None or base is None:
        return None
    return cur - base


# ---------------------------------------------------------------------------
# 运行时符号校准（评测线上问题 N1：msku 报表费用线上实测为正数存储）
# ---------------------------------------------------------------------------
#: 收入类因子的规范字段（值越大对利润越有利、恒为正号存储）——不做符号校准（收入恒正）。
#: reimbursementAmount 是「应收回的钱」，语义同收入（金额为正、越大越好）。
REVENUE_LIKE_FIELDS = frozenset({"product_sales", "revenue", "reimbursement_amount"})


def _median(values: Sequence[float]) -> Optional[float]:
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def storage_sign_calibration(
    msku_rows: Sequence[Dict[str, Any]],
    custom_rows: Sequence[Dict[str, Any]],
    factors_cfg: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """对每个**费用类因子**做运行时存储符号判定。

    配置 profit_sign 的符号假设（如「msku 报表费用为负数存储」）只是缺省：线上实测
    ``get_profit_report_by_msku`` 的费用字段全为**正数**（sales − Σ费用 ≈ grossProfit 成立），
    符号错则费用因子归因整体反号且**不报错**。这里对每个费用类因子取**基期行**（跨全部
    MSKU）的中位数值判断实际存储符号；费用语义下：正号存储 → 有效 profit_sign = -1、
    负号存储 → +1。与配置推导的假设不符时返回修正项（含中位值，供披露），归因按实测
    符号计算并在报告归因表脚注披露。收入类因子（恒正存储）不校准。
    """
    base_msku = [r for r in msku_rows if r.get("_window") == "baseline"]
    base_custom = [r for r in custom_rows if r.get("_window") == "baseline"]
    out: Dict[str, Dict[str, Any]] = {}
    for f in factors_cfg:
        raw = str(f.get("field", ""))
        canon = canonical_for(raw)
        if canon in REVENUE_LIKE_FIELDS:
            continue
        tool = str(f.get("tool") or source_tool_for(canon))
        rows = base_msku if tool == "get_profit_report_by_msku" else base_custom
        vals = [r.get(canon) for r in rows if isinstance(r.get(canon), (int, float))]
        median = _median(vals)
        if median is None or median == 0:
            continue  # 无基期样本 / 中位值恰为 0：无法判定存储符号，保持配置缺省
        observed = 1 if median > 0 else -1
        config_sign = float(f.get("profit_sign", 1))
        assumed = -1 if config_sign > 0 else 1  # 配置 profit_sign 推导的（费用类）存储符号假设
        if observed == assumed:
            continue  # 实测与假设一致：无需校准
        out[canon] = {
            "median": median,
            "observed": observed,
            "config_sign": config_sign,
            "effective_sign": float(-observed),  # 费用语义：正号存储 → 变大=不利 → -1
        }
    return out


# ---------------------------------------------------------------------------
# 归因计算
# ---------------------------------------------------------------------------
def attribute_one(
    msku: str,
    msku_cur: Optional[Dict[str, Any]],
    msku_base: Optional[Dict[str, Any]],
    custom_cur: Optional[Dict[str, Any]],
    custom_base: Optional[Dict[str, Any]],
    *,
    factors_cfg: Sequence[Dict[str, Any]],
    attribution_cfg: Dict[str, Any],
    sign_calibration: Optional[Dict[str, Dict[str, Any]]] = None,
) -> MskuDiagnosis:
    """对单个 MSKU 做归因。缺失的字段标「需人工核查」，不编造、不当 0。"""
    diag = MskuDiagnosis(
        msku=msku,
        asin=(msku_cur or msku_base or {}).get("asin") or "",
        title=(msku_cur or msku_base or {}).get("title") or "",
        current_gross_profit=None,
        baseline_gross_profit=None,
        gross_profit_delta=None,
        gross_profit_drop=None,
        gross_profit_rate_current=None,
        gross_profit_rate_baseline=None,
    )

    # 毛利：msku 报表口径（primary）。缺则标需人工核查，不用自定义报表的 profitPrice 顶替。
    if msku_cur and msku_cur.get("gross_profit") is not None:
        diag.current_gross_profit = msku_cur.get("gross_profit")
        diag.gross_profit_rate_current = msku_cur.get("gross_profit_rate")
    else:
        diag.data_gaps.append("本期毛利（grossProfit，可人工核查：需 get_profit_report_by_msku）")
    if msku_base and msku_base.get("gross_profit") is not None:
        diag.baseline_gross_profit = msku_base.get("gross_profit")
        diag.gross_profit_rate_baseline = msku_base.get("gross_profit_rate")
    else:
        diag.data_gaps.append("基期毛利（grossProfit，可人工核查：需 get_profit_report_by_msku）")

    diag.gross_profit_delta = _delta(diag.current_gross_profit, diag.baseline_gross_profit)
    if diag.gross_profit_delta is not None:
        diag.gross_profit_drop = -diag.gross_profit_delta  # 跌幅取正

    # 每条因子：找到记录中的值（来源工具不同，去各自的记录里取）
    for f in factors_cfg:
        raw = str(f.get("field", ""))
        canon = canonical_for(raw)
        tool = str(f.get("tool") or source_tool_for(canon))
        sign = float(f.get("profit_sign", 1))
        cur_row = msku_cur if tool == "get_profit_report_by_msku" else custom_cur
        base_row = msku_base if tool == "get_profit_report_by_msku" else custom_base

        cur_val = cur_row.get(canon) if cur_row else None
        base_val = base_row.get(canon) if base_row else None

        result = FactorResult(
            key=str(f.get("key", raw)),
            label=str(f.get("label", raw)),
            tool=tool,
            raw_field=raw,
            canonical=canon,
            current=cur_val,
            baseline=base_val,
            contribution=None,
            share_pct=None,
            available=cur_val is not None and base_val is not None,
            hint=str(f.get("hint", "")),
        )

        if not result.available:
            missing_side = []
            if cur_val is None:
                missing_side.append("本期")
            if base_val is None:
                missing_side.append("基期")
            result.reason = f"{'/'.join(missing_side)}无 {raw}（{tool}）"
            diag.unavailable_factors.append(result)
        else:
            # 贡献额 = sign × (基期 − 本期)：正 = 该因子把毛利往下拉。
            cal = (sign_calibration or {}).get(canon)
            if cal:
                # 运行时符号校准：实测存储符号与配置假设不符 → 按实测符号归因并披露。
                sign = float(cal["effective_sign"])
                storage = "正" if cal["observed"] > 0 else "负"
                result.sign_calibrated = True
                result.sign_note = (
                    f"因子「{result.label}」（`{raw}`）实测为{storage}号存储"
                    f"（基期中位值 {cal['median']:,.2f}），与配置假设不符，"
                    f"已按{storage}号口径归因（profit_sign {cal['config_sign']:+.0f} → {sign:+.0f}）。"
                )
            result.contribution = sign * (base_val - cur_val)
            if diag.gross_profit_drop not in (None, 0):
                result.share_pct = result.contribution / diag.gross_profit_drop * 100.0
            diag.factors.append(result)

    # 门槛筛选 → 3~5 条归因（按贡献额绝对值降序）
    min_share = float(attribution_cfg.get("min_share_pct", 20))
    min_amount = float(attribution_cfg.get("min_amount", 50))
    max_items = int(attribution_cfg.get("max_items", 5))
    candidates = [
        r for r in diag.factors
        if r.contribution is not None and r.contribution > 0     # 只列拉住毛利的因子
        and (
            (r.share_pct is not None and r.share_pct >= min_share)
            or abs(r.contribution) >= min_amount
        )
    ]
    candidates.sort(key=lambda r: abs(r.contribution or 0), reverse=True)
    diag.driver_factors = candidates[:max_items]

    # 交叉核对：汇率（msku 报表自带 rate / currencyList，跨币种结论受其影响）
    current_rate = (msku_cur or {}).get("rate")
    base_rate = (msku_base or {}).get("rate")
    if current_rate is not None and base_rate is not None and abs((current_rate or 0) - (base_rate or 0)) > 1e-9:
        diag.cross_check.append(
            f"汇率 rate 由 {base_rate} 变为 {current_rate}（msku 报表自带汇率），"
            "跨币种结论受此影响，务必标注。"
        )
    return diag


def _is_severe(diag: MskuDiagnosis, *, drop_pct_vs_30d: float, rate_min: float) -> bool:
    """严重度：毛利率跌破阈值（thresholds.profit.gross_profit_rate_min）或毛利环比跌幅超阈值。

    两个阈值都来自配置，脚本不内置业务数值（铁律 9）。
    """
    if diag.gross_profit_rate_current is not None and diag.gross_profit_rate_current < rate_min:
        return True
    if diag.baseline_gross_profit and diag.baseline_gross_profit > 0 and diag.gross_profit_drop is not None:
        drop_pct = diag.gross_profit_drop / diag.baseline_gross_profit * 100.0
        return drop_pct >= drop_pct_vs_30d
    return False


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------
def diagnose(
    *,
    msku_rows: Sequence[Dict[str, Any]],
    custom_rows: Sequence[Dict[str, Any]],
    skill_cfg: Dict[str, Any],
    profit_thresholds: Optional[Dict[str, Any]] = None,
) -> DiagnosisReport:
    """全量归因。msku_rows / custom_rows 都带 _window 标记（current / baseline）。"""
    factors_cfg = (skill_cfg.get("attribution") or {}).get("factors") or []
    attribution_cfg = skill_cfg.get("attribution") or {}
    profit_th = profit_thresholds or {}
    drop_pct_vs_30d = float(profit_th.get("drop_pct_vs_30d", 25))
    rate_min = float(profit_th.get("gross_profit_rate_min", 0))
    min_drop = float(attribution_cfg.get("min_gross_profit_drop", 0))

    result = DiagnosisReport()
    if not factors_cfg:
        result.notes.append(
            "⚠️ config/diagnosis.yaml 未定义 attribution.factors，无法归因。请先补齐因子表。"
        )
        return result

    msku_cur, msku_base = _by_window(msku_rows)
    custom_cur, custom_base = _by_window(custom_rows)

    # 运行时符号校准（评测线上问题 N1）：费用类因子按基期行中位值判定实际存储符号，
    # 与配置 profit_sign 推导的假设不符时修正贡献方向并在报告披露（收入类因子不校准）。
    sign_calibration = storage_sign_calibration(msku_rows, custom_rows, factors_cfg)

    # 以「本期有数据」的 MSKU 为主体；基期缺失也保留（标注需人工核查）
    keys = list(msku_cur.keys()) or list(msku_base.keys())
    if not keys:
        result.notes.append("⚠️ 未取回任何 MSKU 行（可能是无数据，而非失败）。")
        return result

    for msku in keys:
        diag = attribute_one(
            msku,
            msku_cur.get(msku), msku_base.get(msku),
            custom_cur.get(msku), custom_base.get(msku),
            factors_cfg=factors_cfg,
            attribution_cfg=attribution_cfg,
            sign_calibration=sign_calibration,
        )
        diag.severe = _is_severe(diag, drop_pct_vs_30d=drop_pct_vs_30d, rate_min=rate_min)
        if min_drop and (diag.gross_profit_drop is None or diag.gross_profit_drop < min_drop):
            # 跌幅太小 → 不进入异常清单（噪声），但保留记录供核对
            diag.driver_factors = []
        result.mskus.append(diag)

    # 排序：严重且归因明确的排前面；再按贡献额降序
    result.mskus.sort(
        key=lambda d: (
            not (d.has_anomaly and d.driver_factors),
            -(d.gross_profit_drop or 0),
        )
    )

    # 汇总
    anomalies = [d for d in result.mskus if d.has_anomaly and d.driver_factors]
    result.totals = {
        "msku_count": len(result.mskus),
        "anomaly_count": len(anomalies),
        "total_drop": sum(d.gross_profit_drop or 0 for d in anomalies),
        "severe_count": sum(1 for d in result.mskus if d.severe),
    }
    return result
