#!/usr/bin/env python3
"""利润异常诊断 —— 落盘校验与组装层（fetch_profit.py，v3：零网络）。

v3 分工（见 `_shared/SELLFOX_CLIENT.md`）：
  * **取数面 = agent**：在会话里调 `mcp__Sellfox-MCP__{help,search,action}`，
    按 `references/workflow.md` 的**取数计划**把每步完整信封落盘到 `out/raw/<step>.json`。
  * **本脚本 = 计算面**：校验落盘文件（存在性/status/空数据/演示指纹/coverage 截断），
    并组装出与旧版完全相同形状的 `FetchResult` —— diagnose.py / render.py 零改动。

字段归属（实测确认）：``get_profit_report_by_msku``（171 字段，财务深度）承载
productSales / grossProfit / commission / fbaFulfillmentFee / cpcCost / storageFee /
refundRate / currencyList / rate 等全部归因因子。
⚠️ 自定义报表（``get_custom_report_page_list``）已不在本技能取数范围：
purchaseCost / headTripPrice / reimbursementAmount / warehouseLostAmount /
missingFromInboundAmount / deferredAmount 等**只存在于自定义报表**的字段，
本技能一律不取、不归因 —— 宽表因子归因由 `custom-report-analysis` 技能承接
（固定口径，见 SKILL.md「诚实边界」）。赔偿**条数级**核对另走
``get_amazon_reimbursement_report``（非自定义报表，保留）。

因此本模块把「字段 → 所属工具」固化成唯一事实来源（``FIELD_SOURCES`` 与 MSKU_FIELDS 表），
下游归因脚本一律按此取值，不在别处再猜字段归属。

落盘步骤名（双窗口，与取数计划一致；同一步骤按店铺分批时可落盘
``<step>.1.json`` / ``<step>.2.json``，本模块自动合并）：
    profit_msku_current.json / profit_msku_base.json   ← get_profit_report_by_msku（reportList）
    profit_shop_current.json / profit_shop_base.json   ← get_profit_report_by_shop（reportList；背景源）
    reimbursement.json                                 ← get_amazon_reimbursement_report（可能 data:{} → 无数据）

用法::

    # 1) agent 先按 workflow.md 取数计划落盘 out/raw/*.json
    # 2) 校验 + 组装（可独立运行，也可由 run_profit_diagnosis.py 在进程内调用）：
    python fetch_profit.py --end 2026-10-15 --period-days 30 --out out/profit_bundle.json

铁律对应（v3 语义）：
  * 脚本零网络 —— 不再 import sellfox_client，一切数据来自落盘信封。
  * 分页硬上限 20 / max_rows —— agent 取数侧安全阀；本模块对合并后的行数再做一次
    计算侧硬截断兜底，**截断时必须披露覆盖范围**（信封 coverage.truncated 同样转披露）。
  * 空数据语义 —— 空字典/空列表记「无数据」，不写 0，不重试。
  * 维度字段是列表 —— msku=["..."] / asin=[{"key":"..."}]，统一走 first_scalar/pick 归一化。
  * 零调用防护 —— 一个信封都没消费（mcp_calls=0）时显式告警，绝不产出「成功但空」的报告。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from _bootstrap import (
    SKILL_ROOT,
    RawEnvelope,
    detect_demo_environment,
    is_no_data,
    load_diagnosis,
    load_envelope,
    load_shops,
)


# ---------------------------------------------------------------------------
# 归一化工具
# ---------------------------------------------------------------------------
def first_scalar(value: Any) -> Any:
    """把赛狐的维度字段（列表 / {key:...}）压成标量。

    实测：msku=["MSKU-DEMO-001"]，asin=[{"key":"B0DEMO0001"}]，
    shopId=[{"key":"演示店铺001"}]。
    """
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            scalar = first_scalar(item)
            if scalar not in (None, ""):
                return scalar
        return None
    if isinstance(value, dict):
        for key in ("key", "value", "name", "label", "labelName"):
            if key in value:
                return first_scalar(value[key])
        return None
    return value


def pick(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            scalar = first_scalar(row[key])
            if scalar is not None and scalar != "":
                return scalar
    return None


def num(value: Any) -> Optional[float]:
    scalar = first_scalar(value)
    if scalar is None or isinstance(scalar, bool):
        return None
    if isinstance(scalar, (int, float)):
        return float(scalar)
    try:
        return float(str(scalar).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ⚠️ 字段 → 所属工具：唯一事实来源（实测确认，见 当前 MCP 契约与共享字段字典）
# ---------------------------------------------------------------------------
# 归因因子字段。key 与 config/diagnosis.yaml → attribution.factors[].field 对应。
# 自定义报表（get_custom_report_page_list）的 purchaseCost / headTripPrice /
# reimbursementAmount / warehouseLostAmount / missingFromInboundAmount / deferredAmount
# 已不在本技能取数范围（宽表因子归因由 custom-report-analysis 技能承接），
# 本表只维护 get_profit_report_by_msku 侧的字段。
MSKU_FIELDS: List[Tuple[str, Tuple[str, ...]]] = [
    # ---- 维度 / 身份 ----
    ("msku", ("msku", "mskuList")),
    ("asin", ("asin", "asinList")),
    ("sku", ("sku", "skuList")),
    ("shop_id", ("shopId",)),
    ("title", ("title", "commodityInfos")),
    # ---- 币种 / 汇率（铁律 7）----
    ("currency", ("currency",)),
    ("rate", ("rate",)),
    ("currency_list", ("currencyList",)),
    # ---- 销售 / 利润 ----
    ("product_sales", ("productSales",)),
    ("sales_num", ("salesNum",)),
    ("gross_profit", ("grossProfit",)),
    ("gross_profit_rate", ("grossProfitRate",)),
    ("roi", ("roi",)),
    ("revenue", ("revenue",)),
    # ---- 平台费用 ----
    ("commission", ("commission",)),
    ("fba_fulfillment_fee", ("fbaFulfillmentFee",)),
    ("fbm_postage_fee", ("fbmPostageFee",)),
    ("storage_fee", ("storageFee",)),
    ("storage_fee_rate", ("storageFeeRate",)),
    ("promotion_value", ("promotionValue",)),
    ("refund_sales", ("refundSales",)),
    # ---- 广告 ----
    ("cpc_cost", ("cpcCost",)),
    ("cpc_cost_rate", ("cpcCostRate",)),
    ("cpc_sales", ("cpcSales",)),
    # ---- 退货 ----
    ("refund_num", ("refundNum",)),
    ("refund_rate", ("refundRate",)),
    ("return_num", ("returnNum",)),
    # ---- 赔偿（msku 侧的汇总字段，明细在自定义报表）----
    ("compensation_fee", ("compensationFee",)),
    # ---- 成本（msku 侧只给金额、无细分；细分在自定义报表，由 custom-report-analysis 承接）----
    ("purchase_pc_fee", ("purchasePcFee",)),
    ("head_trip_pc_fee", ("headTripPcFee",)),
]

#: 字段 → 工具 的逆向索引，供归因表标注来源与人工核查。
FIELD_SOURCES: Dict[str, str] = {
    canonical: "get_profit_report_by_msku" for canonical, _ in MSKU_FIELDS
}


def source_tool_for(canonical: str) -> str:
    """返回某规范字段的所属工具（归因表用它标注来源）。"""
    return FIELD_SOURCES.get(canonical, "⚠️ 未知（需人工核查）")


#: 原始 API 字段名 → 规范名。config/diagnosis.yaml 的 factors[].field 用原始字段名，
#: 归因脚本据此映射到规范化记录里的键。
_RAW_TO_CANONICAL: Dict[str, str] = {}
for _canon, _cands in MSKU_FIELDS:
    for _raw in _cands:
        _RAW_TO_CANONICAL.setdefault(_raw, _canon)
for _canon, _ in MSKU_FIELDS:
    _RAW_TO_CANONICAL.setdefault(_canon, _canon)


def canonical_for(raw_field: str) -> str:
    """原始 API 字段名 → 规范化键（用于把 diagnosis.yaml 的因子映射到记录字段）。"""
    return _RAW_TO_CANONICAL.get(raw_field, raw_field)


def _normalize(row: Dict[str, Any], fields: Sequence[Tuple[str, Tuple[str, ...]]],
               *, source: str) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"_source": source}
    # 窗口标记（current / baseline）由取数编排设置；归一化时必须保留，否则环比分不出本期与基期。
    if "_window" in row:
        rec["_window"] = row["_window"]
    for canonical, candidates in fields:
        rec[canonical] = pick(row, *candidates)
    # 关键数值字段统一数值化（维度字段保持字符串）
    for key, value in list(rec.items()):
        if key in ("_source", "_window", "msku", "asin", "sku", "shop_id", "title", "currency"):
            continue
        if key == "currency_list":
            continue
        rec[key] = num(value)
    rec["msku"] = first_scalar(rec.get("msku")) or ""
    return rec


def normalize_msku_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """get_profit_report_by_msku 行 → 规范化记录（列表字段 reportList）。"""
    return _normalize(row, MSKU_FIELDS, source="msku_report")


# ---------------------------------------------------------------------------
# 结果容器（形状与旧版一致 —— diagnose.py / render.py 零改动）
# ---------------------------------------------------------------------------
@dataclass
class FetchResult:
    msku_rows: List[Dict[str, Any]] = field(default_factory=list)
    shop_rows: List[Dict[str, Any]] = field(default_factory=list)
    reimbursement_rows: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    coverage: List[str] = field(default_factory=list)     # 截断/无数据/失败披露语句
    warnings: List[str] = field(default_factory=list)
    demo_fingerprint: Optional[str] = None
    caliber: str = "settlement"
    mcp_calls: int = 0                                    # v3：实际消费的落盘信封数（防「零取数却报成功」）
    # ---- 评测修复（2026-10-16）新增的三态/范围字段 ----
    reimbursement_status: str = "未取数"                   # 未取数 / 无数据 / 已取数 N 批 M 行 / 取数失败（error_code：X）
    failed_steps: List[str] = field(default_factory=list) # 取数失败步骤（label+error_code），供渲染层区分「失败」与「无数据」
    requested_asins: List[str] = field(default_factory=list)  # 请求侧 ASIN 定向值（信封 scope.search_contents），供渲染核对是否命中

    def index_by_msku(self, rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = row.get("msku") or ""
            if key:
                out[key] = row
        return out


# ---------------------------------------------------------------------------
# 环比窗口（纯计算，v3 不变）
# ---------------------------------------------------------------------------
def resolve_windows(
    end: date, period_days: int, *, max_lookback_days: int
) -> Tuple[date, date, date, date, List[str]]:
    """按等长「本期 vs 上一期」计算两组窗口。

    返回 ``(cur_start, cur_end, base_start, base_end, warnings)``。上一期是紧邻本期的等长窗口，
    不重叠 —— 否则环比会被同期数据自身污染。

    ⚠️ 夹取（clamp）必须**保持等长**且**保证 ``base_start <= base_end``**。利润报表按日查询
    限制最近 ``max_lookback_days`` 天（见 01 接口清单：按日查询限制最近 6 个月）。当本期窗口
    本身就吃掉大半回看额度时，旧的夹取式 ``cur_end - (max_lookback_days - period_days)`` 会算出
    起点晚于终点、甚至晚于本期起点的**非法/非等长基期**，静默产出错误甚至反向的环比归因。

    因此这里改为：本期窗口最多取 ``max_lookback_days // 2`` 天（保证基期仍是被允许的完整等长窗口）；
    一旦 ``period_days`` 超限，**把本期窗口截短**（基期是本期的完整上一等长窗口，二者仍等长），
    并把「已截短」写进返回的 ``warnings``，由调用方写入报告的「数据说明」——绝不做无披露的夹取。
    """
    period_days = max(1, int(period_days))
    warnings: List[str] = []
    cur_end = end

    # 上限：每期最多占回看额度的一半，确保「本期 + 基期」都在 max_lookback_days 之内。
    cap = max(1, int(max_lookback_days) // 2) if max_lookback_days else period_days
    if max_lookback_days and period_days > cap:
        warnings.append(
            f"⚠️ 本期天数 {period_days} 天超过可回看上限（max_lookback_days={max_lookback_days} 的一半 = {cap} 天），"
            f"已把本期与基期一并截短为各 {cap} 天，仍为等长窗口；如需更长窗口请改用按月查询（date_type=1）或调小 --period-days。"
        )
        period_days = cap

    cur_start = cur_end - timedelta(days=period_days - 1)
    base_end = cur_start - timedelta(days=1)
    base_start = base_end - timedelta(days=period_days - 1)

    # 不变式自检：越界即报错，绝不静默返回非法窗口。
    if base_start > base_end:
        raise ValueError(
            f"基期窗口非法：base_start={base_start} 晚于 base_end={base_end}"
            f"（period_days={period_days}，max_lookback_days={max_lookback_days}）。"
        )
    if (base_end - base_start).days != (cur_end - cur_start).days:
        raise ValueError(
            f"基期与本期窗口不等长：本期 {(cur_end - cur_start).days} 天，"
            f"基期 {(base_end - base_start).days} 天。"
        )
    return cur_start, cur_end, base_start, base_end, warnings


# ---------------------------------------------------------------------------
# 落盘信封读取与校验（v3 计算面）
# ---------------------------------------------------------------------------
def _load_step(raw_dir: Path, stem: str) -> Tuple[List[RawEnvelope], List[str]]:
    """读取一个取数步骤的落盘信封（含按店铺分批的 ``<stem>.1.json`` 等批次文件）。

    返回 ``(信封列表, 告警列表)``。文件缺失返回空列表（由调用方决定降级方式）；
    JSON 损坏记告警并跳过，不让单个坏文件拖垮整轮组装。
    """
    envs: List[RawEnvelope] = []
    warns: List[str] = []
    paths = [raw_dir / f"{stem}.json"] + sorted(raw_dir.glob(f"{stem}.*.json"))
    for p in paths:
        if not p.exists():
            continue
        try:
            envs.append(load_envelope(p))
        except Exception as exc:  # noqa: BLE001 — JSON 损坏只降级该文件
            warns.append(f"{p.name} 无法解析（已跳过）：{exc}")
    return envs, warns


def _cov_line(env: RawEnvelope, label: str) -> str:
    """把信封 coverage / 行数转成一句覆盖披露（与旧版 TruncationInfo.describe() 口径一致）。

    ⚠️ 「取数失败」与「无数据」必须区分（评测问题 #2）：failed 信封的 data 恒为 None，
    但它是**失败**（带 error_code），不是该期真的没有数据 —— 混同会掩盖需要人工核查的事实。
    """
    n = len(env.rows())
    if env.status == "failed":
        return f"{label}: 取数失败（error_code：{env.error_code or '?'}）"
    if is_no_data(env.data):
        return f"{label}: 无数据"
    cov = env.coverage or {}
    if cov.get("truncated"):
        # ⚠️ 截断必披露：不信 pagination.has_more，只信 coverage（实测两者矛盾）。
        return (
            f"{label}: ⚠️ 本次仅覆盖 {cov.get('fetched_count', n)} 行"
            f"（服务端报告约 {cov.get('total_count')} 行），已达 max_rows 上限，其余未纳入统计。"
        )
    return f"{label}: 本次共取回 {n} 行（未截断）。"


def _env_issues(env: RawEnvelope, label: str) -> List[str]:
    """status 层面的问题（failed → 不可采信；partial → 可采信但必须披露）。"""
    if env.status == "failed":
        return [
            f"{label} 取数失败（error_code={env.error_code or '?'}）：{env.message or ''}"
            "→ 该源标「⚠️ 需人工核查」，其余照常。"
        ]
    if env.partial:
        return [f"{label} status=partial：部分成功，数据可采信但必须在报告「数据说明」披露。"]
    return []


def _norm_period_date(value: Any) -> Optional[str]:
    """信封 ``period`` 日期值 → ``yyyy-MM-dd``（兼容 ``yyyyMMdd`` 与带时分秒形态）。"""
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return None


def _warn_period_mismatch(
    result: "FetchResult", label: str, env_start: Any, env_end: Any,
    win_start: date, win_end: date,
) -> None:
    """落盘信封 period 与 resolve_windows 口径窗口不一致 → 告警（评测线上问题 N4：差 1 天无告警）。

    v3 信任 agent 取数，但取数窗口跑偏会让环比比较两个不同期间 —— 必须显式告警，不得静默。
    """
    env_ws, env_we = _norm_period_date(env_start), _norm_period_date(env_end)
    exp_ws, exp_we = f"{win_start}", f"{win_end}"
    if env_ws and env_we and (env_ws, env_we) != (exp_ws, exp_we):
        result.warnings.append(
            f"⚠️ {label}：落盘信封请求窗口 {env_ws}~{env_we} 与脚本环比口径窗口 {exp_ws}~{exp_we} 不一致"
            "（取数必须按 resolve_windows 结果传参），环比可能比较了不同期间，请人工核对落盘参数。"
        )


#: 步骤标签 → FetchResult 行字段（msku / shop）。
_TARGET_ATTR = {"MSKU 利润报表本期": "msku_rows", "MSKU 利润报表基期": "msku_rows",
                "店铺级利润本期": "shop_rows", "店铺级利润基期": "shop_rows"}


def _consume_step(
    raw_dir: Path,
    stem: str,
    label: str,
    window: str,
    normalize,
    result: FetchResult,
    *,
    list_key: str,
    max_rows: int,
    source_tool: str,
    win_start: Optional[date] = None,
    win_end: Optional[date] = None,
) -> bool:
    """消费一个双窗口步骤的落盘信封：校验 → 归一化 → 并入 FetchResult。

    返回是否消费到了至少一个信封（用于判断该源是否可用）。
    ``win_start`` / ``win_end`` 是该窗口的脚本口径起止日（resolve_windows 结果），
    用于校验落盘信封 period 是否与口径窗口一致（不一致 → 告警）。
    """
    envs, warns = _load_step(raw_dir, stem)
    result.warnings.extend(warns)
    if not envs:
        return False

    if source_tool not in result.sources:  # 双窗口共用同一工具，数据源只记一次
        result.sources.append(source_tool)
    rows: List[Dict[str, Any]] = []
    from sellfox_offline import detect_date_filter_unreliable
    for env in envs:
        result.mcp_calls += 1
        batch = "" if len(envs) == 1 else f"（批 {env.path.name}）"
        result.coverage.append(_cov_line(env, f"{label}{batch}"))
        result.warnings.extend(_env_issues(env, f"{label}{batch}"))
        result.warnings.extend(str(w) for w in env.warnings)
        period = env.obj.get("period") or {}
        start, end = period.get("start_date"), period.get("end_date")
        if start and end and detect_date_filter_unreliable(env.data, start, end):
            result.warnings.append(f"{label}：返回业务日期与请求窗口不符；仅展示返回样本，不能解释为实际期间变化。")
        if start and end and win_start and win_end:
            _warn_period_mismatch(result, label, start, end, win_start, win_end)
        # 请求侧 ASIN 定向值（范围防御，评测线上问题 N3）：上游 asin 过滤可能被忽略，
        # 把信封 scope 里记录的请求值留给渲染层核对是否 0 命中。
        scope = env.obj.get("scope") or {}
        if source_tool == "get_profit_report_by_msku" and scope.get("search_type") == "asin":
            contents = scope.get("search_contents")
            contents = contents if isinstance(contents, (list, tuple)) else [contents]
            for item in contents:
                scalar = first_scalar(item)
                if scalar is not None and str(scalar) not in result.requested_asins:
                    result.requested_asins.append(str(scalar))
        if env.status == "failed":
            result.failed_steps.append(f"{label}（error_code={env.error_code or '?'}）")
            continue  # failed 信封的行不可采信
        for row in env.rows(list_key):
            if isinstance(row, dict):
                rec = normalize(dict(row))
                rec["_window"] = window
                rows.append(rec)
        fp = detect_demo_environment(env.obj)
        if fp and not result.demo_fingerprint:
            result.demo_fingerprint = fp

    # 计算侧 max_rows 兜底：agent 取数侧应有同值安全阀，这里防止合并批次后超限。
    if len(rows) > max_rows:
        result.coverage.append(
            f"{label}: ⚠️ 合并后共 {len(rows)} 行，超出计算侧 max_rows={max_rows}，"
            f"仅保留前 {max_rows} 行，其余未纳入统计。"
        )
        result.warnings.append(
            f"⚠️ {label} 触达 max_rows={max_rows} 上限，本次仅覆盖部分 MSKU，其余未纳入统计。"
        )
        rows = rows[:max_rows]
    getattr(result, _TARGET_ATTR[label]).extend(rows)
    return True


# ---------------------------------------------------------------------------
# 编排：校验 + 组装（v3 取数面已前置到 agent）
# ---------------------------------------------------------------------------
def assemble_all(
    *,
    shop_ids: Sequence[str],
    cur_start: date,
    cur_end: date,
    base_start: date,
    base_end: date,
    skill_cfg: Dict[str, Any],
    raw_dir: Optional[Path] = None,
    result: Optional[FetchResult] = None,
    window_warnings: Sequence[str] = (),
) -> FetchResult:
    """数据源 × 双窗口：校验 ``out/raw/`` 落盘并组装 FetchResult。

    窗口：本期 = [cur_start, cur_end]；基期 = [base_start, base_end]。
    每个数据源各消费两份落盘（本期 + 基期），文件名见模块 docstring 的落盘映射。

    ``window_warnings`` 由 :func:`resolve_windows` 产出（窗口被截短等），在此并入
    ``result.warnings``，确保「比较口径不再等长/被夹取」这类事实进入报告而非静默。
    """
    result = result or FetchResult()
    result.warnings.extend(window_warnings)
    limits = skill_cfg.get("max_rows") or {}
    batch = int(skill_cfg.get("shop_batch_size", 3))
    caliber = (skill_cfg.get("caliber") or {}).get("default", "settlement")
    result.caliber = caliber

    rd = Path(raw_dir) if raw_dir else (SKILL_ROOT / "out" / "raw")
    rd = rd if rd.is_absolute() else (SKILL_ROOT / rd)
    if not rd.exists() or not any(rd.glob("*.json")):
        raise SystemExit(
            f"❌ 落盘目录为空或不存在：{rd}。\n"
            "   v3 取数面由 agent 完成：请按 references/workflow.md 的「取数计划」\n"
            "   调用 mcp__Sellfox-MCP__{search,action} 并把信封落盘到 out/raw/，再重跑本脚本。"
        )

    # 店铺维度披露：空 = 全部店铺（agent 不传 shop_ids，服务端默认全量）。
    # 明确记一条覆盖语句，避免「空维度」被误读成「无数据」或静默变成零调用。
    effective_shop_ids = [s for s in shop_ids if s]
    if effective_shop_ids:
        result.coverage.append(
            f"店铺范围：{len(effective_shop_ids)} 个店铺，按每批 {batch} 个分批取数。"
        )
    else:
        result.coverage.append(
            "店铺范围：未指定店铺，按【全部店铺】查询（不传 shop_ids，服务端默认全量）。"
        )

    # ---- 主源：MSKU 利润报表（171 字段，财务深度；reportList）----
    msku_max = int(limits.get("msku_report", 200))
    for label, stem, window in (
        ("MSKU 利润报表本期", "profit_msku_current", "current"),
        ("MSKU 利润报表基期", "profit_msku_base", "baseline"),
    ):
        got = _consume_step(
            rd, stem, label, window, normalize_msku_row, result,
            list_key="reportList", max_rows=msku_max, source_tool="get_profit_report_by_msku",
            win_start=cur_start if window == "current" else base_start,
            win_end=cur_end if window == "current" else base_end,
        )
        if not got:
            result.warnings.append(
                f"{label}落盘缺失（out/raw/{stem}.json）：该窗口无 MSKU 记录，"
                "请按取数计划落盘后重跑；相关归因将标「⚠️ 需人工核查」。"
            )

    # 自定义报表（custom_current.json / custom_base.json）已不在本技能取数计划内：
    # 采购成本/头程/赔偿金额/延期结算等宽表因子归因由 custom-report-analysis 技能承接
    #（固定口径，渲染层固定披露）；历史落盘中存在的宽表文件一并忽略、不消费。
    stale_customs = sorted(
        [p.name for p in rd.glob("custom_current*.json")]
        + [p.name for p in rd.glob("custom_base*.json")]
    )
    if stale_customs:
        result.coverage.append(
            f"自定义报表落盘（{', '.join(stale_customs)}）已不在本技能取数范围，未消费；"
            "宽表因子归因（采购成本/头程/赔偿金额/延期结算）由 custom-report-analysis 技能承接。"
        )

    # ---- 背景源：店铺级利润（判断是整体恶化还是个别 MSKU；reportList）----
    shop_max = int(limits.get("shop_report", 60))
    shop_any = False
    for label, stem, window in (
        ("店铺级利润本期", "profit_shop_current", "current"),
        ("店铺级利润基期", "profit_shop_base", "baseline"),
    ):
        shop_any = _consume_step(
            rd, stem, label, window, normalize_msku_row, result,
            list_key="reportList", max_rows=shop_max, source_tool="get_profit_report_by_shop",
            win_start=cur_start if window == "current" else base_start,
            win_end=cur_end if window == "current" else base_end,
        ) or shop_any
    if not shop_any:
        result.warnings.append(
            "店铺级利润报表落盘缺失（out/raw/profit_shop_current.json / profit_shop_base.json，"
            "不影响主流程）：无法判断「整体恶化 vs 个别 MSKU」。"
        )

    # ---- 交叉核对源：Amazon 赔偿报告（可能 data:{} → 无数据，不重试）----
    envs, warns = _load_step(rd, "reimbursement")
    result.warnings.extend(warns)
    if envs:
        # 修复（评测问题 #1）：该源被消费即注册进 sources —— 数据源清单不得漏
        # get_amazon_reimbursement_report；「本次记录数 / 状态」按真实落盘信封给出：
        # 有信封=已取数 N 批（M 行）/ data 空=无数据 / failed=失败（error_code：X）。
        if "get_amazon_reimbursement_report" not in result.sources:
            result.sources.append("get_amazon_reimbursement_report")
        failed_codes: List[str] = []
        for env in envs:
            result.mcp_calls += 1
            batch = "" if len(envs) == 1 else f"（批 {env.path.name}）"
            result.coverage.append(_cov_line(env, f"reimbursement{batch}"))
            result.warnings.extend(_env_issues(env, f"reimbursement{batch}"))
            if env.status == "failed":
                code = env.error_code or "?"
                failed_codes.append(code)
                result.failed_steps.append(f"reimbursement（error_code={code}）")
            else:
                # 只做条数级核对，不臆造字段名（字段结构未在字段清单中固化）。
                result.reimbursement_rows.extend(env.rows("rows"))
        codes_text = "、".join(dict.fromkeys(failed_codes))
        if failed_codes and not result.reimbursement_rows:
            result.reimbursement_status = f"取数失败（error_code：{codes_text}）"
        elif failed_codes:
            result.reimbursement_status = (
                f"已取数 {len(envs)} 批 / {len(result.reimbursement_rows)} 行"
                f"（其中 {len(failed_codes)} 批失败：{codes_text}）"
            )
        elif not result.reimbursement_rows:
            result.reimbursement_status = "无数据"
        else:
            result.reimbursement_status = f"已取数 {len(envs)} 批 / {len(result.reimbursement_rows)} 行"
    else:
        result.warnings.append(
            "Amazon 赔偿报告落盘缺失（out/raw/reimbursement.json，不影响主流程）："
            "赔偿漏领交叉核对不可用。"
        )

    # 演示指纹兜底：信封级扫描未命中时再扫归一化行（形状与旧版一致）。
    result.demo_fingerprint = (
        result.demo_fingerprint
        or detect_demo_environment(result.msku_rows)
    )

    # 零调用防护：正常路径至少会消费主源 A 的两份信封（本期+基期）。
    # 若一个信封都没消费，说明落盘为空/全损——绝不能返回一份「成功但空」的报告
    # 让人误以为当期真的没有异常。
    if result.mcp_calls == 0:
        result.warnings.append(
            "⚠️ 本次未消费任何取数落盘信封（mcp_calls=0，落盘目录："
            f"{rd}）。报告为空**不代表当期无异常**，而是取数未执行 —— "
            "请按 references/workflow.md 取数计划落盘后重跑。"
        )
    return result


# ---------------------------------------------------------------------------
# CLI：python scripts/fetch_profit.py --end ... --period-days ... --out ...
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="利润异常诊断 - 落盘校验与组装（零网络）")
    ap.add_argument("--end", default=None, help="本期期末日 yyyy-MM-dd（默认今天）")
    ap.add_argument("--period-days", type=int, default=None, help="本期天数（默认读 diagnosis.yaml）")
    ap.add_argument("--shop-ids", default=None, help="逗号分隔店铺 ID（默认读 shops.yaml；空=全部店铺）")
    ap.add_argument("--caliber", default=None, help="利润口径 settlement / shipment（默认读配置）")
    ap.add_argument("--raw-dir", default=None, help="落盘目录（默认 <skill>/out/raw）")
    ap.add_argument("--out", default=None, help="输出组装结果 JSON（FetchResult 序列化；缺省只打印摘要）")
    args = ap.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else date.today()
    cfg = load_diagnosis()
    compare = cfg.get("compare") or {}
    period_days = int(args.period_days or compare.get("period_days", 30))
    if args.caliber:
        cfg.setdefault("caliber", {})["default"] = args.caliber

    if args.shop_ids:
        shop_ids = [s.strip() for s in args.shop_ids.split(",") if s.strip()]
    else:
        shop_ids = [str(s) for s in (load_shops().get("default_scope") or [])]

    cur_start, cur_end, base_start, base_end, window_warnings = resolve_windows(
        end, period_days, max_lookback_days=int(compare.get("max_lookback_days", 180))
    )

    result = assemble_all(
        shop_ids=shop_ids,
        cur_start=cur_start, cur_end=cur_end,
        base_start=base_start, base_end=base_end,
        skill_cfg=cfg,
        raw_dir=Path(args.raw_dir) if args.raw_dir else None,
        window_warnings=window_warnings,
    )

    print(
        f"✅ 组装完成：本期 {cur_start}~{cur_end} / 基期 {base_start}~{base_end}，"
        f"msku={len(result.msku_rows)} "
        f"shop={len(result.shop_rows)} reimbursement={len(result.reimbursement_rows)} "
        f"（消费落盘信封 {result.mcp_calls} 份，利润口径 {result.caliber}）"
    )
    if result.demo_fingerprint:
        print(f"⚠️ 演示环境指纹：{result.demo_fingerprint}（数据非真实经营数据）")
    for c in result.coverage:
        print(f"· 覆盖：{c}")
    for w in result.warnings:  # 文本自带 ⚠️ 前缀，不再叠加
        print(w)

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = SKILL_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ 组装结果已写出：{out}")

    return 2 if result.mcp_calls == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
