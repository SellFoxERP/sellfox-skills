#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""时间窗计算（周报 / 月报）。

为什么单独一个模块
------------------
「本周」「上周」「上月同期」「去年同期」的口径必须先固定再取数，否则同一份报告里
不同指标的基线各不相同，趋势会失真。业务日边界以赛狐 ``bizDay``（yyyyMMdd）为准。

约定（显式定义，避免歧义）
--------------------------
* 周：默认周一为起始（``config/weekly.yaml → period.week_start``）。
      默认取「最近一个**完整**自然周」，即 anchor 所在周之前的那一周；
      若 anchor 恰为周日且属于完整周，也可用 ``--include-current`` 纳入本周。
* 月：默认取「最近一个**完整**自然月」。
* 上月同期：当前窗口整体前移一个自然月（日期同号，超出月份天数则取该月最后一天）。
* 同比：当前窗口整体前移一年（同年闰日问题取 2/28 兜底）。

产物
----
``PeriodPlan`` 字典，每个窗口含 ``start`` / ``end``（date）及派生格式串：
    yyyymmdd  → 利润报表用
    iso       → 自定义报表用（yyyy-MM-dd）
"""
from __future__ import annotations

import calendar
import datetime as _dt
import json
from typing import Any, Dict, Optional, Tuple

Date = _dt.date
DATE_FMT_PROFIT = "%Y%m%d"
DATE_FMT_ISO = "%Y-%m-%d"


def _shift_months(d: Date, months: int) -> Date:
    """整体前移/后移 N 个自然月，日期同号（超出则钳到该月最后一天）。"""
    total = (d.year * 12 + (d.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return Date(year, month, min(d.day, last))


def _shift_years(d: Date, years: int) -> Date:
    """整体前移/后移 N 年（2/29 → 2/28 兜底）。"""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, month=2, day=28)


def _fmt(start: Date, end: Date) -> Dict[str, str]:
    return {
        "start_profit": start.strftime(DATE_FMT_PROFIT),
        "end_profit": end.strftime(DATE_FMT_PROFIT),
        "start_iso": start.strftime(DATE_FMT_ISO),
        "end_iso": end.strftime(DATE_FMT_ISO),
    }


def current_week(anchor: Date, week_start: str = "monday", include_current: bool = False) -> Tuple[Date, Date]:
    """返回 anchor 对应的最近一个完整自然周 (start, end)。

    include_current=True 时，若 anchor 所在周已到周末则取该周，否则仍回退到上一完整周。
    """
    weekday = anchor.weekday()                      # Monday=0
    if week_start == "sunday":
        offset = (weekday + 1) % 7
    else:
        offset = weekday
    this_monday = anchor - _dt.timedelta(days=offset)
    this_sunday = this_monday + _dt.timedelta(days=6)
    if include_current and anchor >= this_sunday:
        return this_monday, this_sunday
    prev_monday = this_monday - _dt.timedelta(days=7)
    return prev_monday, prev_monday + _dt.timedelta(days=6)


def current_month(anchor: Date) -> Tuple[Date, Date]:
    """返回 anchor 对应的最近一个完整自然月 (start, end)。"""
    first_this = anchor.replace(day=1)
    last_prev = first_this - _dt.timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return first_prev, last_prev


def month_to_date(anchor: Date) -> Tuple[Date, Date]:
    """当前自然月 1 号 → anchor（用于「本月累计 vs 月目标」达成率）。"""
    return anchor.replace(day=1), anchor


def build_plan(
    period: str = "week",
    anchor: Optional[str] = None,
    week_start: str = "monday",
    include_current: bool = False,
    yoy: bool = True,
) -> Dict[str, Any]:
    """构造完整的时间窗计划。

    :param period: ``week`` 或 ``month``。
    :param anchor: 参考日期 ``YYYY-MM-DD``；缺省取「今天」。
    :param yoy: 是否计算同比窗口。
    """
    if anchor:
        ref = _dt.datetime.strptime(anchor, DATE_FMT_ISO).date()
    else:
        ref = _dt.date.today()

    windows: Dict[str, Any] = {}
    if period == "month":
        cur_start, cur_end = current_month(ref)
        prev_start = _shift_months(cur_start, -1)
        prev_end = _shift_months(cur_end, -1)
    else:
        cur_start, cur_end = current_week(ref, week_start, include_current)
        prev_start = cur_start - _dt.timedelta(days=(cur_end - cur_start).days + 1)
        prev_end = cur_start - _dt.timedelta(days=1)

    windows["current"] = {"start": cur_start, "end": cur_end, **_fmt(cur_start, cur_end)}
    windows["prev"] = {"start": prev_start, "end": prev_end, **_fmt(prev_start, prev_end)}

    # 上月同期：整体前移一个自然月
    pms = _shift_months(cur_start, -1)
    pme = _shift_months(cur_end, -1)
    windows["prev_month_same_period"] = {"start": pms, "end": pme, **_fmt(pms, pme)}

    if yoy:
        ys, ye = _shift_years(cur_start, -1), _shift_years(cur_end, -1)
        windows["yoy"] = {"start": ys, "end": ye, **_fmt(ys, ye)}

    mts, mte = month_to_date(ref)
    windows["month_to_date"] = {"start": mts, "end": mte, **_fmt(mts, mte)}

    return {
        "period": period,
        "anchor": ref.strftime(DATE_FMT_ISO),
        "week_start": week_start,
        "timezone_mode": "bizday",         # 以赛狐 bizDay 为准
        "windows": windows,
        "labels": {
            "current": "本期",
            "prev": "上期",
            "prev_month_same_period": "上月同期",
            "yoy": "去年同期",
            "month_to_date": "本月累计",
        },
    }


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="计算周报/月报时间窗")
    ap.add_argument("--period", choices=["week", "month"], default="week")
    ap.add_argument("--anchor", default=None, help="参考日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--week-start", choices=["monday", "sunday"], default="monday")
    ap.add_argument("--include-current", action="store_true", help="纳入 anchor 所在完整周")
    ap.add_argument("--no-yoy", action="store_true")
    args = ap.parse_args()

    plan = build_plan(
        period=args.period,
        anchor=args.anchor,
        week_start=args.week_start,
        include_current=args.include_current,
        yoy=not args.no_yoy,
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    _main()