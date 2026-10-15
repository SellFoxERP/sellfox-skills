#!/usr/bin/env python3
"""诊断引擎 —— 纯函数，不改系统。

输入：结构数（campaigns/keywords）+ 绩效数（perf_campaign/perf_keyword）+ 阈值配置
输出：Finding 列表；其中可自动执行的项由 :func:`build_change_items` 转成
      ``approval.ChangeItem``（dry-run 提案，仍需人工批准）。

设计原则：
* 只用**实测确认**的字段与取值。``state`` 仅用 enabled/paused/archived；
  ``servingStatus`` 存在但 ``BUDGET_CAPPED`` 取值未实测，故**仅作人工提示**，
  绝不作为自动判定条件。
* 阈值全部来自 ``_shared/config/thresholds.yaml``，幅度来自阈值与 optimizer.yaml。
* 数值缺失（None）一律跳过判定，标「无数据」，绝不按 0 处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# 数值与字段工具
# ---------------------------------------------------------------------------


def to_num(value: Any) -> Optional[float]:
    """尽力转为数值；空串/None/非数值返回 None（不当作 0）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("%", "").replace("$", "")
    if s == "" or s.lower() in ("nan", "none", "null", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pick(row: Dict[str, Any], mapping: Dict[str, Any], key: str) -> Any:
    """按候选列名依次匹配（xlsx 列名以实际导出为准，见 optimizer.yaml）。"""
    candidates = mapping.get(key) or [key]
    for name in candidates:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def metric_view(row: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """把任意来源的一行绩效归一化为规范指标字典。"""
    mv: Dict[str, Optional[float]] = {
        "cost": to_num(pick(row, mapping, "cost")),
        "sales": to_num(pick(row, mapping, "sales")),
        "orders": to_num(pick(row, mapping, "orders")),
        "clicks": to_num(pick(row, mapping, "clicks")),
        "impressions": to_num(pick(row, mapping, "impressions")),
        "acos": to_num(pick(row, mapping, "acos")),
        "roas": to_num(pick(row, mapping, "roas")),
        "click_rate": to_num(pick(row, mapping, "click_rate")),
    }
    # 缺失时优先用基础量自算（口径与报表一致时才可信）。
    if mv["acos"] is None and mv["cost"] is not None and mv["sales"]:
        mv["acos"] = mv["cost"] / mv["sales"] * 100.0
    if mv["roas"] is None and mv["sales"] is not None and mv["cost"]:
        mv["roas"] = mv["sales"] / mv["cost"]
    return mv


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """一条诊断结论。可自动执行的项才会被转成提案。"""

    rule: str
    level: str                 # P0 / P1 / P2
    scope: str                 # campaign / keyword / shop
    target_id: str
    target_name: str
    metrics: Dict[str, Optional[float]]
    proposed_tool: str = ""
    proposed_field: str = ""
    before: Any = None
    after: Any = None
    reason: str = ""
    auto_executable: bool = False
    manual_note: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        cost = self.metrics.get("cost")
        acos = self.metrics.get("acos")
        parts = [f"cost={cost:.2f}" if cost is not None else "cost=无数据"]
        if acos is not None:
            parts.append(f"acos={acos:.1f}%")
        return f"{self.target_name}({self.target_id}) " + " ".join(parts)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _clamp_step(before: float, step_pct: float, max_pct: float, *, direction: int) -> float:
    """按 step_pct 调整 before，并把绝对幅度夹到 max_pct 内。direction=+1 加、-1 减。"""
    pct = min(abs(step_pct), abs(max_pct)) * direction
    return round(before * (1.0 + pct / 100.0), 2)


def _structure_index(rows: Sequence[Dict[str, Any]], id_field: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = r.get(id_field)
        if key is not None:
            out[str(key)] = r
    return out


# ---------------------------------------------------------------------------
# 活动级规则
# ---------------------------------------------------------------------------


def campaign_findings(
    perf_rows: Sequence[Dict[str, Any]],
    campaigns: Sequence[Dict[str, Any]],
    thresholds: Dict[str, Any],
    opt_cfg: Dict[str, Any],
) -> List[Finding]:
    ad = thresholds.get("ad") or {}
    prop = opt_cfg.get("proposal") or {}
    safety = opt_cfg.get("safety") or {}
    appr = thresholds.get("approval") or {}
    mapping = opt_cfg.get("perf_columns") or {}

    zmin = float(ad.get("zero_order_cost_min") or 0)
    acos_max = float(ad.get("acos_abs_max") or 0)
    acos_target = float(ad.get("acos_target") or 0)
    roas_min = float(ad.get("roas_min") or 0)
    min_orders = float(prop.get("acos_min_orders") or 0)
    scale_max_impr = float(prop.get("scale_max_impressions") or 0)
    budget_step = float(prop.get("budget_step_pct") or 0)
    max_pct = float(appr.get("max_budget_change_pct") or 30)

    idx = _structure_index(campaigns, "campaignId")
    findings: List[Finding] = []

    for row in perf_rows:
        cid = pick(row, mapping, "campaign_id")
        if cid is None:
            continue
        cid = str(cid)
        struct = idx.get(cid, {})
        name = str(struct.get("name") or pick(row, mapping, "campaign_name") or cid)
        mv = metric_view(row, mapping)
        cost, orders, acos, roas, impr = mv["cost"], mv["orders"], mv["acos"], mv["roas"], mv["impressions"]

        # 规则 1：高花费零转化 → 暂停活动
        if cost is not None and orders is not None and cost >= zmin and orders == 0:
            before = struct.get("state")
            if str(before) == "paused":
                continue
            findings.append(Finding(
                rule="high_cost_zero_order", level="P0", scope="campaign",
                target_id=cid, target_name=name, metrics=mv,
                proposed_tool="edit_sp_campaign", proposed_field="state",
                before=before, after="paused",
                reason=f"近周期花费 {cost:.2f} 但订单为 0（阈值 {zmin:.0f}）",
                auto_executable=bool(safety.get("allow_campaign_state_edit")),
                manual_note="" if safety.get("allow_campaign_state_edit") else "活动状态编辑已在配置中关闭",
                extra={"shop_id": None},
            ))

        # 规则 2：ACOS 超标 → 需下钻关键词竞价（活动级不直接改，避免因果错误）
        elif (acos is not None and acos > acos_max and orders is not None
              and orders >= min_orders and cost is not None and cost >= zmin):
            findings.append(Finding(
                rule="acos_over", level="P1", scope="campaign",
                target_id=cid, target_name=name, metrics=mv,
                reason=f"ACOS {acos:.1f}% 超上限 {acos_max:.0f}%（订单 {orders:.0f}）",
                auto_executable=False,
                manual_note="活动级无竞价字段；请按关键词级 ACOS 规则下调竞价",
            ))

        # 规则 3：优质词活动扩量 → 提预算（曝光不足）
        if (acos is not None and acos < acos_target and roas is not None and roas > roas_min
                and impr is not None and impr < scale_max_impr and cost is not None and cost > 0):
            before = to_num(struct.get("budget"))
            if before is not None and before > 0:
                after = _clamp_step(before, budget_step, max_pct, direction=1)
                findings.append(Finding(
                    rule="scale_winning", level="P1", scope="campaign",
                    target_id=cid, target_name=name, metrics=mv,
                    proposed_tool="edit_sp_campaign", proposed_field="budget",
                    before=before, after=after,
                    reason=(f"ACOS {acos:.1f}% < 目标 {acos_target:.0f}%、ROAS {roas:.2f} > {roas_min}，"
                            f"曝光 {impr:.0f} 偏低，建议提预算"),
                    auto_executable=bool(safety.get("allow_campaign_budget_edit")),
                    manual_note="" if safety.get("allow_campaign_budget_edit") else "预算编辑已在配置中关闭",
                ))

        # 规则 4：素材衰退 → 仅提示，不自动执行
        baseline_cr = to_num(row.get("__prev_click_rate")) if isinstance(row, dict) else None
        cr = mv["click_rate"]
        drop_thr = float(ad.get("click_rate_drop_pct") or 0)
        if cr is not None and baseline_cr and baseline_cr > 0:
            drop = (baseline_cr - cr) / baseline_cr * 100.0
            if drop > drop_thr:
                findings.append(Finding(
                    rule="creative_decay", level="P2", scope="campaign",
                    target_id=cid, target_name=name, metrics=mv,
                    reason=f"点击率环比下降 {drop:.1f}%（阈值 {drop_thr:.0f}%），疑似素材衰退",
                    auto_executable=False,
                    manual_note="素材更换不在本技能能力范围，需人工处理（本技能不做素材替换）",
                ))

        # 规则 5：servingStatus —— 仅人工提示，绝不作自动判定
        serving = struct.get("servingStatus") or row.get("servingStatus")
        if serving and str(serving) != "CAMPAIGN_ACTIVE":
            findings.append(Finding(
                rule="serving_status_hint", level="P2", scope="campaign",
                target_id=cid, target_name=name, metrics=mv,
                reason=f"servingStatus={serving}（该字段取值未经完整实测，仅提示）",
                auto_executable=False,
                manual_note="是否为预算受限需人工在广告后台确认；本技能不以 servingStatus 自动判定",
            ))

    return findings


# ---------------------------------------------------------------------------
# 关键词级规则
# ---------------------------------------------------------------------------


def keyword_findings(
    perf_rows: Sequence[Dict[str, Any]],
    keywords: Sequence[Dict[str, Any]],
    thresholds: Dict[str, Any],
    opt_cfg: Dict[str, Any],
) -> List[Finding]:
    ad = thresholds.get("ad") or {}
    prop = opt_cfg.get("proposal") or {}
    safety = opt_cfg.get("safety") or {}
    appr = thresholds.get("approval") or {}
    mapping = opt_cfg.get("perf_columns") or {}

    zmin = float(ad.get("zero_order_cost_min") or 0)
    acos_max = float(ad.get("acos_abs_max") or 0)
    acos_target = float(ad.get("acos_target") or 0)
    roas_min = float(ad.get("roas_min") or 0)
    min_clicks = float(prop.get("keyword_min_clicks") or 0)
    bid_step = float(prop.get("bid_step_pct") or 0)
    max_pct = float(appr.get("max_budget_change_pct") or 30)

    idx = _structure_index(keywords, "keywordId")
    findings: List[Finding] = []

    for row in perf_rows:
        kid = pick(row, mapping, "keyword_id")
        if kid is None:
            continue
        kid = str(kid)
        struct = idx.get(kid, {})
        name = str(struct.get("keywordText") or pick(row, mapping, "keyword_text") or kid)
        mv = metric_view(row, mapping)
        cost, orders, acos, roas, clicks = mv["cost"], mv["orders"], mv["acos"], mv["roas"], mv["clicks"]
        bid = to_num(struct.get("bid"))

        # 规则 1：关键词高花费零转化 → 暂停投放（edit_sp_targeting）
        if cost is not None and orders is not None and cost >= zmin and orders == 0:
            state = struct.get("state")
            if str(state) == "paused":
                continue
            findings.append(Finding(
                rule="keyword_zero_order", level="P0", scope="keyword",
                target_id=kid, target_name=name, metrics=mv,
                proposed_tool="edit_sp_targeting", proposed_field="state",
                before=state, after="paused",
                reason=f"关键词花费 {cost:.2f} 但订单为 0（阈值 {zmin:.0f}）",
                auto_executable=False,  # targetId 语义未实测确认 → 默认人工
                manual_note="edit_sp_targeting 的 targetId 语义未实测确认，需人工核对后再执行",
                extra={"targetingType": "keyword", "campaignId": struct.get("campaignId"),
                       "groupId": struct.get("groupId")},
            ))
            continue

        # 规则 2：关键词 ACOS 超标 → 下调竞价
        if (acos is not None and acos > acos_max and clicks is not None and clicks >= min_clicks
                and bid is not None and bid > 0):
            after = _clamp_step(bid, bid_step, max_pct, direction=-1)
            findings.append(Finding(
                rule="keyword_acos_over", level="P1", scope="keyword",
                target_id=kid, target_name=name, metrics=mv,
                proposed_tool="edit_sp_targeting", proposed_field="bid",
                before=bid, after=after,
                reason=f"ACOS {acos:.1f}% 超上限 {acos_max:.0f}%，点击数 {clicks:.0f}，建议降竞价",
                auto_executable=bool(safety.get("allow_keyword_bid_edit")),
                manual_note="" if safety.get("allow_keyword_bid_edit") else
                "关键词竞价编辑默认关闭（targetId 语义未实测确认）",
                extra={"targetingType": "keyword", "campaignId": struct.get("campaignId")},
            ))

        # 规则 3：优质关键词扩量 → 上调竞价
        elif (acos is not None and acos < acos_target and roas is not None and roas > roas_min
              and bid is not None and bid > 0 and clicks is not None and clicks >= min_clicks):
            after = _clamp_step(bid, bid_step, max_pct, direction=1)
            findings.append(Finding(
                rule="keyword_scale_winning", level="P1", scope="keyword",
                target_id=kid, target_name=name, metrics=mv,
                proposed_tool="edit_sp_targeting", proposed_field="bid",
                before=bid, after=after,
                reason=f"ACOS {acos:.1f}% < 目标 {acos_target:.0f}%、ROAS {roas:.2f} > {roas_min}，建议提竞价",
                auto_executable=bool(safety.get("allow_keyword_bid_edit")),
                manual_note="" if safety.get("allow_keyword_bid_edit") else
                "关键词竞价编辑默认关闭（targetId 语义未实测确认）",
                extra={"targetingType": "keyword", "campaignId": struct.get("campaignId")},
            ))

    return findings


# ---------------------------------------------------------------------------
# 诊断入口
# ---------------------------------------------------------------------------


def diagnose(ad_data: Any, thresholds: Dict[str, Any], opt_cfg: Dict[str, Any]) -> List[Finding]:
    findings: List[Finding] = []
    if ad_data.perf_campaign:
        findings += campaign_findings(ad_data.perf_campaign, ad_data.campaigns, thresholds, opt_cfg)
    if ad_data.perf_keyword:
        findings += keyword_findings(ad_data.perf_keyword, ad_data.keywords, thresholds, opt_cfg)
    if not findings:
        findings.append(Finding(
            rule="insufficient_data" if not (ad_data.perf_campaign or ad_data.perf_keyword) else "no_issue", level="P2", scope="shop", target_id="-", target_name="全店",
            metrics={},
            reason="缺少绩效数据，无法判断 ACOS 或转化表现；以下仅展示活动与关键词结构。" if not (ad_data.perf_campaign or ad_data.perf_keyword) else "已取到的绩效样本未触发配置阈值。",
            auto_executable=False,
        ))
    return findings


# ---------------------------------------------------------------------------
# 提案构造
# ---------------------------------------------------------------------------


def build_change_items(findings: Sequence[Finding], opt_cfg: Dict[str, Any]) -> List[Any]:
    """把可自动执行的 Finding 转成 ``approval.ChangeItem``。

    规则：
      * 只取 ``auto_executable`` 且带 tool/field 的项；
      * 若全部项都是「暂停活动」（≥2 条），按禁止清单降级为人工 —— 批量全暂停被强制约束拦截；
      * 条数交由 ``Proposal.validate`` 再做一次上限校验（默认 20）。
    """
    from approval import ChangeItem

    items = []
    for f in findings:
        if not f.auto_executable or not f.proposed_tool or not f.proposed_field:
            continue
        items.append(ChangeItem(
            tool=f.proposed_tool,
            target_type="campaign" if f.scope == "campaign" else "targeting",
            target_id=f.target_id,
            field=f.proposed_field,
            before=f.before,
            after=f.after,
            reason=f.reason,
            action="set_budget" if f.proposed_field == "budget" else
                   ("pause" if f.after == "paused" else "set_bid"),
            risk="L2",
            extra=dict(f.extra),
            index=len(items) + 1,  # 1-based，供审批「批准 1,3,5」与回滚定位
        ))

    # 防「批量全暂停」：全部是暂停且 ≥2 条 → 全部降级为人工，不自动执行。
    if len(items) >= 2 and all(it.field == "state" and it.after == "paused" for it in items):
        for f in findings:
            if f.auto_executable and f.proposed_field == "state":
                f.auto_executable = False
                f.manual_note = (f.manual_note + "；" if f.manual_note else "") + \
                    "命中禁止清单：提案全部为暂停活动，需人工逐项确认"
        return []

    return items